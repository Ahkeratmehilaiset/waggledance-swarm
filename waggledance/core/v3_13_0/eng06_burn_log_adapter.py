# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Provider-neutral ENG-06 burn-log row adapter.

This module normalizes already-fetched daily fireplace burn-log rows into the
shape consumed by :mod:`eng06_fireplace_advisor`. It does not open SQLite,
fetch weather forecasts, read credentials, write state, or sort rows.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Iterable, Mapping


DEFAULT_DAY_KEY = "day_utc"
DEFAULT_FIRE_EVENT_COUNT_KEY = "fire_event_count"
DEFAULT_PEAK_TEMP_KEY = "peak_chimney_temp_c"
DEFAULT_AVERAGE_TEMP_KEY = "average_chimney_temp_c"
TEMP_UNIT_CELSIUS = "celsius"
TEMP_UNIT_FAHRENHEIT = "fahrenheit"
TEMP_UNIT_KELVIN = "kelvin"
SUPPORTED_TEMP_UNITS = frozenset({
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    TEMP_UNIT_KELVIN,
})


class Eng06BurnLogAdapterError(ValueError):
    """Invalid caller input for ENG-06 burn-log adaptation."""


def normalize_burn_log_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    day_key: str = DEFAULT_DAY_KEY,
    fire_count_key: str = DEFAULT_FIRE_EVENT_COUNT_KEY,
    peak_temp_key: str = DEFAULT_PEAK_TEMP_KEY,
    average_temp_key: str = DEFAULT_AVERAGE_TEMP_KEY,
    temp_unit: str = TEMP_UNIT_CELSIUS,
) -> list[dict[str, Any]]:
    """Build solver-ready ENG-06 burn-log rows from source-shaped rows.

    The adapter preserves row order. Duplicate or out-of-order days remain
    visible to the solver's fail-closed checks.
    """
    day_key = _required_key(day_key, "day_key")
    fire_count_key = _required_key(fire_count_key, "fire_count_key")
    peak_temp_key = _required_key(peak_temp_key, "peak_temp_key")
    average_temp_key = _required_key(average_temp_key, "average_temp_key")
    temp_unit = _normalize_temp_unit(temp_unit)

    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Eng06BurnLogAdapterError(f"rows[{index}] must be an object")
        normalized.append({
            "day_utc": _parse_utc_day(
                _required_value(row, day_key, index),
                f"rows[{index}].{day_key}",
            ),
            "fire_event_count": _non_negative_int(
                _required_value(row, fire_count_key, index),
                f"rows[{index}].{fire_count_key}",
            ),
            "peak_chimney_temp_c": _finite_number(
                _required_value(row, peak_temp_key, index),
                f"rows[{index}].{peak_temp_key}",
                temp_unit=temp_unit,
            ),
            "average_chimney_temp_c": _finite_number(
                _required_value(row, average_temp_key, index),
                f"rows[{index}].{average_temp_key}",
                temp_unit=temp_unit,
            ),
        })
    return normalized


def build_eng06_burn_log(
    rows: Iterable[Mapping[str, Any]],
    *,
    day_key: str = DEFAULT_DAY_KEY,
    fire_event_count_key: str = DEFAULT_FIRE_EVENT_COUNT_KEY,
    peak_temp_key: str = DEFAULT_PEAK_TEMP_KEY,
    average_temp_key: str = DEFAULT_AVERAGE_TEMP_KEY,
    temp_unit: str = TEMP_UNIT_CELSIUS,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper for :func:`normalize_burn_log_rows`."""
    return normalize_burn_log_rows(
        rows,
        day_key=day_key,
        fire_count_key=fire_event_count_key,
        peak_temp_key=peak_temp_key,
        average_temp_key=average_temp_key,
        temp_unit=temp_unit,
    )


def _required_key(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Eng06BurnLogAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _required_value(row: Mapping[str, Any], key: str, index: int) -> Any:
    if key not in row:
        raise Eng06BurnLogAdapterError(f"rows[{index}] missing key {key}")
    return row[key]


def _normalize_temp_unit(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Eng06BurnLogAdapterError("temp_unit must be a non-empty string")
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_TEMP_UNITS:
        supported = ", ".join(sorted(SUPPORTED_TEMP_UNITS))
        raise Eng06BurnLogAdapterError(
            f"temp_unit must be one of {supported}"
        )
    return normalized


def _parse_utc_day(raw: Any, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise Eng06BurnLogAdapterError(f"{label} must be a non-empty string")
    value = raw.strip()
    if len(value) == 10:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise Eng06BurnLogAdapterError(
                f"{label} must be ISO YYYY-MM-DD or full UTC Z"
            ) from exc
        return parsed_date.strftime("%Y-%m-%dT00:00:00Z")

    if not value.endswith("Z"):
        raise Eng06BurnLogAdapterError(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise Eng06BurnLogAdapterError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise Eng06BurnLogAdapterError(f"{label} must be UTC")
    if (
        parsed.hour != 0
        or parsed.minute != 0
        or parsed.second != 0
        or parsed.microsecond != 0
    ):
        raise Eng06BurnLogAdapterError(
            f"{label} must be day-aligned at 00:00:00Z"
        )
    return parsed.strftime("%Y-%m-%dT00:00:00Z")


def _non_negative_int(raw: Any, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise Eng06BurnLogAdapterError(
            f"{label} must be a non-negative integer"
        )
    return raw


def _finite_number(raw: Any, label: str, *, temp_unit: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise Eng06BurnLogAdapterError(f"{label} must be numeric")
    value = float(raw)
    if not math.isfinite(value):
        raise Eng06BurnLogAdapterError(f"{label} must be finite")
    if temp_unit == TEMP_UNIT_FAHRENHEIT:
        value = (value - 32.0) * 5.0 / 9.0
    elif temp_unit == TEMP_UNIT_KELVIN:
        value = value - 273.15
    return round(value, 1)


__all__ = [
    "DEFAULT_AVERAGE_TEMP_KEY",
    "DEFAULT_DAY_KEY",
    "DEFAULT_FIRE_EVENT_COUNT_KEY",
    "DEFAULT_PEAK_TEMP_KEY",
    "Eng06BurnLogAdapterError",
    "SUPPORTED_TEMP_UNITS",
    "TEMP_UNIT_CELSIUS",
    "TEMP_UNIT_FAHRENHEIT",
    "TEMP_UNIT_KELVIN",
    "build_eng06_burn_log",
    "normalize_burn_log_rows",
]
