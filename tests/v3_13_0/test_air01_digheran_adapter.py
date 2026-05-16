# SPDX-License-Identifier: BUSL-1.1
"""Tests for AIR-01 Digheran-shaped observation adapter."""
from __future__ import annotations

import json
import math

import pytest

from waggledance.core.v3_13_0.air01_digheran_adapter import (
    CO_PPM,
    OBSERVATION_SCHEMA_VERSION,
    PM25_UG_M3,
    RADON_BQ_M3,
    Air01DigheranAdapterError,
    normalize_digheran_air_quality_payload,
    parse_digheran_air_quality_response,
)


SOURCE_URL = "http://192.168.1.44/api/air/current"


def _payload() -> dict:
    return {
        "device": {"id": "digheran-cottage-1", "model": "Digheran AQ"},
        "timestamp_utc": "2026-05-15T18:00:00Z",
        "readings": {
            "pm25": {"value": 57.25, "unit": "ug/m3"},
            "pm10": {"value": 82.0, "unit": "ug/m3"},
            "co": {"value": 2.1, "unit": "ppm"},
            "radon": {"value": 180, "unit": "Bq/m3"},
        },
    }


def test_normalizes_digheran_payload_to_provider_neutral_observation() -> None:
    observation = normalize_digheran_air_quality_payload(
        _payload(),
        source_url=SOURCE_URL,
        fetched_at_utc="2026-05-15T18:02:00Z",
    )

    assert observation["schema_version"] == OBSERVATION_SCHEMA_VERSION
    assert observation["observation_source"] == (
        "operator_allowlisted_digheran_http"
    )
    assert observation["source_url"] == SOURCE_URL
    assert observation["device_id"] == "digheran-cottage-1"
    assert observation["observed_at_utc"] == "2026-05-15T18:00:00Z"
    assert observation["fetched_at_utc"] == "2026-05-15T18:02:00Z"
    assert observation["readings"][0] == {
        "metric": PM25_UG_M3,
        "value": 57.25,
        "unit": "ug/m3",
        "source_key": "pm25",
        "source_unit": "ug/m3",
    }
    assert observation["readings"][2]["metric"] == CO_PPM
    assert observation["readings"][3]["metric"] == RADON_BQ_M3


def test_parse_response_accepts_json_bytes() -> None:
    observation = parse_digheran_air_quality_response(
        json.dumps(_payload()).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        source_url=SOURCE_URL,
    )

    assert observation["observed_at_utc"] == "2026-05-15T18:00:00Z"


def test_missing_readings_or_unknown_metrics_refuse() -> None:
    payload = _payload()
    payload.pop("readings")
    with pytest.raises(Air01DigheranAdapterError, match="readings"):
        normalize_digheran_air_quality_payload(
            payload,
            source_url=SOURCE_URL,
        )

    payload = _payload()
    payload["readings"] = {"voc": {"value": 3, "unit": "index"}}
    with pytest.raises(Air01DigheranAdapterError, match="no known metrics"):
        normalize_digheran_air_quality_payload(
            payload,
            source_url=SOURCE_URL,
        )


def test_unit_ambiguity_and_non_finite_values_refuse() -> None:
    payload = _payload()
    payload["readings"]["pm25"]["unit"] = "ppm"
    with pytest.raises(Air01DigheranAdapterError, match="unsupported"):
        normalize_digheran_air_quality_payload(
            payload,
            source_url=SOURCE_URL,
        )

    payload = _payload()
    payload["readings"]["co"]["value"] = math.inf
    with pytest.raises(Air01DigheranAdapterError, match="finite"):
        normalize_digheran_air_quality_payload(
            payload,
            source_url=SOURCE_URL,
        )


def test_bool_values_and_secret_source_refs_refuse() -> None:
    payload = _payload()
    payload["readings"]["co"]["value"] = True
    with pytest.raises(Air01DigheranAdapterError, match="numeric"):
        normalize_digheran_air_quality_payload(
            payload,
            source_url=SOURCE_URL,
        )

    with pytest.raises(Air01DigheranAdapterError, match="secrets"):
        normalize_digheran_air_quality_payload(
            _payload(),
            source_url="http://192.168.1.44/api/current?token=abc",
        )


@pytest.mark.parametrize("source_url", [
    "http://192.168.1.44/api/current?access_key=abc",
    "http://192.168.1.44/api/current?private_key=abc",
    "http://192.168.1.44/api/current?secrets=abc",
    "http://192.168.1.44/api/current?tokens=abc",
])
def test_union_secret_source_refs_refuse(source_url: str) -> None:
    with pytest.raises(Air01DigheranAdapterError, match="secrets"):
        normalize_digheran_air_quality_payload(
            _payload(),
            source_url=source_url,
        )
