# SPDX-License-Identifier: BUSL-1.1
"""Tests for AIR-01 indoor air-quality advisor."""
from __future__ import annotations

from pathlib import Path

import yaml

from waggledance.core.v3_13_0.air01_air_quality_advisor import (
    AIR_QUALITY_EMERGENCY,
    AIR_QUALITY_WARNING,
    INVALID_OBSERVATION_REFUSED,
    MISSING_REQUIRED_READING_REFUSED,
    OK,
    STALE_OBSERVATION_REFUSED,
    assess_air_quality,
    build_air_quality_thresholds_from_knowledge_core,
)
from waggledance.core.v3_13_0.air01_digheran_adapter import (
    CO_PPM,
    OBSERVATION_SCHEMA_VERSION,
    PM25_UG_M3,
    RADON_BQ_M3,
)


def _observation(*, pm25: float = 12.0, co: float = 0.5,
                 radon: float | None = None) -> dict:
    readings = [
        {"metric": PM25_UG_M3, "value": pm25, "unit": "ug/m3"},
        {"metric": CO_PPM, "value": co, "unit": "ppm"},
    ]
    if radon is not None:
        readings.append({"metric": RADON_BQ_M3, "value": radon, "unit": "Bq/m3"})
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "source_url": "http://192.168.1.44/api/air/current",
        "device_id": "digheran-cottage-1",
        "observed_at_utc": "2026-05-15T18:00:00Z",
        "fetched_at_utc": "2026-05-15T18:02:00Z",
        "readings": readings,
    }


def test_ok_observation_has_no_write_intent() -> None:
    payload = assess_air_quality(_observation()).to_payload()

    assert payload["result_marker"] == OK
    assert payload["risk_level"] == "ok"
    assert payload["write_intent"] == "none"
    assert payload["device_id"] == "digheran-cottage-1"


def test_pm25_warning_uses_air_quality_knowledge_threshold() -> None:
    result = assess_air_quality(_observation(pm25=57.25, co=2.1))

    assert result.result_marker == AIR_QUALITY_WARNING
    assert result.risk_level == "warning"
    assert result.triggered_metrics[0]["metric"] == PM25_UG_M3
    assert result.triggered_metrics[0]["threshold"] == 50.0


def test_co_emergency_takes_priority_over_pm25_warning() -> None:
    result = assess_air_quality(_observation(pm25=57.25, co=120.0))

    assert result.result_marker == AIR_QUALITY_EMERGENCY
    assert result.risk_level == "emergency"
    assert any(item["metric"] == CO_PPM for item in result.triggered_metrics)


def test_radon_reference_and_urgent_thresholds_are_optional_readings() -> None:
    warning = assess_air_quality(_observation(radon=250.0))
    emergency = assess_air_quality(_observation(radon=450.0))

    assert warning.result_marker == AIR_QUALITY_WARNING
    assert emergency.result_marker == AIR_QUALITY_EMERGENCY


def test_missing_required_reading_refuses_fail_closed() -> None:
    observation = _observation()
    observation["readings"] = [
        {"metric": PM25_UG_M3, "value": 12.0, "unit": "ug/m3"},
    ]

    result = assess_air_quality(observation)

    assert result.result_marker == MISSING_REQUIRED_READING_REFUSED
    assert result.missing_metrics == (CO_PPM,)


def test_stale_observation_refuses_before_advice() -> None:
    observation = _observation(pm25=57.25, co=120.0)
    observation["fetched_at_utc"] = "2026-05-15T19:00:00Z"

    result = assess_air_quality(observation, stale_threshold_minutes=30)

    assert result.result_marker == STALE_OBSERVATION_REFUSED
    assert result.triggered_metrics == ()
    assert result.refusal_reason == "observation_age_60m_exceeds_threshold_30m"


def test_invalid_schema_unit_or_bool_refuses() -> None:
    observation = _observation()
    observation["schema_version"] = "other"
    assert assess_air_quality(observation).result_marker == (
        INVALID_OBSERVATION_REFUSED
    )

    observation = _observation()
    observation["readings"][0]["unit"] = "ppm"
    assert assess_air_quality(observation).result_marker == (
        INVALID_OBSERVATION_REFUSED
    )

    observation = _observation()
    observation["readings"][0]["value"] = True
    assert assess_air_quality(observation).result_marker == (
        INVALID_OBSERVATION_REFUSED
    )


def test_builds_thresholds_from_air_quality_knowledge_yaml() -> None:
    raw = Path("knowledge/air_quality/core.yaml").read_text(encoding="utf-8")
    knowledge_core = yaml.safe_load(raw)

    thresholds = build_air_quality_thresholds_from_knowledge_core(knowledge_core)

    assert thresholds.pm25_warning_ug_m3 == 50.0
    assert thresholds.co_warning_ppm == 35.0
    assert thresholds.radon_reference_bq_m3 == 200.0
    assert thresholds.radon_urgent_bq_m3 == 400.0
