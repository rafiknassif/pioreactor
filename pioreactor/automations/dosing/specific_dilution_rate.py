# -*- coding: utf-8 -*-
from __future__ import annotations

from pioreactor.automations import events
from pioreactor.automations.dosing.base import DosingAutomationJob
from pioreactor.exc import CalibrationError
from pioreactor.utils import local_persistent_storage


class SDR(DosingAutomationJob):
    """
    SDR mode - try to keep [nutrient] constant.
    """

    automation_name = "specific_dilution_rate"
    published_settings = {
        "volume": {"datatype": "float", "settable": True, "unit": "mL"},
        "specific_dilution_rate": {"datatype": "float", "settable": True, "unit": "1/h"},
    }

    def __init__(self, volume: float | str, specific_dilution_rate: float | str, **kwargs) -> None:
        
        super().__init__(
            unit=kwargs.pop("unit", None),
            experiment=kwargs.pop("experiment", None),
            volume=float(volume),  # `self.volume` is now properly assigned
            specific_dilution_rate=float(specific_dilution_rate),
            **kwargs
        )

        with local_persistent_storage("active_calibrations") as cache:
            if "media_pump" not in cache:
                raise CalibrationError("Media and waste pump calibration must be performed first.")
            elif "waste_pump" not in cache:
                raise CalibrationError("Media and waste pump calibration must be performed first.")

    def execute(self) -> events.DilutionEvent:
        volume_actually_cycled = self.execute_io_action(media_ml=self.volume, waste_ml=self.volume)
        return events.DilutionEvent(
            f"exchanged {volume_actually_cycled['waste_ml']}mL",
            data={"volume_actually_cycled": volume_actually_cycled["waste_ml"]},
        )
