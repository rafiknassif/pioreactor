# -*- coding: utf-8 -*-
"""
Continuously take optical density readings from the ODSensorV2 external sensor via I2C.

The ODReader polls the sensor at a configurable interval and publishes readings
to MQTT in the same format as the original built-in pipeline, so all downstream
jobs (growth_rate_calculating, automations, UI) work unchanged.

Dataflow:
  ODSensorV2 (I2C @ 0x69)  →  ODReader  →  MQTT  →  Database

The sensor handles all signal processing internally:
  laser control, photodiode sampling, AC noise rejection, normalization,
  calibration, and optional UKF filtering.
"""
from __future__ import annotations

import math
import threading
import types
from time import sleep, time
from typing import Callable, Optional

import click

from pioreactor import exc
from pioreactor import hardware
from pioreactor import structs
from pioreactor import types as pt
from pioreactor import whoami
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.config import config
from pioreactor.pubsub import QOS
from pioreactor.utils import timing
from pioreactor.utils.odsensorv2 import (
    ODSensorV2,
    CMD_START,
    CMD_STOP,
    STATUS_JOB_RUNNING,
)


ALL_PD_CHANNELS: list[pt.PdChannel] = ["1", "2"]
VALID_PD_ANGLES: list[pt.PdAngle] = ["45", "90", "135", "180"]


class ODReader(BackgroundJob):
    """
    Produce a stream of OD readings from the ODSensorV2 external sensor.

    Parameters
    -----------
    channel_angle_map: dict
        dict of (channel: angle) pairs, ex: {"1": "180"}
    interval: float
        seconds between readings
    sensor: ODSensorV2
        the I2C driver instance
    """

    job_name = "od_reading"
    published_settings = {
        "first_od_obs_time": {"datatype": "float", "settable": False},
        "interval": {"datatype": "float", "settable": False, "unit": "s"},
        "ods": {"datatype": "ODReadings", "settable": False},
        "od1": {"datatype": "ODReading", "settable": False},
    }

    _pre_read: list[Callable] = []
    _post_read: list[Callable] = []
    od1: structs.ODReading
    ods: structs.ODReadings

    def __init__(
        self,
        channel_angle_map: dict[pt.PdChannel, pt.PdAngle],
        interval: Optional[float],
        sensor: ODSensorV2,
        unit: str,
        experiment: str,
    ) -> None:
        super(ODReader, self).__init__(unit=unit, experiment=experiment)

        if len(channel_angle_map) == 0:
            self.logger.error("No channel/angle mapping provided.")
            self.clean_up()
            raise ValueError("No channel/angle mapping provided.")

        self.sensor = sensor
        self.channel_angle_map = channel_angle_map
        self.interval = interval
        self.first_od_obs_time: Optional[float] = None
        self._set_for_iterating = threading.Event()
        self._last_reading_number: Optional[int] = None
        self.record_from_adc_timer = None

        # Verify sensor is reachable
        if not self.sensor.test_connection():
            self.logger.error("ODSensorV2 not found at I2C address 0x69.")
            self.clean_up()
            raise exc.HardwareNotFoundError("ODSensorV2 not found at I2C address 0x69.")

        # Start the OD job on the sensor
        self.sensor.send_command(CMD_START)
        if not self.sensor.wait_for_status_bit(STATUS_JOB_RUNNING, timeout_s=5.0):
            self.logger.error("ODSensorV2 failed to start OD job.")
            self.clean_up()
            raise exc.HardwareNotFoundError("ODSensorV2 failed to start OD job.")

        self.logger.info("ODSensorV2 OD job started successfully.")

        # Bind pre/post read callbacks
        self.pre_read_callbacks: list[Callable] = self._prepare_pre_callbacks()
        self.post_read_callbacks: list[Callable] = self._prepare_post_callbacks()

        # Subscribe to MQTT topic for sampling interval updates
        self.subscribe_and_callback(
            self._handle_interval_update,
            f"pioreactor/{self.unit}/{self.experiment}/od_reading/update_interval",
            qos=QOS.AT_LEAST_ONCE,
        )

        # Start periodic reading
        if (self.interval is not None) and self.interval > 0:
            self.record_from_adc_timer = timing.RepeatedTimer(
                self.interval,
                self.record_from_sensor,
                job_name=self.job_name,
                run_immediately=True,
                logger=self.logger,
            ).start()

        self.logger.debug(
            f"Starting od_reading with channels {channel_angle_map}, every {self.interval} seconds"
        )

    def record_from_sensor(self) -> structs.ODReadings:
        if self.first_od_obs_time is None:
            self.first_od_obs_time = time()

        # Pre-read callbacks
        for pre_function in self.pre_read_callbacks:
            try:
                pre_function()
            except Exception:
                self.logger.debug(f"Error in pre_function={pre_function.__name__}.", exc_info=True)

        timestamp_of_readings = timing.current_utc_datetime()

        # Read measured reflectance from sensor
        try:
            od_value = self.sensor.read_measured_reflectance()
        except Exception as e:
            self.logger.debug(f"Error reading from ODSensorV2: {e}", exc_info=True)
            od_value = float("nan")

        # Track reading number (for diagnostics, no log — polling is often faster than sensor rate)
        try:
            self._last_reading_number = self.sensor.read_reading_number()
        except Exception:
            pass

        # Skip publishing NaN values (sensor still initializing or read failed)
        if math.isnan(od_value):
            self._set_for_iterating.set()
            return getattr(self, "ods", structs.ODReadings(timestamp=timestamp_of_readings, ods={}))

        od_readings = structs.ODReadings(
            timestamp=timestamp_of_readings,
            ods={
                channel: structs.ODReading(
                    od=od_value,
                    angle=angle,
                    timestamp=timestamp_of_readings,
                    channel=channel,
                )
                for channel, angle in self.channel_angle_map.items()
            },
        )

        self.ods = od_readings
        for channel in self.channel_angle_map:
            setattr(self, f"od{channel}", od_readings.ods[channel])

        # Post-read callbacks
        for post_function in self.post_read_callbacks:
            try:
                post_function(od_readings)
            except Exception:
                self.logger.debug(f"Error in post_function={post_function.__name__}.", exc_info=True)

        self._set_for_iterating.set()
        return od_readings

    def _handle_interval_update(self, message: pt.MQTTMessage) -> None:
        try:
            new_interval = float(message.payload.decode("utf-8"))
            if new_interval > 0:
                self.update_sampling_interval(new_interval)
                self.logger.info(f"Updated sampling interval to {new_interval} seconds via MQTT.")
            else:
                self.logger.warning(f"Ignored invalid sampling interval: {new_interval}")
        except ValueError as e:
            self.logger.error(f"Failed to decode sampling interval: {e}")

    def update_sampling_interval(self, new_interval: float) -> None:
        if new_interval <= 0:
            raise ValueError("Sampling interval must be positive.")
        self.logger.info(f"Updating sampling interval to {new_interval} seconds.")
        if self.record_from_adc_timer:
            self.record_from_adc_timer.pause()
            self.record_from_adc_timer.interval = new_interval
            self.record_from_adc_timer.unpause()
        else:
            self.record_from_adc_timer = timing.RepeatedTimer(
                interval=new_interval,
                function=self.record_from_sensor,
                job_name=self.job_name,
                run_immediately=False,
            ).start()
        self.interval = new_interval

    def _prepare_post_callbacks(self) -> list[Callable]:
        callbacks: list[Callable] = []
        for func in self._post_read:
            setattr(self, func.__name__, types.MethodType(func, self))
            callbacks.append(getattr(self, func.__name__))
        return callbacks

    def _prepare_pre_callbacks(self) -> list[Callable]:
        callbacks: list[Callable] = []
        for func in self._pre_read:
            setattr(self, func.__name__, types.MethodType(func, self))
            callbacks.append(getattr(self, func.__name__))
        return callbacks

    @classmethod
    def add_pre_read_callback(cls, function: Callable) -> None:
        cls._pre_read.append(function)

    @classmethod
    def add_post_read_callback(cls, function: Callable) -> None:
        cls._post_read.append(function)

    def on_sleeping(self) -> None:
        if self.record_from_adc_timer:
            self.record_from_adc_timer.pause()

    def on_sleeping_to_ready(self) -> None:
        if self.record_from_adc_timer:
            self.record_from_adc_timer.unpause()

    def on_disconnected(self) -> None:
        try:
            if self.record_from_adc_timer:
                self.record_from_adc_timer.cancel()
        except Exception:
            pass

        # Stop OD job on sensor
        try:
            self.sensor.send_command(CMD_STOP)
            self.logger.info("ODSensorV2 OD job stopped.")
        except Exception:
            pass

    def __iter__(self) -> ODReader:
        return self

    def __next__(self) -> structs.ODReadings:
        while self._set_for_iterating.wait():
            self._set_for_iterating.clear()
            assert self.ods is not None
            return self.ods
        assert False


def start_od_reading(
    interval: Optional[float] = None,
    unit: Optional[str] = None,
    experiment: Optional[str] = None,
) -> ODReader:
    """
    Create and start an ODReader that reads from the ODSensorV2.
    """
    unit = unit or whoami.get_unit_name()
    experiment = experiment or whoami.get_assigned_experiment_name(unit)

    if interval is None:
        interval = 1 / config.getfloat("od_reading.config", "samples_per_second", fallback=0.2)

    if interval <= 0:
        raise ValueError("interval must be positive.")

    angle = config.get("od_reading.config", "odsensorv2_angle", fallback="180")
    channel_angle_map: dict[pt.PdChannel, pt.PdAngle] = {"1": angle}

    sensor = ODSensorV2()

    return ODReader(
        channel_angle_map,
        interval=interval,
        sensor=sensor,
        unit=unit,
        experiment=experiment,
    )


@click.command(name="od_reading")
def click_od_reading() -> None:
    """
    Start the optical density reading job using ODSensorV2.
    """
    od = start_od_reading()
    od.block_until_disconnected()
