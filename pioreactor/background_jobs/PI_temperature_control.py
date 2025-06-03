from __future__ import annotations
from typing import Optional
from pioreactor.automations.temperature.base import TemperatureAutomationJob
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.utils import clamp


MAX_HEATER_DUTY_CYCLE = 70

class PIControlTemperature(TemperatureAutomationJob):

    # Implements a PI (Proportional-Integral) controller for temperature regulation.
    # Adjusts heater duty cycle based on the error between target and actual temperature.
    automation_name = "pi_control_temperature"
    
    published_settings = {
        "target_temperature": {"datatype": "float", "settable": True, "unit": "℃"}
    }
    
    def __init__(
        self,
        target_temperature: Optional[float] = None,
        kp: float = 1.2,
        ki: float = 0.015,  
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        
        self.target_temperature = target_temperature or 30.0  
        self.kp = kp
        self.ki = ki
        self.integral_error = 0.0
        self.last_time = current_utc_datetime()

    def execute(self) -> None:
        # Executes the PI control adjusting the heater duty cycle.
       # Called every INFERENCE_EVERY_N_SECONDS seconds after temperature measurement.
        
        if self.target_temperature is None or self.temperature is None:
            self.logger.warning("Target temperature or current temperature not set. Skipping execution.")
            return
        
        # Calculate time delta for integral term
        current_time = current_utc_datetime()
        dt = (current_time - self.last_time).total_seconds() / 3600.0 
        self.last_time = current_time
        
        error = self.target_temperature - self.temperature.temperature
        self.integral_error += error * dt
        proportional_term = self.kp * error
        integral_term = self.ki * self.integral_error
        
        new_duty_cycle = proportional_term + integral_term
        new_duty_cycle = clamp(0.0, new_duty_cycle, MAX_HEATER_DUTY_CYCLE)
        
        if self.update_heater(new_duty_cycle):
            self.logger.debug(
                f"PI Control: error={error:.2f}°C, integral_error={self.integral_error:.2f}, "
                f"duty_cycle={new_duty_cycle:.2f}%"
            )
        else:
            self.logger.debug("PWM is locked, unable to update heater duty cycle.")
            
        self.check_for_liquid_loss()