from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
from pioreactor.background_jobs.read_lightrod_temps import ReadLightRodTemps
from contextlib import nullcontext


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
        self.light_active: bool = False

    def on_init(self):
        """
        Ensure read_lightrod_temps is running during initialization.
        """
        if not is_pio_job_running("read_lightrod_temps"):
            self.logger.info("Starting read_lightrod_temps.")
            job = ReadLightRodTemps(unit=self.unit, experiment=self.experiment)
            job.block_until_ready(timeout=30)
        else:
            self.logger.info("read_lightrod_temps is already running.")

    def execute(self) -> events.AutomationEvent | None:
        """
        Execute periodically checks read_lightrod_temps status and sets LED intensity.
        """
        if not is_pio_job_running("read_lightrod_temps"):
            self.logger.warning("read_lightrod_temps is not running. Turning off LED automation.")
            self.clean_up()
            return events.ChangedLedIntensity("Turned off LEDs due to read_lightrod_temps stopping.")

        if not self.light_active:
            self.light_active = True
            for channel in self.channels:
                self.set_led_intensity(channel, self.light_intensity)
            return events.ChangedLedIntensity(f"Turned on LEDs at intensity {self.light_intensity}%.")

        return None  # No change to report

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
        self.light_active = False
        for channel in self.channels:
            self.set_led_intensity(channel, 0)
