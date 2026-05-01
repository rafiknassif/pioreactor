# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import time
from typing import Optional

from msgspec.json import decode

from pioreactor import structs
from pioreactor import types as pt
from pioreactor.automations import events
from pioreactor.automations.dosing.base import DosingAutomationJob
from pioreactor.calibrations import load_active_calibration
from pioreactor.config import config
from pioreactor.exc import CalibrationError
from pioreactor.pubsub import publish
from pioreactor.utils import local_persistent_storage
from pioreactor.utils.streaming_calculations import ExponentialMovingAverage
from pioreactor.utils.timing import current_utc_datetime


class SDRODStop(DosingAutomationJob):
    """
    Dilutes at a specific dilution rate (SDR) until the calibrated density
    drops to a target fraction of its starting value, then stops dosing.

    Parameters
    ----------
    relative_density : float
        Fraction of starting density at which to stop dosing (e.g. 0.5 means
        stop when density reaches 50% of starting density).
    volume : float
        Volume to exchange per cycle (mL). Passed to base class.
    sdr : float
        Specific dilution rate (1/h). Passed to base class.
    """

    automation_name = "sdr_od_stop"
    published_settings = {
        "volume": {"datatype": "float", "settable": True, "unit": "mL"},
        "sdr": {"datatype": "float", "settable": True, "unit": "1/h"},
        "relative_density": {"datatype": "float", "settable": True, "unit": ""},
        "target_density": {"datatype": "float", "settable": False, "unit": "g/L"},
        "starting_density": {"datatype": "float", "settable": False, "unit": "g/L"},
    }

    def __init__(
        self,
        relative_density: float | str,
        **kwargs,
    ) -> None:
        # Default volume to max_subdose if not provided, so the user only needs to specify SDR
        if kwargs.get("volume") is None and kwargs.get("sdr") is not None:
            kwargs["volume"] = config.getfloat("bioreactor", "max_subdose", fallback=1.0)

        super().__init__(**kwargs)

        with local_persistent_storage("active_calibrations") as cache:
            if "media_pump" not in cache:
                raise CalibrationError("Media pump calibration must be performed first.")
            if "waste_pump" not in cache:
                raise CalibrationError("Waste pump calibration must be performed first.")

        self.relative_density = float(relative_density)
        self.starting_density: Optional[float] = None
        self.target_density: Optional[float] = None
        self._latest_density: Optional[float] = None
        self._latest_density_at = current_utc_datetime()
        self._latest_raw_density: Optional[float] = None
        self._latest_raw_density_at = current_utc_datetime()
        self._smoothed_raw_density: Optional[float] = None
        self._smoothed_raw_density_at = current_utc_datetime()
        self._stop_signal_source = "filtered_density_fallback"
        self._signal_channel = config.get("sdr_od_stop.config", "signal_channel", fallback="1")
        self._raw_density_ema = ExponentialMovingAverage(
            config.getfloat("sdr_od_stop.config", "raw_density_ema_alpha", fallback=0.55)
        )
        self._raw_outlier_abs_delta = config.getfloat(
            "sdr_od_stop.config", "raw_density_outlier_abs_delta", fallback=0.20
        )
        self._raw_outlier_rel_delta = config.getfloat(
            "sdr_od_stop.config", "raw_density_outlier_rel_delta", fallback=0.08
        )
        self._raw_signal_max_age_seconds = config.getfloat(
            "sdr_od_stop.config", "raw_density_signal_max_age_seconds", fallback=5 * 60
        )
        self._dosing_complete = False
        self._primed = False

        self._validate_sdr_achievable()

    def _validate_sdr_achievable(self) -> None:
        """Check that the requested SDR is physically possible given pump speeds."""
        if self.sdr is None or self.volume is None:
            return

        media_cal = load_active_calibration("media_pump")
        waste_cal = load_active_calibration("waste_pump")
        if media_cal is None or waste_cal is None:
            self.logger.warning("Could not load pump calibrations to validate SDR feasibility.")
            return

        max_subdose = config.getfloat("bioreactor", "max_subdose", fallback=1.0)
        n_subdoses = math.ceil(self.volume / max_subdose)
        subdose_vol = self.volume / n_subdoses

        pause = config.getfloat("bioreactor", "pause_between_subdoses_seconds", fallback=5.0)
        waste_multiplier = config.getfloat("bioreactor", "waste_removal_multiplier", fallback=2.0)

        media_time = media_cal.ml_to_duration(subdose_vol)
        waste_time = waste_cal.ml_to_duration(subdose_vol * waste_multiplier)
        min_cycle_time = n_subdoses * (media_time + pause + waste_time + 0.05)

        initial_volume = config.getfloat("bioreactor", "initial_volume_ml", fallback=14)
        requested_duration = 1 / ((self.sdr / 3600) * (initial_volume / self.volume))

        safety_factor = 1.5
        if requested_duration < min_cycle_time * safety_factor:
            max_sdr = self.volume / (initial_volume * min_cycle_time * safety_factor) * 3600
            raise ValueError(
                f"Requested SDR {self.sdr} 1/h is not physically achievable. "
                f"A single dilution cycle takes ~{min_cycle_time:.1f}s, "
                f"but the requested SDR requires a cycle every {requested_duration:.1f}s. "
                f"With a {safety_factor}x safety factor, max achievable SDR is ~{max_sdr:.2f} 1/h. "
                f"Reduce SDR, increase volume, or adjust max_subdose/pause settings."
            )

    @property
    def latest_density(self) -> float:
        if self._latest_density is None:
            self.logger.info("Waiting for calibrated density data to arrive...")
            elapsed = 0.0
            while self._latest_density is None and elapsed < 30.0:
                time.sleep(1.0)
                elapsed += 1.0

            if self._latest_density is None:
                raise RuntimeError(
                    "No calibrated density data received within 30 seconds. "
                    "Check that growth_rate_calculating is running and that the OD sensor "
                    "has a density calibration/LUT loaded. The sensor returns NaN for "
                    "calibrated density when no LUT is loaded, and those values are not published."
                )

        if (current_utc_datetime() - self._latest_density_at).seconds > 5 * 60:
            raise RuntimeError(
                f"Calibrated density data is stale (last update: {self._latest_density_at}). "
                f"Is growth_rate_calculating running?"
            )

        return self._latest_density

    def _set_density(self, message: pt.MQTTMessage) -> None:
        if not message.payload:
            return
        payload = decode(message.payload, type=structs.Density)
        self._latest_density = payload.density
        self._latest_density_at = payload.timestamp

    def _set_raw_density(self, message: pt.MQTTMessage) -> None:
        if not message.payload:
            return

        payload = decode(message.payload, type=structs.ODReadings)
        if not payload.ods:
            return

        reading = payload.ods.get(self._signal_channel)
        if reading is None:
            reading = next(iter(payload.ods.values()))

        raw_density = reading.od
        if not math.isfinite(raw_density):
            return

        if self._latest_raw_density is not None:
            baseline = max(abs(self._latest_raw_density), 1e-6)
            outlier_threshold = max(
                self._raw_outlier_abs_delta, self._raw_outlier_rel_delta * baseline
            )
            if abs(raw_density - self._latest_raw_density) > outlier_threshold:
                self.logger.debug(
                    "Rejecting raw density outlier: raw=%.4f, previous=%.4f, limit=%.4f",
                    raw_density,
                    self._latest_raw_density,
                    outlier_threshold,
                )
                return

        self._latest_raw_density = raw_density
        self._latest_raw_density_at = payload.timestamp
        self._smoothed_raw_density = self._raw_density_ema.update(raw_density)
        self._smoothed_raw_density_at = payload.timestamp

    def _get_stop_density(self) -> tuple[float, str]:
        if (
            self._smoothed_raw_density is not None
            and (current_utc_datetime() - self._smoothed_raw_density_at).total_seconds()
            <= self._raw_signal_max_age_seconds
        ):
            return self._smoothed_raw_density, "ema_raw_density"

        return self.latest_density, "filtered_density_fallback"

    def start_passive_listeners(self) -> None:
        super().start_passive_listeners()
        self.subscribe_and_callback(
            self._set_density,
            f"pioreactor/{self.unit}/{self.experiment}/growth_rate_calculating/density",
        )
        self.subscribe_and_callback(
            self._set_raw_density,
            f"pioreactor/{self.unit}/{self.experiment}/od_reading/ods",
        )

    def execute(self) -> Optional[events.DilutionEvent]:
        if not self._primed:
            max_volume_ml = config.getfloat("bioreactor", "max_volume_ml", fallback=14)
            prime_volume = 5.0
            self.logger.info(
                f"Priming: running waste pump for ~{prime_volume:.1f}mL of overshoot "
                f"to ensure liquid level is at outflow tube height (max_volume_ml={max_volume_ml})."
            )
            self.remove_waste_from_bioreactor(
                unit=self.unit,
                experiment=self.experiment,
                ml=prime_volume,
                source_of_event="sdr_od_stop_prime",
                mqtt_client=self.pub_client,
                logger=self.logger,
            )
            self._primed = True

        current_density, signal_source = self._get_stop_density()
        if signal_source != self._stop_signal_source:
            self.logger.info("Stop signal source changed to `%s`.", signal_source)
            self._stop_signal_source = signal_source

        if self.starting_density is None:
            self.starting_density = current_density
            self.target_density = self.relative_density * self.starting_density
            self.logger.info(
                f"Captured starting density: {self.starting_density:.4f} g/L, "
                f"target density: {self.target_density:.4f} g/L "
                f"(relative_density={self.relative_density}, signal={signal_source})"
            )
            estimated_hours = -math.log(self.relative_density) / self.sdr
            estimated_minutes = estimated_hours * 60
            self.logger.info(
                f"Estimated dilution time: {estimated_hours:.1f} hours ({estimated_minutes:.0f} minutes) "
                f"to reach {self.target_density:.4f} g/L at SDR={self.sdr} 1/h "
                f"[t = -ln(relative_density) / SDR]"
            )
            working_volume_ml = config.getfloat("bioreactor", "max_volume_ml", fallback=14)
            estimated_media_ml = -working_volume_ml * math.log(self.relative_density)
            self.logger.info(
                f"Estimated media required: {estimated_media_ml:.1f} mL "
                f"(working volume = {working_volume_ml:.1f} mL, relative_density = {self.relative_density}) "
                f"[V_total = -V_working * ln(relative_density)]"
            )
            cycle_interval = self.volume / ((self.sdr / 3600) * working_volume_ml)
            self.logger.info(
                f"Dosing cycle interval: {cycle_interval:.1f} seconds "
                f"(volume = {self.volume:.1f} mL per cycle) "
                f"[interval = volume / (SDR/3600 * V_working)]"
            )

        if current_density > self.target_density:
            volume_actually_cycled = self.execute_io_action(
                media_ml=self.volume, waste_ml=self.volume
            )
            publish(
                f"pioreactor/{self.unit}/{self.experiment}/dosing_automation/specific_dilution_rate",
                str(self.sdr),
            )
            return events.DilutionEvent(
                f"density={current_density:.4f} > target={self.target_density:.4f} g/L "
                f"(signal={signal_source}); "
                f"exchanged {volume_actually_cycled['media_ml']:.2f}mL",
                data={
                    "current_density": current_density,
                    "target_density": self.target_density,
                    "signal_source": signal_source,
                    "volume_actually_cycled": volume_actually_cycled["waste_ml"],
                },
            )
        else:
            self.logger.info(
                f"Target density reached: density={current_density:.4f} <= "
                f"target={self.target_density:.4f} g/L (signal={signal_source}). "
                f"Dosing complete — ending job."
            )
            publish(
                f"pioreactor/{self.unit}/{self.experiment}/dosing_automation/specific_dilution_rate",
                "0",
            )
            self.clean_up()
            return events.NoEvent(
                f"density={current_density:.4f} <= target={self.target_density:.4f} g/L "
                f"(signal={signal_source}); dosing complete"
            )
