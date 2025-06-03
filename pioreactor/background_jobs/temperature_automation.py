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


MAX_HEATER_DUTY_CYCLE = 70.0

class TemperatureAutomationJob(AutomationJob):
    """
    This is the super class that Temperature automations inherit from.
    The `execute` function, which is what subclasses will define, is updated every time a new temperature is computed.
    Temperatures are updated every `INFERENCE_EVERY_N_SECONDS` seconds.

    To change setting over MQTT:

    `pioreactor/<unit>/<experiment>/temperature_automation/<setting>/set` value
    """

    MAX_TEMP_TO_REDUCE_HEATING = 63.0
    MAX_TEMP_TO_DISABLE_HEATING = 65.0
    MAX_TEMP_TO_SHUTDOWN = 66.0

    INFERENCE_EVERY_N_SECONDS: float = 30
    
    # Constants for liquid loss detection

    PLATEAU_WINDOW_SECONDS = 300  # Time to declare plateau with <=0 positive slope
    PLATEAU_TEMP_CHANGE_THRESHOLD: float = 0.05  # °C change considered a plateau
    PLATEAU_MIN_DUTY_CYCLE: float = 65  # Minimum duty cycle to consider plateau detection

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

        if whoami.is_testing_env():
            from pioreactor.utils.mock import MockTMP1075 as TMP1075
        else:
            from pioreactor.utils.temps import MCP9600  # type: ignore
            from pioreactor.hardware import Thermocouple_ADDR

        self.heater_duty_cycle = 0.0
        self.pwm = self.setup_pwm()

        self.heating_pcb_tmp_driver = MCP9600(Thermocouple_ADDR)

        # Initialize liquid loss detection
        self.history = []

        # Single timer triggers infer_temperature() at self.INFERENCE_EVERY_N_SECONDS
        self.temperature_timer = RepeatedTimer(
            int(self.INFERENCE_EVERY_N_SECONDS),
            self.infer_temperature,
            job_name=self.job_name,
            run_immediately=True,
        ).start()

        # COMMENTED OUT: timestamps related to OD & growth rate
        # self.latest_normalized_od_at: datetime = current_utc_datetime()
        # self.latest_growth_rate_at: datetime = current_utc_datetime()
        self.latest_temperture_at: datetime = current_utc_datetime()

        # self.kp = 1.2  # Starting Guesses for Kp/Ki
        # self.ki = 0.015
        # self.integral_error = 0.0


    

    def on_init_to_ready(self):
        if whoami.is_testing_env() or self.seconds_since_last_active_heating() >= 10:
            # if we turn off heating and turn on again, without some sort of time to cool, the first temperature looks wonky
            self.temperature = Temperature(
                temperature=self.read_external_temperature(),
                timestamp=current_utc_datetime(),
            )

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

    ########## Private & internal methods

    def _read_external_temperature(self) -> float:
        """
        Read the current temperature from our sensor, in Celsius
        """
        try:
            running_sum, running_count = 0.0, 0
            for _ in range(6):
                running_sum += self.heating_pcb_tmp_driver.get_hot_junction_temperature()
                running_count += 1
                sleep(0.05)
            averaged_temp = running_sum / running_count
            with local_intermittent_storage("temperature_and_heating") as cache:
                cache["water_temperature"] = averaged_temp
                cache["water_temperature_at"] = current_utc_timestamp()
            return self._check_if_exceeds_max_temp(averaged_temp)
        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError("Water temperature sensor not found.")

    def _update_heater(self, new_duty_cycle: float) -> bool:
        # if new_duty_cycle < 5:  # lower duty cycle
        #     new_duty_cycle = 0.0
        # clamp to [required range], round to two decimals
        self.heater_duty_cycle = clamp(0.0, round(float(new_duty_cycle), 3), 70)  # last number upper duty cycle
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
            self.turn_off_heater()

    def on_sleeping(self) -> None:
        self.temperature_timer.pause()
        self._update_heater(0)

    def on_sleeping_to_ready(self) -> None:
        self.temperature_timer.unpause()

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
