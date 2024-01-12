# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

import pytest

from pioreactor.actions.leader.experiment_profile import execute_experiment_profile
from pioreactor.actions.leader.experiment_profile import hours_to_seconds
from pioreactor.experiment_profiles.profile_struct import _LogOptions
from pioreactor.experiment_profiles.profile_struct import Log
from pioreactor.experiment_profiles.profile_struct import Metadata
from pioreactor.experiment_profiles.profile_struct import Profile
from pioreactor.experiment_profiles.profile_struct import Start
from pioreactor.experiment_profiles.profile_struct import Stop
from pioreactor.experiment_profiles.profile_struct import Update
from pioreactor.pubsub import collect_all_logs_of_level
from pioreactor.pubsub import subscribe_and_callback


# First test the hours_to_seconds function
def test_hours_to_seconds() -> None:
    assert hours_to_seconds(1) == 3600
    assert hours_to_seconds(0.5) == 1800
    assert hours_to_seconds(0) == 0


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_profile_order(mock__load_experiment_profile) -> None:
    action1 = Start(hours_elapsed=0 / 60 / 60)
    action2 = Start(hours_elapsed=2 / 60 / 60)
    action3 = Stop(hours_elapsed=4 / 60 / 60)

    profile = Profile(
        experiment_profile_name="test_profile",
        plugins=[],
        common={"job1": {"actions": [action1]}},
        pioreactors={"unit1": {"jobs": {"job2": {"actions": [action2, action3]}}}},
        metadata=Metadata(author="test_author"),
        labels={"unit1": "label1"},
    )

    mock__load_experiment_profile.return_value = profile

    actions = []

    def collection_actions(msg):
        actions.append(msg.topic)

    subscribe_and_callback(
        collection_actions,
        ["pioreactor/unit1/_testing_experiment/#", "pioreactor/$broadcast/_testing_experiment/#"],
        allow_retained=False,
    )

    execute_experiment_profile("profile.yaml")

    assert actions == [
        "pioreactor/$broadcast/_testing_experiment/run/job1",
        "pioreactor/unit1/_testing_experiment/run/job2",
        "pioreactor/unit1/_testing_experiment/job2/$state/set",
    ]


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_profile_hack_for_led_intensity(
    mock__load_experiment_profile,
) -> None:
    action1 = Start(hours_elapsed=0 / 60 / 60, options={"A": 50})
    action2 = Update(hours_elapsed=1 / 60 / 60, options={"A": 40, "B": 22.5})
    action3 = Stop(hours_elapsed=2 / 60 / 60)
    job = "led_intensity"

    profile = Profile(
        experiment_profile_name="test_profile",
        plugins=[],
        pioreactors={"unit1": {"jobs": {job: {"actions": [action1, action2, action3]}}}},
        metadata=Metadata(author="test_author"),
    )

    mock__load_experiment_profile.return_value = profile

    actions = []

    def collection_actions(msg):
        actions.append((msg.topic, msg.payload.decode()))

    subscribe_and_callback(
        collection_actions,
        ["pioreactor/unit1/_testing_experiment/#"],
        allow_retained=False,
    )

    execute_experiment_profile("profile.yaml")

    assert actions == [
        (
            "pioreactor/unit1/_testing_experiment/run/led_intensity",
            '{"options":{"A":50},"args":[]}',
        ),
        (
            "pioreactor/unit1/_testing_experiment/run/led_intensity",
            '{"options":{"A":40,"B":22.5},"args":[]}',
        ),
        (
            "pioreactor/unit1/_testing_experiment/run/led_intensity",
            '{"options":{"A":0,"B":0,"C":0,"D":0},"args":[]}',
        ),
    ]


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_log_actions(mock__load_experiment_profile) -> None:
    action1 = Log(hours_elapsed=0 / 60 / 60, options=_LogOptions(message="test {unit}"))
    action2 = Log(
        hours_elapsed=2 / 60 / 60, options=_LogOptions(message="test {job} on {unit}", level="INFO")
    )
    action3 = Log(hours_elapsed=4 / 60 / 60, options=_LogOptions(message="test {experiment}"))

    profile = Profile(
        experiment_profile_name="test_profile",
        plugins=[],
        common={"job1": {"actions": [action1]}},
        pioreactors={"unit1": {"jobs": {"job2": {"actions": [action2, action3]}}}},
        metadata=Metadata(author="test_author"),
        labels={"unit1": "label1"},
    )

    mock__load_experiment_profile.return_value = profile

    with collect_all_logs_of_level(
        "NOTICE", "testing_unit", "_testing_experiment"
    ) as notice_bucket, collect_all_logs_of_level(
        "INFO", "testing_unit", "_testing_experiment"
    ) as info_bucket:
        execute_experiment_profile("profile.yaml")

        assert [
            log["message"] for log in notice_bucket[1:-1]
        ] == [  # slice to remove the first and last NOTICE
            "test $broadcast",
            "test _testing_experiment",
        ]
        assert [log["message"] for log in info_bucket] == [
            "test job2 on unit1",
        ]


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_start_and_stop_controller(mock__load_experiment_profile) -> None:
    action1 = Start(hours_elapsed=0 / 60 / 60, options={"automation_name": "silent"})
    action2 = Stop(
        hours_elapsed=1 / 60 / 60,
    )

    profile = Profile(
        experiment_profile_name="test_profile",
        common={"temperature_control": {"actions": [action1, action2]}},
        metadata=Metadata(author="test_author"),
    )

    mock__load_experiment_profile.return_value = profile

    execute_experiment_profile("profile.yaml")


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_update_automations_not_controllers(
    mock__load_experiment_profile,
) -> None:
    action1 = Start(
        hours_elapsed=0 / 60 / 60,
        options={"automation_name": "thermostat", "target_temperature": 25},
    )
    action2 = Update(hours_elapsed=1 / 60 / 60, options={"target_temperature": 30})

    profile = Profile(
        experiment_profile_name="test_profile",
        common={"temperature_control": {"actions": [action1, action2]}},
        metadata=Metadata(author="test_author"),
    )

    mock__load_experiment_profile.return_value = profile

    with pytest.raises(ValueError, match="Update"):
        execute_experiment_profile("profile.yaml")


@patch("pioreactor.actions.leader.experiment_profile._load_experiment_profile")
def test_execute_experiment_start_controller_and_stop_automation_fails(
    mock__load_experiment_profile,
) -> None:
    action1 = Start(hours_elapsed=0 / 60 / 60, options={"automation_name": "silent"})
    action2 = Stop(
        hours_elapsed=1 / 60 / 60,
    )

    profile = Profile(
        experiment_profile_name="test_profile",
        common={
            "temperature_control": {"actions": [action1]},
            "temperature_automation": {"actions": [action2]},
        },
        metadata=Metadata(author="test_author"),
    )

    mock__load_experiment_profile.return_value = profile

    with pytest.raises(ValueError, match="stop"):
        execute_experiment_profile("profile.yaml")
