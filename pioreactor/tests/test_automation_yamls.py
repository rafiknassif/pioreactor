# -*- coding: utf-8 -*-
# test automation_yamls
from __future__ import annotations

from yaml import load  # type: ignore
from yaml import Loader  # type: ignore

from pioreactor.automations import *  # noqa: F403, F401
from pioreactor.background_jobs.dosing_control import DosingController
from pioreactor.background_jobs.led_control import LEDController
from pioreactor.background_jobs.temperature_control import TemperatureController
from pioreactor.mureq import get


def get_specific_yaml(path):
    data = get(f"https://raw.githubusercontent.com/Pioreactor/pioreactorui/master/{path}")
    return load(data.content, Loader=Loader)


def test_automations_and_their_yamls_have_the_same_data():
    for automation_name, klass in LEDController.available_automations.items():
        data = get_specific_yaml(f"contrib/automations/led/{automation_name}.yaml")
        assert data["automation_name"] == automation_name

        for field in data["fields"]:
            key = field["key"]
            assert field["unit"] == klass.published_settings[key]["unit"]

        for setting in klass.published_settings:
            assert any([f["key"] == setting for f in data["fields"]])

    for automation_name, klass in DosingController.available_automations.items():
        data = get_specific_yaml(f"contrib/automations/dosing/{automation_name}.yaml")
        assert data["automation_name"] == automation_name

        for field in data["fields"]:
            key = field["key"]
            assert field["unit"] == klass.published_settings[key]["unit"]

        for setting in klass.published_settings:
            assert any([f["key"] == setting for f in data["fields"]])

    for automation_name, klass in TemperatureController.available_automations.items():
        data = get_specific_yaml(f"contrib/automations/temperature/{automation_name}.yaml")
        assert data["automation_name"] == automation_name

        for field in data["fields"]:
            key = field["key"]
            assert field["unit"] == klass.published_settings[key]["unit"]

        for setting in klass.published_settings:
            assert any([f["key"] == setting for f in data["fields"]])


# TODO: turn off plugin loading with a env variable.
