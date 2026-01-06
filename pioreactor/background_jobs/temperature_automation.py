# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone, timedelta
from time import sleep
from typing import Any
from typing import cast
from typing import Optional

import click
from msgspec.json import decode

from pioreactor import error_codes
from pioreactor import exc
from pioreactor import hardware
from pioreactor import structs
from pioreactor import types as pt
from pioreactor.automations.base import AutomationJob
from pioreactor.config import config
from pioreactor.logging import create_logger
from pioreactor.structs import Temperature
from pioreactor.utils import clamp
from pioreactor.utils import is_pio_job_running
from pioreactor.utils import local_intermittent_storage
from pioreactor.utils import whoami
from pioreactor.utils.pwm import PWM
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.utils.timing import current_utc_timestamp
from pioreactor.utils.timing import RepeatedTimer
from pioreactor.utils.timing import to_datetime
from pioreactor.version import rpi_version_info


MAX_HEATER_DUTY_CYCLE = 100.0

class TemperatureAutomationJob(AutomationJob):
    """
    This is the super class that Temperature automations inherit from.
    The `execute` function, which is what subclasses will define, is updated every time a new temperature is computed.
    Temperatures are updated every `INFERENCE_EVERY_N_SECONDS` seconds.

    To change setting over MQTT:

    `pioreactor/<unit>/<experiment>/temperature_automation/<setting>/set` value
    """

    MAX_TEMP_TO_REDUCE_HEATING = 38.0
    MAX_TEMP_TO_DISABLE_HEATING = 40.0
    MAX_TEMP_TO_SHUTDOWN = 45.0
    
    # Heater temperature limits for cascade control
    MAX_HEATER_TEMP = 80.0              # Hard limit - reduce to 0% above this
    HEATER_LIMIT_START = 75.0           # Start proportional reduction
    EMERGENCY_SHUTDOWN_TEMP = 90.0      # Emergency shutdown regardless of other conditions
    
    # Safety thresholds
    DRY_HEATER_DELTA = 40.0             # Heater-water temp difference indicating dry heater
    RUNAWAY_TEMP_DELTA = 8.0            # Water temp above target indicating runaway
    NO_RESPONSE_TIME = 15               # Seconds of high DC with no heating response
    NO_RESPONSE_MIN_DC = 10             # Minimum DC to check for heater response
    NO_RESPONSE_MIN_RISE = 3.0          # Minimum temperature rise expected

    INFERENCE_EVERY_N_SECONDS: float = 10   # Outer loop (water temp control)
    HEATER_CHECK_EVERY_N_SECONDS: float = 1  # Inner loop (heater limiting)
    
    # Constants for liquid loss detection

    PLATEAU_WINDOW_SECONDS = 60*30  # Time to declare plateau with <=0 positive slope
    PLATEAU_TEMP_CHANGE_THRESHOLD: float = 0.05  # °C change considered a plateau
    PLATEAU_MIN_DUTY_CYCLE: float = 65  # Minimum duty cycle to consider plateau detection

    latest_temperature = None

    automation_name = "temperature_automation_base"  # is overwritten in subclasses
    job_name = "temperature_automation"

    published_settings: dict[str, pt.PublishableSetting] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if (
            hasattr(cls, "automation_name")
            and getattr(cls, "automation_name") != "temperature_automation_base"
        ):
            available_temperature_automations[cls.automation_name] = cls

    def __init__(
        self,
        unit: str,
        experiment: str,
        **kwargs,
    ) -> None:
        super(TemperatureAutomationJob, self).__init__(unit, experiment)

        # declare these to be published automatically
        self.add_to_published_settings(
            "temperature", {"datatype": "Temperature", "settable": False, "unit": "℃"}
        )
        self.add_to_published_settings(
            "heater_duty_cycle", {"datatype": "float", "settable": False, "unit": "%"}
        )
        self.add_to_published_settings(
            "heater_temperature", {"datatype": "float", "settable": False, "unit": "℃"}
        )

        if whoami.is_testing_env():
            from pioreactor.utils.mock import MockTMP1075 as TMP1075
        else:
            from pioreactor.utils.temps import ADS1115_Thermistor
            from pioreactor.hardware import (
                NTC_Thermistor_ADDR,
                WATER_TEMP_CHANNEL, WATER_TEMP_REF_CHANNEL, WATER_TEMP_R_REF, WATER_TEMP_DATA_RATE,
                WATER_TEMP_STEINHART_A, WATER_TEMP_STEINHART_B, WATER_TEMP_STEINHART_C,
                HEATER_TEMP_CHANNEL, HEATER_TEMP_REF_CHANNEL, HEATER_TEMP_R_REF, HEATER_TEMP_DATA_RATE,
                HEATER_TEMP_STEINHART_A, HEATER_TEMP_STEINHART_B, HEATER_TEMP_STEINHART_C
            )

        self.heater_duty_cycle = 0.0
        self.desired_duty_cycle = 0.0  # Output from outer loop (water PID)
        self.pwm = self.setup_pwm()

        # Initialize water temperature sensor (10K NTC on A0)
        self.water_temp_driver = ADS1115_Thermistor(
            address=NTC_Thermistor_ADDR,
            thermistor_channel=WATER_TEMP_CHANNEL,
            ref_channel=WATER_TEMP_REF_CHANNEL,
            r_ref=WATER_TEMP_R_REF,
            data_rate=WATER_TEMP_DATA_RATE  # 128 SPS - fast
        )
        self.water_temp_driver.set_thermistor_parameters(
            steinhart_a=WATER_TEMP_STEINHART_A,
            steinhart_b=WATER_TEMP_STEINHART_B,
            steinhart_c=WATER_TEMP_STEINHART_C
        )

        # Initialize heater temperature sensor (100K NTC on A1)
        self.heater_temp_driver = ADS1115_Thermistor(
            address=NTC_Thermistor_ADDR,
            thermistor_channel=HEATER_TEMP_CHANNEL,
            ref_channel=HEATER_TEMP_REF_CHANNEL,
            r_ref=HEATER_TEMP_R_REF,
            data_rate=HEATER_TEMP_DATA_RATE  # 16 SPS - slow for settling
        )
        self.heater_temp_driver.set_thermistor_parameters(
            steinhart_a=HEATER_TEMP_STEINHART_A,
            steinhart_b=HEATER_TEMP_STEINHART_B,
            steinhart_c=HEATER_TEMP_STEINHART_C
        )

        # Initialize liquid loss detection
        self.history = []
        
        # Heater safety tracking
        self.heater_temperature = None
        self.heater_high_dc_start_time = None  # Track when high DC started for no-response check
        self.heater_temp_at_high_dc_start = None

        # Single timer triggers infer_temperature() at self.INFERENCE_EVERY_N_SECONDS
        self.temperature_timer = RepeatedTimer(
            int(self.INFERENCE_EVERY_N_SECONDS),
            self.infer_temperature,
            job_name=self.job_name,
            run_immediately=True,
        ).start()
        
        # Inner loop timer for heater temperature monitoring and limiting
        self.heater_check_timer = RepeatedTimer(
            int(self.HEATER_CHECK_EVERY_N_SECONDS),
            self.check_and_limit_heater,
            job_name=self.job_name,
            run_immediately=False,  # Let outer loop start first
        ).start()

        # COMMENTED OUT: timestamps related to OD & growth rate
        # self.latest_normalized_od_at: datetime = current_utc_datetime()
        # self.latest_growth_rate_at: datetime = current_utc_datetime()
        self.latest_temperture_at: datetime = current_utc_datetime()

        # self.kp = 1.2  # Starting Guesses for Kp/Ki
        # self.ki = 0.015
        # self.integral_error = 0.0
        self.latest_temperture_at: datetime = current_utc_datetime()
    

    def on_init_to_ready(self):
        if whoami.is_testing_env() or self.seconds_since_last_active_heating() >= 10:
            # if we turn off heating and turn on again, without some sort of time to cool, the first temperature looks wonky
            self.temperature = Temperature(
                temperature=self.read_external_temperature(),
                timestamp=current_utc_datetime(),
            )
            self._set_latest_temperature(self.temperature)

    @staticmethod
    def seconds_since_last_active_heating() -> float:
        with local_intermittent_storage("temperature_and_heating") as cache:
            if "last_heating_timestamp" in cache:
                return (current_utc_datetime() - to_datetime(cache["last_heating_timestamp"])).total_seconds()
            else:
                return 1_000_000

    def turn_off_heater(self) -> None:
        self._update_heater(0)
        self.pwm.clean_up()
        self.pwm = self.setup_pwm()
        self._update_heater(0)
        self.pwm.clean_up()

    def update_heater(self, new_duty_cycle: float) -> bool:
        """
        Update heater's duty cycle. This function checks for the PWM lock, and will not
        update if the PWM is locked.

        Returns true if the update was made (eg: no lock), else returns false
        """

        if not self.pwm.is_locked():
            return self._update_heater(new_duty_cycle)
        else:
            return False

    def update_heater_with_delta(self, delta_duty_cycle: float) -> bool:
        """
        Update heater's duty cycle by `delta_duty_cycle` amount. This function checks for the PWM lock, and will not
        update if the PWM is locked.

        Returns true if the update was made (eg: no lock), else returns false
        """
        return self.update_heater(self.heater_duty_cycle + delta_duty_cycle)

    def read_external_temperature(self) -> float:
        return self._check_if_exceeds_max_temp(self._read_external_temperature())

    def is_heater_pwm_locked(self) -> bool:
        """
        Check if the heater PWM channels is locked
        """
        return self.pwm.is_locked()

    def detect_temp_plateau(self) -> bool:
        """
        Detect suspicious temperature plateaus during heating which may indicate liquid loss.
        Simply checks if temperature change between last two readings is below threshold.
        
        Returns:
            bool: True if a plateau is detected (potential liquid loss), False otherwise
        """
        # Need both temperature readings and significant heating to detect a plateau
        if self.current_temp is None or self.previous_temp is None or self.heater_duty_cycle < self.PLATEAU_MIN_DUTY_CYCLE:
            return False
        
        # Calculate temperature change between current and previous reading
        temp_change = self.current_temp - self.previous_temp
        
        self.logger.debug(f"Temp change: {temp_change:.3f}°C")
        
        # If heater is on significantly but temperature is barely rising, this is suspicious
        if (self.heater_duty_cycle >= self.PLATEAU_MIN_DUTY_CYCLE and 
            temp_change < self.PLATEAU_TEMP_CHANGE_THRESHOLD):
            
            self.plateau_count += 1
            self.logger.debug(f"Temperature plateau detected ({self.plateau_count}/{self.PLATEAU_CONSECUTIVE_COUNT}). "
                            f"Duty cycle: {self.heater_duty_cycle}%, "
                            f"Temp change: {temp_change:.3f}°C")
            
            if self.plateau_count >= self.PLATEAU_CONSECUTIVE_COUNT:
                return True
        else:
            self.plateau_count = 0
            
        return False

    def check_and_limit_heater(self) -> None:
        """
        Inner loop: Read heater temperature and apply limiting/safety checks.
        This runs every HEATER_CHECK_EVERY_N_SECONDS (5s) and modulates the
        duty cycle set by the outer water temperature control loop.
        
        Safety checks implemented:
        1. Heater overheat protection (proportional limiting 75-80°C, hard limit >80°C)
        2. Emergency shutdown (>90°C)
        3. Dry heater detection (heater-water temp delta >40°C)
        4. No response detection (high DC but no temperature rise)
        5. Water runaway detection (water >8°C above target)
        """
        try:
            # Read heater temperature
            heater_temp = self._read_heater_temperature()
            self.heater_temperature = heater_temp
            
            # Get current water temperature for safety checks
            water_temp = self.latest_temperature if self.latest_temperature is not None else 25.0
            
            # Start with the desired duty cycle from outer loop
            limited_dc = self.desired_duty_cycle
            
            # === SAFETY CHECK 1: Emergency Shutdown ===
            if heater_temp > self.EMERGENCY_SHUTDOWN_TEMP:
                self.logger.error(
                    f"EMERGENCY: Heater temperature {heater_temp:.1f}°C exceeds {self.EMERGENCY_SHUTDOWN_TEMP}°C. "
                    f"Shutting down immediately."
                )
                self._update_heater(0)
                self.blink_error_code(error_codes.PCB_TEMPERATURE_TOO_HIGH)
                self.set_state(self.DISCONNECTED)
                return
            
            # === SAFETY CHECK 2: Heater Overheat Protection (Proportional Limiting) ===
            if heater_temp > self.MAX_HEATER_TEMP:
                # Above max: exponential reduction
                overshoot = heater_temp - self.MAX_HEATER_TEMP
                reduction_factor = max(0, 1.0 - (overshoot / 5.0))  # 0% at +5°C over limit
                limited_dc = self.desired_duty_cycle * reduction_factor
                self.logger.warning(
                    f"Heater temp {heater_temp:.1f}°C exceeds {self.MAX_HEATER_TEMP}°C. "
                    f"Limiting duty cycle from {self.desired_duty_cycle:.1f}% to {limited_dc:.1f}%"
                )
            elif heater_temp > self.HEATER_LIMIT_START:
                # Approaching max: linear reduction
                temp_margin = self.MAX_HEATER_TEMP - heater_temp
                full_margin = self.MAX_HEATER_TEMP - self.HEATER_LIMIT_START
                limit_factor = temp_margin / full_margin  # 1.0 at 75°C, 0.0 at 80°C
                limited_dc = self.desired_duty_cycle * limit_factor
                self.logger.debug(
                    f"Heater temp {heater_temp:.1f}°C approaching limit. "
                    f"Reducing duty cycle to {limited_dc:.1f}% (factor: {limit_factor:.2f})"
                )
            
            # === SAFETY CHECK 3: Dry Heater Detection ===
            temp_delta = heater_temp - water_temp
            if temp_delta > self.DRY_HEATER_DELTA:
                self.logger.error(
                    f"Heater temp ({heater_temp:.1f}°C) is {temp_delta:.1f}°C above water temp ({water_temp:.1f}°C). "
                    f"Heater may be out of water. Shutting down."
                )
                self._update_heater(0)
                self.set_state(self.DISCONNECTED)
                return
            elif temp_delta > self.DRY_HEATER_DELTA * 0.7:  # Warning at 70% of threshold
                self.logger.warning(
                    f"Heater temp ({heater_temp:.1f}°C) is {temp_delta:.1f}°C above water temp ({water_temp:.1f}°C). "
                    f"Monitor for potential dry heater condition."
                )
            
            # === SAFETY CHECK 4: No Response Detection ===
            if limited_dc > self.NO_RESPONSE_MIN_DC:
                if self.heater_high_dc_start_time is None:
                    # Start tracking
                    self.heater_high_dc_start_time = current_utc_datetime()
                    self.heater_temp_at_high_dc_start = heater_temp
                else:
                    # Check if enough time has passed
                    time_elapsed = (current_utc_datetime() - self.heater_high_dc_start_time).total_seconds()
                    if time_elapsed > self.NO_RESPONSE_TIME:
                        temp_rise = heater_temp - self.heater_temp_at_high_dc_start
                        if temp_rise < self.NO_RESPONSE_MIN_RISE:
                            self.logger.error(
                                f"Heater duty cycle >{self.NO_RESPONSE_MIN_DC}% for {time_elapsed:.0f}s "
                                f"but temperature only rose {temp_rise:.1f}°C (expected >{self.NO_RESPONSE_MIN_RISE}°C). "
                                f"Heater may be disconnected or sensor faulty. Shutting down."
                            )
                            self._update_heater(0)
                            self.set_state(self.DISCONNECTED)
                            return
            else:
                # Reset tracking when DC drops
                self.heater_high_dc_start_time = None
                self.heater_temp_at_high_dc_start = None
            
            # === SAFETY CHECK 5: Water Runaway Detection ===
            if hasattr(self, 'target_temperature') and self.target_temperature is not None:
                if water_temp > self.target_temperature + self.RUNAWAY_TEMP_DELTA:
                    self.logger.error(
                        f"Water temperature ({water_temp:.1f}°C) exceeds target ({self.target_temperature:.1f}°C) "
                        f"by {water_temp - self.target_temperature:.1f}°C. Runaway condition detected. Shutting down."
                    )
                    self._update_heater(0)
                    self.set_state(self.DISCONNECTED)
                    return
            
            # Apply the limited duty cycle
            if limited_dc != self.heater_duty_cycle:
                self._update_heater(limited_dc)
                self.logger.debug(f"Heater check: water={water_temp:.1f}°C, heater={heater_temp:.1f}°C, DC={limited_dc:.1f}%")
                
        except OSError as e:
            self.logger.warning(f"Could not read heater temperature: {e}")
            # Don't shut down on transient sensor errors, but log them

    ########## Private & internal methods

    def _read_external_temperature(self) -> float:
        """
        Read the current water temperature from 10K NTC sensor on A0
        """
        try:
            # Driver now averages resistance before converting (more accurate)
            averaged_temp = self.water_temp_driver.get_temperature(samples=5)
            
            with local_intermittent_storage("temperature_and_heating") as cache:
                cache["water_temperature"] = averaged_temp
                cache["water_temperature_at"] = current_utc_timestamp()
            
            return self._check_if_exceeds_max_temp(averaged_temp)
        
        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError("Water temperature sensor not found.")

    def _read_heater_temperature(self) -> float:
        """
        Read the heater element temperature from 100K NTC sensor on A1
        """
        try:
            # Fewer samples since this runs more frequently
            # Driver averages resistance first for better accuracy
            averaged_temp = self.heater_temp_driver.get_temperature(samples=5)
            
            with local_intermittent_storage("temperature_and_heating") as cache:
                cache["heater_temperature"] = averaged_temp
                cache["heater_temperature_at"] = current_utc_timestamp()
            
            return averaged_temp
        
        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError("Heater temperature sensor not found.")
    
    def _update_heater(self, new_duty_cycle: float) -> bool:
        # clamp to [required range], round to three decimals
        self.heater_duty_cycle = clamp(0.0, round(float(new_duty_cycle), 3), MAX_HEATER_DUTY_CYCLE)
        self.pwm.change_duty_cycle(self.heater_duty_cycle)

        if self.heater_duty_cycle == 0.0:
            with local_intermittent_storage("temperature_and_heating") as cache:
                cache["last_heating_timestamp"] = current_utc_timestamp()

        return True

    def _check_if_exceeds_max_temp(self, temp: float) -> float:
        if temp > self.MAX_TEMP_TO_SHUTDOWN:
            self.logger.error(
                f"Water temp has exceeded {self.MAX_TEMP_TO_SHUTDOWN}℃ - currently {temp}℃. Shutting down")
            self._update_heater(0)
            self.blink_error_code(error_codes.PCB_TEMPERATURE_TOO_HIGH)
            from subprocess import call
            call("sudo shutdown now --poweroff", shell=True)

        elif temp > self.MAX_TEMP_TO_DISABLE_HEATING:
            self.blink_error_code(error_codes.PCB_TEMPERATURE_TOO_HIGH)
            self.logger.warning(
                f"Temperature of water has exceeded {self.MAX_TEMP_TO_DISABLE_HEATING}℃ - currently {temp}℃. Shutting down heater")
            self._update_heater(0)

        elif temp > self.MAX_TEMP_TO_REDUCE_HEATING:
            self.logger.debug(
                f"Temperature of water has exceeded {self.MAX_TEMP_TO_REDUCE_HEATING}℃ - currently {temp}℃. Reducing heater power")
            self._update_heater(self.heater_duty_cycle * 0.9)

        return temp

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self._update_heater(0)

        with suppress(AttributeError):
            self.temperature_timer.cancel()
        
        with suppress(AttributeError):
            self.heater_check_timer.cancel()

        with suppress(AttributeError):
            self.turn_off_heater()

    def on_sleeping(self) -> None:
        self.temperature_timer.pause()
        self.heater_check_timer.pause()
        self._update_heater(0)

    def on_sleeping_to_ready(self) -> None:
        self.temperature_timer.unpause()
        self.heater_check_timer.unpause()

    def setup_pwm(self) -> PWM:
        # technically this doesn't need to be high: it could even be 1hz. However, we want to smooth it's
        # impact (mainly: current sink), over the second. Ex: imagine freq=1hz, dc=40%, and the pump needs to run for
        # 0.3s. The influence of when the heat is on the pump can be sign-[/ificant in a power-constrained system.
        hertz = .75
        pin = hardware.PWM_TO_PIN[hardware.HEATER_PWM_TO_PIN]
        pwm = PWM(pin, hertz, unit=self.unit, experiment=self.experiment, pubsub_client=self.pub_client)
        pwm.start(0)
        return pwm

    def infer_temperature(self) -> None:
        """
        1. lock PWM and turn off heater
        2. read temperature once (or more) and publish directly
        3. check for liquid loss condition
        """
        # CHANGED: removed the logic that took multiple samples to do a regression.
        # CHANGED: instead, we are simply measuring once (while turning off the heater or not) and publishing.
        # CHANGED: We still lock the PWM so that nothing else changes it while we measure
        assert not self.pwm.is_locked(), "PWM is locked - it shouldn't be though!"
        with self.pwm.lock_temporarily():
            previous_heater_dc = self.heater_duty_cycle
            self._update_heater(0)  # turn off heater if you want a passive measurement
            sleep(1)
            measured_temp = self.read_external_temperature()
            self._update_heater(previous_heater_dc)

        # Update temperature record
        self.temperature = Temperature(
            temperature=round(measured_temp, 2),
            timestamp=current_utc_datetime(),
        )
        
        #check for liquid losses
        timestamp = current_utc_timestamp()
        self.history.append((timestamp, self.temperature, self.heater_duty_cycle))
        now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        window_start = now - timedelta(seconds=600)
        self.history = [
            entry for entry in self.history
            if datetime.fromisoformat(entry[0].replace("Z", "+00:00")) >= window_start
        ]
        self.check_for_liquid_loss()
        self._set_latest_temperature(self.temperature)

    def check_for_liquid_loss(self):
        now = current_utc_datetime()  # Use datetime object
        window_start = now - timedelta(seconds=self.PLATEAU_WINDOW_SECONDS)
        recent_history = [
            entry for entry in self.history
            if datetime.fromisoformat(entry[0].replace("Z", "+00:00")) >= window_start
        ]
        if len(recent_history) < 2:
            return
        start_time_str, start_temp, _ = recent_history[0]
        end_time_str, end_temp, _ = recent_history[-1]
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
        time_span = (end_time - start_time).total_seconds()
        if time_span < self.PLATEAU_WINDOW_SECONDS * 0.8:
            return
        temp_change = end_temp.temperature - start_temp.temperature
        duty_cycles = [entry[2] for entry in recent_history]
        avg_duty_cycle = sum(duty_cycles) / len(duty_cycles) if duty_cycles else 0
        if avg_duty_cycle > self.PLATEAU_MIN_DUTY_CYCLE and temp_change < self.PLATEAU_TEMP_CHANGE_THRESHOLD:
            self.logger.debug(
                f"Avg duty cycle: {avg_duty_cycle:.2f}%, Temp change: {temp_change:.2f}°C over {time_span:.1f}s"
            )
            self.logger.error("Heater may be out of water. Disabling heating.")
            self.set_state(self.DISCONNECTED)

    def _set_latest_temperature(self, temperature: structs.Temperature) -> None:
        # Note: this doesn't use MQTT data (previously it use to)
        self.previous_temperature = self.latest_temperature
        self.latest_temperature = temperature.temperature
        self.latest_temperature_at = temperature.timestamp

        if self.state == self.READY or self.state == self.INIT:
            self.latest_event = self.execute()

        return

class TemperatureAutomationJobContrib(TemperatureAutomationJob):
    automation_name: str


def start_temperature_automation(
    automation_name: str,
    unit: Optional[str] = None,
    experiment: Optional[str] = None,
    **kwargs,
) -> TemperatureAutomationJob:
    from pioreactor.automations import temperature  # noqa: F401

    unit = unit or whoami.get_unit_name()
    experiment = experiment or whoami.get_assigned_experiment_name(unit)
    try:
        klass = available_temperature_automations[automation_name]
    except KeyError:
        raise KeyError(
            f"Unable to find {automation_name}. "
            f"Available automations are {list( available_temperature_automations.keys())}"
        )

    if "skip_first_run" in kwargs:
        del kwargs["skip_first_run"]

    try:
        return klass(
            unit=unit,
            experiment=experiment,
            automation_name=automation_name,
            **kwargs,
        )
    except Exception as e:
        logger = create_logger("temperature_automation")
        logger.error(e)
        logger.debug(e, exc_info=True)
        raise e


available_temperature_automations: dict[str, type[TemperatureAutomationJob]] = {}


@click.command(
    name="temperature_automation",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.option(
    "--automation-name",
    help="set the automation of the system: silent, etc.",
    show_default=True,
    required=True,
)
@click.pass_context
def click_temperature_automation(ctx, automation_name):
    """
    Start an Temperature automation
    """
    la = start_temperature_automation(
        automation_name=automation_name,
        **{ctx.args[i][2:].replace("-", "_"): ctx.args[i + 1] for i in range(0, len(ctx.args), 2)},
    )
    la.block_until_disconnected()