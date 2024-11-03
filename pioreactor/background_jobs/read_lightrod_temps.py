from contextlib import suppress
from time import sleep
import numpy as np

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import LightRodTemp_ADDR
from pioreactor.structs import LightRodTemperature
from pioreactor.structs import LightRodTemperatures
from pioreactor.utils.temps import TMP1075
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime


class ReadLightRodTempsJob(BackgroundJob):

    job_name="read_lightrod_temps_job"
    published_settings = {
        'warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        "lightrod_temps": {"datatype": "LightRodTemperatures", "settable": False}
    }
    TEMP_THRESHOLD= 40  # over-temperature warning level [degrees C]

    def __init__(self, unit, experiment, temp_thresh=TEMP_THRESHOLD):
        super().__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(LightRodTemp_ADDR)
        self.set_warning_threshold(temp_thresh)

        self.read_lightrod_temperature_timer = RepeatedTimer(
            30,
            self.read_temps,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, addr_map):
        self.tmp_driver_map = {
            LightRod: [TMP1075(address=addr) for addr in addresses]
            for LightRod, addresses in addr_map.items()
        }

    def set_warning_threshold(self, temp_thresh):
        self.TEMP_THRESHOLD = temp_thresh

    def read_temps(self):
        lightrod_dict = {}
        for lightRod, drivers in self.tmp_driver_map.items():
            temps = np.zeros(3)
            for i in range(3):
                temps[i] = self._read_average_temperature(drivers[i])

            lightrod_dict[lightRod] = LightRodTemperature(
                top_temp=round(temps[0], 2),
                middle_temp=round(temps[1], 2),
                bottom_temp=round(temps[2], 2),
                timestamp=current_utc_datetime(),
            )
        lightRod_temperatures = LightRodTemperature(
            timestamp=current_utc_datetime(),
            temperatures=lightrod_dict,
        )

        return lightRod_temperatures

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.read_lightrod_temperature_timer.cancel()


    ########## Private & internal methods

    def _read_average_temperature(self, driver) -> float:
        """
        Read the current temperature from sensor, in Celsius
        """
        running_sum, running_count = 0.0, 0
        try:
            # check temp is fast, let's do it a few times to reduce variance.
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
        if temp > self.TEMP_THRESHOLD:

            self.logger.warning(
                f"Temperature of light rod has exceeded {self.TEMP_THRESHOLD}℃ - currently {temp}℃. Some action will be taken maybe idk"
                # TODO implement overtemperature correction action
            )

        return temp > self.TEMP_THRESHOLD

if __name__ == "__main__":
    from pioreactor.whoami import get_unit_name
    from pioreactor.whoami import get_assigned_experiment_name

    unit = get_unit_name()
    experiment = get_unit_name(unit)

    job = ReadLightRodTempsJob(unit=unit, experiment=experiment)


    job.block_until_disconnected()