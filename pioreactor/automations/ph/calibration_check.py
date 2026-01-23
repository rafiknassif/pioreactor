# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress

from pioreactor.automations.events import NoEvent
from pioreactor.automations.ph.base import PHAutomationJob
from pioreactor.utils.timing import RepeatedTimer


class CalibrationCheck(PHAutomationJob):
    """
    A pH automation for checking calibration accuracy.
    Reads pH every 5 seconds and prints to console without saving to database.
    Useful for verifying calibration with buffer solutions.
    """
    automation_name = "calibration_check"

    def __init__(self, **kwargs) -> None:
        super(CalibrationCheck, self).__init__(**kwargs)

        # Cancel the default timer and create one with 5-second interval
        with suppress(AttributeError):
            self.ph_timer.cancel()

        self.ph_timer = RepeatedTimer(
            5.0,  # 5 seconds between readings
            self.read_pH,
            job_name=self.job_name,
            run_immediately=True,
        ).start()

    def read_pH(self) -> None:
        """Override to print to console without publishing to MQTT/database."""
        pH_value = self._read_average_pH()

        # Print to console for calibration verification
        self.logger.info(f"pH: {pH_value:.2f}  |  Voltage: {self.voltage_mv:.1f} mV")

        # Don't set self.pH - this prevents publishing to MQTT/database

    def execute(self) -> NoEvent:
        return NoEvent()
