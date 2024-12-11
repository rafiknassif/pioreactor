from contextlib import suppress
from time import sleep
import numpy as np
from typing import Optional

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import LightRodTemp_ADDR
from pioreactor.structs import LightRodTemperature, LightRodTemperatures
from pioreactor.utils.temps import TMP1075
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity
from pioreactor.whoami import get_unit_name, get_assigned_experiment_name


class ReadLightRodTemps(BackgroundJob):
    """
    Reads temperature data from the light rod and publishes it over MQTT.
    Shuts off LEDs if temperature exceeds a defined threshold.
    """
    job_name = "read_lightrod_temps"
    published_settings = {
        "warning_threshold": {"datatype": "float", "unit": "℃", "settable": True},
        "lightrod_temps": {"datatype": "LightRodTemperatures", "settable": False},
    }
    TEMP_THRESHOLD = 40  # Default over-temperature warning level in degrees Celsius.

    def __init__(self, unit: str, experiment: str, temp_thresh: float = TEMP_THRESHOLD) -> None:
        super().__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(LightRodTemp_ADDR)
        self.set_warning_threshold(temp_thresh)
        self.lightrod_temps = None  # Initialize for MQTT broadcast.

        # Timer interval for reading temperatures
        dt = 1 / config.getfloat("lightrod_temp_reading.config", "samples_per_second", fallback=0.033)

        # Start repeated timer for reading temperatures
        self.read_lightrod_temperature_timer = RepeatedTimer(
            dt,
            self.read_temps,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, addr_map):
        """
        Initialize TMP1075 temperature sensor drivers.
        """
        self.tmp_driver_map = {
            LightRod: [TMP1075(address=addr) for addr in addresses]
            for LightRod, addresses in addr_map.items()
        }

    def set_warning_threshold(self, temp_thresh: float) -> None:
        """
        Set the temperature threshold for triggering a warning.
        """
        self.warning_threshold = temp_thresh

    def read_temps(self) -> None:
        """
        Read temperatures from the light rod and publish them over MQTT.
        """
        lightrod_dict = {}
        for lightRod, drivers in self.tmp_driver_map.items():
            temps = np.zeros(3)
            for i in range(3):
                temps[i] = self._read_average_temperature(drivers[i])

            lightrod_dict[lightRod] = LightRodTemperature(
                top_temp=float(round(temps[0], 2)),
                middle_temp=float(round(temps[1], 2)),
                bottom_temp=float(round(temps[2], 2)),
                timestamp=current_utc_datetime(),
            )
        lightRod_temperatures = LightRodTemperatures(
            timestamp=current_utc_datetime(),
            temperatures=lightrod_dict,
        )
        self.lightrod_temps = lightRod_temperatures

    def on_disconnected(self) -> None:
        """
        Cleanup when the job is disconnected.
        """
        with suppress(AttributeError):
            self.read_lightrod_temperature_timer.cancel()
        super().on_disconnected()

    ########## Private & Internal Methods ##########

    def _read_average_temperature(self, driver) -> float:
        """
        Read the current temperature from the sensor and compute an average.
        """
        running_sum, running_count = 0.0, 0
        try:
            # Perform multiple readings to reduce variance
            for i in range(6):
                running_sum += driver.get_temperature()
                running_count += 1
                sleep(0.05)
        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the Light Rod connected to the I2C bus? Unable to find temperature sensor."
            )
        averaged_temp = running_sum / running_count
        self._check_if_exceeds_max_temp(averaged_temp)
        return averaged_temp

    def _check_if_exceeds_max_temp(self, temp: float) -> bool:
        """
        Check if the temperature exceeds the warning threshold and take corrective action.
        """
        if temp > self.warning_threshold:
            self.logger.warning(
                f"Temperature of light rod exceeded {self.warning_threshold}℃ - currently {temp}℃. Turning off LEDs."
            )
            channel = "B"
            success = led_intensity(
                {channel: 0},
                unit=self.unit,
                experiment=self.experiment,
                pubsub_client=self.pub_client,
                source_of_event=self.job_name,
            )
            if success:
                self.logger.warning("LEDs turned off due to high temperature.")
        return temp > self.warning_threshold


########## Entry Point Function ##########

def start_read_lightrod_temps(
    temp_thresh: float = config.getfloat("lightrod_temp_reading.config", "warning_threshold", fallback=40),
    unit: Optional[str] = None,
    experiment: Optional[str] = None,
) -> ReadLightRodTemps:
    """
    Entry-point function to start the read_lightrod_temps job.
    """
    unit = unit or get_unit_name()
    experiment = experiment or get_assigned_experiment_name(unit)

    job = ReadLightRodTemps(
        temp_thresh=temp_thresh,
        unit=unit,
        experiment=experiment,
    )
    return job


########## CLI Command ##########

import click

@click.command(name="read_lightrod_temps")
@click.option(
    "--warning-threshold",
    default=40,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
def click_read_lightrod_temps(warning_threshold):

    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    job = start_read_lightrod_temps(temp_thresh=warning_threshold, unit=unit, experiment=experiment)
    job.block_until_disconnected()
