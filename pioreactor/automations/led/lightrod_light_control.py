from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
import subprocess


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
        super().__init__(**kwargs)
        self.light_intensity = float(light_intensity)
        self.channels: list[LedChannel] = ["D", "C"]
        self.lightrod_process = None
        self.light_active: bool = False

    def on_init(self):
        """
        Turn on lightrod_temps during initialization.
        """
        try:
            self.lightrod_process = subprocess.Popen(["python", "path/to/lightrod_temps.py"])
            self.logger.info("lightrod_temps started.")
        except Exception as e:
            self.logger.error(f"Failed to start lightrod_temps: {e}")
            self.clean_up()

    def execute(self) -> events.AutomationEvent | None:
        """
        Execute is called periodically and checks if lightrod_temps is running. It sets
        LED intensity if the external process is active.
        """
        if not self.is_lightrod_running():
            self.logger.warning("lightrod_temps is not running. Turning off LED automation.")
            self.clean_up()
            return events.ChangedLedIntensity("Turned off LEDs due to lightrod_temps stopping.")

        if not self.light_active:
            self.light_active = True
            for channel in self.channels:
                self.set_led_intensity(channel, self.light_intensity)
            return events.ChangedLedIntensity(f"Turned on LEDs at intensity {self.light_intensity}%.")

        return None  # No change to report

    def is_lightrod_running(self) -> bool:
        """
        Check if lightrod_temps process is still running.
        """
        return self.lightrod_process and self.lightrod_process.poll() is None

    def set_light_intensity(self, intensity: float | str):
        """
        Set light intensity dynamically.
        """
        self.light_intensity = float(intensity)
        if self.light_active:
            for channel in self.channels:
                self.set_led_intensity(channel, self.light_intensity)

    def clean_up(self):
        """
        Cleanup resources and turn off LEDs.
        """
        if self.lightrod_process:
            self.lightrod_process.terminate()
            self.logger.info("lightrod_temps terminated.")
        self.light_active = False
        for channel in self.channels:
            self.set_led_intensity(channel, 0)
