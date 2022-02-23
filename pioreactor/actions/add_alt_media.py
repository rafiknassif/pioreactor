# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional

import click

from pioreactor import structs
from pioreactor.actions.pump import pump
from pioreactor.whoami import get_latest_experiment_name
from pioreactor.whoami import get_unit_name


def add_alt_media(
    unit: str,
    experiment: str,
    ml: Optional[float] = None,
    duration: Optional[float] = None,
    source_of_event: Optional[str] = None,
    calibration: Optional[structs.PumpCalibration] = None,
    continuously: bool = False,
) -> float:
    """
    Parameters
    ------------
    unit: str
    experiment: str
    ml: float
        Amount of volume to pass, in mL
    duration: float
        Duration to run pump, in s
    calibration:
        specify a calibration for the dosing. Should be a dict
        with fields "duration_", "hz", "dc", and "bias_"
    continuously: bool
        Run pump continuously.
    source_of_event: str
        A human readable description of the source


    Returns
    -----------
    Amount of volume passed (approximate in some cases)

    """
    pump_name = "alt_media"
    return pump(
        unit,
        experiment,
        pump_name,
        ml,
        duration,
        source_of_event,
        calibration,
        continuously,
    )


@click.command(name="add_alt_media")
@click.option("--ml", type=float)
@click.option("--duration", type=float)
@click.option("--continuously", is_flag=True, help="continuously run until stopped.")
@click.option(
    "--source-of-event",
    default="CLI",
    type=str,
    help="who is calling this function - data goes into database and MQTT",
)
def click_add_alt_media(
    ml: Optional[float],
    duration: Optional[float],
    continuously: bool,
    source_of_event: Optional[str],
):
    """
    Remove waste/media from unit
    """
    unit = get_unit_name()
    experiment = get_latest_experiment_name()

    return add_alt_media(
        ml=ml,
        duration=duration,
        continuously=continuously,
        source_of_event=source_of_event,
        unit=unit,
        experiment=experiment,
    )
