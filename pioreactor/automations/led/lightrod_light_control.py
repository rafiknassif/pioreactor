from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
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

    def execute(self) -> Optional[events.AutomationEvent]:
        """
        Periodically check ReadLightRodTemps status and adjust LED state accordingly.
        If ReadLightRodTemps is not running, log an error and disconnect.
        """
        self.logger.info("Executing LightrodLightControl check.")

        # Check if ReadLightRodTemps is running
        is_running = is_pio_job_running("read_lightrod_temps")
        self.logger.debug(f"read_lightrod_temps running status: {is_running}")

        if not is_running:
            self.logger.error("ReadLightRodTemps is not running. Disconnecting LED automation.")
            self.light_active = False
            for channel in self.channels:
                self.set_led_intensity(channel, 0)
            if self.state != self.DISCONNECTED:
                self.set_state(self.DISCONNECTED)
            return events.ChangedLedIntensity("Turned off LEDs due to ReadLightRodTemps not running.")

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