from contextlib import suppress
from time import sleep
import numpy as np
import json

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import LightRodTemp_ADDR
from pioreactor.structs import LightRodTemperature
from pioreactor.structs import LightRodTemperatures
from pioreactor.structs import PlotLightRodTemperatures
from pioreactor.utils.temps import TMP1075
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity
from pioreactor.whoami import get_unit_name, get_assigned_experiment_name


class ReadLightRodTemps(BackgroundJob):
    job_name = "read_lightrod_temps"
    published_settings = {
        'warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        "lightrod_temps": {"datatype": "LightRodTemperatures", "settable": False},
    }
    TEMP_THRESHOLD = 40  # over-temperature warning level [degrees C]

    def __init__(self, unit: str, experiment: str, temp_thresh=TEMP_THRESHOLD) -> None:
        super(ReadLightRodTemps, self).__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(LightRodTemp_ADDR)
        self.set_warning_threshold(temp_thresh)
        self.lightrod_temps = None  # initialize for mqtt broadcast
        self.connected_lightrods = {}  # Track which lightrods are connected

        dt = 1 / (config.getfloat("lightrod_temp_reading.config", "samples_per_second", fallback=0.033))

        self.read_lightrod_temperature_timer = RepeatedTimer(
            dt,
            self.read_temps,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, addr_map):
        self.tmp_driver_map = {
            LightRod: [TMP1075(address=addr) for addr in addresses]
            for LightRod, addresses in addr_map.items()
        }
        # Check which lightrods are actually connected
        self.check_connected_lightrods()

    def check_connected_lightrods(self):
        """Check which lightrods are connected by trying to read from them once"""
        self.connected_lightrods = {}
        for lightRod, drivers in self.tmp_driver_map.items():
            rod_connected = True
            # Try to read from each sensor in the lightrod
            for i, driver in enumerate(drivers):
                try:
                    driver.get_temperature()
                except OSError:
                    # If any sensor fails, mark the whole lightrod as disconnected
                    rod_connected = False
                    break
            
            self.connected_lightrods[lightRod] = rod_connected
            if not rod_connected:
                self.logger.info(f"Lightrod {lightRod} appears to be disconnected - skipping it for temperature readings.")
            else:
                self.logger.info(f"Lightrod {lightRod} is connected and will be monitored.")

    def set_warning_threshold(self, temp_thresh):
        self.warning_threshold = temp_thresh

    def read_temps(self):
        lightrod_dict = {}
        sensor_success = None
        for lightRod, drivers in self.tmp_driver_map.items():
            # Skip disconnected lightrods

            self.logger.debug(f"connect LRs: {self.connected_lightrods.__repr__()}")
            if not self.connected_lightrods.get(lightRod, False):
                continue

            try:
                temps = np.zeros(3)
                sensor_success = False
                
                for i in range(3):
                    try:
                        temps[i] = self._read_average_temperature(drivers[i])
                        sensor_success = True
                    except exc.HardwareNotFoundError as e:
                        # Individual sensor failure
                        self.logger.debug(f"Sensor {i} on lightrod {lightRod} failed: {str(e)}")
                        temps[i] = float('nan')  # Mark as NaN
                    except Exception as e:
	                    # Handle other failure types
                        self.logger.error(f"LR sensor {i} failed to read for unknown reason: {e}", exc_info=True)
                        return

                # Only add this lightrod if at least one sensor worked
                if sensor_success:
                    lightrod_dict[lightRod] = LightRodTemperature(
                        top_temp=float(round(temps[0], 2)) if not np.isnan(temps[0]) else float('nan'),
                        middle_temp=float(round(temps[1], 2)) if not np.isnan(temps[1]) else float('nan'),
                        bottom_temp=float(round(temps[2], 2)) if not np.isnan(temps[2]) else float('nan'),
                        timestamp=current_utc_datetime(),
                    )
                else:
                    # All sensors failed, mark lightrod as disconnected
                    self.connected_lightrods[lightRod] = False
                    self.logger.warning(f"All sensors on lightrod {lightRod} failed - marking as disconnected")
                    
            except Exception as e:
                # Error with entire lightrod
                self.logger.warning(f"Lightrod {lightRod} disconnected during operation: {str(e)}")
                self.connected_lightrods[lightRod] = False
                continue
        
        # Only proceed if we successfully read from at least one sensor
        self.logger.debug(f"sensor success: {sensor_success}")
        self.logger.debug(f"lightrod_dict: {lightrod_dict.__repr__()}")

        if sensor_success and lightrod_dict:
            self.publish_max_temps(lightrod_dict)
            lightRod_temperatures = LightRodTemperatures(
                timestamp=current_utc_datetime(),
                temperatures=lightrod_dict,
            )
            # self.log_lightrod_temperatures(lightRod_temperatures)
            self.lightrod_temps = lightRod_temperatures
        else:
            self.logger.warning("No lightrods connected - unable to read any temperatures")
        
    def publish_max_temps(self, lightrod_dict):
        unit = get_unit_name()
        experiment = get_assigned_experiment_name(unit)

        for lightRod, lightRodTemp in lightrod_dict.items():
            self.logger.debug(f"Lightrod: {lightRod},  LRT: {lightRodTemp}")
            max_temp = max(lightRodTemp.top_temp, lightRodTemp.middle_temp, lightRodTemp.bottom_temp)
            self.logger.debug(f"max LRT: {max_temp}")

            # Create PlotLightRodTemperatures object
            plotTemp = PlotLightRodTemperatures(
                timestamp=current_utc_datetime(),
                channel=lightRod,
                max_temp=max_temp
            )

            # Pass the object directly to publish
            BackgroundJob.publish(
                self,
                topic=f"pioreactor/{unit}/{experiment}/read_lightrod_temps/max_lightrod_temp",
                payload=plotTemp  # Publish as an object
            )

    def log_lightrod_temperatures(self, lightRod_temperatures):
        for lightRod, temperature in lightRod_temperatures.temperatures.items():
            self.logger.debug(
                f"LightRod: {lightRod} | "
                f"Top: {temperature.top_temp}℃, "
                f"Middle: {temperature.middle_temp}℃, "
                f"Bottom: {temperature.bottom_temp}℃ | "
                f"Timestamp: {temperature.timestamp}"
            )

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.read_lightrod_temperature_timer.cancel()

    # def reset_connected_status(self):
    #     """
    #     Method to rescan and reset connected status of all lightrods.
    #     This could be called periodically or via MQTT to check for reconnected lightrods.
    #     """
    #     self.logger.info("Rescanning for connected lightrods...")
    #     self.check_connected_lightrods()
    #     return self.connected_lightrods

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
        if temp > self.warning_threshold:
            self.logger.warning(
                f"Temperature of light rod has exceeded {self.warning_threshold}℃ - currently {temp}℃. Some action will be taken maybe idk"
                # TODO implement overtemperature correction action
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

        return temp > self.warning_threshold


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

    job = ReadLightRodTemps(
        temp_thresh=warning_threshold,
        unit=unit,
        experiment=experiment,
    )
    job.block_until_disconnected()