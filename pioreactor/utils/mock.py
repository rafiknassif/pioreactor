# -*- coding: utf-8 -*-
# mock pieces for testing
from __future__ import annotations
from typing import Any
from json import loads
from pioreactor.config import config
from pioreactor.pubsub import subscribe_and_callback
from pioreactor.whoami import am_I_active_worker, is_testing_env
import random


class MockI2C:
    def __init__(self, SCL: int, SDA: int) -> None:
        pass

    def writeto(self, *args, **kwargs) -> None:
        return

    def try_lock(self, *args, **kwargs) -> bool:
        return True

    def unlock(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> MockI2C:
        return self

    def __exit__(self, *args: Any) -> None:
        return


class MockAnalogIn:
    INIT_STATE = 0.01
    state = INIT_STATE
    _counter = 0.0

    def __init__(self, ads, channel, **kwargs) -> None:
        from pioreactor.whoami import get_unit_name, get_latest_experiment_name

        # import pandas as pd
        # self.source = pd.read_csv(f"/Users/camerondavidson-pilon/code/pioreactor/demo_od{channel}.csv", index_col=0)

        # subscribe to dosing events
        assert channel in [0, 1], "channel must be in 0, 1"
        subscribe_and_callback(
            self.react_to_dosing,
            f"pioreactor/{get_unit_name()}/{get_latest_experiment_name()}/dosing_events",
        )
        self.max_gr = 0.25 + 0.1 * random.random()
        self.scale_factor = 0.00035 + 0.00005 * random.random()
        self.lag = 2 * 60 * 60 - 1 * 60 * 60 * random.random()

    def react_to_dosing(self, message) -> None:

        payload = loads(message.payload)

        if payload["event"] not in ["add_media", "add_alt_media"]:
            return
        self.state = self.state * (1 - (payload["volume_change"] / 14))

    def growth_rate(self, duration_as_seconds: int) -> float:
        import numpy as np

        return (
            self.max_gr
            / (1 + np.exp(-self.scale_factor * (duration_as_seconds - self.lag)))
            * (
                1
                - 1
                / (
                    1
                    + np.exp(
                        -self.scale_factor * 2 * (duration_as_seconds - 3 * self.lag)
                    )
                )
            )
        )

    @property
    def voltage(self) -> float:
        import random
        import numpy as np

        self.gr = self.growth_rate(
            self._counter / config.getfloat("od_config", "samples_per_second")
        )
        self.state *= np.exp(
            self.gr
            / 60
            / 60
            / config.getfloat("od_config", "samples_per_second")
            / 25  # divide by 25 from oversampling_count
        )
        self._counter += 1.0 / 25.0  # divide by 25 from oversampling_count
        return self.state + random.normalvariate(0, sigma=self.state * 0.001)

    @property
    def value(self) -> int:
        return round(self.voltage * 2 ** 17)


class MockDAC43608:

    _DEVICE_CONFIG = 1
    _STATUS = 2
    _BRDCAST = 3
    A = 8
    B = 9
    C = 10
    D = 11
    E = 12
    F = 13
    G = 14
    H = 15

    def __init__(self, *args, **kwargs) -> None:
        pass

    def set_intensity_to(self, channel: str, intensity: float) -> None:
        assert 0 <= intensity <= 1, "intensity should be between 0 and 1"
        assert channel in list(range(8, 16)), "register should be in 8 to 15"
        # TODO: this should update MQTT too
        return

    def power_up(*args) -> None:
        pass

    def power_down(*args) -> None:
        pass


class MockTMP1075:
    def __init__(*args) -> None:
        pass

    def get_temperature(self) -> float:
        import time, math, random

        return 3 * math.sin(0.1 * time.time() / 60) + 25 + 0.2 * random.random()


if am_I_active_worker() or is_testing_env():

    from rpi_hardware_pwm import HardwarePWM

    class MockHardwarePWM(HardwarePWM):
        def __init__(self, pwm_channel: int, hz: float) -> None:
            self.pwm_channel = pwm_channel
            self._hz = hz
            self.pwm_dir = ""

        def is_overlay_loaded(self) -> bool:
            return True

        def is_export_writable(self) -> bool:
            return True

        def does_pwmX_exists(self) -> bool:
            return True

        def echo(self, m: int, fil: str) -> None:
            pass
