from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
from pioreactor.background_jobs.read_lightrod_temps import click_read_lightrod_temps
from click.testing import CliRunner
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
        Ensure the ReadLightRodTemps process is running by invoking the Click command.
        """
        self.logger.info("Ensuring ReadLightRodTemps is running.")
        if not is_pio_job_running("read_lightrod_temps"):
            try:
                runner = CliRunner()
                result = runner.invoke(click_read_lightrod_temps, ["--warning-threshold", "40"])
                if result.exit_code == 0:
                    self.logger.info("Triggered ReadLightRodTemps successfully.")
                else:
                    self.logger.error(f"Failed to trigger ReadLightRodTemps: {result.output}")
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
        self.logger.debug(f"read_lightrod_temps running status: {is_running}")

        if not is_running:
            self.logger.warning("read_lightrod_temps has stopped. Attempting to restart.")
            self.ensure_read_lightrod_temps_running()
            # Re-check status after attempting to restart
            if not is_pio_job_running("read_lightrod_temps"):
                self.logger.error("Failed to restart read_lightrod_temps. Turning off LED automation.")
                self.light_active = False
                for channel in self.channels:
                    self.set_led_intensity(channel, 0)
                if self.state != self.DISCONNECTED:
                    self.set_state(self.DISCONNECTED)
                return events.ChangedLedIntensity("Turned off LEDs due to read_lightrod_temps failure.")

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
