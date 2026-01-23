# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from time import sleep
from typing import Optional

import click
import numpy as np

from pioreactor import exc
from pioreactor import structs
from pioreactor import types as pt
from pioreactor.automations.base import AutomationJob
from pioreactor.calibrations import load_active_calibration
from pioreactor.config import config
from pioreactor.exc import HardwareNotFoundError
from pioreactor.hardware import PH_ADDR
from pioreactor.logging import create_logger
from pioreactor.structs import PH
from pioreactor.utils import whoami
from pioreactor.utils.adcs import ADC101C02x
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.utils.timing import RepeatedTimer


# ADC conversion constants
ADC_REFERENCE_VOLTAGE = 3300.0  # 3.3V reference in mV
ADC_MAX_VALUE = 1023.0  # 10-bit ADC


class PHAutomationJob(AutomationJob):
    """
    This is the super class that pH automations inherit from.
    The `execute` function, which is what subclasses will define, is updated every time a new pH is computed.
    pH values are updated every `samples_per_second` interval from config.

    To change setting over MQTT:

    `pioreactor/<unit>/<experiment>/ph_automation/<setting>/set` value
    to run calibration use: pio calibrations run --device ph --protocol-name three_point

    """

    automation_name = "ph_automation_base"
    job_name = "ph_automation"

    published_settings: dict[str, pt.PublishableSetting] = {}

    # pH thresholds
    DEFAULT_UPPER_WARNING_THRESHOLD = 8.0
    DEFAULT_LOWER_WARNING_THRESHOLD = 6.0

    latest_pH: float | None = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if (
            hasattr(cls, "automation_name")
            and getattr(cls, "automation_name") != "ph_automation_base"
        ):
            available_ph_automations[cls.automation_name] = cls

    def __init__(
        self,
        unit: str,
        experiment: str,
        upper_warning_threshold: float = DEFAULT_UPPER_WARNING_THRESHOLD,
        lower_warning_threshold: float = DEFAULT_LOWER_WARNING_THRESHOLD,
        **kwargs,
    ) -> None:
        super(PHAutomationJob, self).__init__(unit, experiment)

        self.upper_warning_threshold = upper_warning_threshold
        self.lower_warning_threshold = lower_warning_threshold

        # Load pH calibration
        self.calibration = load_active_calibration("ph")
        if self.calibration is None:
            self.logger.warning("No pH calibration found. Run 'pio calibration run --device ph' first. Using fallback calculation.")

        # Declare published settings
        self.add_to_published_settings(
            "pH", {"datatype": "PH", "settable": False, "unit": "pH"}
        )
        self.add_to_published_settings(
            "upper_warning_threshold", {"datatype": "float", "settable": True, "unit": "pH"}
        )
        self.add_to_published_settings(
            "lower_warning_threshold", {"datatype": "float", "settable": True, "unit": "pH"}
        )
        self.add_to_published_settings(
            "voltage_mv", {"datatype": "float", "settable": False, "unit": "mV"}
        )

        # Initialize ADC driver
        self._initialize_driver(PH_ADDR)

        self.pH: PH | None = None
        self.voltage_mv: float = 0.0

        # Get sample interval from config
        dt = 1 / config.getfloat("ph_automation.config", "samples_per_second", fallback=0.033)

        self.ph_timer = RepeatedTimer(
            dt,
            self.read_pH,
            job_name=self.job_name,
            run_immediately=True,
        ).start()

        self.latest_pH_at: datetime = current_utc_datetime()

    def _initialize_driver(self, i2c_addr: int) -> None:
        self.driver = ADC101C02x(i2c_addr)
        try:
            if not self.driver.test_connection():
                raise OSError("No response")
        except OSError:
            raise HardwareNotFoundError(
                f"Unable to find ADC at 0x{i2c_addr:02X}. Is it plugged into the I2C bus?"
            )

    def set_upper_warning_threshold(self, ph_thresh: float) -> None:
        self.upper_warning_threshold = ph_thresh

    def set_lower_warning_threshold(self, ph_thresh: float) -> None:
        self.lower_warning_threshold = ph_thresh

    def read_pH(self) -> None:
        """Read pH and update state."""
        pH_value = self._read_average_pH()

        self.pH = PH(
            timestamp=current_utc_datetime(),
            pH=pH_value,
        )

        self._set_latest_pH(self.pH)

    def _set_latest_pH(self, pH: structs.PH) -> None:
        self.previous_pH = self.latest_pH
        self.latest_pH = pH.pH
        self.latest_pH_at = pH.timestamp

        self.logger.debug(f"PBR pH: {self.latest_pH:.2f}")

        if self.state == self.READY or self.state == self.INIT:
            self.latest_event = self.execute()

    def _read_average_pH(self) -> float:
        """
        Read the current pH from sensor, averaging multiple samples to reduce noise.
        """
        # Discard first read to allow sampling capacitor to settle
        self.driver.read_raw()
        sleep(0.05)

        running_sum, running_count = 0.0, 0
        try:
            for _ in range(6):
                running_sum += self._read_pH()
                running_count += 1
                sleep(0.05)

        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the pH sensor connected to the I2C bus? Unable to find pH sensor."
            )

        averaged_pH = round(running_sum / running_count, 2)
        self._check_if_exceeds_pH_range(averaged_pH)

        return averaged_pH

    def _read_pH(self) -> float:
        """Convert raw ADC reading to pH value using calibration curve."""
        raw = self.driver.read_raw()

        # Convert raw ADC to millivolts
        voltage_mv = raw * (ADC_REFERENCE_VOLTAGE / ADC_MAX_VALUE)
        self.voltage_mv = voltage_mv

        if self.calibration is None:
            # Fallback: assume linear with rough defaults
            # Typical pH probe: neutral ~1500mV = pH 7, acid ~2032mV = pH 4
            # This gives roughly -3 pH per 532 mV increase
            pH_value = 7.0 + (voltage_mv - 1500.0) * (-3.0 / 532.0)
        else:
            # Use polynomial calibration curve
            # Convert to Python float to avoid numpy.float64 serialization issues
            pH_value = float(np.polyval(self.calibration.curve_data_, voltage_mv))

        return pH_value

    def _check_if_exceeds_pH_range(self, pH: float) -> bool:
        """Check if pH is outside warning thresholds and log warnings."""
        exceeds = False

        if pH > self.upper_warning_threshold:
            self.logger.warning(
                f"PBR pH has exceeded {self.upper_warning_threshold:.2f} - currently {pH:.2f}."
            )
            exceeds = True
        elif pH < self.lower_warning_threshold:
            self.logger.warning(
                f"PBR pH has fallen below {self.lower_warning_threshold:.2f} - currently {pH:.2f}."
            )
            exceeds = True

        return exceeds

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.ph_timer.cancel()


class PHAutomationJobContrib(PHAutomationJob):
    automation_name: str


def start_ph_automation(
    automation_name: str,
    unit: Optional[str] = None,
    experiment: Optional[str] = None,
    **kwargs,
) -> PHAutomationJob:
    from pioreactor.automations import ph  # noqa: F401

    unit = unit or whoami.get_unit_name()
    experiment = experiment or whoami.get_assigned_experiment_name(unit)

    try:
        klass = available_ph_automations[automation_name]
    except KeyError:
        raise KeyError(
            f"Unable to find {automation_name}. "
            f"Available automations are {list(available_ph_automations.keys())}"
        )

    try:
        return klass(
            unit=unit,
            experiment=experiment,
            automation_name=automation_name,
            **kwargs,
        )
    except Exception as e:
        logger = create_logger("ph_automation")
        logger.error(e)
        logger.debug(e, exc_info=True)
        raise e


available_ph_automations: dict[str, type[PHAutomationJob]] = {}


@click.command(
    name="ph_automation",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.option(
    "--automation-name",
    help="set the automation of the system: only_record_ph, etc.",
    show_default=True,
    required=True,
)
@click.option(
    "--upper-warning-threshold",
    default=8.0,
    show_default=True,
    type=click.FloatRange(0, 14, clamp=True),
)
@click.option(
    "--lower-warning-threshold",
    default=6.0,
    show_default=True,
    type=click.FloatRange(0, 14, clamp=True),
)
@click.pass_context
def click_ph_automation(ctx, automation_name, upper_warning_threshold, lower_warning_threshold):
    """
    Start a pH automation
    """
    la = start_ph_automation(
        automation_name=automation_name,
        upper_warning_threshold=upper_warning_threshold,
        lower_warning_threshold=lower_warning_threshold,
        **{ctx.args[i][2:].replace("-", "_"): ctx.args[i + 1] for i in range(0, len(ctx.args), 2)},
    )
    la.block_until_disconnected()
