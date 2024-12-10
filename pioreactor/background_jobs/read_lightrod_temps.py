from contextlib import suppress
from time import sleep
import numpy as np
from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import LightRodTemp_ADDR
from pioreactor.structs import LightRodTemperature, LightRodTemperatures
from pioreactor.utils.temps import TMP1075
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity


class ReadLightRodTemps(BackgroundJob):
    job_name = "read_lightrod_temps"
    published_settings = {
        "warning_threshold": {"datatype": "float", "unit": "℃", "settable": True},
        "lightrod_temps": {"datatype": "LightRodTemperatures", "settable": False},
    }
    TEMP_THRESHOLD = 40  # Over-temperature warning level [degrees C]
    _instance = None  # Singleton instance reference

    def __new__(cls, *args, **kwargs):
        if cls._instance is not None:
            raise RuntimeError("An instance of ReadLightRodTemps is already running.")
        cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, unit, experiment, temp_thresh=TEMP_THRESHOLD):
        super().__init__(unit=unit, experiment=experiment)

        self.tmp_driver_map = None
        self.read_lightrod_temperature_timer = None

        # Initialize temperature sensor drivers
        self.initializeDrivers(LightRodTemp_ADDR)

        # Set warning threshold and initialize temperatures
        self.set_warning_threshold(temp_thresh)
        self.lightrod_temps = None  # Initialize for MQTT broadcast

        # Timer interval for temperature readings
        dt = 1 / config.getfloat("lightrod_temp_reading.config", "samples_per_second", fallback=30)

        # Set up and start repeated timer
        self.read_lightrod_temperature_timer = RepeatedTimer(
            dt,
            self.read_temps,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, addr_map):
        """Initialize temperature sensor drivers."""
        self.tmp_driver_map = {
            LightRod: [TMP1075(address=addr) for addr in addresses]
            for LightRod, addresses in addr_map.items()
        }

    def set_warning_threshold(self, temp_thresh):
        """Set the warning threshold for light rod temperatures."""
        self.warning_threshold = temp_thresh

    def read_temps(self):
        """Read temperatures from the light rods and publish them."""
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
        self.lightrod_temps = LightRodTemperatures(
            timestamp=current_utc_datetime(),
            temperatures=lightrod_dict,
        )

    def on_disconnected(self) -> None:
        """Handle disconnection and clean up resources."""
        # Cancel the repeated timer
        if self.read_lightrod_temperature_timer:
            self.read_lightrod_temperature_timer.cancel()

        # Explicit cleanup for drivers
        if self.tmp_driver_map:
            for drivers in self.tmp_driver_map.values():
                for driver in drivers:
                    # No specific clean_up required, dereference the object
                    del driver

        self.tmp_driver_map = None

        # Release the singleton instance
        ReadLightRodTemps._instance = None

    def _read_average_temperature(self, driver) -> float:
        """Read the current temperature from a sensor, in Celsius."""
        running_sum, running_count = 0.0, 0
        try:
            # Read temperature multiple times to reduce variance
            for _ in range(6):
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
        """Check if the temperature exceeds the warning threshold."""
        if temp > self.warning_threshold:
            self.logger.warning(
                f"Temperature of light rod has exceeded {self.warning_threshold}℃ - currently {temp}℃."
            )
            # Example action: Turn off LEDs if temperature is too high
            channel = "B"
            success = led_intensity(
                {channel: 0},
                unit=self.unit,
                experiment=self.experiment,
                pubsub_client=self.pub_client,
                source_of_event=f"{self.job_name}",
            )
            if success:
                self.logger.warning("Lights were turned off due to high temperature.")

        return temp > self.warning_threshold


# CLI Command
import click


@click.command(name="read_lightrod_temps")
@click.option(
    "--warning-threshold",
    default=40,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
def click_read_lightrod_temps(warning_threshold):
    """Start the ReadLightRodTemps job with the specified warning threshold."""
    from pioreactor.whoami import get_unit_name, get_assigned_experiment_name

    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    job = ReadLightRodTemps(
        temp_thresh=warning_threshold,
        unit=unit,
        experiment=experiment,
    )
    job.block_until_disconnected()
