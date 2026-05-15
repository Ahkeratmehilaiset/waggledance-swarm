# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""AIR-01 indoor air-quality first-slice advisor.

Pure deterministic logic for an operator-facing first slice:
"interpret already-fetched indoor air sensor readings against the air-quality
knowledge thresholds". This module does not fetch LAN data, scan networks, read
credentials, write state, or call an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping, Sequence

from waggledance.core.v3_13_0.air01_digheran_adapter import (
    CO_PPM,
    OBSERVATION_SCHEMA_VERSION,
    PM25_UG_M3,
    PM10_UG_M3,
    RADON_BQ_M3,
)


CASE_ID = "AIR-01__indoor_air_quality_advisor__cottage"

OK = "OK"
AIR_QUALITY_WARNING = "AIR_QUALITY_WARNING"
AIR_QUALITY_EMERGENCY = "AIR_QUALITY_EMERGENCY"
STALE_OBSERVATION_REFUSED = "STALE_OBSERVATION_REFUSED"
MISSING_REQUIRED_READING_REFUSED = "MISSING_REQUIRED_READING_REFUSED"
INVALID_OBSERVATION_REFUSED = "INVALID_OBSERVATION_REFUSED"

DEFAULT_REQUIRED_METRICS = (PM25_UG_M3, CO_PPM)


@dataclass(frozen=True)
class AirQualityThresholds:
    """Thresholds interpreted from ``knowledge/air_quality/core.yaml``."""

    pm25_warning_ug_m3: float = 50.0
    pm25_smoke_ug_m3: float = 100.0
    co_warning_ppm: float = 35.0
    co_emergency_ppm: float = 100.0
    radon_reference_bq_m3: float = 200.0
    radon_urgent_bq_m3: float = 400.0
    knowledge_source_refs: tuple[str, ...] = (
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.pm25_ug",
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor",
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.radon_bq",
    )


DEFAULT_THRESHOLDS = AirQualityThresholds()


@dataclass(frozen=True)
class Air01AirQualityAdvisory:
    """Operator-facing AIR-01 advisory result or explicit refusal."""

    result_marker: str
    risk_level: str
    advice: tuple[str, ...] = ()
    triggered_metrics: tuple[dict[str, Any], ...] = ()
    observed_at_utc: str | None = None
    fetched_at_utc: str | None = None
    source_url: str | None = None
    device_id: str | None = None
    recommendation_freshness_ttl_minutes: int = 15
    refusal_reason: str | None = None
    missing_metrics: tuple[str, ...] = ()
    knowledge_source_refs: tuple[str, ...] = DEFAULT_THRESHOLDS.knowledge_source_refs

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "risk_level": self.risk_level,
            "write_intent": "none",
            "recommendation_freshness_ttl_minutes":
                self.recommendation_freshness_ttl_minutes,
            "knowledge_source_refs": list(self.knowledge_source_refs),
        }
        if self.advice:
            payload["advice"] = list(self.advice)
        if self.triggered_metrics:
            payload["triggered_metrics"] = list(self.triggered_metrics)
        if self.observed_at_utc is not None:
            payload["observed_at_utc"] = self.observed_at_utc
        if self.fetched_at_utc is not None:
            payload["fetched_at_utc"] = self.fetched_at_utc
        if self.source_url is not None:
            payload["source_url"] = self.source_url
        if self.device_id is not None:
            payload["device_id"] = self.device_id
        if self.refusal_reason is not None:
            payload["refusal_reason"] = self.refusal_reason
        if self.missing_metrics:
            payload["missing_metrics"] = list(self.missing_metrics)
        return payload


@dataclass(frozen=True)
class _Reading:
    metric: str
    value: float
    unit: str


class _InvalidObservation(ValueError):
    """Internal parse refusal for malformed AIR-01 observations."""


def build_air_quality_thresholds_from_knowledge_core(
    knowledge_core: Mapping[str, Any],
) -> AirQualityThresholds:
    """Extract AIR-01 threshold values from the loaded air-quality knowledge."""
    if not isinstance(knowledge_core, Mapping):
        raise ValueError("knowledge_core must be an object")
    thresholds = knowledge_core.get("DECISION_METRICS_AND_THRESHOLDS")
    if not isinstance(thresholds, Mapping):
        raise ValueError("knowledge_core missing DECISION_METRICS_AND_THRESHOLDS")

    pm25 = _metric_entry(thresholds, "pm25_ug")
    co = _metric_entry(thresholds, "co_ppm_indoor")
    radon = _metric_entry(thresholds, "radon_bq")

    pm25_warning = _first_number(pm25.get("action"), DEFAULT_THRESHOLDS.pm25_warning_ug_m3)
    co_warning = _first_number(co.get("action"), DEFAULT_THRESHOLDS.co_warning_ppm)
    radon_numbers = _numbers_from_text(radon.get("action"))
    radon_reference = (
        radon_numbers[0]
        if radon_numbers
        else DEFAULT_THRESHOLDS.radon_reference_bq_m3
    )
    radon_urgent = (
        radon_numbers[1]
        if len(radon_numbers) > 1
        else DEFAULT_THRESHOLDS.radon_urgent_bq_m3
    )
    return AirQualityThresholds(
        pm25_warning_ug_m3=pm25_warning,
        pm25_smoke_ug_m3=DEFAULT_THRESHOLDS.pm25_smoke_ug_m3,
        co_warning_ppm=co_warning,
        co_emergency_ppm=DEFAULT_THRESHOLDS.co_emergency_ppm,
        radon_reference_bq_m3=radon_reference,
        radon_urgent_bq_m3=radon_urgent,
    )


def assess_air_quality(
    observation: Mapping[str, Any],
    *,
    required_metrics: Sequence[str] = DEFAULT_REQUIRED_METRICS,
    stale_threshold_minutes: int = 30,
    thresholds: AirQualityThresholds = DEFAULT_THRESHOLDS,
) -> Air01AirQualityAdvisory:
    """Return a deterministic air-quality advisory or fail-closed refusal."""
    try:
        threshold_minutes = _positive_int(
            stale_threshold_minutes,
            "stale_threshold_minutes",
        )
        required = _required_metrics(required_metrics)
        parsed = _parse_observation(observation)
    except _InvalidObservation as exc:
        return _invalid(str(exc), thresholds)

    age_minutes = (
        parsed["fetched_at"] - parsed["observed_at"]
    ).total_seconds() / 60
    if age_minutes < 0:
        return _invalid("fetched_at_utc before observed_at_utc", thresholds)
    if age_minutes > threshold_minutes:
        return Air01AirQualityAdvisory(
            result_marker=STALE_OBSERVATION_REFUSED,
            risk_level="unknown",
            refusal_reason=(
                f"observation_age_{int(age_minutes)}m_exceeds_threshold_"
                f"{threshold_minutes}m"
            ),
            observed_at_utc=parsed["observed_at_utc"],
            fetched_at_utc=parsed["fetched_at_utc"],
            source_url=parsed["source_url"],
            device_id=parsed["device_id"],
            knowledge_source_refs=thresholds.knowledge_source_refs,
        )

    readings_by_metric = {
        item.metric: item for item in parsed["readings"]
    }
    missing = tuple(metric for metric in required if metric not in readings_by_metric)
    if missing:
        return Air01AirQualityAdvisory(
            result_marker=MISSING_REQUIRED_READING_REFUSED,
            risk_level="unknown",
            refusal_reason="missing_required_air_quality_reading",
            missing_metrics=missing,
            observed_at_utc=parsed["observed_at_utc"],
            fetched_at_utc=parsed["fetched_at_utc"],
            source_url=parsed["source_url"],
            device_id=parsed["device_id"],
            knowledge_source_refs=thresholds.knowledge_source_refs,
        )

    triggered: list[dict[str, Any]] = []
    advice: list[str] = []
    risk_rank = 0
    _evaluate_pm25(readings_by_metric, thresholds, triggered, advice)
    _evaluate_co(readings_by_metric, thresholds, triggered, advice)
    _evaluate_radon(readings_by_metric, thresholds, triggered, advice)
    for item in triggered:
        risk_rank = max(risk_rank, 2 if item["risk_level"] == "emergency" else 1)

    if risk_rank == 0:
        marker = OK
        risk_level = "ok"
        advice.append("No threshold action from available AIR-01 readings.")
    elif risk_rank == 1:
        marker = AIR_QUALITY_WARNING
        risk_level = "warning"
    else:
        marker = AIR_QUALITY_EMERGENCY
        risk_level = "emergency"

    return Air01AirQualityAdvisory(
        result_marker=marker,
        risk_level=risk_level,
        advice=tuple(advice),
        triggered_metrics=tuple(triggered),
        observed_at_utc=parsed["observed_at_utc"],
        fetched_at_utc=parsed["fetched_at_utc"],
        source_url=parsed["source_url"],
        device_id=parsed["device_id"],
        knowledge_source_refs=thresholds.knowledge_source_refs,
    )


def _evaluate_pm25(
    readings_by_metric: Mapping[str, _Reading],
    thresholds: AirQualityThresholds,
    triggered: list[dict[str, Any]],
    advice: list[str],
) -> None:
    reading = readings_by_metric.get(PM25_UG_M3)
    if reading is None:
        return
    if reading.value > thresholds.pm25_smoke_ug_m3:
        _trigger(
            triggered,
            PM25_UG_M3,
            reading.value,
            reading.unit,
            thresholds.pm25_smoke_ug_m3,
            "emergency",
            "possible_smoke_event",
        )
        advice.append("Close windows and ventilation intake; use filtration if available.")
    elif reading.value > thresholds.pm25_warning_ug_m3:
        _trigger(
            triggered,
            PM25_UG_M3,
            reading.value,
            reading.unit,
            thresholds.pm25_warning_ug_m3,
            "warning",
            "pm25_above_air_quality_action_threshold",
        )
        advice.append("Reduce smoke ingress and avoid adding indoor combustion load.")


def _evaluate_co(
    readings_by_metric: Mapping[str, _Reading],
    thresholds: AirQualityThresholds,
    triggered: list[dict[str, Any]],
    advice: list[str],
) -> None:
    reading = readings_by_metric.get(CO_PPM)
    if reading is None:
        return
    if reading.value > thresholds.co_emergency_ppm:
        _trigger(
            triggered,
            CO_PPM,
            reading.value,
            reading.unit,
            thresholds.co_emergency_ppm,
            "emergency",
            "co_emergency_threshold_exceeded",
        )
        advice.append("Leave the area, ventilate, stop combustion sources, and escalate emergency response.")
    elif reading.value > thresholds.co_warning_ppm:
        _trigger(
            triggered,
            CO_PPM,
            reading.value,
            reading.unit,
            thresholds.co_warning_ppm,
            "warning",
            "co_warning_threshold_exceeded",
        )
        advice.append("Ventilate and inspect combustion sources before continued occupancy.")


def _evaluate_radon(
    readings_by_metric: Mapping[str, _Reading],
    thresholds: AirQualityThresholds,
    triggered: list[dict[str, Any]],
    advice: list[str],
) -> None:
    reading = readings_by_metric.get(RADON_BQ_M3)
    if reading is None:
        return
    if reading.value > thresholds.radon_urgent_bq_m3:
        _trigger(
            triggered,
            RADON_BQ_M3,
            reading.value,
            reading.unit,
            thresholds.radon_urgent_bq_m3,
            "emergency",
            "radon_urgent_threshold_exceeded",
        )
        advice.append("Treat radon mitigation as urgent and verify with a calibrated measurement.")
    elif reading.value > thresholds.radon_reference_bq_m3:
        _trigger(
            triggered,
            RADON_BQ_M3,
            reading.value,
            reading.unit,
            thresholds.radon_reference_bq_m3,
            "warning",
            "radon_reference_threshold_exceeded",
        )
        advice.append("Plan radon mitigation and confirm the reading with an appropriate measurement period.")


def _trigger(
    triggered: list[dict[str, Any]],
    metric: str,
    value: float,
    unit: str,
    threshold: float,
    risk_level: str,
    reason: str,
) -> None:
    triggered.append({
        "metric": metric,
        "value": value,
        "unit": unit,
        "threshold": threshold,
        "risk_level": risk_level,
        "reason": reason,
    })


def _parse_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(observation, Mapping):
        raise _InvalidObservation("observation must be an object")
    if observation.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise _InvalidObservation("schema_version refused")
    observed_at_utc = _required_str(observation, "observed_at_utc")
    fetched_at_utc = _required_str(observation, "fetched_at_utc")
    observed_at = _parse_utc_instant(observed_at_utc, "observed_at_utc")
    fetched_at = _parse_utc_instant(fetched_at_utc, "fetched_at_utc")
    readings = _parse_readings(observation.get("readings"))
    return {
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "observed_at_utc": observed_at_utc,
        "fetched_at_utc": fetched_at_utc,
        "source_url": _optional_str(observation.get("source_url")),
        "device_id": _optional_str(observation.get("device_id")),
        "readings": readings,
    }


def _parse_readings(raw: Any) -> tuple[_Reading, ...]:
    if not isinstance(raw, list) or not raw:
        raise _InvalidObservation("readings must be a non-empty list")
    readings: list[_Reading] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise _InvalidObservation(f"readings[{index}] must be an object")
        metric = _required_str(item, "metric")
        unit = _required_str(item, "unit")
        expected_unit = _expected_unit(metric)
        if unit != expected_unit:
            raise _InvalidObservation(f"readings[{index}].unit refused")
        raw_value = item.get("value")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise _InvalidObservation(f"readings[{index}].value must be numeric")
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise _InvalidObservation(
                f"readings[{index}].value must be non-negative finite"
            )
        readings.append(_Reading(metric=metric, value=round(value, 3), unit=unit))
    return tuple(readings)


def _expected_unit(metric: str) -> str:
    if metric in {PM25_UG_M3, PM10_UG_M3}:
        return "ug/m3"
    if metric == CO_PPM:
        return "ppm"
    if metric == RADON_BQ_M3:
        return "Bq/m3"
    raise _InvalidObservation(f"unknown metric {metric}")


def _required_metrics(raw: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _InvalidObservation("required_metrics must be a sequence")
    metrics: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise _InvalidObservation(f"required_metrics[{index}] refused")
        metrics.append(item.strip())
    if not metrics:
        raise _InvalidObservation("required_metrics must not be empty")
    return tuple(metrics)


def _positive_int(raw: Any, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise _InvalidObservation(f"{label} must be a positive integer")
    return raw


def _parse_utc_instant(value: str, label: str) -> datetime:
    if not value.endswith("Z"):
        raise _InvalidObservation(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _InvalidObservation(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise _InvalidObservation(f"{label} must be UTC")
    return parsed


def _required_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidObservation(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _InvalidObservation("optional string field refused")
    return value.strip()


def _metric_entry(
    thresholds: Mapping[str, Any],
    metric: str,
) -> Mapping[str, Any]:
    entry = thresholds.get(metric)
    if not isinstance(entry, Mapping):
        raise ValueError(f"knowledge_core missing {metric}")
    return entry


def _first_number(raw: Any, default: float) -> float:
    numbers = _numbers_from_text(raw)
    return numbers[0] if numbers else default


def _numbers_from_text(raw: Any) -> tuple[float, ...]:
    if raw is None:
        return ()
    values = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)", str(raw))
    return tuple(float(value) for value in values)


def _invalid(
    reason: str,
    thresholds: AirQualityThresholds,
) -> Air01AirQualityAdvisory:
    return Air01AirQualityAdvisory(
        result_marker=INVALID_OBSERVATION_REFUSED,
        risk_level="unknown",
        refusal_reason=reason,
        knowledge_source_refs=thresholds.knowledge_source_refs,
    )


__all__ = [
    "AIR_QUALITY_EMERGENCY",
    "AIR_QUALITY_WARNING",
    "Air01AirQualityAdvisory",
    "AirQualityThresholds",
    "CASE_ID",
    "DEFAULT_REQUIRED_METRICS",
    "DEFAULT_THRESHOLDS",
    "INVALID_OBSERVATION_REFUSED",
    "MISSING_REQUIRED_READING_REFUSED",
    "OK",
    "STALE_OBSERVATION_REFUSED",
    "assess_air_quality",
    "build_air_quality_thresholds_from_knowledge_core",
]
