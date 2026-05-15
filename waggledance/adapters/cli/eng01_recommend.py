# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for ENG-01 cheapest-hour recommendations.

The CLI reads either an already-fetched hourly price JSON file or an
operator-selected HTTP JSON feed, normalizes it through the provider-neutral
ENG-01 feed adapter, runs the solver, and prints a JSON payload. It does not
store credentials or contain provider-specific URLs.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO
from urllib.parse import urlsplit

from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    PRICE_UNIT_EUR_PER_MWH,
    build_eng01_price_feed,
)
from waggledance.core.v3_13_0.eng01_price_feed_http_transport import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    Eng01PriceFeedTransport,
    fetch_price_feed_http_response,
)
from waggledance.core.v3_13_0.eng01_price_feed_response_parser import (
    parse_price_feed_response,
)
from waggledance.core.v3_13_0.eng01_spot_electricity import (
    recommend_top_3_cheapest_hours,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ENG-01 cheapest-hour recommendation from JSON",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        help="Path to local hourly price JSON",
    )
    source.add_argument(
        "--url",
        help="Operator-selected HTTP JSON price feed URL",
    )
    parser.add_argument(
        "--price-unit",
        default=None,
        choices=["EUR_per_MWh", "EUR_per_kWh"],
        help="Override price unit declared by the input JSON or URL mode",
    )
    parser.add_argument(
        "--headers-file",
        default=None,
        help="JSON object of HTTP headers for --url mode; credentials refused",
    )
    parser.add_argument(
        "--timeout-seconds",
        default=DEFAULT_TIMEOUT_SECONDS,
        type=float,
        help="HTTP timeout for --url mode",
    )
    parser.add_argument(
        "--max-response-bytes",
        default=DEFAULT_MAX_RESPONSE_BYTES,
        type=int,
        help="Maximum HTTP response size for --url mode",
    )
    parser.add_argument(
        "--rows-path",
        default="",
        help="Dot- or comma-separated JSON path to rows in --url mode",
    )
    parser.add_argument(
        "--hour-key",
        default="hour_utc",
        help="Row key containing the UTC hour in --url mode",
    )
    parser.add_argument(
        "--price-key",
        default="price",
        help="Row key containing the price in --url mode",
    )
    parser.add_argument(
        "--fetched-at-utc",
        default=None,
        help="Override fetched_at_utc for --url mode",
    )
    parser.add_argument(
        "--horizon-start-utc",
        default=None,
        help="Override horizon_start_utc; defaults to first parsed row hour",
    )
    parser.add_argument(
        "--horizon-hours",
        default=24,
        type=int,
        help="Horizon hours for --url mode",
    )
    parser.add_argument(
        "--feed-source",
        default=None,
        help="Feed source label for --url mode",
    )
    parser.add_argument(
        "--stale-threshold-hours",
        default=12,
        type=int,
        help="Freshness threshold hours for --url mode",
    )
    parser.add_argument(
        "--allow-credential-headers",
        action="store_true",
        default=False,
        help="Allow credential-like headers in --url mode",
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


def run_from_url(
    *,
    url: str,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    rows_path: tuple[str, ...] = (),
    hour_key: str = "hour_utc",
    price_key: str = "price",
    fetched_at_utc: str | None = None,
    horizon_start_utc: str | None = None,
    horizon_hours: int = 24,
    feed_source: str | None = None,
    price_unit: str = PRICE_UNIT_EUR_PER_MWH,
    stale_threshold_hours: int = 12,
    allow_credential_headers: bool = False,
    transport: Eng01PriceFeedTransport | None = None,
) -> dict[str, Any]:
    response = fetch_price_feed_http_response(
        url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        allow_credential_headers=allow_credential_headers,
        transport=transport,
    )
    rows = parse_price_feed_response(
        response.body,
        content_type=response.content_type,
        rows_path=rows_path,
        hour_key=hour_key,
        price_key=price_key,
    )
    payload = {
        "rows": rows,
        "fetched_at_utc": fetched_at_utc or _utc_now_timestamp(),
        "horizon_start_utc": horizon_start_utc or rows[0][hour_key],
        "horizon_hours": horizon_hours,
        "feed_source": feed_source or _derive_feed_source(url),
        "price_unit": price_unit,
        "stale_threshold_hours": stale_threshold_hours,
        "hour_key": hour_key,
        "price_key": price_key,
    }
    return run_from_payload(payload)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: Eng01PriceFeedTransport | None = None,
) -> int:
    args = parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        if args.input is not None:
            _ensure_url_only_args_are_absent(args)
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("input JSON must be an object")
            if args.price_unit is not None:
                payload = {**payload, "price_unit": args.price_unit}
            result = run_from_payload(payload)
        else:
            result = run_from_url(
                url=args.url,
                headers=_load_headers_file(args.headers_file),
                timeout_seconds=args.timeout_seconds,
                max_response_bytes=args.max_response_bytes,
                rows_path=_parse_rows_path(args.rows_path),
                hour_key=args.hour_key,
                price_key=args.price_key,
                fetched_at_utc=args.fetched_at_utc,
                horizon_start_utc=args.horizon_start_utc,
                horizon_hours=args.horizon_hours,
                feed_source=args.feed_source,
                price_unit=args.price_unit or PRICE_UNIT_EUR_PER_MWH,
                stale_threshold_hours=args.stale_threshold_hours,
                allow_credential_headers=args.allow_credential_headers,
                transport=transport,
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


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"input JSON must contain {key}")
    return value


def _load_headers_file(path: str | None) -> dict[str, str] | None:
    if path is None:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("headers file must contain a JSON object")
    headers = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("headers file must contain string keys and values")
        headers[key] = value
    return headers


def _parse_rows_path(raw: str) -> tuple[str, ...]:
    if raw == "":
        return ()
    separator = "," if "," in raw else "."
    parts = tuple(part.strip() for part in raw.split(separator))
    if any(not part for part in parts):
        raise ValueError("rows-path entries must be non-empty")
    return parts


def _ensure_url_only_args_are_absent(args: argparse.Namespace) -> None:
    if args.headers_file is not None:
        raise ValueError("--headers-file requires --url")
    if args.rows_path:
        raise ValueError("--rows-path requires --url")
    if args.fetched_at_utc is not None:
        raise ValueError("--fetched-at-utc requires --url")
    if args.horizon_start_utc is not None:
        raise ValueError("--horizon-start-utc requires --url")
    if args.feed_source is not None:
        raise ValueError("--feed-source requires --url")
    if args.allow_credential_headers:
        raise ValueError("--allow-credential-headers requires --url")


def _utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_feed_source(url: str) -> str:
    host = urlsplit(url).hostname or "unknown"
    chars = [
        char.lower() if char.isalnum() else "_"
        for char in host
    ]
    normalized = "".join(chars).strip("_") or "unknown"
    return f"operator_selected_{normalized}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
