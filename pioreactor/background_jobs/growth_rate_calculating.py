# -*- coding: utf-8 -*-
"""
Reads growth rate and filtered density data from the ODSensorV2 sensor's
internal Unscented Kalman Filter, triggered by incoming OD readings from MQTT.

Phase A/A2 ODSensor firmware port: filter operates in density basis. The
od_filtered topic now carries UKF-filtered density (g/L), sourced from the
same register (0x16) as the density topic — they hold identical values, but
are kept as separate topics for backwards-compat with downstream consumers.
Reg 0x15 (legacy filtered_reflectance) is deleted in this firmware version.

Topics published:
    pioreactor/<unit>/<experiment>/growth_rate_calculating/growth_rate
    pioreactor/<unit>/<experiment>/growth_rate_calculating/od_filtered
    pioreactor/<unit>/<experiment>/growth_rate_calculating/density
    pioreactor/<unit>/<experiment>/growth_rate_calculating/absolute_growth_rate
    pioreactor/<unit>/<experiment>/growth_rate_calculating/specific_dilution_rate
"""
from __future__ import annotations

import math

import click
from msgspec import DecodeError
from msgspec.json import decode

from pioreactor import structs
from pioreactor import types as pt
from pioreactor import whoami
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.config import config
from pioreactor.pubsub import QOS
from pioreactor.utils.odsensorv2 import ODSensorV2


class GrowthRateCalculator(BackgroundJob):
    """
    Reads Kalman-filtered outputs from the ODSensorV2 sensor whenever
    a new OD reading is published to MQTT.
    """

    job_name = "growth_rate_calculating"
    published_settings = {
        "growth_rate": {
            "datatype": "GrowthRate",
            "settable": False,
            "unit": "h⁻¹",
            "persist": True,
        },
        "od_filtered": {"datatype": "ODFiltered", "settable": False},
        "kalman_filter_outputs": {
            "datatype": "KalmanFilterOutput",
            "settable": False,
            "persist": False,
        },
        "absolute_growth_rate": {
            "datatype": "AbsoluteGrowthRate",
            "settable": False,
            "unit": "g/Lh",
            "persist": True,
        },
        "density": {"datatype": "Density", "settable": False},
        "specific_dilution_rate": {
            "datatype": "SpecificDilutionRate",
            "settable": False,
            "unit": "1/h",
            "persist": True,
        },
    }

    def __init__(
        self,
        unit: str,
        experiment: str,
        source_obs_from_mqtt: bool = True,
    ):
        super(GrowthRateCalculator, self).__init__(unit=unit, experiment=experiment)
        self.source_obs_from_mqtt = source_obs_from_mqtt
        self.latest_sdr = 0.0
        self.sensor = ODSensorV2()

        if not self.sensor.test_connection():
            self.logger.error("ODSensorV2 not found at I2C address 0x69.")
            self.clean_up()
            raise Exception("ODSensorV2 not found.")

    def on_ready(self) -> None:
        if self.source_obs_from_mqtt:
            self.start_passive_listeners()

    def respond_to_od_readings_from_mqtt(self, message: pt.MQTTMessage) -> None:
        """Triggered when od_reading publishes new OD data. We read the sensor's KF outputs."""
        if self.state != self.READY:
            return

        try:
            od_readings = decode(message.payload, type=structs.ODReadings)
            self._update_from_sensor(od_readings)
        except DecodeError:
            self.logger.debug(f"Decode error in `{message.payload.decode()}` to structs.ODReadings")

    def _update_from_sensor(self, od_readings: structs.ODReadings) -> None:
        """Read KF outputs from sensor registers and publish them.

        NaN values (e.g. calibration-dependent registers when no LUT is loaded,
        or UKF registers when UKF is disabled) are skipped — not published.
        """
        timestamp = od_readings.timestamp

        # Read growth rate (UKF μ, 1/h on density post-A2)
        try:
            gr = self.sensor.read_growth_rate()
        except Exception as e:
            self.logger.debug(f"Error reading growth_rate: {e}")
            gr = float("nan")

        # Read UKF-filtered density (reg 0x16). Feeds both od_filtered and
        # density topics — Phase A/A2 port: legacy filtered_reflectance (reg
        # 0x15) is deleted, so od_filtered now carries the same density-basis
        # value as the density topic. Same on-wire bytes, two topic names
        # preserved for downstream compat.
        try:
            dens = self.sensor.read_filtered_calibrated_density()
        except Exception as e:
            self.logger.debug(f"Error reading filtered_calibrated_density: {e}")
            dens = float("nan")

        # Read filtered calibrated growth rate → absolute_growth_rate
        try:
            abs_gr = self.sensor.read_filtered_calibrated_growth_rate()
        except Exception as e:
            self.logger.debug(f"Error reading filtered_calibrated_growth_rate: {e}")
            abs_gr = float("nan")

        if not math.isnan(gr):
            self.growth_rate = structs.GrowthRate(
                growth_rate=gr,
                timestamp=timestamp,
            )

        if not math.isnan(dens):
            self.od_filtered = structs.ODFiltered(
                od_filtered=dens,
                timestamp=timestamp,
            )
            self.density = structs.Density(
                density=dens,
                timestamp=timestamp,
            )

        if not math.isnan(abs_gr):
            self.absolute_growth_rate = structs.AbsoluteGrowthRate(
                absolute_growth_rate=abs_gr,
                timestamp=timestamp,
            )

        self.specific_dilution_rate = structs.SpecificDilutionRate(
            specific_dilution_rate=self.latest_sdr,
            timestamp=timestamp,
        )

        # KalmanFilterOutput — publish with available values, use 0.0 for NaN.
        # state[0] = filtered density (g/L), state[1] = μ (1/h on density),
        # state[2] = unused (reserved for legacy 3-state UKF compatibility).
        self.kalman_filter_outputs = structs.KalmanFilterOutput(
            state=[0.0 if math.isnan(dens) else dens,
                   0.0 if math.isnan(gr) else gr,
                   0.0],
            covariance_matrix=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            timestamp=timestamp,
        )

    def _update_sdr(self, message: pt.MQTTMessage) -> None:
        """Callback to update the latest specific dilution rate from MQTT."""
        if message.payload:
            try:
                self.latest_sdr = float(message.payload.decode())
                self.logger.debug(f"Updated SDR from MQTT: {self.latest_sdr} 1/h")
            except ValueError:
                self.logger.warning(f"Invalid SDR value received: {message.payload}")

    def start_passive_listeners(self) -> None:
        self.subscribe_and_callback(
            self.respond_to_od_readings_from_mqtt,
            f"pioreactor/{self.unit}/{self.experiment}/od_reading/ods",
            qos=QOS.EXACTLY_ONCE,
            allow_retained=False,
        )
        self.subscribe_and_callback(
            self._update_sdr,
            f"pioreactor/{self.unit}/{self.experiment}/dosing_automation/specific_dilution_rate",
            qos=QOS.EXACTLY_ONCE,
            allow_retained=True,
        )


@click.group(invoke_without_command=True, name="growth_rate_calculating")
@click.pass_context
def click_growth_rate_calculating(ctx):
    """
    Start calculating growth rate from ODSensorV2 sensor data.
    """
    if ctx.invoked_subcommand is None:
        unit = whoami.get_unit_name()
        experiment = whoami.get_assigned_experiment_name(unit)

        calculator = GrowthRateCalculator(
            unit=unit,
            experiment=experiment,
        )
        calculator.block_until_disconnected()
