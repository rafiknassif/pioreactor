# -*- coding: utf-8 -*-
# test_monitor
from __future__ import annotations

import time

import pytest

from pioreactor.background_jobs.monitor import Monitor
from pioreactor.pubsub import publish
from pioreactor.pubsub import subscribe
from pioreactor.whoami import get_assigned_experiment_name
from pioreactor.whoami import get_unit_name
from pioreactor.whoami import UNIVERSAL_EXPERIMENT


def pause(n=1):
    time.sleep(n * 0.5)


@pytest.mark.xfail()
def test_check_job_states_in_monitor() -> None:
    unit = get_unit_name()
    exp = UNIVERSAL_EXPERIMENT

    # suppose od_reading is READY when monitor starts, but isn't actually running, ex after a reboot on a worker.
    publish(
        f"pioreactor/{unit}/{get_assigned_experiment_name(unit)}/od_reading/$state",
        "ready",
        retain=True,
    )

    with Monitor(unit=unit, experiment=exp):
        pause(20)
        message = subscribe(f"pioreactor/{unit}/{get_assigned_experiment_name(unit)}/od_reading/$state")
        assert message is not None
        assert message.payload.decode() == "lost"
