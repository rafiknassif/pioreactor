# -*- coding: utf-8 -*-
from __future__ import annotations

from pioreactor.automations.events import NoEvent
from pioreactor.automations.ph.base import PHAutomationJob


class OnlyRecordPH(PHAutomationJob):
    """
    A simple pH automation that only records pH readings without taking any action.
    """
    automation_name = "only_record_ph"

    def __init__(self, **kwargs) -> None:
        super(OnlyRecordPH, self).__init__(**kwargs)

    def execute(self) -> NoEvent:
        return NoEvent()
