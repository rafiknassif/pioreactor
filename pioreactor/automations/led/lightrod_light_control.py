from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.types import LedDriverChannel, LedChannel
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
from typing import Optional
from pioreactor.actions.led_driver import initialize_dac
from pioreactor.types import LedIntensityValue

import click


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on ReadLightRodTemps status.
    """

    automation_name: str = "lightrod_light_control"
    published_settings = {
        "relay_enabled": {"datatype": "float", "settable": True, "unit": "%"},
        "DRV_A_intensity": {"datatype": "float", "settable": True, "unit": "%"},
        "DRV_B_intensity": {"datatype": "float", "settable": True, "unit": "%"},
    }

    def __init__(
        self,
        DRV_A_intensity: float | str,
        DRV_B_intensity: float | str,
        relay_enabled: float | str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.drv_intensity: list[LedIntensityValue] = [float(DRV_A_intensity), float(DRV_B_intensity)]
        self.relay_enabled = float(relay_enabled)
        self.channels: list[LedDriverChannel] = ["DRV_A", "DRV_B"]
        self.relayChannel : LedChannel = "B"
        self.light_active: bool = False
        initialize_dac()

    def execute(self) -> Optional[events.AutomationEvent]:
        """
        Periodically check ReadLightRodTemps status and adjust LED state accordingly.
        If ReadLightRodTemps is not running, log an error and disconnect.
        """
        self.logger.debug("Executing LightrodLightControl check.")

        # Check if ReadLightRodTemps is running
        is_running = is_pio_job_running("read_lightrod_temps")
        self.logger.debug(f"read_lightrod_temps running status: {is_running}")

        if not is_running:
            self.logger.error("ReadLightRodTemps is not running. Disconnecting LED automation.")
            self.light_active = False
            self.disable_relay()
            if self.state != self.DISCONNECTED:
                self.set_state(self.DISCONNECTED)
            return events.ChangedLedIntensity("Turned off relay. LEDs disabled due to ReadLightRodTemps not running.")
        
        if not self.light_active and self.relay_enabled == 100:
            self.light_active = True
            self.enable_relay()
            self.logger.info(f"Turned on relay.")

        if not self.relay_enabled == 100:
            self.light_active = False
            self.disable_relay()
            self.logger.info(f"Turned off relay.")

        if self.light_active:
            self.set_driver_intensity(self.drv_intensity)

        return None
    
    def disable_relay(self):
        self.set_led_intensity(self.relayChannel, 0)  # turn off the relay
        self.logger.debug(f"Disable LED relay")

    def enable_relay(self):
        self.set_led_intensity(self.relayChannel, 100)  # turn on the relay 
        self.logger.debug(f"Enable LED relay")

    def set_driver_intensity(self, intensity: list[float | str]):
        """
        Update light intensity for the bioreactor.
        """
        if intensity == 0:
            self.disable_relay()

        self.drv_intensity = float(intensity)
        if self.light_active:
            for i in range(len(self.channels)):
                self.set_led_driver_intensity(self.channels[i], self.drv_intensity[i])
                self.logger.debug(f"Set LED channel {self.channels[i]} to an intensity of {self.drv_intensity[i]}")


# @click.option(
#     "--set-both-channels",
#     type=click.IntRange(min=0, max=1),
#     help="Both channels will get set to the instensity specified for DRV_A",
# )