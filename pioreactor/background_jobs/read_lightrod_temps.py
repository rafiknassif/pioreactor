from contextlib import suppress
from time import sleep
import numpy as np
import json

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import LightRodTemp_ADDR, PCA9546_CH_LR
from pioreactor.structs import LightRodTemperature
from pioreactor.structs import LightRodTemperatures
from pioreactor.structs import PlotLightRodTemperatures, LEDDriverIntensity
from pioreactor.utils.temps import TMP1075
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity 
from pioreactor.actions.led_driver import led_driver_intensity 
from pioreactor.whoami import get_unit_name, get_assigned_experiment_name
from pioreactor.pubsub import publish, QOS


from pioreactor.automations.led.lightrod_light_control import LightrodLightControl


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

        self.disconnect_counter = {}  # store a list of light rods that fail to communicate, and increment a counter of how many consecutive readings fail. 
        self.disconnect_threshold = 5  # number of consecutive disconnects before throwing an error

        dt = 1 / (config.getfloat("lightrod_temp_reading.config", "samples_per_second", fallback=0.033))
        
        self.read_lightrod_temperature_timer = RepeatedTimer(
            dt,
            self.read_temps,
            job_name=self.job_name,
            run_immediately=False,
        ).start()
        self.rescan_timer = RepeatedTimer(
            600,
            self.reset_connected_status,
            job_name=f"{self.job_name}_rescan",
            run_immediately=False,
        ).start()

    def initializeDrivers(self, addr_map):
        self.tmp_driver_map = {
            LightRod: [TMP1075(address=addr, mux_channel=PCA9546_CH_LR) for addr in addresses]
            for LightRod, addresses in addr_map.items()
        }
        # Check which lightrods are actually connected
        self.check_connected_lightrods()

    def set_warning_threshold(self, temp_thresh):
        self.warning_threshold = temp_thresh

    def check_connected_lightrods(self):
        self.connected_lightrods = {}
        for lightRod, drivers in self.tmp_driver_map.items():
            rod_connected = False
            for driver in drivers:
                sensor_connected = False
                for attempt in range(3):
                    try:
                        driver.get_temperature()
                        sensor_connected = True
                        break  # Success
                    except OSError:
                        sleep(0.1)
                if sensor_connected:
                    rod_connected = True
                    break  # At least one sensor is connected, no need to check others
            self.connected_lightrods[lightRod] = rod_connected
            if not rod_connected:
                self.logger.info(f"Lightrod {lightRod} appears to be disconnected - skipping it for temperature readings.")

    def read_temps(self):
        lightrod_dict = {}
        sensor_success = None
        for lightRod, drivers in self.tmp_driver_map.items():
            # Skip disconnected lightrods

            # self.logger.debug(f"connect LRs: {self.connected_lightrods.__repr__()}")
            if not self.connected_lightrods.get(lightRod, False):  # skip rods that are disconnected or not in the dict
                continue

            temps = np.zeros(3)
            sensor_success = False

            try:
                for i in range(3):
                    try:
                        temps[i] = self._read_average_temperature(drivers[i])
                        if not np.isnan(temps[i]):
                            sensor_success = True
                    except exc.HardwareNotFoundError as e:
                        # Individual sensor failure
                        self.logger.debug(f"Sensor {i} on lightrod {lightRod} failed: {str(e)}")
                        temps[i] = float('nan')  # Mark as NaN
                    except Exception as e:
                        # Handle other failure types
                        self.logger.debug(f"LR sensor {i} on lightrod {lightRod} failed to read for unknown reason: {e}", exc_info=True)

                # Only add this lightrod if at least one sensor worked
                if sensor_success:
                    lightrod_dict[lightRod] = LightRodTemperature(
                        top_temp=float(round(temps[0], 2)) if not np.isnan(temps[0]) else float('nan'),
                        middle_temp=float(round(temps[1], 2)) if not np.isnan(temps[1]) else float('nan'),
                        bottom_temp=float(round(temps[2], 2)) if not np.isnan(temps[2]) else float('nan'),
                        timestamp=current_utc_datetime(),
                    )
                else:
                    self.disconnect_counter[lightRod] = self.disconnect_counter.get(lightRod, 0) + 1
                    if self.disconnect_counter[lightRod] > self.disconnect_threshold:
                        # All sensors failed, mark lightrod as disconnected after disconnect_threshold exceeded
                        del self.disconnect_counter[lightRod]
                        self.connected_lightrods[lightRod] = False
                        self.logger.warning(f"All sensors on lightrod {lightRod} failed to read {self.disconnect_threshold} times - marking as disconnected")

            except Exception as e:
                self.disconnect_counter[lightRod] = self.disconnect_counter.get(lightRod, 0) + 1
                self.logger.warning(f"Lightrod {lightRod} error: {str(e)} - incrementing disconnect counter to {self.disconnect_counter[lightRod]}")
                if self.disconnect_counter[lightRod] > self.disconnect_threshold:
                    self.logger.warning(f"Lightrod {lightRod} failed {self.disconnect_counter[lightRod]} times - marking as disconnected")
                    del self.disconnect_counter[lightRod]
                    self.connected_lightrods[lightRod] = False
                continue
        
        # self.logger.debug(f"sensor success: {sensor_success}")
        # self.logger.debug(f"lightrod_dict: {lightrod_dict.__repr__()}")

        if lightrod_dict:
            self.publish_max_temps(lightrod_dict)
            lightRod_temperatures = LightRodTemperatures(
                timestamp=current_utc_datetime(),
                temperatures=lightrod_dict,
            )
            # self.log_lightrod_temperatures(lightRod_temperatures)
            self.lightrod_temps = lightRod_temperatures
        else:
            self.logger.warning("No lightrods connected or all failed - unable to read any temperatures")
        
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

    def reset_connected_status(self):
        
        self.logger.info("Rescanning for connected lightrods...")
        self.check_connected_lightrods()
        return self.connected_lightrods

    ########## Private & internal methods

    def _read_average_temperature(self, driver) -> float:
        """
        Read the current temperature from sensor, in Celsius.
        Computes the median of 6 readings, and averages only values within 10% of the median.
        """
        temperatures = []
        averaged_temp = 0.0
        
        # check temp is fast, let's do it a few times to reduce variance.
        for i in range(6):
            try:
                temp = driver.get_temperature()
                temperatures.append(temp)
                sleep(0.1)
            except OSError as e:
                self.logger.debug(e, exc_info=True)
                self.logger.debug(exc.HardwareNotFoundError(
                    f"TMP1075 sensor {hex(driver.address)} dropped packet."
                ))

        if not temperatures:
            return float('nan')
        med = np.median(temperatures)

        # Filter values within 10% of the median
        threshold = 0.1 * med
        filtered = [t for t in temperatures if abs(t - med) <= threshold]
        averaged_temp = sum(filtered) / len(filtered)
        
        self._check_if_exceeds_max_temp(averaged_temp)

        return averaged_temp

    def _check_if_exceeds_max_temp(self, temp: float) -> bool:
        if temp > self.warning_threshold:
            self.logger.warning(
                f"Temperature of light rod has exceeded {self.warning_threshold}℃ - currently {temp}℃. Some action will be taken maybe idk"
                # TODO implement overtemperature correction action
            )
            # Turn off drivers
            publish(f"pioreactor/{self.unit}/{self.experiment}/lightrod_light_control/control", "shutdown_drivers", qos=QOS.AT_LEAST_ONCE)

            # Turn off relay
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