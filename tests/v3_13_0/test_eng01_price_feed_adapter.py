# SPDX-License-Identifier: BUSL-1.1
"""Tests for the provider-neutral ENG-01 price feed adapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    Eng01PriceFeedAdapterError,
    PRICE_UNIT_EUR_PER_KWH,
    PRICE_UNIT_EUR_PER_MWH,
    build_eng01_price_feed,
)
from waggledance.core.v3_13_0.eng01_spot_electricity import (
    MISSING_HOUR_REFUSED,
    OK,
    recommend_top_3_cheapest_hours,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "tests" / "fixtures" / "v3_13_0" /
    "eng01_spot_electricity_shadow.json"
)


def _winter_fixture() -> dict:
    scenarios = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"]
    return scenarios[
        "happy_path__synthetic_24h_winter_with_known_min_at_02_00_local"
    ]


def test_builds_solver_feed_from_eur_per_mwh_rows() -> None:
    scenario = _winter_fixture()
    feed_input = scenario["input"]
    rows = [
        {
            "hour_utc": item["hour_utc"],
            "price": item["price_eur_per_kwh"] * 1000.0,
        }
        for item in feed_input["prices_eur_per_kwh"]
    ]

    feed = build_eng01_price_feed(
        rows,
        fetched_at_utc=feed_input["fetched_at_utc"],
        horizon_start_utc=feed_input["horizon_start_utc"],
        feed_source="entsoe_transparency_platform_day_ahead_shadow",
        price_unit=PRICE_UNIT_EUR_PER_MWH,
    )
    result = recommend_top_3_cheapest_hours(feed)

    assert feed["feed_source"] == \
        "entsoe_transparency_platform_day_ahead_shadow"
    assert result.result_marker == OK
    assert result.to_payload()["top_3_cheapest_hours_utc"] == \
        scenario["expected_output"]["top_3_cheapest_hours_utc"]


def test_accepts_eur_per_kwh_rows_without_unit_conversion() -> None:
    feed = build_eng01_price_feed(
        [
            {"hour_utc": "2026-01-16T00:00:00Z", "price": 0.111},
            {"hour_utc": "2026-01-16T01:00:00Z", "price": 0.095},
            {"hour_utc": "2026-01-16T02:00:00Z", "price": 0.130},
        ],
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
        price_unit=PRICE_UNIT_EUR_PER_KWH,
    )

    assert feed["prices_eur_per_kwh"] == [
        {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.111},
        {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.095},
        {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.13},
    ]


def test_partial_window_remains_visible_to_solver_refusal() -> None:
    feed = build_eng01_price_feed(
        [
            {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
            {"hour_utc": "2026-01-16T02:00:00Z", "price": 120.0},
        ],
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
    )

    result = recommend_top_3_cheapest_hours(feed)

    assert result.result_marker == MISSING_HOUR_REFUSED
    assert result.to_payload()["missing_hours_utc"] == [
        "2026-01-16T01:00:00Z",
    ]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"horizon_hours": True}, "horizon_hours"),
        ({"stale_threshold_hours": True}, "stale_threshold_hours"),
    ],
)
def test_bool_integer_fields_are_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(Eng01PriceFeedAdapterError, match=match):
        build_eng01_price_feed(
            [{"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0}],
            fetched_at_utc="2026-01-15T20:00:00Z",
            horizon_start_utc="2026-01-16T00:00:00Z",
            **kwargs,
        )


def test_sub_hour_price_timestamp_is_rejected_at_adapter_boundary() -> None:
    with pytest.raises(Eng01PriceFeedAdapterError, match="hour-aligned"):
        build_eng01_price_feed(
            [{"hour_utc": "2026-01-16T00:30:00Z", "price": 100.0}],
            fetched_at_utc="2026-01-15T20:00:00Z",
            horizon_start_utc="2026-01-16T00:00:00Z",
        )


def test_secret_like_feed_source_is_rejected() -> None:
    with pytest.raises(Eng01PriceFeedAdapterError, match="secret"):
        build_eng01_price_feed(
            [{"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0}],
            fetched_at_utc="2026-01-15T20:00:00Z",
            horizon_start_utc="2026-01-16T00:00:00Z",
            feed_source="provider?x-api-key=do-not-log",
        )
