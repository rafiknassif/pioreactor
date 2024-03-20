# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def run_around_tests(request):
    from pioreactor.utils import local_intermittent_storage
    from pioreactor.utils import local_persistant_storage

    test_name = request.node.name

    with local_intermittent_storage("pwm_dc") as cache:
        for key in cache.iterkeys():
            del cache[key]

    with local_intermittent_storage("led_locks") as cache:
        for key in cache.iterkeys():
            del cache[key]

    with local_intermittent_storage("pwm_locks") as cache:
        for key in cache.iterkeys():
            del cache[key]

    with local_intermittent_storage("leds") as cache:
        for key in cache.iterkeys():
            del cache[key]

    with local_persistant_storage("current_od_calibration") as cache:
        for key in cache.iterkeys():
            del cache[key]

    with local_persistant_storage("media_throughput") as c:
        c.pop(test_name)
    with local_persistant_storage("alt_media_throughput") as c:
        c.pop(test_name)
    with local_persistant_storage("alt_media_fraction") as c:
        c.pop(test_name)
    with local_persistant_storage("vial_volume") as c:
        c.pop(test_name)

    yield


@pytest.fixture(autouse=True)
def mock_external_leader_webserver_apis(mocker):
    # used mostly in pioreactor.config.py
    def mock_get_response(url):
        if url.endswith("/api/workers"):
            mm = MagicMock()
            mm.json.return_value = [
                {"pioreactor_unit": "unit1", "is_active": 1},
                {"pioreactor_unit": "unit2", "is_active": 1},
                {"pioreactor_unit": "unit3", "is_active": 0},
            ]
            return mm
        elif re.search("/api/experiments/.*/workers", url):
            mm = MagicMock()
            mm.json.return_value = [
                {"pioreactor_unit": "unit1", "is_active": 1},
                {"pioreactor_unit": "unit2", "is_active": 1},
            ]
            return mm
        elif re.search("/api/workers/.*/experiment", url):
            mm = MagicMock()
            mm.json.return_value = {"experiment": "_testing_experiment"}
            return mm
        else:
            raise ValueError(f"{url} not mocked")

    mock_get = mocker.patch("pioreactor.config.get", autospec=True, side_effect=mock_get_response)

    return mock_get
