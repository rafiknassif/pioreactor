# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps

import pytest

from pioreactor.cli.pio import get_non_prerelease_tags_of_pioreactor
from pioreactor.cli.pio import get_tag_to_install
from pioreactor.mureq import Response


# Mock the get function
def mock_get(status_code, body):
    response = Response("", status_code, {}, body)
    return response


# Test get_non_prerelease_tags_of_pioreactor function
def test_get_non_prerelease_tags_of_pioreactor(monkeypatch):
    fake_releases = [
        {"prerelease": False, "tag_name": "22.1.1"},
        {"prerelease": True, "tag_name": "22.2.1rc"},
        {"prerelease": False, "tag_name": "22.2.1"},
        {"prerelease": False, "tag_name": "22.3.1"},
    ]

    def mock_get_request(url, headers):
        return mock_get(200, dumps(fake_releases))

    monkeypatch.setattr("pioreactor.cli.pio.get", mock_get_request)

    result = get_non_prerelease_tags_of_pioreactor()
    assert result == ["22.3.1", "22.2.1", "22.1.1"]

    # Test when response status code is not 200
    def mock_get_bad_request(url, headers):
        return mock_get(404, "")

    monkeypatch.setattr("pioreactor.cli.pio.get", mock_get_bad_request)

    with pytest.raises(Exception):
        get_non_prerelease_tags_of_pioreactor()


# Test get_tag_to_install function
def test_get_tag_to_install(monkeypatch):
    monkeypatch.setattr(
        "pioreactor.cli.pio.get_non_prerelease_tags_of_pioreactor",
        lambda: ["22.4.1", "22.3.1", "22.2.1", "22.1.1", "21.12.1"],
    )
    assert get_tag_to_install("latest") == "latest"
    assert get_tag_to_install("22.3.1") == "tags/22.3.1"

    monkeypatch.setattr("pioreactor.version.__version__", "22.2.1")
    assert get_tag_to_install(None) == "tags/22.3.1"

    monkeypatch.setattr("pioreactor.version.__version__", "22.3.1")
    assert get_tag_to_install(None) == "tags/22.4.1"

    monkeypatch.setattr("pioreactor.version.__version__", "22.4.1")
    assert get_tag_to_install(None) == "latest"

    monkeypatch.setattr("pioreactor.version.__version__", "30.4.1")
    assert get_tag_to_install(None) == "latest"