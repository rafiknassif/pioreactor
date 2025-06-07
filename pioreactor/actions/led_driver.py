# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import contextmanager
from contextlib import nullcontext
from os import getpid
from typing import Any
from typing import Iterator

import click
from msgspec.json import encode

from pioreactor import structs
from pioreactor.hardware import LR_DAC_ADDR
from pioreactor.exc import HardwareNotFoundError
from pioreactor.logging import create_logger
from pioreactor.pubsub import Client
from pioreactor.pubsub import create_client
from pioreactor.pubsub import QOS
from pioreactor.types import LedDriverChannel, LedIntensityValue, LedDriverCurrent
from pioreactor.utils import local_intermittent_storage
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.whoami import get_assigned_experiment_name
from pioreactor.whoami import get_unit_name
from pioreactor.whoami import is_active
from pioreactor.whoami import is_testing_env

ALL_DRIVER_CHANNELS: list[LedDriverChannel] = ["DRV_A", "DRV_B"]
LEDsToIntensityMapping = dict[LedDriverChannel, LedIntensityValue]
LEDsToCurrentMapping = dict[LedDriverChannel, LedDriverCurrent]
dac = None

def initialize_dac():
    from pioreactor.utils.dacs import MCP47CxBxx
    global dac
    dac = MCP47CxBxx(LR_DAC_ADDR, 8)  # Hard coded to 8 bit resolution

def _update_current_state(
    state: LEDsToIntensityMapping,
) -> tuple[LEDsToIntensityMapping, LEDsToIntensityMapping]:

    with local_intermittent_storage("led_driver") as led_driver_cache:
        # rehydrate old cache
        old_state: LEDsToIntensityMapping = {
            channel: led_driver_cache.get(str(channel), 0.0) for channel in ALL_DRIVER_CHANNELS
        }

        # update cache
        for channel, intensity in state.items():
            led_driver_cache[channel] = intensity

        new_state: LEDsToIntensityMapping = {
            channel: led_driver_cache.get(str(channel), 0.0) for channel in ALL_DRIVER_CHANNELS
        }

        return new_state, old_state


def led_driver_intensity(
    desired_state: LEDsToIntensityMapping | LEDsToCurrentMapping,
    unit: str | None = None,
    experiment: str | None = None,
    verbose: bool = True,
    source_of_event: str | None = None,
    pubsub_client: Client | None = None,
) -> bool:
    """
    Change the intensity of the LED Driver channels A and B to an value between 0 and 100.

    Parameters
    ------------
    desired_state: dict
        what you want the desired LED state to be. Leave keys out if you do wish to update that channel.
    unit: str
    experiment: str
    verbose: bool
        if True, log the change, and send event to led_event table & mqtt. This is FALSE
        in od_reading job, so as to not create spam.
    source_of_event: str
        A human readable string of who is calling this function
    pubsub_client:
        provide a MQTT paho client to use for publishing.


    Returns
    --------
    bool representing if the all LED channels intensity were successfully changed


    Notes
    -------
    State is updated in MQTT and the temporary cache `led_driver`:

        pioreactor/<unit>/<experiment>/leds/intensity    {'A': intensityA, 'B': intensityB, ...}

    """
    unit = unit or get_unit_name()
    experiment = experiment or get_assigned_experiment_name(unit)
    logger = create_logger("led_driver_intensity", experiment=experiment, unit=unit, pub_client=pubsub_client)

    if not is_active(unit):
        return False

    updated_successfully = True

    if pubsub_client is None:
        mqtt_publishing = create_client(client_id=f"led_driver_intensity-{unit}-{experiment}")
        mqtt_publish = mqtt_publishing.publish
    else:
        mqtt_publishing = nullcontext()
        mqtt_publish = pubsub_client.publish

    with mqtt_publishing:

        for channel, setpoint in desired_state.items():
            try:
                assert (channel in ALL_DRIVER_CHANNELS), f"Saw incorrect channel {channel}, not in {ALL_DRIVER_CHANNELS}"
                logger.info(f"Type Setpoint: {type(setpoint)}")
                if isinstance(setpoint, LedIntensityValue):
                    logger.info(f"CELLULITIS")
                    assert (0.0 <= setpoint.value <= 100.0), f"Channel {channel} intensity should be between 0 and 100, inclusive"
                    logger.info(f"CELLULITIS2")
                    dac.set_intensity_to(channel, setpoint.value)
                    logger.info(f"LED Driver Intensity Set to {setpoint.value}")
                elif isinstance(setpoint, LedDriverCurrent):
                    logger.info(f"ANTIBIOTICS")
                    assert (0.0 <= setpoint.value <= 750.0), f"Channel {channel} current should be between 0 and 750, inclusive"
                    logger.info(f"ANTIBIOTICS2")
                    dac.set_current_to(channel, setpoint.value)
                    logger.info(f"LED Driver Current Set to {setpoint}")
            except (ValueError, HardwareNotFoundError) as e:
                logger.debug(e, exc_info=True)
                logger.error(
                    "Unable to find i2c for LED driver. Confirm i2c connected. Did the LED driver explode unexpectedly?"
                )
                updated_successfully = False
                return updated_successfully
            except AssertionError as e:
                logger.error(e)
                updated_successfully = False
                return updated_successfully

        new_state, old_state = _update_current_state(desired_state)

        mqtt_publish(
            f"pioreactor/{unit}/{experiment}/leds/driverIntensity",
            encode(new_state),
            qos=QOS.AT_MOST_ONCE,
            retain=True,
        )

        if verbose:
            timestamp_of_change = current_utc_datetime()

            for channel, intensity in desired_state.items():
                if old_state[channel] != new_state[channel]:  # only log on change
                    event = structs.LEDChangeEvent(
                        channel=channel,
                        intensity=intensity.value,
                        source_of_event=source_of_event,
                        timestamp=timestamp_of_change,
                    )

                    mqtt_publish(
                        f"pioreactor/{unit}/{experiment}/led_driver_change_events",
                        encode(event),
                        qos=QOS.AT_MOST_ONCE,
                        retain=False,
                    )

                    logger.info(
                        f"Updated LED Driver {channel} from {old_state[channel]:0.3g}% to {new_state[channel]:0.3g}%."
                    )
        return updated_successfully


@click.command(name="led_driver_intensity")
@click.option(
    "--DRV_A",
    help="value between 0 and 100",
    type=click.FloatRange(0, 100)
)
@click.option(
    "--DRV_B",
    help="value between 0 and 100",
    type=click.FloatRange(0, 100)
)
@click.option(
    "--source-of-event",
    default="CLI",
    type=str,
    help="who is calling this function (for logging purposes)",
)
@click.option("--no-log", is_flag=True, help="skip logging")
def click_led_intensity(
    a: float | None = None,
    b: float | None = None,
    source_of_event: str | None = None,
    no_log: bool = False,
) -> bool:
    """
    Modify the intensity of LED Driver channel(s)
    """
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    state: LEDsToIntensityMapping = {}
    if a is not None:
        state["DRV_A"] = LedIntensityValue(a)
    if b is not None:
        state["DRV_B"] = LedIntensityValue(b)

    status = led_driver_intensity(
        state,
        source_of_event=source_of_event,
        unit=unit,
        experiment=experiment,
        verbose=not no_log,
    )
    return status