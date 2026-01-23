# -*- coding: utf-8 -*-
"""
3-point pH calibration using pH 4.0, 7.0, and 10.01 buffer solutions.
Maps voltage (mV) from ADC to pH values using polynomial fit.
"""
from __future__ import annotations

from time import sleep

import click
from click import clear
from click import confirm
from click import echo
from click import prompt
from click import style
from msgspec.json import encode
from msgspec.json import format

from pioreactor import structs
from pioreactor.calibrations import utils
from pioreactor.hardware import PH_ADDR
from pioreactor.utils import local_persistent_storage
from pioreactor.utils import managed_lifecycle
from pioreactor.utils.adcs import ADC101C02x
from pioreactor.utils.timing import current_utc_datestamp
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.whoami import get_testing_experiment_name
from pioreactor.whoami import get_unit_name


# ADC conversion constants
ADC_REFERENCE_VOLTAGE = 3300.0  # 3.3V reference in mV
ADC_MAX_VALUE = 1023.0  # 10-bit ADC

# Standard buffer pH values
BUFFER_PH_VALUES = [4.0, 7.0, 10.01]


def green(string: str) -> str:
    return style(string, fg="green")


def red(string: str) -> str:
    return style(string, fg="red")


def bold(string: str) -> str:
    return style(string, bold=True)


def introduction() -> None:
    import logging

    logging.disable(logging.WARNING)

    clear()
    echo(
        """This routine will calibrate the pH sensor using 3-point calibration. You'll need:
    1. pH 4.0 buffer solution
    2. pH 7.0 buffer solution
    3. pH 10.01 buffer solution
    4. Distilled water for rinsing the probe between solutions
    5. A clean container for each buffer solution

The calibration will measure the voltage at each pH value and fit a polynomial curve.
"""
    )


def get_name_from_user() -> str:
    with local_persistent_storage("ph_calibrations") as cache:
        while True:
            name = prompt(
                green(
                    f"Optional: Provide a name for this calibration. [enter] to use default name `ph-cal-{current_utc_datestamp()}`"
                ),
                type=str,
                default=f"ph-cal-{current_utc_datestamp()}",
                show_default=False,
            ).strip()

            if name == "":
                echo("Name cannot be empty")
                continue
            elif name in cache:
                if confirm(green("Name already exists. Do you wish to overwrite?")):
                    return name
            elif name == "current":
                echo("Name cannot be `current`.")
                continue
            else:
                return name


def read_voltage_mv(driver: ADC101C02x, n_samples: int = 10) -> float:
    """Read average voltage in millivolts from the ADC."""
    readings = []
    for _ in range(n_samples):
        raw = driver.read_raw()
        voltage_mv = raw * (ADC_REFERENCE_VOLTAGE / ADC_MAX_VALUE)
        readings.append(voltage_mv)
        sleep(0.1)
    return sum(readings) / len(readings)


def calibrate_single_point(driver: ADC101C02x, ph_value: float, point_num: int) -> float:
    """Guide user through calibrating a single pH point and return the voltage."""
    clear()
    echo(bold(f"\n=== Calibration Point {point_num}/3: pH {ph_value} ===\n"))
    echo(f"1. Rinse the pH probe with distilled water")
    echo(f"2. Gently dry the probe tip (don't rub)")
    echo(f"3. Place the probe in pH {ph_value} buffer solution")
    echo(f"4. Wait for the reading to stabilize (about 30 seconds)")

    while not confirm(green("\nIs the probe in the buffer solution and stable?"), default=True):
        pass

    echo("\nReading voltage...")
    for i in range(3):
        echo(".", nl=False)
        sleep(1)

    voltage_mv = read_voltage_mv(driver)

    echo(f"\nVoltage at pH {ph_value}: {voltage_mv:.1f} mV")

    if confirm(green("Accept this reading?"), default=True):
        return voltage_mv
    else:
        echo("Let's try again...")
        return calibrate_single_point(driver, ph_value, point_num)


def run_ph_calibration() -> structs.PHCalibration:
    unit = get_unit_name()
    experiment = get_testing_experiment_name()

    with managed_lifecycle(unit, experiment, "ph_calibration"):
        introduction()
        name = get_name_from_user()

        echo("\nInitializing pH sensor...")
        try:
            driver = ADC101C02x(PH_ADDR)
            if not driver.test_connection():
                raise OSError("No response from pH ADC")
        except OSError as e:
            echo(red(f"Error: Could not connect to pH sensor at address 0x{PH_ADDR:02X}"))
            echo(red(f"Details: {e}"))
            raise click.Abort()

        echo(green("pH sensor connected successfully!\n"))

        voltages = []
        ph_values = []

        # Calibrate each buffer point
        for i, ph in enumerate(BUFFER_PH_VALUES, 1):
            voltage = calibrate_single_point(driver, ph, i)
            voltages.append(voltage)
            ph_values.append(ph)

            clear()
            utils.plot_data(
                voltages,
                ph_values,
                title="pH Calibration (in progress)",
                x_label="Voltage (mV)",
                y_label="pH",
            )

        echo("\n" + green(bold("All calibration points collected!")))
        echo(f"\nData summary:")
        for v, p in zip(voltages, ph_values):
            echo(f"  pH {p:5.2f} -> {v:7.1f} mV")

        # Create calibration struct with empty curve_data_ (will be filled by crunch_data_and_confirm_with_user)
        cal = structs.PHCalibration(
            created_at=current_utc_datetime(),
            calibrated_on_pioreactor_unit=unit,
            calibration_name=name,
            curve_data_=[],
            curve_type="poly",
            recorded_data={"x": voltages, "y": ph_values},
        )

        # Let user choose polynomial degree and confirm
        cal = utils.crunch_data_and_confirm_with_user(cal)

        echo()
        echo(style(f"Calibration curve for `{name}`", underline=True, bold=True))
        echo(utils.curve_to_functional_form(cal.curve_type, cal.curve_data_))
        echo()
        echo(style(f"Data for `{name}`", underline=True, bold=True))
        print(format(encode(cal)).decode())
        echo()
        echo(f"Finished calibration of `{name}`")

        return cal
