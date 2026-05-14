# SPDX-License-Identifier: BUSL-1.1
"""Tests for ENG-01 spot electricity first-slice solver core."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.v3_13_0.eng01_spot_electricity import (
    Eng01SpotElectricityError,
    INVALID_PRICE_FEED_REFUSED,
    MISSING_HOUR_REFUSED,
    NON_MONOTONIC_HORIZON_REFUSED,
    OK,
    STALE_DATA_REFUSED,
    recommend_top_3_cheapest_hours,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "tests" / "fixtures" / "v3_13_0" /
    "eng01_spot_electricity_shadow.json"
)


def _scenarios() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"]


@pytest.mark.parametrize(
    "scenario_id",
    [
        "happy_path__synthetic_24h_winter_with_known_min_at_02_00_local",
        "happy_path__synthetic_24h_summer_with_low_spread",
    ],
)
def test_happy_paths_match_shadow_fixture_top_3(scenario_id: str) -> None:
    scenario = _scenarios()[scenario_id]

    result = recommend_top_3_cheapest_hours(scenario["input"])
    payload = result.to_payload()

    assert result.result_marker == OK
    assert payload["top_3_cheapest_hours_utc"] == \
        scenario["expected_output"]["top_3_cheapest_hours_utc"]
    assert payload["horizon_24h_min_price_eur_per_kwh"] == \
        scenario["expected_output"]["horizon_24h_min_price_eur_per_kwh"]
    assert payload["horizon_24h_max_price_eur_per_kwh"] == \
        scenario["expected_output"]["horizon_24h_max_price_eur_per_kwh"]
    assert payload["horizon_24h_avg_price_eur_per_kwh"] == \
        scenario["expected_output"]["horizon_24h_avg_price_eur_per_kwh"]
    assert payload["expected_savings_eur_per_kwh_vs_peak"] == \
        scenario["expected_output"]["expected_savings_eur_per_kwh_vs_peak"]


@pytest.mark.parametrize(
    ("scenario_id", "expected_marker"),
    [
        ("failure_mode__stale_data_refused", STALE_DATA_REFUSED),
        ("failure_mode__missing_hour_refused", MISSING_HOUR_REFUSED),
        (
            "failure_mode__non_monotonic_horizon_refused",
            NON_MONOTONIC_HORIZON_REFUSED,
        ),
    ],
)
def test_failure_modes_refuse_with_fixture_markers(
    scenario_id: str,
    expected_marker: str,
) -> None:
    scenario = _scenarios()[scenario_id]

    result = recommend_top_3_cheapest_hours(scenario["input"])
    payload = result.to_payload()

    assert result.result_marker == expected_marker
    assert payload["result_marker"] == \
        scenario["expected_output"]["result_marker"]
    assert payload["top_3_cheapest_hours_utc"] == []


def test_ties_are_ranked_by_price_then_hour() -> None:
    result = recommend_top_3_cheapest_hours({
        "fetched_at_utc": "2026-01-15T20:00:00Z",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 3,
        "prices_eur_per_kwh": [
            {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1},
            {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.2},
            {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.1},
        ],
    })

    assert result.to_payload()["top_3_cheapest_hours_utc"] == [
        {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1,
         "rank": 1},
        {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.1,
         "rank": 2},
        {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.2,
         "rank": 3},
    ]


def test_invalid_feed_shape_raises_before_advice() -> None:
    with pytest.raises(Eng01SpotElectricityError, match="non-empty list"):
        recommend_top_3_cheapest_hours({
            "fetched_at_utc": "2026-01-15T20:00:00Z",
            "horizon_start_utc": "2026-01-16T00:00:00Z",
            "horizon_hours": 24,
            "prices_eur_per_kwh": [],
        })


def test_short_complete_but_wrong_count_refuses() -> None:
    result = recommend_top_3_cheapest_hours({
        "fetched_at_utc": "2026-01-15T20:00:00Z",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 4,
        "prices_eur_per_kwh": [
            {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1},
            {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.2},
            {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.3},
        ],
    })

    assert result.result_marker == MISSING_HOUR_REFUSED


def test_unexpected_count_refuses_when_hours_are_not_missing() -> None:
    result = recommend_top_3_cheapest_hours({
        "fetched_at_utc": "2026-01-15T20:00:00Z",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 2,
        "prices_eur_per_kwh": [
            {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1},
            {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.2},
            {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.3},
        ],
    })

    assert result.result_marker == INVALID_PRICE_FEED_REFUSED


# --------------------------------------------------------------------------
# Fail-closed hardening regressions (PR #392 RCO nits, hardening lane)
# --------------------------------------------------------------------------


def test_bool_horizon_hours_rejected_not_silently_treated_as_one() -> None:
    """Python isinstance(True, int) is True. The earlier _positive_int
    accepted bool and silently meant horizon_hours=1. The hardened helper
    must reject bool explicitly so a buggy caller never gets a partial
    1-hour window in place of a 24-hour one."""
    with pytest.raises(Eng01SpotElectricityError,
                       match="horizon_hours must be a positive integer"):
        recommend_top_3_cheapest_hours({
            "fetched_at_utc": "2026-01-15T20:00:00Z",
            "horizon_start_utc": "2026-01-16T00:00:00Z",
            "horizon_hours": True,
            "prices_eur_per_kwh": [
                {"hour_utc": "2026-01-16T00:00:00Z",
                 "price_eur_per_kwh": 0.1},
            ],
        })


def test_bool_stale_threshold_hours_rejected() -> None:
    """Same Python bool/int conflation applies to the stale-data threshold.
    A buggy caller passing stale_threshold_hours=True must fail loudly
    instead of silently meaning '1 hour stale threshold'."""
    with pytest.raises(Eng01SpotElectricityError,
                       match="stale_threshold_hours must be a positive integer"):
        recommend_top_3_cheapest_hours(
            {
                "fetched_at_utc": "2026-01-15T20:00:00Z",
                "horizon_start_utc": "2026-01-16T00:00:00Z",
                "horizon_hours": 24,
                "prices_eur_per_kwh": [
                    {"hour_utc": "2026-01-16T00:00:00Z",
                     "price_eur_per_kwh": 0.1},
                ],
            },
            stale_threshold_hours=True,
        )


def test_sub_hour_timestamp_refuses_with_missing_hour_marker() -> None:
    """Hour-aligned UTC Z is the contract for prices_eur_per_kwh[*].hour_utc.
    A sub-hour timestamp (e.g. 00:30 instead of 00:00) parses successfully
    as a UTC datetime but is treated by the missing-hour check as not
    covering the expected hour-aligned slot, so the solver refuses with
    MISSING_HOUR_REFUSED rather than silently rounding. Locks the
    safe-refusal behavior referenced in the _parse_utc comment."""
    result = recommend_top_3_cheapest_hours({
        "fetched_at_utc": "2026-01-15T20:00:00Z",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 1,
        "prices_eur_per_kwh": [
            {"hour_utc": "2026-01-16T00:30:00Z",
             "price_eur_per_kwh": 0.1},
        ],
    })

    assert result.result_marker == MISSING_HOUR_REFUSED
    assert "2026-01-16T00:00:00Z" in result.missing_hours_utc
    assert result.top_3_cheapest_hours_utc == ()
