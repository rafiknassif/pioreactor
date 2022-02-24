# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import cast
from typing import Optional

from msgspec.json import decode
from msgspec.json import encode

from pioreactor import exc
from pioreactor import structs
from pioreactor import types as pt
from pioreactor.background_jobs.subjobs import BackgroundSubJob
from pioreactor.background_jobs.temperature_control import TemperatureController
from pioreactor.pubsub import QOS
from pioreactor.utils import is_pio_job_running
from pioreactor.utils.timing import current_utc_time


class TemperatureAutomation(BackgroundSubJob):
    """
    This is the super class that Temperature automations inherit from.
    The `execute` function, which is what subclasses will define, is updated every time a new temperature is recorded to MQTT.
    Temperatures are updated every 10 minutes.

    To change setting over MQTT:

    `pioreactor/<unit>/<experiment>/temperature_automation/<setting>/set` value

    """

    _latest_growth_rate: Optional[float] = None
    _latest_od: Optional[float] = None
    previous_od: Optional[float] = None
    previous_growth_rate: Optional[float] = None
    latest_od_at: float = 0
    latest_growth_rate_at: float = 0

    latest_temperature = None
    previous_temperature = None

    _latest_settings_started_at = current_utc_time()
    _latest_settings_ended_at = None
    automation_name = "temperature_automation_base"  # is overwritten in subclasses

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # this registers all subclasses of TemperatureAutomation back to TemperatureController, so the subclass
        # can be invoked in TemperatureController.
        if hasattr(cls, "automation_name") and cls.automation_name is not None:
            TemperatureController.automations[cls.automation_name] = cls

    def __init__(
        self, unit: str, experiment: str, parent: TemperatureController, **kwargs
    ) -> None:
        super(TemperatureAutomation, self).__init__(
            job_name="temperature_automation", unit=unit, experiment=experiment
        )

        self.temperature_control_parent = parent

        self.start_passive_listeners()

    def update_heater(self, new_duty_cycle: float) -> bool:
        """
        Update heater's duty cycle. This function checks for a lock on the PWM, and will not
        update if the PWM is locked.

        Returns true if the update was made (eg: no lock), else returns false
        """
        return self.temperature_control_parent.update_heater(new_duty_cycle)

    def is_heater_pwm_locked(self) -> bool:
        """
        Check if the heater PWM channels is locked
        """
        return self.temperature_control_parent.pwm.is_locked()

    def update_heater_with_delta(self, delta_duty_cycle: float) -> bool:
        """
        Update heater's duty cycle by value `delta_duty_cycle`. This function checks for a lock on the PWM, and will not
        update if the PWM is locked.

        Returns true if the update was made (eg: no lock), else returns false
        """
        return self.temperature_control_parent.update_heater_with_delta(delta_duty_cycle)

    def execute(self):
        """
        Overwrite in base class
        """
        raise NotImplementedError

    @property
    def most_stale_time(self) -> float:
        return min(self.latest_od_at, self.latest_growth_rate_at)

    @property
    def latest_growth_rate(self) -> float:
        # check if None
        if self._latest_growth_rate is None:
            # this should really only happen on the initialization.
            self.logger.debug("Waiting for OD and growth rate data to arrive")
            if not is_pio_job_running("od_reading", "growth_rate_calculating"):
                raise exc.JobRequiredError(
                    "`od_reading` and `growth_rate_calculating` should be running."
                )

        # check most stale time
        if (time.time() - self.most_stale_time) > 5 * 60:
            raise exc.JobRequiredError(
                "readings are too stale (over 5 minutes old) - are `od_reading` and `growth_rate_calculating` running?"
            )

        return cast(float, self._latest_growth_rate)

    @property
    def latest_od(self) -> float:
        # check if None
        if self._latest_od is None:
            # this should really only happen on the initialization.
            self.logger.debug("Waiting for OD and growth rate data to arrive")
            if not is_pio_job_running("od_reading", "growth_rate_calculating"):
                raise exc.JobRequiredError(
                    "`od_reading` and `growth_rate_calculating` should be running."
                )

        # check most stale time
        if (time.time() - self.most_stale_time) > 5 * 60:
            raise exc.JobRequiredError(
                "readings are too stale (over 5 minutes old) - are `od_reading` and `growth_rate_calculating` running?"
            )

        return cast(float, self._latest_od)

    ########## Private & internal methods

    def on_disconnected(self) -> None:
        self._latest_settings_ended_at = current_utc_time()
        self._send_details_to_mqtt()

    def __setattr__(self, name, value) -> None:
        super(TemperatureAutomation, self).__setattr__(name, value)
        if name in self.published_settings and name != "state":
            self._latest_settings_ended_at = current_utc_time()
            self._send_details_to_mqtt()
            self._latest_settings_started_at, self._latest_settings_ended_at = (
                current_utc_time(),
                None,
            )

    def _set_growth_rate(self, message: pt.MQTTMessage) -> None:
        self.previous_growth_rate = self._latest_growth_rate
        self._latest_growth_rate = decode(
            message.payload, type=structs.GrowthRate
        ).growth_rate
        self.latest_growth_rate_at = time.time()

    def _set_temperature(self, message: pt.MQTTMessage) -> None:
        if not message.payload:
            return

        self.previous_temperature = self.latest_temperature
        self.latest_temperature = decode(
            message.payload, type=structs.Temperature
        ).temperature

        if self.state != self.SLEEPING:
            self.execute()

    def _set_OD(self, message: pt.MQTTMessage) -> None:
        self.previous_od = self._latest_od
        self._latest_od = decode(message.payload, type=structs.ODFiltered).od_filtered
        self.latest_od_at = time.time()

    def _send_details_to_mqtt(self) -> None:
        self.publish(
            f"pioreactor/{self.unit}/{self.experiment}/{self.job_name}/temperature_automation_settings",
            encode(
                structs.AutomationSettings(
                    pioreactor_unit=self.unit,
                    experiment=self.experiment,
                    started_at=self._latest_settings_started_at,
                    ended_at=self._latest_settings_ended_at,
                    automation_name=self.automation_name,
                    settings=encode(
                        {
                            attr: getattr(self, attr, None)
                            for attr in self.published_settings
                            if attr != "state"
                        }
                    ),
                )
            ),
            qos=QOS.EXACTLY_ONCE,
        )

    def start_passive_listeners(self) -> None:
        self.subscribe_and_callback(
            self._set_growth_rate,
            f"pioreactor/{self.unit}/{self.experiment}/growth_rate_calculating/growth_rate",
            allow_retained=False,
        )

        self.subscribe_and_callback(
            self._set_temperature,
            f"pioreactor/{self.unit}/{self.experiment}/temperature_control/temperature",
            allow_retained=False,  # only use fresh data from Temp Control.
        )

        self.subscribe_and_callback(
            self._set_OD,
            f"pioreactor/{self.unit}/{self.experiment}/growth_rate_calculating/od_filtered",
            allow_retained=False,
        )


class TemperatureAutomationContrib(TemperatureAutomation):
    automation_name: str
