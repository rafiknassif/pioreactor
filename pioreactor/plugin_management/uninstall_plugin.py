# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
from shlex import quote

import click

from pioreactor.logging import create_logger
from pioreactor.whoami import UNIVERSAL_EXPERIMENT


def uninstall_plugin(name_of_plugin: str) -> None:

    logger = create_logger("uninstall_plugin", experiment=UNIVERSAL_EXPERIMENT)
    logger.debug(f"Uninstalling plugin {name_of_plugin}.")

    result = subprocess.run(
        [
            "bash",
            "/usr/local/bin/uninstall_pioreactor_plugin.sh",
            quote(name_of_plugin),
        ],
        capture_output=True,
    )
    if "as it is not installed" in result.stdout.decode("utf-8"):
        logger.warning(result.stdout)
    elif result.returncode == 0:
        logger.notice(f"Successfully uninstalled plugin {name_of_plugin}.")  # type: ignore
    else:
        logger.error(f"Failed to uninstall plugin {name_of_plugin}. See logs.")
        logger.debug(result.stdout)
        logger.debug(result.stderr)


@click.command(name="uninstall-plugin", short_help="uninstall an existing plugin")
@click.argument("name-of-plugin")
def click_uninstall_plugin(name_of_plugin: str) -> None:
    uninstall_plugin(name_of_plugin)
