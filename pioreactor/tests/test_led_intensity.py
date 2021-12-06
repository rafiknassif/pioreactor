# -*- coding: utf-8 -*-
# test_led_intensity
import pytest
from pioreactor.actions.led_intensity import (
    lock_leds_temporarily,
    led_intensity,
    LED_Channel,
)
from pioreactor.utils import local_intermittent_storage


def test_lock_will_prevent_led_from_updating() -> None:

    channel: LED_Channel = "A"

    assert led_intensity(channels=channel, intensities=20)

    with lock_leds_temporarily([channel]):
        assert not led_intensity(channels=channel, intensities=10)

    with local_intermittent_storage("leds") as cache:
        assert float(cache[channel]) == 20


def test_lock_will_prevent_led_from_updating_multiple_channels() -> None:

    assert led_intensity(channels=["A", "B"], intensities=[20, 20])

    with local_intermittent_storage("leds") as cache:
        assert float(cache["B"]) == 20
        assert float(cache["A"]) == 20

    with lock_leds_temporarily(["A"]):
        assert not led_intensity(channels=["A", "B"], intensities=[10, 10])

    with local_intermittent_storage("leds") as cache:
        assert float(cache["B"]) == 10
        assert float(cache["A"]) == 20


def test_error_is_thrown_if_lengths_are_wrong() -> None:

    with pytest.raises(RuntimeError):
        led_intensity(channels=["A", "B"], intensities=[20])

    with pytest.raises(RuntimeError):
        led_intensity(channels=["A", "B"], intensities=20)

    assert led_intensity(channels=["A"], intensities=20)


def test_local_cache_is_updated() -> None:

    channel: LED_Channel = "B"
    assert led_intensity(channels=channel, intensities=20)

    with local_intermittent_storage("leds") as cache:
        assert float(cache["B"]) == 20
