# -*- coding: utf-8 -*-
import time
from pioreactor.background_jobs.subjobs.dosing_automation import DosingAutomation
from pioreactor.dosing_automations import events
from pioreactor.actions.add_media import add_media
from pioreactor.actions.remove_waste import remove_waste


class ContinuouslyRunning(DosingAutomation):
    """
    Useful for using the Pioreactor as an inline sensor.

    Idea 1: I start add and remove in threads
        - only fires dosing_events once
        - UI dosing doesn't update well

    Idea 2: in `execute` have a while loop that flips between add and remove
        - don't need threads
        - not very spammy

    Idea 3: set duration very small, and fire small dosing events
        - follows closest to existing dosing automation patterns.

    """

    def __init__(self, **kwargs):
        super(ContinuouslyRunning, self).__init__(**kwargs)

    def execute(self, *args, **kwargs) -> events.Event:
        add_media(
            ml=0.25,
            source_of_event=self.job_name,
            unit=self.unit,
            experiment=self.experiment,
        )
        time.sleep(3)
        remove_waste(
            ml=0.30,
            source_of_event=self.job_name,
            unit=self.unit,
            experiment=self.experiment,
        )
        return events.ContinuouslyDosing("Pumps will always run")
