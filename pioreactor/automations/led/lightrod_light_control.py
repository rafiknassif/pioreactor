from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.utils import logger
from pioreactor.pubsub import publish
from pioreactor.types import LedChannel
import subprocess
import time


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on lightrod_temps status.
    """
    automation_name = "lightrod_light_control"
    published_settings = {
        "light_intensity": {"datatype": "float", "settable": True, "unit": "%"},
    }

    def __init__(self, light_intensity: float | str, **kwargs):
        super().__init__(**kwargs)
        self.light_intensity = float(light_intensity)
        # Hardcoding channels as in LightDarkCycle
        self.channels: list[LedChannel] = ["B"]
        self.lightrod_process = None

    def on_init(self):
        """
        Turn on lightrod_temps during initialization.
        """
        try:
            self.lightrod_process = subprocess.Popen(["python", "path/to/lightrod_temps.py"])
            logger.info("lightrod_temps started.")
        except Exception as e:
            logger.error(f"Failed to start lightrod_temps: {e}")
            self.clean_up()

    def process(self):
        """
        Main loop to control LED based on lightrod_temps status.
        """
        try:
            while self.is_running:
                if not self.is_lightrod_running():
                    logger.warning("lightrod_temps is not running. Turning off LED automation.")
                    self.clean_up()
                    break

                self.set_leds_intensity(self.light_intensity)
                time.sleep(1)
        except Exception as e:
            logger.error(f"Error in Lightrod light process: {e}")
        finally:
            self.clean_up()

    def is_lightrod_running(self):
        """
        Check if lightrod_temps process is still running.
        """
        return self.lightrod_process and self.lightrod_process.poll() is None

    def set_leds_intensity(self, intensity):
        """
        Set the LED intensity for all hardcoded channels.
        """
        for channel in self.channels:
            publish(
                f"pioreactor/{self.unit}/{self.experiment}/leds/{channel}/intensity",
                intensity,
            )

    def clean_up(self):
        """
        Cleanup resources and turn off LEDs.
        """
        if self.lightrod_process:
            self.lightrod_process.terminate()
            logger.info("lightrod_temps terminated.")
        self.set_leds_intensity(0)
