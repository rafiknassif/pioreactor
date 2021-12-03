# -*- coding: utf-8 -*-
import time
from pioreactor.background_jobs import temperature_control
from pioreactor.automations.temperature import Silent, Stable, ConstantDutyCycle
from pioreactor.whoami import get_unit_name, get_latest_experiment_name
from pioreactor import pubsub

unit = get_unit_name()
experiment = get_latest_experiment_name()


def pause(n=1):
    # to avoid race conditions when updating state
    time.sleep(n)


def test_stable_automation() -> None:
    with temperature_control.TemperatureController(
        "stable", target_temperature=50, unit=unit, experiment=experiment
    ) as algo:
        pause(2)
        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_control/temperature",
            '{"temperature": 55, "timestamp": "2020-01-01"}',
        )
        pause(2)

        algo.automation_job.target_temperature == 55


def test_changing_temperature_algo_over_mqtt() -> None:
    pubsub.publish(
        f"pioreactor/{unit}/{experiment}/temperature_control/temperature",
        None,
        retain=True,
    )

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as algo:
        assert algo.automation_name == "silent"
        assert isinstance(algo.automation_job, Silent)

        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_control/automation/set",
            '{"automation_name": "stable", "target_temperature": 20}',
        )
        time.sleep(8)
        assert algo.automation_name == "stable"
        assert isinstance(algo.automation_job, Stable)
        assert algo.automation_job.target_temperature == 20


def test_changing_temperature_algo_over_mqtt_and_then_update_params() -> None:
    pubsub.publish(
        f"pioreactor/{unit}/{experiment}/temperature_control/temperature",
        None,
        retain=True,
    )

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as algo:
        assert algo.automation_name == "silent"
        assert isinstance(algo.automation_job, Silent)

        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_control/automation/set",
            '{"automation_name": "constant_duty_cycle", "duty_cycle": 25}',
        )
        time.sleep(8)
        assert algo.automation_name == "constant_duty_cycle"
        assert isinstance(algo.automation_job, ConstantDutyCycle)
        assert algo.automation_job.duty_cycle == 25

        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_automation/duty_cycle/set", 30
        )
        pause()
        assert algo.automation_job.duty_cycle == 30


def test_heating_is_reduced_when_set_temp_is_exceeded() -> None:

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        t.tmp_driver.get_temperature = lambda *args: t.MAX_TEMP_TO_REDUCE_HEATING + 0.1

        t._update_heater(50)
        assert t.heater_duty_cycle == 50
        pause()
        t.read_external_temperature()
        pause()

        assert 0 < t.heater_duty_cycle < 50


def test_heating_stops_when_max_temp_is_exceeded() -> None:

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        # monkey path the driver
        t.tmp_driver.get_temperature = lambda *args: t.MAX_TEMP_TO_DISABLE_HEATING + 0.1

        t._update_heater(50)
        assert t.heater_duty_cycle == 50
        pause()
        t.read_external_temperature()
        pause()

        assert t.heater_duty_cycle == 0


def test_child_cant_update_heater_when_locked() -> None:

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment, eval_and_publish_immediately=False
    ) as t:
        assert t.automation_job.update_heater(50)

        with t.pwm.lock_temporarily():
            assert not t.automation_job.update_heater(50)
            assert not t.update_heater(50)

        assert t.automation_job.update_heater(50)


def test_constant_duty_cycle_init() -> None:
    pubsub.publish(
        f"pioreactor/{unit}/{experiment}/temperature_control/temperature",
        None,
        retain=True,
    )

    dc = 50
    with temperature_control.TemperatureController(
        "constant_duty_cycle", unit=unit, experiment=experiment, duty_cycle=dc
    ) as algo:
        pause()
        assert algo.heater_duty_cycle == 50


def test_setting_pid_control_after_startup_will_start_some_heating() -> None:
    # this test tries to replicate what a user does in the UI

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        # change to PID stable
        assert t.heater_duty_cycle == 0
        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_control/automation/set",
            '{"automation_name": "stable", "target_temperature": 35}',
        )

        pause(3)
        assert t.heater_duty_cycle > 0


def test_duty_cycle_is_published_and_not_settable() -> None:

    dc_msgs = []

    def collect(msg):
        dc_msgs.append(msg.payload)

    pubsub.subscribe_and_callback(
        collect,
        f"pioreactor/{get_unit_name()}/{get_latest_experiment_name()}/temperature_control/heater_duty_cycle",
    )

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ):
        # change to PID stable

        pubsub.publish(
            f"pioreactor/{unit}/{experiment}/temperature_control/automation/set",
            '{"automation_name": "stable", "target_temperature": 35}',
        )

        pause(3)

        # should produce an "Unable to set heater_duty_cycle"
        pubsub.publish(
            f"pioreactor/{get_unit_name()}/{get_latest_experiment_name()}/temperature_control/heater_duty_cycle/set",
            10,
        )

        pause(1)

    assert len(dc_msgs) > 0


def test_temperature_approximation1() -> None:

    features = {
        "previous_heater_dc": 17,
        "time_series_of_temp": [
            37.8125,
            36.625,
            35.6875,
            35.0,
            34.5,
            34.0625,
            33.6875,
            33.4375,
            33.1875,
            33.0,
            32.875,
            32.6875,
            32.5625,
            32.4375,
            32.375,
            32.25,
            32.1875,
        ],
    }

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert 32.0 <= t.approximate_temperature(features) <= 33.4


def test_temperature_approximation2() -> None:

    features = {
        "previous_heater_dc": 17,
        "time_series_of_temp": [
            44.8125,
            43.8125,
            43.0,
            42.25,
            41.5625,
            40.875,
            40.3125,
            39.75,
            39.1875,
            38.6875,
            38.25,
            37.8125,
            37.375,
            37.0,
            36.625,
            36.25,
            35.9375,
        ],
    }

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert 38 <= t.approximate_temperature(features) <= 39


def test_temperature_approximation3() -> None:

    features = {
        "previous_heater_dc": 17,
        "time_series_of_temp": [
            49.875,
            47.5,
            45.8125,
            44.375,
            43.1875,
            42.0625,
            41.125,
            40.3125,
            39.5625,
            38.875,
            38.1875,
            37.625,
            37.125,
            36.625,
            36.1875,
            35.8125,
            35.4375,
        ],
    }

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert 38 <= t.approximate_temperature(features) <= 39


def test_temperature_approximation_if_constant() -> None:

    features = {"previous_heater_dc": 17, "time_series_of_temp": 15 * [32.0]}

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert 32.0 == t.approximate_temperature(features)


def test_temperature_approximation_even_if_very_tiny_heat_source() -> None:
    import numpy as np

    features = {
        "previous_heater_dc": 14.5,
        "time_series_of_temp": list(
            22
            + 10 * np.exp(-0.008 * np.arange(0, 17))
            + 0.5 * np.exp(-0.28 * np.arange(0, 17))
        ),
    }

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert (32 * np.exp(-0.008 * 17)) < t.approximate_temperature(features) < 32


def test_temperature_approximation_even_if_very_large_heat_source() -> None:
    import numpy as np

    features = {
        "previous_heater_dc": 14.5,
        "time_series_of_temp": list(
            22
            + 3 * np.exp(-0.008 * np.arange(0, 17))
            + 20 * np.exp(-0.28 * np.arange(0, 17))
        ),
    }

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert (24 * np.exp(-0.008 * 17)) < t.approximate_temperature(features) < 25


def test_temperature_approximation_if_dc_is_nil() -> None:

    features = {"previous_heater_dc": 0, "time_series_of_temp": [37.8125, 32.1875]}

    with temperature_control.TemperatureController(
        "silent", unit=unit, experiment=experiment
    ) as t:
        assert t.approximate_temperature(features) == 32.1875
