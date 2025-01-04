# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from datetime import datetime
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


class TemperatureAutomationJob(AutomationJob):
    """
    This is the super class that Temperature automations inherit from.
    The `execute` function, which is what subclasses will define, is updated every time a new temperature is computed.
    Temperatures are updated every `INFERENCE_EVERY_N_SECONDS` seconds.

    To change setting over MQTT:

    `pioreactor/<unit>/<experiment>/temperature_automation/<setting>/set` value

    """

    INFERENCE_SAMPLES_EVERY_T_SECONDS: float = 5.0

    if whoami.get_pioreactor_version() == (1, 0):
        # made from PLA
        MAX_TEMP_TO_REDUCE_HEATING = 63.0
        MAX_TEMP_TO_DISABLE_HEATING = 65.0  # probably okay, but can't stay here for too long
        MAX_TEMP_TO_SHUTDOWN = 66.0
        INFERENCE_N_SAMPLES: int = 29
        INFERENCE_EVERY_N_SECONDS: float = 225.0

    elif whoami.get_pioreactor_version() >= (1, 1):
        # made from PC-CF
        MAX_TEMP_TO_REDUCE_HEATING = 78.0
        MAX_TEMP_TO_DISABLE_HEATING = 80.0
        MAX_TEMP_TO_SHUTDOWN = 85.0  # risk damaging PCB components
        INFERENCE_N_SAMPLES = 21
        INFERENCE_EVERY_N_SECONDS = 200.0

    inference_total_time = INFERENCE_SAMPLES_EVERY_T_SECONDS * INFERENCE_N_SAMPLES
    assert INFERENCE_EVERY_N_SECONDS > inference_total_time
    # PWM is on for (INFERENCE_EVERY_N_SECONDS - inference_total_time) seconds
    # the ratio of time a PWM is on is equal to (INFERENCE_EVERY_N_SECONDS - inference_total_time) / INFERENCE_EVERY_N_SECONDS

    _latest_growth_rate: Optional[float] = None
    _latest_normalized_od: Optional[float] = None
    previous_normalized_od: Optional[float] = None
    previous_growth_rate: Optional[float] = None

    latest_temperature = None
    previous_temperature = None

    automation_name = "temperature_automation_base"  # is overwritten in subclasses
    job_name = "temperature_automation"

    published_settings: dict[str, pt.PublishableSetting] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # this registers all subclasses of TemperatureAutomationJob
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

        self.add_to_published_settings(
            "temperature", {"datatype": "Temperature", "settable": False, "unit": "℃"}
        )

        self.add_to_published_settings(
            "heater_duty_cycle",
            {"datatype": "float", "settable": False, "unit": "%"},
        )

        if whoami.is_testing_env():
            from pioreactor.utils.mock import MockTMP1075 as TMP1075
        else:
            from pioreactor.utils.temps import MCP9600  # type: ignore
            from pioreactor.hardware import Thermocouple_ADDR

        self.heater_duty_cycle = 0.0
        self.pwm = self.setup_pwm()

        self.heating_pcb_tmp_driver = MCP9600(Thermocouple_ADDR)

        self.read_external_temperature_timer = RepeatedTimer(
            53,
            self.read_external_temperature,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

        self.publish_temperature_timer = RepeatedTimer(
            int(self.INFERENCE_EVERY_N_SECONDS),
            self.infer_temperature,
            job_name=self.job_name,
            run_after=self.INFERENCE_EVERY_N_SECONDS
            - self.inference_total_time,  # This gives an automation a "full" PWM cycle to be on before an inference starts.
            run_immediately=True,
        ).start()

        self.latest_normalized_od_at: datetime = current_utc_datetime()
        self.latest_growth_rate_at: datetime = current_utc_datetime()
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
        # we re-instantiate it as some other process may have messed with the channel.
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

    @property
    def most_stale_time(self) -> datetime:
        return min(self.latest_normalized_od_at, self.latest_growth_rate_at)

    @property
    def latest_growth_rate(self) -> float:
        # check if None
        if self._latest_growth_rate is None:
            # this should really only happen on the initialization.
            self.logger.debug("Waiting for OD and growth rate data to arrive")
            if not all(is_pio_job_running(["od_reading", "growth_rate_calculating"])):
                raise exc.JobRequiredError("`od_reading` and `growth_rate_calculating` should be Ready.")

        # check most stale time
        if (current_utc_datetime() - self.most_stale_time).seconds > 5 * 60:
            raise exc.JobRequiredError(
                "readings are too stale (over 5 minutes old) - are `od_reading` and `growth_rate_calculating` running?"
            )

        return cast(float, self._latest_growth_rate)

    @property
    def latest_normalized_od(self) -> float:
        # check if None
        if self._latest_normalized_od is None:
            # this should really only happen on the initialization.
            self.logger.debug("Waiting for OD and growth rate data to arrive")
            if not all(is_pio_job_running(["od_reading", "growth_rate_calculating"])):
                raise exc.JobRequiredError("`od_reading` and `growth_rate_calculating` should be running.")

        # check most stale time
        if (current_utc_datetime() - self.most_stale_time).seconds > 5 * 60:
            raise exc.JobRequiredError(
                "readings are too stale (over 5 minutes old) - are `od_reading` and `growth_rate_calculating` running?"
            )

        return cast(float, self._latest_normalized_od)

    ########## Private & internal methods

    def _read_external_temperature(self) -> float:
        """
        Read the current temperature from our sensor, in Celsius
        """
        running_sum, running_count = 0.0, 0
        try:
            # check temp is fast, let's do it a few times to reduce variance.
            for i in range(6):
                running_sum += self.heating_pcb_tmp_driver.get_hot_junction_temperature()
                running_count += 1
                sleep(0.05)

        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the Heating PCB attached to the Pioreactor HAT? Unable to find temperature sensor."
            )

        averaged_temp = running_sum / running_count
        if averaged_temp == 0.0 and self.automation_name != "only_record_temperature":
            # this is a hardware fluke, not sure why, see #308. We will return something very high to make it shutdown
            # todo: still needed? last observed on  July 18, 2022
            self.logger.error("Temp sensor failure. Switching off. See issue #308")
            self._update_heater(0.0)

        with local_intermittent_storage("temperature_and_heating") as cache:
            cache["heating_pcb_temperature"] = averaged_temp
            cache["heating_pcb_temperature_at"] = current_utc_timestamp()

        return averaged_temp

    def _update_heater(self, new_duty_cycle: float) -> bool:
        self.heater_duty_cycle = clamp(0.0, round(float(new_duty_cycle), 2), 100.0)
        self.pwm.change_duty_cycle(self.heater_duty_cycle)

        if self.heater_duty_cycle == 0.0:
            with local_intermittent_storage("temperature_and_heating") as cache:
                cache["last_heating_timestamp"] = current_utc_timestamp()

        return True

    def _check_if_exceeds_max_temp(self, temp: float) -> float:
        if temp > self.MAX_TEMP_TO_SHUTDOWN:
            self.logger.error(
                f"Temperature of heating surface has exceeded {self.MAX_TEMP_TO_SHUTDOWN}℃ - currently {temp}℃. This is beyond our recommendations. Shutting down Raspberry Pi to prevent further problems. Take caution when touching the heating surface and wetware."
            )
            self._update_heater(0)

            self.blink_error_code(error_codes.PCB_TEMPERATURE_TOO_HIGH)

            from subprocess import call

            call("sudo shutdown now --poweroff", shell=True)

        elif temp > self.MAX_TEMP_TO_DISABLE_HEATING:
            self.blink_error_code(error_codes.PCB_TEMPERATURE_TOO_HIGH)

            self.logger.warning(
                f"Temperature of heating surface has exceeded {self.MAX_TEMP_TO_DISABLE_HEATING}℃ - currently {temp}℃. This is beyond our recommendations. The heating PWM channel will be forced to 0. Take caution when touching the heating surface and wetware."
            )

            self._update_heater(0)

        elif temp > self.MAX_TEMP_TO_REDUCE_HEATING:
            self.logger.debug(
                f"Temperature of heating surface has exceeded {self.MAX_TEMP_TO_REDUCE_HEATING}℃ - currently {temp}℃. This is close to our maximum recommended value. The heating PWM channel will be reduced to 90% its current value. Take caution when touching the heating surface and wetware."
            )

            self._update_heater(self.heater_duty_cycle * 0.9)

        return temp

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self._update_heater(0)

        with suppress(AttributeError):
            self.read_external_temperature_timer.cancel()
            self.publish_temperature_timer.cancel()

        with suppress(AttributeError):
            self.turn_off_heater()

    def on_sleeping(self) -> None:
        self.publish_temperature_timer.pause()
        self._update_heater(0)

    def on_sleeping_to_ready(self) -> None:
        self.publish_temperature_timer.unpause()

    def setup_pwm(self) -> PWM:
        hertz = 16  # technically this doesn't need to be high: it could even be 1hz. However, we want to smooth it's
        # impact (mainly: current sink), over the second. Ex: imagine freq=1hz, dc=40%, and the pump needs to run for
        # 0.3s. The influence of when the heat is on the pump can be significant in a power-constrained system.
        pin = hardware.PWM_TO_PIN[hardware.HEATER_PWM_TO_PIN]
        pwm = PWM(pin, hertz, unit=self.unit, experiment=self.experiment, pubsub_client=self.pub_client)
        pwm.start(0)
        return pwm

    @staticmethod
    def _get_room_temperature():
        # TODO: improve somehow
        return 22.0

    def infer_temperature(self) -> None:
        """
        1. lock PWM and turn off heater
        2. read temperature once (or more) and publish directly
        """
        # CHANGED: removed the logic that took multiple samples to do a regression.
        # CHANGED: instead, we are simply measuring once (while turning off the heater or not) and publishing.

        # CHANGED: We still lock the PWM so that nothing else changes it while we measure
        assert not self.pwm.is_locked(), "PWM is locked - it shouldn't be though!"
        with self.pwm.lock_temporarily():
            # CHANGED: We'll store the previous heater DC so we can restore it after measurement
            previous_heater_dc = self.heater_duty_cycle

            # CHANGED: Turn off active heating if you wish to measure passively
            self._update_heater(0)

            # CHANGED: Just measure once (you can measure multiple times or average, if you like)
            measured_temp = self.read_external_temperature()

            # CHANGED: Restore the heater to its prior DC
            self._update_heater(previous_heater_dc)

        # CHANGED: Directly create a Temperature object from the measured value
        self.temperature = Temperature(
            temperature=round(measured_temp, 2),
            timestamp=current_utc_datetime(),
        )
        self._set_latest_temperature(self.temperature)

        # CHANGED: That’s all. We no longer call approximate_temperature_1_0 or approximate_temperature_2_0.


    # CHANGED: Removed the approximate_temperature_1_0 and approximate_temperature_2_0 methods entirely.
    #           If you still want them in the file for reference, you can keep them, but commented out.
    #
    #           If you do remove them, references to them in "infer_temperature" are obviously removed.


    def _set_growth_rate(self, message: pt.MQTTMessage) -> None:
        if not message.payload:
            return

        self.previous_growth_rate = self._latest_growth_rate
        payload = decode(message.payload, type=structs.GrowthRate)
        self._latest_growth_rate = payload.growth_rate
        self.latest_growth_rate_at = payload.timestamp

    def _set_latest_temperature(self, temperature: structs.Temperature) -> None:
        # Note: this doesn't use MQTT data (previously it use to)
        self.previous_temperature = self.latest_temperature
        self.latest_temperature = temperature.temperature
        self.latest_temperature_at = temperature.timestamp

        if self.state == self.READY or self.state == self.INIT:
            self.latest_event = self.execute()

        return

    def _set_OD(self, message: pt.MQTTMessage) -> None:
        if not message.payload:
            return
        self.previous_normalized_od = self._latest_normalized_od
        payload = decode(message.payload, type=structs.ODFiltered)
        self._latest_normalized_od = payload.od_filtered
        self.latest_normalized_od_at = payload.timestamp

    def start_passive_listeners(self) -> None:
        self.subscribe_and_callback(
            self._set_growth_rate,
            f"pioreactor/{self.unit}/{self.experiment}/growth_rate_calculating/growth_rate",
            allow_retained=False,
        )

        self.subscribe_and_callback(
            self._set_OD,
            f"pioreactor/{self.unit}/{self.experiment}/growth_rate_calculating/od_filtered",
            allow_retained=False,
        )


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
            f"Unable to find {automation_name}. Available automations are {list( available_temperature_automations.keys())}"
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
