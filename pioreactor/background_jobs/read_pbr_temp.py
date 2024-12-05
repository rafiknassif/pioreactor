from contextlib import suppress
from time import sleep
import numpy as np
import json

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import Thermocouple_ADDR
from pioreactor.structs import Temperature
from pioreactor.utils.temps import MCP9600
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity
import click


class ReadPBRTemp(BackgroundJob):
    job_name = "read_pbr_temp"
    published_settings = {
        'upper_warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        'lower_warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        "PBR_temp": {"datatype": "Temperature", "settable": False}
    }


    def __init__(self, unit, experiment, upper_warning_threshold=35, lower_warning_threshold=18):
        super().__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(Thermocouple_ADDR)
        self.set_upper_warning_threshold(upper_warning_threshold)
        self.set_lower_warning_threshold(lower_warning_threshold)
        self.PBR_temp = None  # initialize for mqtt broadcast

        dt = 1 / (config.getfloat("lightrod_temp_reading.config", "samples_per_second", fallback=0.033))

        self.read_pbr_temperature_timer = RepeatedTimer(
            dt,
            self.read_temp,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, i2c_addr):
        self.mcp9600_driver = MCP9600(i2c_addr)
        self.mcp9600_driver.set_thermocouple_type('K')

    def set_upper_warning_threshold(self, temp_thresh):
        self.upper_warning_threshold = temp_thresh

    def set_lower_warning_threshold(self, temp_thresh):
        self.lower_warning_threshold = temp_thresh

    def read_temp(self):
        temp = self._read_average_temperature()

        self.PBR_temp = Temperature(
            timestamp=current_utc_datetime(),
            temperature=temp,
        )

    def log_PBR_temperature(self):
        self.logger.debug(
            f"PBR Temperature: {self.PBR_temp.temperature}"
        )

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.read_pbr_temperature_timer.cancel()

    ########## Private & internal methods

    def _read_average_temperature(self) -> float:
        """
        Read the current temperature from sensor, in Celsius
        """
        running_sum, running_count = 0.0, 0
        try:
            # check temp is fast, let's do it a few times to reduce variance.
            for i in range(6):
                running_sum += self.mcp9600_driver.get_hot_junction_temperature()
                running_count += 1
                sleep(0.05)

        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the thermocouple connected to the I2C bus? Unable to find temperature sensor."
            )

        averaged_temp = running_sum / running_count
        self._check_if_exceeds_temp_range(averaged_temp)

        return averaged_temp

    def _check_if_exceeds_temp_range(self, temp: float) -> bool:
        if temp > self.upper_warning_threshold:
            self.logger.warning(
                f"Temperature of thermocouple has exceeded {self.upper_warning_threshold}℃ - currently {temp}℃. LEDs will be powered off"
            )

            channel = 'B'
            success = led_intensity(
                {channel: 0},
                unit=self.unit,
                experiment=self.experiment,
                pubsub_client=self.pub_client,
                source_of_event=f"{self.job_name}",
            )
            if success:
                self.logger.warning("lights were turned off due to high temp")
        elif temp < self.lower_warning_threshold:
            self.logger.warning(
                f"Temperature of thermocouple has fallen below {self.lower_warning_threshold}℃ - currently {temp}℃. Some action will be taken maybe idk"
            )
            # TODO implement undertemperature correction action

        return temp > self.upper_warning_threshold and temp < self.lower_warning_threshold


@click.command(name="read_pbr_temp")
@click.option(
    "--upper-warning-threshold",
    default=35,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
@click.option(
    "--lower-warning-threshold",
    default=18,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
def click_read_pbr_temp(upper_warning_threshold, lower_warning_threshold):
    from pioreactor.whoami import get_unit_name, get_assigned_experiment_name

    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    job = ReadPBRTemp(
        upper_warning_threshold=upper_warning_threshold,
        lower_warning_threshold=lower_warning_threshold,
        unit=unit,
        experiment=experiment,
    )
    job.block_until_disconnected()