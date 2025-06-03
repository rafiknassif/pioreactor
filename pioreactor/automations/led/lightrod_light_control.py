from pioreactor.automations.led.base import LEDAutomationJob
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.types import LedDriverChannel, LedChannel
from pioreactor.structs import LEDDriverIntensity
from pioreactor.automations import events
from pioreactor.utils import is_pio_job_running
from typing import Optional
from pioreactor.actions.led_driver import initialize_dac
from pioreactor.types import LedIntensityValue
from pioreactor.whoami import get_unit_name, get_assigned_experiment_name
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.pubsub import QOS


import click


class LightrodLightControl(LEDAutomationJob):
    """
    Lightrod light control automation for managing LED based on ReadLightRodTemps status.
    """
    instances = []
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
        self.instances.append(self)
        super().__init__(**kwargs)
        self.DRV_A_intensity = float(DRV_A_intensity)
        self.DRV_B_intensity = float(DRV_B_intensity)
        self.relay_enabled = float(relay_enabled)
        self.channels: list[LedDriverChannel] = ["DRV_A", "DRV_B"]
        self.relayChannel : LedChannel = "B"
        self.light_active: bool = False
        self.prev_A_intensity = 0
        self.prev_B_intensity = 0

        initialize_dac()

        # Subscribe to control topic
        self.subscribe_and_callback(
            self.handle_control_message,
            f"pioreactor/{self.unit}/{self.experiment}/lightrod_light_control/control",
            qos=QOS.AT_LEAST_ONCE,
        )

        self.unit = get_unit_name()
        self.experiment = get_assigned_experiment_name(self.unit)
    
    def on_disconnected(self) -> None:
        self.shutdown_drivers()
        self.disable_relay()
        super().on_disconnected()

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
            self.set_driver_intensity()

        return None
    
    def handle_control_message(self, message):
        command = message.payload.decode()
        if command == "shutdown_drivers":
            self.shutdown_drivers()
        elif "DRV_A_intensity" in command:
            _, param = command.split(" ", 1)
            self.DRV_A_intensity = float(param)
            self.set_driver_intensity()
        elif "DRV_B_intensity" in command:
            _, param = command.split(" ", 1)
            self.DRV_B_intensity = float(param)
            self.set_driver_intensity()
        else:
            self.logger.warning(f"Unknown command: {command}")
    
    def disable_relay(self):
        self.set_led_intensity(self.relayChannel, 0)  # turn off the relay
        self.logger.debug(f"Disable LED relay")

    def enable_relay(self):
        self.set_led_intensity(self.relayChannel, 100)  # turn on the relay 
        self.logger.debug(f"Enable LED relay")

    def shutdown_drivers(self):
        self.DRV_A_intensity = 0
        self.DRV_B_intensity = 0
        self.set_driver_intensity()
        self.logger.debug("shutdown drivers called - setting intensities to 0")

    def set_driver_intensity(self):
        """
        Update light intensity for the bioreactor.
        """
        if self.DRV_A_intensity==0 and self.DRV_B_intensity==0:
            self.disable_relay()

        if self.light_active:
            self.set_led_driver_intensity(self.channels[0], self.DRV_A_intensity)
            if self.DRV_A_intensity != self.prev_A_intensity:
                self.prev_A_intensity = self.DRV_A_intensity
                self.publish_intensity(self.channels[0], self.DRV_A_intensity)
                self.logger.debug(f"Set LED channel {self.channels[0]} to an intensity of {self.DRV_A_intensity}")

            self.set_led_driver_intensity(self.channels[1], self.DRV_B_intensity)
            if self.DRV_B_intensity != self.prev_B_intensity:
                self.prev_B_intensity = self.DRV_B_intensity
                self.publish_intensity(self.channels[1], self.DRV_B_intensity)
                self.logger.debug(f"Set LED channel {self.channels[1]} to an intensity of {self.DRV_B_intensity}")

    def publish_intensity(self, channel, intensity):
        driverIntensity = LEDDriverIntensity(
            timestamp=current_utc_datetime(),
            channel=channel,
            driver_intensity=intensity
        )
        BackgroundJob.publish(
            self,
            topic=f"pioreactor/{self.unit}/{self.experiment}/lightrod_light_control/driver_intensity",
            payload=driverIntensity  # Publish as an object
        )

    @classmethod
    def getInstance(cls):
        return cls.instances[0] if cls.instances else None

import click

@click.command(name="lightrod_light_control")
@click.option(
    "--DRV_A_intensity",
    default=0,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
@click.option(
    "--DRV_B_intensity",
    default=0,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
def click_lightrod_light_control(DRV_A_SETPOINT, DRV_B_SETPOINT):
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    from pioreactor.logging import create_logger
    logger = create_logger("click_lightrod_light_control")
    logger.debug(f"updated led driver setpoints: {DRV_A_SETPOINT}, {DRV_B_SETPOINT}")

    inst = LightrodLightControl.getInstance()
    logger.debug(f"updating lightrodLightControl instance: {inst.__repr__()}")
    inst.DRV_A_intensity = float(DRV_A_SETPOINT)
    inst.DRV_B_intensity = float(DRV_B_SETPOINT)