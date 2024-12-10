from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
from threading import Event
from typing import Optional


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on lightrod_temps status.
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
        # Initialize required attributes first
        self.channels: list[LedChannel] = ["B"]
        self.light_active: bool = False
        self._blocking_event = Event()  # Explicitly initialize the blocking event

        # Call the parent class initializer
        super().__init__(**kwargs)

        # Additional initialization specific to this class
        self.light_intensity = float(light_intensity)

    def on_init(self):
        """
        Ensure read_lightrod_temps is running during initialization.
        """
        if not is_pio_job_running("read_lightrod_temps"):
            self.logger.error("read_lightrod_temps must be turned on first.")
            # Raising an exception to cleanly abort initialization
            raise RuntimeError("read_lightrod_temps is not running. Cannot initialize LightrodLightControl.")
        else:
            self.logger.info("read_lightrod_temps is already running.")

    def execute(self) -> Optional[events.AutomationEvent]:
        """
        Periodically checks read_lightrod_temps status and adjusts LED state accordingly.
        """
        self.logger.info("Executing LightrodLightControl check.")
        is_running = is_pio_job_running("read_lightrod_temps")
        self.logger.debug(f"read_lightrod_temps running status: {is_running}")

        if not is_running:
            self.logger.warning("read_lightrod_temps has stopped. Turning off LED automation.")
            self.light_active = False
            if hasattr(self, "channels"):  # Ensure channels are initialized
                for channel in self.channels:
                    self.set_led_intensity(channel, 0)
            if self.state != self.DISCONNECTED:
                self.set_state(self.DISCONNECTED)
            return events.ChangedLedIntensity("Turned off LEDs due to read_lightrod_temps stopping.")

        if not self.light_active:
            self.light_active = True
            if hasattr(self, "channels"):  # Ensure channels are initialized
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
            if hasattr(self, "channels"):  # Ensure channels are initialized
                for channel in self.channels:
                    self.set_led_intensity(channel, self.light_intensity)

    def set_state(self, state: str) -> None:
        """
        Transition the automation state explicitly to avoid LOST status.
        """
        super().set_state(state)
        if state == self.DISCONNECTED:
            self.logger.info("LightrodLightControl is now disconnected.")
            self.light_active = False
            # Ensure channels are safely managed
            if hasattr(self, "channels"):  # Check if channels exist
                for channel in self.channels:
                    self.set_led_intensity(channel, 0)
