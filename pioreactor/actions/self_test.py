# -*- coding: utf-8 -*-
"""
This action performs internal hardware & software tests of the system to confirm things work as expected.

Functions with prefix `test_` are ran, and any exception thrown means the test failed.

Outputs from each test go into MQTT, and return to the command line.
"""
from __future__ import annotations

import sys
from json import dumps
from threading import Thread
from time import sleep
from typing import Callable
from typing import cast
from typing import Iterator
from typing import Optional

import click

from pioreactor.automations.temperature.only_record_temperature import OnlyRecordTemperature
from pioreactor.background_jobs import stirring
from pioreactor.config import config
from pioreactor.config import temporary_config_change
from pioreactor.hardware import is_HAT_present
from pioreactor.hardware import is_heating_pcb_present
from pioreactor.hardware import voltage_in_aux
from pioreactor.logging import create_logger
from pioreactor.logging import CustomLogger
from pioreactor.pubsub import Client
from pioreactor.pubsub import post_into_leader
from pioreactor.pubsub import prune_retained_messages
from pioreactor.utils import is_pio_job_running
from pioreactor.utils import local_persistent_storage
from pioreactor.utils import managed_lifecycle
from pioreactor.utils import SummableDict
from pioreactor.utils.math_helpers import correlation
from pioreactor.utils.math_helpers import mean
from pioreactor.whoami import get_assigned_experiment_name
from pioreactor.whoami import get_testing_experiment_name
from pioreactor.whoami import get_unit_name
from pioreactor.whoami import is_testing_env


def test_pioreactor_HAT_present(managed_state, logger: CustomLogger, unit: str, experiment: str) -> None:
    assert is_HAT_present(), "HAT is not connected"




def test_detect_heating_pcb(managed_state, logger: CustomLogger, unit: str, experiment: str) -> None:
    assert is_heating_pcb_present(), "Heater PCB is not connected, or i2c is not working."


def test_positive_correlation_between_temperature_and_heating(
    managed_state, logger: CustomLogger, unit: str, experiment: str
) -> None:
    assert is_heating_pcb_present(), "Heater PCB is not connected, or i2c is not working."

    measured_pcb_temps = []
    dcs = list(range(0, 30, 3))

    with OnlyRecordTemperature(unit=unit, experiment=experiment) as tc:
        logger.debug("Varying heating.")
        for dc in dcs:
            tc._update_heater(dc)
            sleep(1.5)
            measured_pcb_temps.append(tc.read_external_temperature())

        tc._update_heater(0)
        measured_correlation = round(correlation(dcs, measured_pcb_temps), 2)
        logger.debug(f"Correlation between temp sensor and heating: {measured_correlation}")
        assert (
            measured_correlation > 0.9
        ), f"Temp and DC% correlation was not high enough {dcs=}, {measured_pcb_temps=}"


def test_aux_power_is_not_too_high(client: Client, logger: CustomLogger, unit: str, experiment: str) -> None:
    assert is_HAT_present(), "HAT was not detected."
    assert voltage_in_aux() <= 18.0, f"Voltage measured {voltage_in_aux()} > 18.0V"


def test_positive_correlation_between_rpm_and_stirring(
    client, logger: CustomLogger, unit: str, experiment: str
) -> None:
    assert is_HAT_present(), "HAT was not detected."
    assert is_heating_pcb_present(), "Heating PCB was not detected."
    assert voltage_in_aux() <= 18.0, f"Voltage measured {voltage_in_aux()} > 18.0V"

    initial_dc = config.getfloat("stirring.config", "initial_duty_cycle")

    dcs = []
    measured_rpms = []
    n_samples = 8
    start = min(initial_dc * 1.2, 100)
    end = max(initial_dc * 0.8, 5)

    with stirring.RpmFromFrequency() as rpm_calc:
        rpm_calc.setup()
        with stirring.Stirrer(target_rpm=None, unit=unit, experiment=experiment, rpm_calculator=None) as st:
            st.set_duty_cycle(initial_dc)
            sleep(0.75)

            for i in range(n_samples):
                p = i / n_samples
                dc = start * (1 - p) + p * end

                st.set_duty_cycle(dc)
                sleep(0.75)
                measured_rpms.append(rpm_calc.estimate(3.0))
                dcs.append(dc)

        measured_correlation = round(correlation(dcs, measured_rpms), 2)
        logger.debug(f"Correlation between stirring RPM and duty cycle: {measured_correlation}")
        logger.debug(f"{dcs=}, {measured_rpms=}")
        assert measured_correlation > 0.9, f"RPM correlation not high enough: {(dcs, measured_rpms)}"


class BatchTestRunner:
    def __init__(self, tests_to_run: list[Callable], *test_func_args, experiment: str) -> None:
        self.count_tested = 0
        self.count_passed = 0
        self.tests_to_run = tests_to_run
        self.experiment = experiment
        self._thread = Thread(target=self._run, args=test_func_args)  # don't make me daemon: 295

    def start(self):
        self._thread.start()
        return self

    def collect(self) -> SummableDict:
        self._thread.join()
        return SummableDict({"count_tested": self.count_tested, "count_passed": self.count_passed})

    def _run(self, managed_state, logger: CustomLogger, unit: str, testing_experiment: str) -> None:
        for test in self.tests_to_run:
            test_name = test.__name__

            logger.debug(f"Starting test {test_name}...")
            try:
                test(managed_state, logger, unit, testing_experiment)
                res = True
            except Exception as e:
                res = False
                logger.debug(e, exc_info=True)
                logger.warning(f"{test_name.replace('_', ' ')}: {e}")

            logger.debug(f"{test_name}: {'✅' if res else '❌'}")

            self.count_tested += 1
            self.count_passed += int(res)

            managed_state.publish_setting(test_name, int(res))

            with local_persistent_storage("self_test_results") as c:
                c[(self.experiment, test_name)] = int(res)


def get_failed_test_names(experiment: str) -> Iterator[str]:
    with local_persistent_storage("self_test_results") as c:
        for name in get_all_test_names():
            if c.get((experiment, name)) == 0:
                yield name


def get_all_test_names() -> Iterator[str]:
    return (name for name in vars(sys.modules[__name__]).keys() if name.startswith("test_"))


@click.command(name="self_test")
@click.option("-k", help="see pytest's -k argument", type=str)
@click.option("--retry-failed", is_flag=True, help="retry only previous failed tests", type=str)
def click_self_test(k: Optional[str], retry_failed: bool) -> int:
    """
    Test the input/output in the Pioreactor
    """
    unit = get_unit_name()
    testing_experiment = get_testing_experiment_name()
    experiment = get_assigned_experiment_name(unit)
    logger = create_logger("self_test", unit=unit, experiment=experiment)

    A_TESTS = (
        test_pioreactor_HAT_present,
        test_detect_heating_pcb,
        test_positive_correlation_between_temperature_and_heating,
        test_aux_power_is_not_too_high,
    )
    B_TESTS = (
        test_positive_correlation_between_rpm_and_stirring,
    )

    with managed_lifecycle(unit, experiment, "self_test") as managed_state, temporary_config_change(
        config, "stirring.config", "enable_dodging_od", "false"
    ):
        if any(
            is_pio_job_running(
                ["od_reading", "temperature_automation", "stirring", "dosing_automation", "led_automation"]
            )
        ):
            logger.error(
                "Make sure Optical Density, any automations, and Stirring are off before running a self test. Exiting."
            )
            raise click.Abort()

        # flicker to assist the user to confirm they are testing the right pioreactor.
        post_into_leader(f"/api/workers/{unit}/blink")

        # automagically finds the test_ functions.
        tests_to_run: Iterator[str]
        if retry_failed:
            tests_to_run = get_failed_test_names(experiment)
        else:
            tests_to_run = get_all_test_names()

        if k:
            tests_to_run = (name for name in tests_to_run if k in name)

        functions_to_test = {vars(sys.modules[__name__])[name] for name in tuple(tests_to_run)}

        logger.info(f"Starting self-test. Running {len(functions_to_test)} tests.")

        # and clear the mqtt cache first
        for f in functions_to_test:
            managed_state.publish_setting(f.__name__, None)

        # some tests can be run in parallel.
        test_args = (managed_state, logger, unit, testing_experiment)
        RunnerA = BatchTestRunner(
            [f for f in A_TESTS if f in functions_to_test], *test_args, experiment=experiment
        ).start()
        RunnerB = BatchTestRunner(
            [f for f in B_TESTS if f in functions_to_test], *test_args, experiment=experiment
        ).start()

        results = RunnerA.collect() + RunnerB.collect()
        count_tested, count_passed = results["count_tested"], results["count_passed"]
        count_failures = int(count_tested - count_passed)

        managed_state.publish_setting("all_tests_passed", int(count_failures == 0))

        if count_tested == 0:
            logger.info("No tests ran 🟡")
        elif count_failures == 0:
            logger.info("All tests passed ✅")
        elif count_failures > 0:
            logger.info(f"{count_failures} failed test{'s' if count_failures > 1 else ''} ❌")

        # clear my retained messages
        prune_retained_messages(f"pioreactor/{unit}/{testing_experiment}/#")

        return int(count_failures > 0)
