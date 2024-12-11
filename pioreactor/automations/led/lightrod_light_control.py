from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import publish_mqtt
from typing import Optional


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on remote ReadLightRodTemps status.
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

        # Trigger ReadLightRodTemps remotely
        self.trigger_read_lightrod_temps()

    def trigger_read_lightrod_temps(self):
        """
        Start the ReadLightRodTemps process remotely via MQTT or another method.
        """
        self.logger.info("Ensuring ReadLightRodTemps is running remotely.")
        try:
            publish_mqtt(
                f"pioreactor/{self.unit}/{self.experiment}/background_jobs/read_lightrod_temps/start",
                "start",
            )
            self.logger.info("Triggered ReadLightRodTemps remotely.")
        except Exception as e:
            self.logger.error(f"Failed to trigger ReadLightRodTemps: {e}")

    def execute(self) -> Optional[events.AutomationEvent]:
        """
        Periodically check status and adjust LED state.
        """
        self.logger.info("Executing LightrodLightControl check.")

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
