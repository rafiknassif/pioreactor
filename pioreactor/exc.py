# -*- coding: utf-8 -*-
from __future__ import annotations


class PWMError(OSError):
    """
    Something went wrong with PWM outputs
    """


class HardwareNotFoundError(OSError):
    """
    A hardware device is missing.
    """


class JobRequiredError(Exception):
    """
    A job should be running, but isn't found.
    """


class CalibrationError(Exception):
    """
    An issue with calibration (pump, stirring, OD, etc.)
    """


class NotActiveWorkerError(Exception):
    """
    if a worker is not active, things shouldn't happen.
    """


class NotAssignedAnExperimentError(Exception):
    """
    if a worker is assigned an experiment, and something is trying to run
    """


class NoWorkerFoundError(Exception):
    """
    if a worker is assigned an experiment, and something is trying to run
    """
