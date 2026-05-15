# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for ENG-06 cottage fireplace burn-log summaries.

The CLI reads an already-fetched local burn-log JSON file, runs the pure
ENG-06 solver core, and prints a JSON payload. It does not open SQLite,
fetch weather forecasts, store credentials, write state, or control devices.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from waggledance.core.v3_13_0.eng06_burn_log_adapter import (
    DEFAULT_AVERAGE_TEMP_KEY,
    DEFAULT_DAY_KEY,
    DEFAULT_FIRE_EVENT_COUNT_KEY,
    DEFAULT_PEAK_TEMP_KEY,
    TEMP_UNIT_CELSIUS,
    normalize_burn_log_rows,
)
from waggledance.core.v3_13_0.eng06_fireplace_advisor import (
    summarize_burn_log,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ENG-06 fireplace burn-log summary from JSON",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to local fireplace burn-log JSON",
    )
    parser.add_argument(
        "--horizon-start-utc",
        default=None,
        help="Override horizon_start_utc; defaults to payload or first row day",
    )
    parser.add_argument(
        "--horizon-end-utc",
        default=None,
        help="Override horizon_end_utc; defaults to payload or last row day",
    )
    parser.add_argument(
        "--stale-threshold-hours",
        default=None,
        type=int,
        help="Override stale_threshold_hours for the burn-log solver",
    )
    parser.add_argument(
        "--day-key",
        default=DEFAULT_DAY_KEY,
        help="Input row key containing the burn-log day",
    )
    parser.add_argument(
        "--fire-count-key",
        default=DEFAULT_FIRE_EVENT_COUNT_KEY,
        help="Input row key containing the daily fire event count",
    )
    parser.add_argument(
        "--peak-temp-key",
        default=DEFAULT_PEAK_TEMP_KEY,
        help="Input row key containing the daily peak chimney temperature",
    )
    parser.add_argument(
        "--average-temp-key",
        default=DEFAULT_AVERAGE_TEMP_KEY,
        help="Input row key containing the daily average chimney temperature",
    )
    parser.add_argument(
        "--temp-unit",
        default=TEMP_UNIT_CELSIUS,
        help="Temperature unit for input rows: celsius, fahrenheit, or kelvin",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Print indented JSON instead of compact JSON",
    )
    return parser.parse_args(argv)


def run_from_payload(
    payload: dict[str, Any],
    *,
    horizon_start_utc: str | None = None,
    horizon_end_utc: str | None = None,
    stale_threshold_hours: int | None = None,
    day_key: str = DEFAULT_DAY_KEY,
    fire_count_key: str = DEFAULT_FIRE_EVENT_COUNT_KEY,
    peak_temp_key: str = DEFAULT_PEAK_TEMP_KEY,
    average_temp_key: str = DEFAULT_AVERAGE_TEMP_KEY,
    temp_unit: str = TEMP_UNIT_CELSIUS,
) -> dict[str, Any]:
    burn_log = payload.get("burn_log")
    if not isinstance(burn_log, list):
        raise ValueError("input JSON must contain burn_log list")
    normalized_burn_log = normalize_burn_log_rows(
        burn_log,
        day_key=day_key,
        fire_count_key=fire_count_key,
        peak_temp_key=peak_temp_key,
        average_temp_key=average_temp_key,
        temp_unit=temp_unit,
    )
    resolved_horizon_start = (
        horizon_start_utc
        or _optional_str(payload, "horizon_start_utc")
        or _burn_log_day(normalized_burn_log, 0, "first burn_log row")
    )
    resolved_horizon_end = (
        horizon_end_utc
        or _optional_str(payload, "horizon_end_utc")
        or _burn_log_day(normalized_burn_log, -1, "last burn_log row")
    )
    resolved_stale_threshold = (
        stale_threshold_hours
        if stale_threshold_hours is not None
        else payload.get("stale_threshold_hours")
    )
    result = summarize_burn_log(
        normalized_burn_log,
        horizon_start_utc=resolved_horizon_start,
        horizon_end_utc=resolved_horizon_end,
        stale_threshold_hours=resolved_stale_threshold,
    ).to_payload()
    result["horizon_start_utc"] = resolved_horizon_start
    result["horizon_end_utc"] = resolved_horizon_end
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input JSON must be an object")
        result = run_from_payload(
            payload,
            horizon_start_utc=args.horizon_start_utc,
            horizon_end_utc=args.horizon_end_utc,
            stale_threshold_hours=args.stale_threshold_hours,
            day_key=args.day_key,
            fire_count_key=args.fire_count_key,
            peak_temp_key=args.peak_temp_key,
            average_temp_key=args.average_temp_key,
            temp_unit=args.temp_unit,
        )
    except Exception as exc:
        print(
            json.dumps({
                "result_marker": "INVALID_INPUT_REFUSED",
                "error": str(exc),
            }, sort_keys=True),
            file=err,
        )
        return 2

    print(
        json.dumps(
            result,
            indent=2 if args.pretty else None,
            sort_keys=True,
        ),
        file=out,
    )
    return 0


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _burn_log_day(burn_log: list[Any], index: int, label: str) -> str:
    if not burn_log:
        raise ValueError("burn_log must be non-empty when horizon is omitted")
    row = burn_log[index]
    if not isinstance(row, Mapping):
        raise ValueError(f"{label} must be an object")
    day_utc = row.get("day_utc")
    if not isinstance(day_utc, str) or not day_utc.strip():
        raise ValueError(f"{label}.day_utc must be a non-empty string")
    return day_utc.strip()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
