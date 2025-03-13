# -*- coding: utf-8 -*-
from __future__ import annotations
from pioreactor.automations import events
from pioreactor.automations.dosing.base import DosingAutomationJob
from pioreactor.exc import CalibrationError
from pioreactor.utils import local_persistent_storage
from pioreactor.pubsub import publish


class SDR(DosingAutomationJob):
    """
    SDR mode - try to keep [nutrient] constant.
    """
    automation_name = "specific_dilution_rate"
    published_settings = {
        "volume": {"datatype": "float", "settable": True, "unit": "mL"},
        "sdr": {"datatype": "float", "settable": True, "unit": "1/h"},
    }

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        with local_persistent_storage("active_calibrations") as cache:
            if "media_pump" not in cache:
                raise CalibrationError("Media and waste pump calibration must be performed first.")
            elif "waste_pump" not in cache:
                raise CalibrationError("Media and waste pump calibration must be performed first.")

    def execute(self) -> events.DilutionEvent:
        volume_actually_cycled = self.execute_io_action(media_ml=self.volume, waste_ml=self.volume)

        # Calculate and Publish the specific dilution rate (SDR)
        dilution_rate = self.sdr if self.sdr is not None else 0  # Ensure a default value

        publish(
            f"pioreactor/{self.unit}/{self.experiment}/dosing_automation/specific_dilution_rate",
            str(dilution_rate)
        )

        self.logger.debug(f"Published SDR: {dilution_rate} 1/h")

        return events.DilutionEvent(
            f"exchanged {volume_actually_cycled['waste_ml']}mL",
            data={"volume_actually_cycled": volume_actually_cycled["waste_ml"]},
        )
