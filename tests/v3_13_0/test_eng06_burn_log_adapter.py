# SPDX-License-Identifier: BUSL-1.1
"""Tests for the provider-neutral ENG-06 burn-log adapter."""
from __future__ import annotations

import math

import pytest

from waggledance.core.v3_13_0.eng06_burn_log_adapter import (
    Eng06BurnLogAdapterError,
    TEMP_UNIT_FAHRENHEIT,
    TEMP_UNIT_KELVIN,
    build_eng06_burn_log,
    normalize_burn_log_rows,
)
from waggledance.core.v3_13_0.eng06_fireplace_advisor import (
    MISSING_DAY_REFUSED,
    NON_MONOTONIC_HORIZON_REFUSED,
    OK,
    summarize_burn_log,
)


def _source_rows() -> list[dict]:
    return [
        {
            "date_utc": "2026-01-01T00:00:00Z",
            "burns": 1,
            "peak_temp_c": 142.4,
            "avg_temp_c": 88.2,
        },
        {
            "date_utc": "2026-01-02T00:00:00Z",
            "burns": 0,
            "peak_temp_c": 40.0,
            "avg_temp_c": 24.0,
        },
        {
            "date_utc": "2026-01-03T00:00:00Z",
            "burns": 2,
            "peak_temp_c": 176.8,
            "avg_temp_c": 96.5,
        },
    ]


def test_builds_solver_ready_rows_from_custom_keys() -> None:
    burn_log = normalize_burn_log_rows(
        _source_rows(),
        day_key="date_utc",
        fire_count_key="burns",
        peak_temp_key="peak_temp_c",
        average_temp_key="avg_temp_c",
    )

    assert burn_log == [
        {
            "day_utc": "2026-01-01T00:00:00Z",
            "fire_event_count": 1,
            "peak_chimney_temp_c": 142.4,
            "average_chimney_temp_c": 88.2,
        },
        {
            "day_utc": "2026-01-02T00:00:00Z",
            "fire_event_count": 0,
            "peak_chimney_temp_c": 40.0,
            "average_chimney_temp_c": 24.0,
        },
        {
            "day_utc": "2026-01-03T00:00:00Z",
            "fire_event_count": 2,
            "peak_chimney_temp_c": 176.8,
            "average_chimney_temp_c": 96.5,
        },
    ]
    result = summarize_burn_log(
        burn_log,
        horizon_start_utc="2026-01-01T00:00:00Z",
        horizon_end_utc="2026-01-03T00:00:00Z",
    )
    assert result.result_marker == OK
    assert result.fire_event_count_30d == 3
    assert result.days_with_fire == 2
    assert result.average_chimney_temp_c == 92.3


def test_preserves_row_order_for_solver_non_monotonic_refusal() -> None:
    rows = _source_rows()
    rows[1], rows[2] = rows[2], rows[1]
    burn_log = build_eng06_burn_log(
        rows,
        day_key="date_utc",
        fire_event_count_key="burns",
        peak_temp_key="peak_temp_c",
        average_temp_key="avg_temp_c",
    )

    result = summarize_burn_log(
        burn_log,
        horizon_start_utc="2026-01-01T00:00:00Z",
        horizon_end_utc="2026-01-03T00:00:00Z",
    )

    assert result.result_marker == NON_MONOTONIC_HORIZON_REFUSED


def test_normalizes_fahrenheit_temperatures_to_celsius() -> None:
    burn_log = normalize_burn_log_rows(
        [
            {
                "day": "2026-01-01",
                "fires": 1,
                "peak_f": 212.0,
                "avg_f": 122.0,
            },
        ],
        day_key="day",
        fire_count_key="fires",
        peak_temp_key="peak_f",
        average_temp_key="avg_f",
        temp_unit=TEMP_UNIT_FAHRENHEIT,
    )

    assert burn_log == [
        {
            "day_utc": "2026-01-01T00:00:00Z",
            "fire_event_count": 1,
            "peak_chimney_temp_c": 100.0,
            "average_chimney_temp_c": 50.0,
        },
    ]


def test_normalizes_kelvin_temperatures_to_celsius() -> None:
    burn_log = normalize_burn_log_rows(
        [
            {
                "day": "2026-01-01",
                "fires": 1,
                "peak_k": 373.15,
                "avg_k": 323.15,
            },
        ],
        day_key="day",
        fire_count_key="fires",
        peak_temp_key="peak_k",
        average_temp_key="avg_k",
        temp_unit=TEMP_UNIT_KELVIN,
    )

    assert burn_log[0]["peak_chimney_temp_c"] == 100.0
    assert burn_log[0]["average_chimney_temp_c"] == 50.0


def test_partial_window_remains_visible_to_solver_missing_day_refusal() -> None:
    rows = [_source_rows()[0], _source_rows()[2]]
    burn_log = build_eng06_burn_log(
        rows,
        day_key="date_utc",
        fire_event_count_key="burns",
        peak_temp_key="peak_temp_c",
        average_temp_key="avg_temp_c",
    )

    result = summarize_burn_log(
        burn_log,
        horizon_start_utc="2026-01-01T00:00:00Z",
        horizon_end_utc="2026-01-03T00:00:00Z",
    )

    assert result.result_marker == MISSING_DAY_REFUSED
    assert result.missing_days_utc == ("2026-01-02T00:00:00Z",)


def test_date_only_day_is_canonicalized_to_solver_utc_day() -> None:
    burn_log = normalize_burn_log_rows(
        [
            {
                "day": "2026-01-01",
                "fires": 1,
                "peak": 142.4,
                "average": 88.2,
            },
        ],
        day_key="day",
        fire_count_key="fires",
        peak_temp_key="peak",
        average_temp_key="average",
    )

    assert burn_log[0]["day_utc"] == "2026-01-01T00:00:00Z"


def test_default_keys_match_solver_row_shape() -> None:
    rows = [
        {
            "day_utc": "2026-01-01T00:00:00Z",
            "fire_event_count": 1,
            "peak_chimney_temp_c": 142.44,
            "average_chimney_temp_c": 88.24,
        },
    ]

    assert build_eng06_burn_log(rows) == [
        {
            "day_utc": "2026-01-01T00:00:00Z",
            "fire_event_count": 1,
            "peak_chimney_temp_c": 142.4,
            "average_chimney_temp_c": 88.2,
        },
    ]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"day_key": ""}, "day_key"),
        ({"fire_event_count_key": ""}, "fire_count_key"),
        ({"peak_temp_key": ""}, "peak_temp_key"),
        ({"average_temp_key": ""}, "average_temp_key"),
    ],
)
def test_key_names_must_be_non_empty_strings(
    kwargs: dict,
    match: str,
) -> None:
    with pytest.raises(Eng06BurnLogAdapterError, match=match):
        build_eng06_burn_log(_source_rows(), **kwargs)


def test_non_object_row_refuses() -> None:
    with pytest.raises(Eng06BurnLogAdapterError, match="rows\\[1\\]"):
        build_eng06_burn_log(
            [_source_rows()[0], "bad-row"],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_missing_day_field_refuses() -> None:
    row = _source_rows()[0]
    row.pop("date_utc")

    with pytest.raises(Eng06BurnLogAdapterError, match="date_utc"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


@pytest.mark.parametrize(
    ("key", "match"),
    [
        ("burns", "burns"),
        ("peak_temp_c", "peak_temp_c"),
        ("avg_temp_c", "avg_temp_c"),
    ],
)
def test_missing_value_fields_refuse(key: str, match: str) -> None:
    row = _source_rows()[0]
    row.pop(key)

    with pytest.raises(Eng06BurnLogAdapterError, match=match):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_sub_day_timestamp_refuses_at_adapter_boundary() -> None:
    row = _source_rows()[0]
    row["date_utc"] = "2026-01-01T12:00:00Z"

    with pytest.raises(Eng06BurnLogAdapterError, match="day-aligned"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_non_string_day_refuses() -> None:
    row = _source_rows()[0]
    row["date_utc"] = 20260101

    with pytest.raises(Eng06BurnLogAdapterError, match="date_utc"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_bool_fire_event_count_refuses_not_silently_as_integer() -> None:
    row = _source_rows()[0]
    row["burns"] = True

    with pytest.raises(Eng06BurnLogAdapterError, match="burns"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_negative_fire_event_count_refuses() -> None:
    row = _source_rows()[0]
    row["burns"] = -1

    with pytest.raises(Eng06BurnLogAdapterError, match="burns"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_bool_temperature_refuses_not_silently_as_numeric() -> None:
    row = _source_rows()[0]
    row["peak_temp_c"] = False

    with pytest.raises(Eng06BurnLogAdapterError, match="peak_temp_c"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )


def test_unknown_temperature_unit_refuses() -> None:
    with pytest.raises(Eng06BurnLogAdapterError, match="temp_unit"):
        normalize_burn_log_rows(_source_rows(), temp_unit="rankine")


def test_non_finite_temperature_refuses() -> None:
    row = _source_rows()[0]
    row["avg_temp_c"] = math.inf

    with pytest.raises(Eng06BurnLogAdapterError, match="finite"):
        build_eng06_burn_log(
            [row],
            day_key="date_utc",
            fire_event_count_key="burns",
            peak_temp_key="peak_temp_c",
            average_temp_key="avg_temp_c",
        )
