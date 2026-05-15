# SPDX-License-Identifier: BUSL-1.1
"""Tests for ENG-01 operator advisory card rendering."""
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.eng01_advisory_card import (
    CARD_SCHEMA_VERSION,
    CASE_ID,
    Eng01AdvisoryCardError,
    RISK_CLASS,
    render_eng01_advisory_card,
)


def _ok_payload() -> dict:
    return {
        "result_marker": "OK",
        "feed_source": "operator_selected_spot_price_public_feed_sample",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 24,
        "recommendation_freshness_ttl_minutes": 60,
        "horizon_24h_min_price_eur_per_kwh": 0.031,
        "horizon_24h_max_price_eur_per_kwh": 0.172,
        "horizon_24h_avg_price_eur_per_kwh": 0.111,
        "expected_savings_eur_per_kwh_vs_peak": 0.141,
        "top_3_cheapest_hours_utc": [
            {"hour_utc": "2026-01-16T02:00:00Z",
             "price_eur_per_kwh": 0.031, "rank": 1},
            {"hour_utc": "2026-01-16T01:00:00Z",
             "price_eur_per_kwh": 0.038, "rank": 2},
            {"hour_utc": "2026-01-16T03:00:00Z",
             "price_eur_per_kwh": 0.042, "rank": 3},
        ],
    }


def test_render_ok_payload_as_operator_card() -> None:
    card = render_eng01_advisory_card(_ok_payload())

    assert card["schema_version"] == CARD_SCHEMA_VERSION
    assert card["case_id"] == CASE_ID
    assert card["risk_class"] == RISK_CLASS
    assert card["write_intent"] == "none"
    assert card["status"] == "ok"
    assert card["title"] == "ENG-01 cheapest electricity hours"
    assert "2026-01-16T02:00:00Z (0.031 EUR/kWh)" in card["summary"]
    assert card["top_hours"][0] == {
        "rank": 1,
        "hour_utc": "2026-01-16T02:00:00Z",
        "price_eur_per_kwh": 0.031,
    }
    assert card["metrics"]["expected_savings_eur_per_kwh_vs_peak"] == 0.141
    assert "Do not automate" in card["operator_guidance"][1]


def test_render_refusal_payload_as_operator_card() -> None:
    card = render_eng01_advisory_card({
        "result_marker": "MISSING_HOUR_REFUSED",
        "refusal_reason": "missing_hour_in_horizon",
        "missing_hours_utc": ["2026-01-16T03:00:00Z"],
        "recommendation_freshness_ttl_minutes": 60,
    })

    assert card["status"] == "refused"
    assert card["write_intent"] == "none"
    assert card["summary"] == (
        "ENG-01 advice refused: MISSING_HOUR_REFUSED "
        "(missing_hour_in_horizon)"
    )
    assert card["refusal_details"]["missing_hours_utc"] == [
        "2026-01-16T03:00:00Z"
    ]
    assert "Do not act" in card["operator_guidance"][0]


def test_refuses_missing_result_marker() -> None:
    with pytest.raises(Eng01AdvisoryCardError, match="result_marker"):
        render_eng01_advisory_card({})


def test_refuses_ok_payload_without_top_hours() -> None:
    payload = _ok_payload()
    payload["top_3_cheapest_hours_utc"] = []

    with pytest.raises(Eng01AdvisoryCardError, match="OK payload"):
        render_eng01_advisory_card(payload)


def test_refuses_bool_price_in_top_hours() -> None:
    payload = _ok_payload()
    payload["top_3_cheapest_hours_utc"][0]["price_eur_per_kwh"] = True

    with pytest.raises(Eng01AdvisoryCardError, match="price_eur_per_kwh"):
        render_eng01_advisory_card(payload)


def test_refuses_bool_horizon_hours() -> None:
    payload = _ok_payload()
    payload["horizon_hours"] = True

    with pytest.raises(Eng01AdvisoryCardError, match="horizon_hours"):
        render_eng01_advisory_card(payload)
