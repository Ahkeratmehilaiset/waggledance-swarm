# SPDX-License-Identifier: BUSL-1.1
"""Tests for ENG-06 cottage fireplace first-slice solver core."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from waggledance.core.v3_13_0.eng06_fireplace_advisor import (
    CASE_ID,
    INVALID_LOG_FEED_REFUSED,
    MISSING_DAY_REFUSED,
    NON_MONOTONIC_HORIZON_REFUSED,
    NO_FIRES_IN_HORIZON_REFUSED,
    OK,
    STALE_LOG_REFUSED,
    summarize_burn_log,
)


def _day(offset: int) -> str:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (start + timedelta(days=offset)).strftime("%Y-%m-%dT00:00:00Z")


def _row(
    offset: int,
    *,
    events: int = 0,
    peak: float = 40.0,
    average: float = 24.0,
) -> dict[str, Any]:
    return {
        "day_utc": _day(offset),
        "fire_event_count": events,
        "peak_chimney_temp_c": peak,
        "average_chimney_temp_c": average,
    }


def _rows(days: int = 30) -> list[dict[str, Any]]:
    rows = [_row(index) for index in range(days)]
    rows[2] = _row(2, events=1, peak=142.4, average=88.2)
    rows[10] = _row(10, events=2, peak=176.8, average=96.5)
    rows[21] = _row(21, events=1, peak=131.2, average=84.1)
    return rows


def test_summarizes_30_day_burn_log_happy_path() -> None:
    result = summarize_burn_log(
        _rows(),
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
        stale_threshold_hours=48,
    )

    assert result.result_marker == OK
    assert result.fire_event_count_30d == 4
    assert result.days_with_fire == 3
    assert result.average_chimney_temp_c == 89.6
    assert result.peak_chimney_temp_c == 176.8
    assert result.burn_log_first_day_utc == _day(0)
    assert result.burn_log_last_day_utc == _day(29)


def test_payload_contains_case_id_and_operator_summary_fields() -> None:
    payload = summarize_burn_log(
        _rows(),
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
    ).to_payload()

    assert payload["case_id"] == CASE_ID
    assert payload["result_marker"] == OK
    assert payload["fire_event_count_30d"] == 4
    assert payload["days_with_fire"] == 3
    assert payload["recommendation_freshness_ttl_minutes"] == 60
    assert payload["burn_log_first_day_utc"] == _day(0)
    assert payload["burn_log_last_day_utc"] == _day(29)


def test_zero_fire_days_refuses_instead_of_reporting_advice() -> None:
    result = summarize_burn_log(
        [_row(index) for index in range(30)],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
    )

    assert result.result_marker == NO_FIRES_IN_HORIZON_REFUSED
    assert result.fire_event_count_30d == 0
    assert result.days_with_fire == 0
    assert result.refusal_reason == "no_fire_events_in_horizon"


def test_empty_log_refuses_as_no_fire_signal() -> None:
    result = summarize_burn_log(
        [],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
    )

    assert result.result_marker == NO_FIRES_IN_HORIZON_REFUSED
    assert result.refusal_reason == "no_burn_log_rows_in_horizon"


def test_stale_log_refuses_before_missing_day_details() -> None:
    result = summarize_burn_log(
        _rows(days=27),
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
        stale_threshold_hours=24,
    )

    assert result.result_marker == STALE_LOG_REFUSED
    assert result.refusal_reason == "log_age_72h_exceeds_threshold_24h"
    assert result.burn_log_last_day_utc == _day(26)


def test_missing_day_refuses_with_missing_days_list() -> None:
    rows = _rows()
    del rows[12]

    result = summarize_burn_log(
        rows,
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
        stale_threshold_hours=96,
    )

    assert result.result_marker == MISSING_DAY_REFUSED
    assert result.missing_days_utc == (_day(12),)
    assert result.to_payload()["missing_days_utc"] == [_day(12)]


def test_duplicate_day_refuses_as_non_monotonic_horizon() -> None:
    rows = _rows()
    rows[13] = dict(rows[12])

    result = summarize_burn_log(
        rows,
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
    )

    assert result.result_marker == NON_MONOTONIC_HORIZON_REFUSED
    assert result.duplicate_days_utc == (_day(12),)


def test_out_of_order_day_refuses_as_non_monotonic_horizon() -> None:
    rows = _rows()
    rows[12], rows[13] = rows[13], rows[12]

    result = summarize_burn_log(
        rows,
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(29),
    )

    assert result.result_marker == NON_MONOTONIC_HORIZON_REFUSED
    assert result.duplicate_days_utc == (_day(12),)


def test_horizon_end_before_start_refuses() -> None:
    result = summarize_burn_log(
        _rows(),
        horizon_start_utc=_day(29),
        horizon_end_utc=_day(0),
    )

    assert result.result_marker == NON_MONOTONIC_HORIZON_REFUSED
    assert result.refusal_reason == "horizon_end_before_horizon_start"


def test_non_object_row_refuses_as_invalid_feed() -> None:
    result = summarize_burn_log(
        [_row(0), "bad-row"],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(1),
    )

    assert result.result_marker == INVALID_LOG_FEED_REFUSED
    assert result.refusal_reason == "burn_log[1] must be an object"


def test_bool_fire_event_count_refuses_not_silently_as_integer() -> None:
    row = _row(0)
    row["fire_event_count"] = True

    result = summarize_burn_log(
        [row],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(0),
    )

    assert result.result_marker == INVALID_LOG_FEED_REFUSED
    assert "fire_event_count must be a non-negative integer" in (
        result.refusal_reason or ""
    )


def test_bool_temperature_refuses_not_silently_as_numeric() -> None:
    row = _row(0, events=1)
    row["peak_chimney_temp_c"] = False

    result = summarize_burn_log(
        [row],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(0),
    )

    assert result.result_marker == INVALID_LOG_FEED_REFUSED
    assert "peak_chimney_temp_c must be numeric" in (
        result.refusal_reason or ""
    )


def test_non_finite_temperature_refuses() -> None:
    row = _row(0, events=1)
    row["average_chimney_temp_c"] = math.nan

    result = summarize_burn_log(
        [row],
        horizon_start_utc=_day(0),
        horizon_end_utc=_day(0),
    )

    assert result.result_marker == INVALID_LOG_FEED_REFUSED
    assert "average_chimney_temp_c must be finite" in (
        result.refusal_reason or ""
    )
