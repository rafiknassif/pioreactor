# -*- coding: utf-8 -*-
import click
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.config import config
from pioreactor.hardware import PWM_TO_PIN
from pioreactor.utils import clamp
from pioreactor.utils.pwm import PWM

class AirBubbler(BackgroundJob):
    job_name = "custom_air_bubbler"
    published_settings = {"duty_cycle": {"settable": True, "unit": "%", "datatype": "float"}}

    def __init__(self, unit: str, experiment: str, duty_cycle: float, hertz: float = 60):
        super().__init__(unit=unit, experiment=experiment)


        self.hertz = hertz
        try:
            self.pin = PWM_TO_PIN[config.get("PWM_reverse", "air_bubbler")]
        except KeyError:
            raise KeyError("Unable to find `air_bubbler` under PWM section in the config.ini")

        self.duty_cycle = duty_cycle
        self.pwm = PWM(self.pin, self.hertz, unit=self.unit, experiment=self.experiment)
        self.pwm.start(0)

    def on_disconnected(self):
        self.stop_pumping()
        self.pwm.stop()
        self.pwm.clean_up()

    def stop_pumping(self):
        if hasattr(self, "pwm"):
            self.pwm.change_duty_cycle(0)

    def start_pumping(self):
        self.pwm.change_duty_cycle(self.duty_cycle)

    def on_sleeping(self):
        self.stop_pumping()

    def on_sleeping_to_ready(self) -> None:
        self.start_pumping()

    def set_duty_cycle(self, value):
        self.duty_cycle = clamp(0, round(float(value)), 100)
        self.pwm.change_duty_cycle(self.duty_cycle)



@click.command(name="custom_air_bubbler")
def click_air_bubbler():
    """
    turn on air bubbler
    """
    from pioreactor.whoami import get_unit_name, get_latest_experiment_name


    dc = config.getfloat("custom_air_bubbler.config", "duty_cycle")
    hertz = config.getfloat("custom_air_bubbler.config", "hertz")

    ab = AirBubbler(unit=get_unit_name(), experiment=get_latest_experiment_name(), duty_cycle=dc, hertz=hertz)
    ab.start_pumping()
    ab.block_until_disconnected()