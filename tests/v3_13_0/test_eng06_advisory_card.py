# SPDX-License-Identifier: BUSL-1.1
"""Tests for ENG-06 operator advisory card rendering."""
from __future__ import annotations

import math

import pytest

from waggledance.core.v3_13_0.eng06_advisory_card import (
    CARD_SCHEMA_VERSION,
    CASE_ID,
    Eng06AdvisoryCardError,
    RISK_CLASS,
    render_eng06_advisory_card,
)


def _ok_payload() -> dict:
    return {
        "case_id": CASE_ID,
        "result_marker": "OK",
        "fire_event_count_30d": 4,
        "days_with_fire": 3,
        "average_chimney_temp_c": 89.6,
        "peak_chimney_temp_c": 176.8,
        "burn_log_first_day_utc": "2026-01-01T00:00:00Z",
        "burn_log_last_day_utc": "2026-01-30T00:00:00Z",
        "recommendation_freshness_ttl_minutes": 60,
    }


def test_render_ok_payload_as_operator_card() -> None:
    card = render_eng06_advisory_card(_ok_payload())

    assert card["schema_version"] == CARD_SCHEMA_VERSION
    assert card["case_id"] == CASE_ID
    assert card["risk_class"] == RISK_CLASS
    assert card["write_intent"] == "none"
    assert card["status"] == "ok"
    assert card["title"] == "ENG-06 fireplace burn-log summary"
    assert "4 fire events across 3 fire days" in card["summary"]
    assert card["burn_log_window"] == {
        "first_day_utc": "2026-01-01T00:00:00Z",
        "last_day_utc": "2026-01-30T00:00:00Z",
    }
    assert card["metrics"] == {
        "fire_event_count_30d": 4,
        "days_with_fire": 3,
        "average_chimney_temp_c": 89.6,
        "peak_chimney_temp_c": 176.8,
    }
    assert "Do not automate" in card["operator_guidance"][1]


def test_render_refusal_payload_as_operator_card() -> None:
    card = render_eng06_advisory_card({
        "result_marker": "MISSING_DAY_REFUSED",
        "refusal_reason": "missing_day_in_horizon",
        "missing_days_utc": ["2026-01-12T00:00:00Z"],
        "burn_log_first_day_utc": "2026-01-01T00:00:00Z",
        "burn_log_last_day_utc": "2026-01-30T00:00:00Z",
        "recommendation_freshness_ttl_minutes": 60,
    })

    assert card["status"] == "refused"
    assert card["write_intent"] == "none"
    assert card["summary"] == (
        "ENG-06 advice refused: MISSING_DAY_REFUSED "
        "(missing_day_in_horizon)"
    )
    assert card["refusal_details"]["missing_days_utc"] == [
        "2026-01-12T00:00:00Z"
    ]
    assert card["refusal_details"]["burn_log_first_day_utc"] == (
        "2026-01-01T00:00:00Z"
    )
    assert "Do not act" in card["operator_guidance"][0]


def test_render_refusal_payload_preserves_duplicate_day_details() -> None:
    card = render_eng06_advisory_card({
        "result_marker": "NON_MONOTONIC_HORIZON_REFUSED",
        "refusal_reason": "non_monotonic_or_duplicate_day",
        "duplicate_days_utc": ["2026-01-12T00:00:00Z"],
    })

    assert card["status"] == "refused"
    assert card["refusal_reason"] == "non_monotonic_or_duplicate_day"
    assert card["refusal_details"]["duplicate_days_utc"] == [
        "2026-01-12T00:00:00Z"
    ]


def test_refuses_missing_result_marker() -> None:
    with pytest.raises(Eng06AdvisoryCardError, match="result_marker"):
        render_eng06_advisory_card({})


def test_refuses_ok_payload_without_required_metric() -> None:
    payload = _ok_payload()
    payload.pop("peak_chimney_temp_c")

    with pytest.raises(Eng06AdvisoryCardError, match="peak_chimney_temp_c"):
        render_eng06_advisory_card(payload)


def test_refuses_bool_fire_count_metric() -> None:
    payload = _ok_payload()
    payload["fire_event_count_30d"] = True

    with pytest.raises(Eng06AdvisoryCardError, match="fire_event_count_30d"):
        render_eng06_advisory_card(payload)


def test_refuses_bool_temperature_metric() -> None:
    payload = _ok_payload()
    payload["average_chimney_temp_c"] = False

    with pytest.raises(Eng06AdvisoryCardError, match="average_chimney_temp_c"):
        render_eng06_advisory_card(payload)


def test_refuses_non_finite_temperature_metric() -> None:
    payload = _ok_payload()
    payload["peak_chimney_temp_c"] = math.nan

    with pytest.raises(Eng06AdvisoryCardError, match="peak_chimney_temp_c"):
        render_eng06_advisory_card(payload)


def test_refuses_ok_payload_without_burn_log_window() -> None:
    payload = _ok_payload()
    payload.pop("burn_log_first_day_utc")

    with pytest.raises(Eng06AdvisoryCardError, match="burn_log_first_day_utc"):
        render_eng06_advisory_card(payload)
