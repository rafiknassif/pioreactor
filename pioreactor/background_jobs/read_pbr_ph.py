import random
from contextlib import suppress
from time import sleep
import numpy as np
import json

from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import PH_ADDR
from pioreactor.structs import PH
from pioreactor.utils.temps import MCP9600
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity
import click


class ReadPBRPH(BackgroundJob):
    job_name = "read_pbr_ph"
    published_settings = {
        'upper_warning_threshold': {'datatype': "float", "unit": "pH", "settable": True},
        'lower_warning_threshold': {'datatype': "float", "unit": "pH", "settable": True},
        "PBR_pH": {"datatype": "PH", "settable": False}
    }


    def __init__(self, unit, experiment, upper_warning_threshold=6, lower_warning_threshold=8):
        super().__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(PH_ADDR)
        self.set_upper_warning_threshold(upper_warning_threshold)
        self.set_lower_warning_threshold(lower_warning_threshold)
        self.PBR_pH = None  # initialize for mqtt broadcast

        dt = 1 / (config.getfloat("pbr_ph_reading.config", "samples_per_second", fallback=0.033))

        self.read_pbr_ph_timer = RepeatedTimer(
            dt,
            self.read_ph,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, i2c_addr):
        self.driver = None
        # TODO implement pH probe drivers

    def set_upper_warning_threshold(self, ph_thresh):
        self.upper_warning_threshold = ph_thresh

    def set_lower_warning_threshold(self, ph_thresh):
        self.lower_warning_threshold = ph_thresh

    def read_ph(self):
        pH = self._read_average_ph()

        self.PBR_pH = PH(
            timestamp=current_utc_datetime(),
            pH=pH,
        )

    def log_PBR_pH(self):
        self.logger.debug(
            f"PBR Temperature: {self.PBR_pH.pH}"
        )

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.read_pbr_ph_timer.cancel()

    ########## Private & internal methods

    def _read_average_ph(self) -> float:
        """
        Read the current pH from sensor
        """
        running_sum, running_count = 0.0, 0
        try:
            # check temp is fast, let's do it a few times to reduce variance.
            for i in range(6):
                running_sum += random.uniform(-5, 20)#self.driver.read_pH  # TODO temproary placeholder, set this correctly
                running_count += 1
                sleep(0.05)

        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the thermocouple connected to the I2C bus? Unable to find temperature sensor."
            )

        averaged_pH = running_sum / running_count
        self._check_if_exceeds_temp_range(averaged_pH)

        return averaged_pH

    def _check_if_exceeds_pH_range(self, ph: float) -> bool:
        if ph > self.upper_warning_threshold:
            self.logger.warning(
                f"PBR PH has exceeded {self.upper_warning_threshold} - currently {ph}. idk what to do"
            )
        elif ph < self.lower_warning_threshold:
            self.logger.warning(
                f"PBR pH has fallen below {self.lower_warning_threshold} - currently {ph}. Some action will be taken maybe idk"
            )
            # TODO implement correction action

        return ph > self.upper_warning_threshold and ph < self.lower_warning_threshold


@click.command(name="read_pbr_ph")
@click.option(
    "--upper-warning-threshold",
    default=8,
    show_default=True,
    type=click.FloatRange(0, 14, clamp=True),
)
@click.option(
    "--lower-warning-threshold",
    default=6,
    show_default=True,
    type=click.FloatRange(0, 14, clamp=True),
)
def click_read_pbr_ph(upper_warning_threshold, lower_warning_threshold):
    from pioreactor.whoami import get_unit_name, get_assigned_experiment_name

    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    job = ReadPBRPH(
        upper_warning_threshold=upper_warning_threshold,
        lower_warning_threshold=lower_warning_threshold,
        unit=unit,
        experiment=experiment,
    )
    job.block_until_disconnected()