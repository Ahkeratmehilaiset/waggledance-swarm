# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Provider-neutral ENG-01 price feed adapter.

This module deliberately does not fetch live data or handle API keys. It only
normalizes already-fetched hourly day-ahead price rows into the feed shape
consumed by :mod:`eng01_spot_electricity`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


PRICE_UNIT_EUR_PER_KWH = "EUR_per_kWh"
PRICE_UNIT_EUR_PER_MWH = "EUR_per_MWh"
DEFAULT_FEED_SOURCE = "provider_neutral_day_ahead_price_feed"


class Eng01PriceFeedAdapterError(ValueError):
    """Invalid caller input for ENG-01 price feed adaptation."""


def build_eng01_price_feed(
    rows: Iterable[Mapping[str, Any]],
    *,
    fetched_at_utc: str,
    horizon_start_utc: str,
    horizon_hours: int = 24,
    feed_source: str = DEFAULT_FEED_SOURCE,
    price_unit: str = PRICE_UNIT_EUR_PER_MWH,
    stale_threshold_hours: int = 12,
    hour_key: str = "hour_utc",
    price_key: str = "price",
) -> dict[str, Any]:
    """Build an ENG-01 solver feed from hourly price rows.

    The adapter preserves row order instead of sorting provider output. Duplicate
    or non-monotonic hours remain visible to the solver's fail-closed checks.
    """
    _parse_utc_hour(fetched_at_utc, "fetched_at_utc", require_hour=False)
    _parse_utc_hour(horizon_start_utc, "horizon_start_utc")
    horizon_hours = _positive_int(horizon_hours, "horizon_hours")
    stale_threshold_hours = _positive_int(
        stale_threshold_hours,
        "stale_threshold_hours",
    )
    feed_source = _safe_source_ref(feed_source)
    if price_unit not in {PRICE_UNIT_EUR_PER_KWH, PRICE_UNIT_EUR_PER_MWH}:
        raise Eng01PriceFeedAdapterError(
            "price_unit must be EUR_per_kWh or EUR_per_MWh"
        )

    normalized_prices = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Eng01PriceFeedAdapterError(f"rows[{index}] must be an object")
        hour_utc = _parse_utc_hour(str(row.get(hour_key, "")),
                                  f"rows[{index}].{hour_key}")
        raw_price = row.get(price_key)
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            raise Eng01PriceFeedAdapterError(
                f"rows[{index}].{price_key} must be numeric"
            )
        price_eur_per_kwh = float(raw_price)
        if price_unit == PRICE_UNIT_EUR_PER_MWH:
            price_eur_per_kwh = price_eur_per_kwh / 1000.0
        normalized_prices.append({
            "hour_utc": hour_utc,
            "price_eur_per_kwh": round(price_eur_per_kwh, 3),
        })

    return {
        "fetched_at_utc": fetched_at_utc,
        "horizon_start_utc": horizon_start_utc,
        "horizon_hours": horizon_hours,
        "feed_source": feed_source,
        "stale_threshold_hours": stale_threshold_hours,
        "prices_eur_per_kwh": normalized_prices,
    }


def _positive_int(raw: Any, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise Eng01PriceFeedAdapterError(f"{label} must be a positive integer")
    return raw


def _parse_utc_hour(
    value: str,
    label: str,
    *,
    require_hour: bool = True,
) -> str:
    if not value.endswith("Z"):
        raise Eng01PriceFeedAdapterError(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise Eng01PriceFeedAdapterError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise Eng01PriceFeedAdapterError(f"{label} must be UTC")
    if require_hour and (
        parsed.minute != 0 or parsed.second != 0 or parsed.microsecond != 0
    ):
        raise Eng01PriceFeedAdapterError(f"{label} must be hour-aligned UTC")
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_source_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Eng01PriceFeedAdapterError("feed_source must be a non-empty string")
    normalized = value.strip()
    lowered = normalized.lower()
    forbidden = (
        "credential:",
        "credentials:",
        "secret:",
        "token:",
        "password" + "=",
        "passwd" + "=",
        "api" + "_key=",
        "x-api-key",
        "credentials" + ".json",
        "token" + ".json",
        ".env",
        ".pem",
        ".key",
    )
    if any(marker in lowered for marker in forbidden):
        raise Eng01PriceFeedAdapterError(
            "feed_source must not contain secret material"
        )
    return normalized


__all__ = [
    "DEFAULT_FEED_SOURCE",
    "Eng01PriceFeedAdapterError",
    "PRICE_UNIT_EUR_PER_KWH",
    "PRICE_UNIT_EUR_PER_MWH",
    "build_eng01_price_feed",
]
