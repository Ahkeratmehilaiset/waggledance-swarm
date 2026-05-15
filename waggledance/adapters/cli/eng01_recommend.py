# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Offline CLI for ENG-01 cheapest-hour recommendations.

The CLI reads an already-fetched hourly price JSON file, normalizes it through
the provider-neutral ENG-01 feed adapter, runs the solver, and prints a JSON
payload. It performs no live network calls and does not read credentials.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    PRICE_UNIT_EUR_PER_MWH,
    build_eng01_price_feed,
)
from waggledance.core.v3_13_0.eng01_spot_electricity import (
    recommend_top_3_cheapest_hours,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ENG-01 cheapest-hour recommendation from local JSON",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to local hourly price JSON; no live fetch is performed",
    )
    parser.add_argument(
        "--price-unit",
        default=None,
        choices=["EUR_per_MWh", "EUR_per_kWh"],
        help="Override price unit declared by the input JSON",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Print indented JSON instead of compact JSON",
    )
    return parser.parse_args(argv)


def run_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("input JSON must contain rows list")
    feed = build_eng01_price_feed(
        rows,
        fetched_at_utc=_required_str(payload, "fetched_at_utc"),
        horizon_start_utc=_required_str(payload, "horizon_start_utc"),
        horizon_hours=payload.get("horizon_hours", 24),
        feed_source=str(
            payload.get("feed_source", "operator_local_price_file")
        ),
        price_unit=str(payload.get("price_unit", PRICE_UNIT_EUR_PER_MWH)),
        stale_threshold_hours=payload.get("stale_threshold_hours", 12),
        hour_key=str(payload.get("hour_key", "hour_utc")),
        price_key=str(payload.get("price_key", "price")),
    )
    result = recommend_top_3_cheapest_hours(feed)
    output = result.to_payload()
    output["feed_source"] = feed["feed_source"]
    output["horizon_start_utc"] = feed["horizon_start_utc"]
    output["horizon_hours"] = feed["horizon_hours"]
    return output


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
        if args.price_unit is not None:
            payload = {**payload, "price_unit": args.price_unit}
        result = run_from_payload(payload)
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


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"input JSON must contain {key}")
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
