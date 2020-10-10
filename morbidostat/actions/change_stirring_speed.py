# -*- coding: utf-8 -*-
import click

from morbidostat.utils import get_unit_from_hostname, get_latest_experiment_name
from morbidostat.utils.pubsub import publish


def change_stirring_speed(duty_cycle, verbose=0):
    assert 0 <= duty_cycle <= 100

    unit = get_unit_from_hostname()
    experiment = get_latest_experiment_name()

    publish(f"morbidostat/{unit}/{experiment}/stirring/duty_cycle", duty_cycle, verbose=verbose)
    return


@click.command()
@click.option("--duty_cycle", type=int)
@click.option(
    "--verbose", default=0, help="print to std. out (may be redirected to morbidostat.log). Increasing values log more."
)
def click_change_stirring_speed(ml, duration, duty_cycle, verbose):
    return change_stirring_speed(duty_cycle, verbose)


if __name__ == "__main__":
    click_change_stirring_speed()
