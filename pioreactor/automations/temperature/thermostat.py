# -*- coding: utf-8 -*-
from __future__ import annotations

from pioreactor.automations.events import UpdatedHeaterDC
from pioreactor.automations.temperature.base import TemperatureAutomationJob
from pioreactor.config import config
from pioreactor.utils import clamp
from pioreactor.utils import is_pio_job_running
from pioreactor.utils.streaming_calculations import PID


class Thermostat(TemperatureAutomationJob):
    """
    CASCADE CONTROL SYSTEM FOR WATER TEMPERATURE WITH HEATER PROTECTION
    ====================================================================
    
    This thermostat uses a dual-loop cascade control system with comprehensive safety checks:
    
    CONTROL ARCHITECTURE:
    ---------------------
    
    OUTER LOOP (30 seconds):
        - Measures: Water temperature (10K NTC sensor on A0)
        - Setpoint: User-defined target temperature (e.g., 37°C)
        - Controller: PID (Kp, Ki, Kd from config)
        - Output: desired_duty_cycle (0-100%)
        - Purpose: Maintain water at target temperature
    
    INNER LOOP (5 seconds):
        - Measures: Heater element temperature (100K NTC sensor on A1)
        - Input: desired_duty_cycle from outer loop
        - Controller: Proportional limiter based on heater temperature
        - Output: actual_duty_cycle (0-100%, limited if heater too hot)
        - Purpose: Prevent heater overheat and provide fast safety response
    
    WHY CASCADE CONTROL?
        - Decouples heater thermal mass from water thermal mass
        - Prevents heater overshoot and thermal runaway
        - Faster response to dangerous conditions
        - Inherent protection against dry heater scenarios
    
    SAFETY CHECKS (Inner Loop - Every 5 seconds):
    ----------------------------------------------
    
    1. HEATER OVERHEAT PROTECTION:
       - At 75-80°C: Proportionally reduce duty cycle (linear)
       - At 80-85°C: Exponentially reduce duty cycle
       - At >90°C: EMERGENCY SHUTDOWN
       - Prevents heater element damage
    
    2. DRY HEATER DETECTION:
       - If heater_temp - water_temp > 40°C: SHUTDOWN
       - Warning at >28°C difference
       - Indicates heater is out of water (catastrophic)
    
    3. NO RESPONSE DETECTION:
       - If duty cycle >50% for >60 seconds but heater temp rise <5°C: SHUTDOWN
       - Indicates sensor failure or disconnected heater
    
    4. WATER RUNAWAY DETECTION:
       - If water_temp > target + 8°C: SHUTDOWN
       - Indicates loss of control
    
    5. WATER OVERHEAT (Inherited from base class):
       - >66°C: System shutdown
       - >65°C: Disable heating
       - >63°C: Reduce heating power
    
    CONTROL FLOW:
    -------------
    User sets target_water_temp = 37°C
                    ↓
    [Every 30s] Outer loop reads water temp → PID → desired_dc
                    ↓
    [Every 5s] Inner loop reads heater temp → Apply limits → actual_dc
                    ↓
           Check all safety conditions
                    ↓
              Update PWM heater
    
    PUBLISHED SETTINGS:
    -------------------
    - temperature: Water temperature (°C)
    - heater_temperature: Heater element temperature (°C)
    - heater_duty_cycle: Actual PWM duty cycle (%)
    - target_temperature: User setpoint (°C, settable)
    """

    MAX_TARGET_TEMP = 40

    automation_name = "thermostat"
    published_settings = {"target_temperature": {"datatype": "float", "unit": "℃", "settable": True}}

    def __init__(self, target_temperature: float | str, **kwargs) -> None:
        super().__init__(**kwargs)
        assert target_temperature is not None, "target_temperature must be set"

        # Outer loop PID controller for water temperature
        self.pid = PID(
            Kp=config.getfloat("temperature_automation.thermostat", "Kp"),
            Ki=config.getfloat("temperature_automation.thermostat", "Ki"),
            Kd=config.getfloat("temperature_automation.thermostat", "Kd"),
            setpoint=None,
            unit=self.unit,
            experiment=self.experiment,
            job_name=self.job_name,
            target_name="temperature",
            output_limits=(-15, 15),  # avoid whiplashing - max ±25% change per cycle
        )

        self.set_target_temperature(target_temperature)

    def on_init_to_ready(self):
        super().on_init_to_ready()
        if not is_pio_job_running("custom_air_bubbler"):
            self.logger.warning("It's recommended to have airbubbler on when using the thermostat.")

    def _clamp_target_temperature(self, target_temperature: float) -> float:
        if target_temperature > self.MAX_TARGET_TEMP:
            self.logger.warning(
                f"Values over {self.MAX_TARGET_TEMP}℃ are not supported. Setting to {self.MAX_TARGET_TEMP}℃."
            )

        return clamp(0.0, target_temperature, self.MAX_TARGET_TEMP)

    def execute(self) -> UpdatedHeaterDC:
        """
        Outer loop execution (every 30 seconds):
        - Read water temperature
        - Calculate PID output (delta duty cycle)
        - Update desired_duty_cycle (which inner loop will limit)
        """
        while not hasattr(self, "pid"):
            # sometimes when initializing, this execute can run before the subclasses __init__ is resolved.
            pass

        assert self.latest_temperature is not None
        
        # PID calculates change in duty cycle based on water temperature error
        output = self.pid.update(self.latest_temperature, dt=1)
        
        # Update desired duty cycle (outer loop output)
        # Inner loop will apply heater temperature limiting to this value
        self.desired_duty_cycle = clamp(0.0, self.heater_duty_cycle + output, 100.0)
        
        self.logger.debug(
            f"Outer loop: water={self.latest_temperature:.1f}°C, "
            f"target={self.target_temperature:.1f}°C, "
            f"PID_output={output:.1f}%, "
            f"desired_dc={self.desired_duty_cycle:.1f}%, "
            f"rate={self.temperature_rate_of_change:.2f}°C/min"
        )

        return UpdatedHeaterDC(
            f"delta_dc={output}",
            data={
                "current_dc": self.heater_duty_cycle,
                "delta_dc": output,
                "desired_dc": self.desired_duty_cycle,
                "target_temperature": self.target_temperature,
                "latest_temperature": self.latest_temperature,
                "heater_temperature": self.heater_temperature,
            },
        )

    def set_target_temperature(self, target_temperature: float | str) -> None:
        """
        Parameters
        ------------

        target_temperature: float
            the new target temperature for water
        """
        target_temperature = float(target_temperature)
        self.target_temperature = self._clamp_target_temperature(target_temperature)
        self.pid.set_setpoint(self.target_temperature)