from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running, publish_mqtt
from typing import Optional


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on ReadLightRodTemps status.
    """

    automation_name: str = "lightrod_light_control"
    published_settings = {
        "light_intensity": {"datatype": "float", "settable": True, "unit": "%"},
    }

    def __init__(
        self,
        light_intensity: float | str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.light_intensity = float(light_intensity)
        self.channels: list[LedChannel] = ["B"]
        self.light_active: bool = False

        # Ensure ReadLightRodTemps is running on initialization
        self.ensure_read_lightrod_temps_running()

    def ensure_read_lightrod_temps_running(self):
        """
        Ensure the ReadLightRodTemps process is running remotely.
        """
        self.logger.info("Ensuring ReadLightRodTemps is running.")
        if not is_pio_job_running("read_lightrod_temps"):
            try:
                publish_mqtt(
                    f"pioreactor/{self.unit}/{self.experiment}/background_jobs/read_lightrod_temps/start",
                    "start",
                )
                self.logger.info("Triggered ReadLightRodTemps remotely.")
            except Exception as e:
                self.logger.error(f"Failed to trigger ReadLightRodTemps: {e}")
        else:
            self.logger.info("ReadLightRodTemps is already running.")

    def execute(self) -> Optional[events.AutomationEvent]:
        """
        Periodically check ReadLightRodTemps status and adjust LED state accordingly.
        """
        self.logger.info("Executing LightrodLightControl check.")

        # Check ReadLightRodTemps status
        is_running = is_pio_job_running("read_lightrod_temps")
        self.logger.debug(f"ReadLightRodTemps running status: {is_running}")

        if not is_running:
            self.logger.warning("ReadLightRodTemps is not running. Attempting to restart remotely.")
            self.ensure_read_lightrod_temps_running()

        if not self.light_active:
            self.light_active = True
            for channel in self.channels:
                self.set_led_intensity(channel, self.light_intensity)
            return events.ChangedLedIntensity(f"Turned on LEDs at intensity {self.light_intensity}%.")

        return None

    def set_light_intensity(self, intensity: float | str):
        """
        Dynamically update light intensity.
        """
        self.light_intensity = float(intensity)
        if self.light_active:
            for channel in self.channels:
                self.set_led_intensity(channel, self.light_intensity)
