# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""ENG-01 spot electricity first-slice solver core.

Pure deterministic logic for the first operator-facing slice:
"fetch next 24h spot prices and return the 3 cheapest hours". This module
does not fetch live data, read credentials, write state, or call a network.
Callers provide an already-fetched feed-shaped mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


OK = "OK"
STALE_DATA_REFUSED = "STALE_DATA_REFUSED"
MISSING_HOUR_REFUSED = "MISSING_HOUR_REFUSED"
NON_MONOTONIC_HORIZON_REFUSED = "NON_MONOTONIC_HORIZON_REFUSED"
INVALID_PRICE_FEED_REFUSED = "INVALID_PRICE_FEED_REFUSED"


class Eng01SpotElectricityError(ValueError):
    """Invalid caller input for ENG-01 first-slice logic."""


@dataclass(frozen=True)
class Eng01FirstSliceResult:
    """Operator-facing ENG-01 advisory result or explicit refusal."""

    result_marker: str
    top_3_cheapest_hours_utc: tuple[dict[str, Any], ...]
    horizon_24h_min_price_eur_per_kwh: float | None = None
    horizon_24h_max_price_eur_per_kwh: float | None = None
    horizon_24h_avg_price_eur_per_kwh: float | None = None
    expected_savings_eur_per_kwh_vs_peak: float | None = None
    recommendation_freshness_ttl_minutes: int = 60
    refusal_reason: str | None = None
    missing_hours_utc: tuple[str, ...] = ()
    duplicate_hours_utc: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "result_marker": self.result_marker,
            "top_3_cheapest_hours_utc": list(self.top_3_cheapest_hours_utc),
            "recommendation_freshness_ttl_minutes":
                self.recommendation_freshness_ttl_minutes,
        }
        if self.horizon_24h_min_price_eur_per_kwh is not None:
            payload["horizon_24h_min_price_eur_per_kwh"] = (
                self.horizon_24h_min_price_eur_per_kwh
            )
        if self.horizon_24h_max_price_eur_per_kwh is not None:
            payload["horizon_24h_max_price_eur_per_kwh"] = (
                self.horizon_24h_max_price_eur_per_kwh
            )
        if self.horizon_24h_avg_price_eur_per_kwh is not None:
            payload["horizon_24h_avg_price_eur_per_kwh"] = (
                self.horizon_24h_avg_price_eur_per_kwh
            )
        if self.expected_savings_eur_per_kwh_vs_peak is not None:
            payload["expected_savings_eur_per_kwh_vs_peak"] = (
                self.expected_savings_eur_per_kwh_vs_peak
            )
        if self.refusal_reason is not None:
            payload["refusal_reason"] = self.refusal_reason
        if self.missing_hours_utc:
            payload["missing_hours_utc"] = list(self.missing_hours_utc)
        if self.duplicate_hours_utc:
            payload["duplicate_hours_utc"] = list(self.duplicate_hours_utc)
        return payload


@dataclass(frozen=True)
class _PricePoint:
    hour_utc: str
    price_eur_per_kwh: float


def recommend_top_3_cheapest_hours(
    feed: Mapping[str, Any],
    *,
    stale_threshold_hours: int | None = None,
) -> Eng01FirstSliceResult:
    """Return top-3 cheapest UTC hours or a deterministic refusal."""
    fetched_at = _parse_utc(str(feed.get("fetched_at_utc", "")),
                            "fetched_at_utc")
    horizon_start = _parse_utc(str(feed.get("horizon_start_utc", "")),
                               "horizon_start_utc")
    horizon_hours = _positive_int(feed.get("horizon_hours", 24),
                                  "horizon_hours")
    threshold = _positive_int(
        stale_threshold_hours
        if stale_threshold_hours is not None
        else feed.get("stale_threshold_hours", 12),
        "stale_threshold_hours",
    )

    feed_age_hours = (horizon_start - fetched_at).total_seconds() / 3600
    if feed_age_hours > threshold:
        return Eng01FirstSliceResult(
            result_marker=STALE_DATA_REFUSED,
            top_3_cheapest_hours_utc=(),
            refusal_reason=(
                f"feed_age_{int(feed_age_hours)}h_exceeds_threshold_"
                f"{threshold}h"
            ),
        )

    prices = _parse_prices(feed.get("prices_eur_per_kwh"))

    explicit_duplicates = tuple(str(value) for value in (
        feed.get("duplicate_hours_utc")
        if isinstance(feed.get("duplicate_hours_utc"), list)
        else ([feed["duplicate_hour_utc"]]
              if isinstance(feed.get("duplicate_hour_utc"), str) else [])
    ))
    duplicate_hours = explicit_duplicates or _duplicate_or_non_monotonic(prices)
    if duplicate_hours:
        return Eng01FirstSliceResult(
            result_marker=NON_MONOTONIC_HORIZON_REFUSED,
            top_3_cheapest_hours_utc=(),
            duplicate_hours_utc=tuple(sorted(set(duplicate_hours))),
            refusal_reason="non_monotonic_or_duplicate_hour",
        )

    explicit_missing = feed.get("missing_hours_utc")
    if isinstance(explicit_missing, list) and explicit_missing:
        missing_hours = tuple(str(value) for value in explicit_missing)
    else:
        missing_hours = _missing_hours(prices, horizon_start, horizon_hours)
    if missing_hours:
        return Eng01FirstSliceResult(
            result_marker=MISSING_HOUR_REFUSED,
            top_3_cheapest_hours_utc=(),
            missing_hours_utc=tuple(sorted(missing_hours)),
            refusal_reason="missing_hour_in_horizon",
        )

    if len(prices) != horizon_hours:
        return Eng01FirstSliceResult(
            result_marker=INVALID_PRICE_FEED_REFUSED,
            top_3_cheapest_hours_utc=(),
            refusal_reason=(
                f"expected_{horizon_hours}_prices_got_{len(prices)}"
            ),
        )

    ranked = sorted(prices, key=lambda item: (
        item.price_eur_per_kwh,
        item.hour_utc,
    ))
    top_3 = tuple(
        {
            "hour_utc": item.hour_utc,
            "price_eur_per_kwh": item.price_eur_per_kwh,
            "rank": rank,
        }
        for rank, item in enumerate(ranked[:3], start=1)
    )
    values = [item.price_eur_per_kwh for item in prices]
    min_price = min(values)
    max_price = max(values)
    return Eng01FirstSliceResult(
        result_marker=OK,
        top_3_cheapest_hours_utc=top_3,
        horizon_24h_min_price_eur_per_kwh=round(min_price, 3),
        horizon_24h_max_price_eur_per_kwh=round(max_price, 3),
        horizon_24h_avg_price_eur_per_kwh=round(sum(values) / len(values), 3),
        expected_savings_eur_per_kwh_vs_peak=round(max_price - min_price, 3),
    )


def _parse_prices(raw: Any) -> tuple[_PricePoint, ...]:
    if not isinstance(raw, list) or not raw:
        raise Eng01SpotElectricityError(
            "prices_eur_per_kwh must be a non-empty list"
        )
    prices: list[_PricePoint] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Eng01SpotElectricityError(
                f"prices_eur_per_kwh[{index}] must be an object"
            )
        hour_utc = str(item.get("hour_utc", ""))
        _parse_utc(hour_utc, f"prices_eur_per_kwh[{index}].hour_utc")
        price = item.get("price_eur_per_kwh")
        if not isinstance(price, (int, float)):
            raise Eng01SpotElectricityError(
                f"prices_eur_per_kwh[{index}].price_eur_per_kwh must be numeric"
            )
        prices.append(_PricePoint(
            hour_utc=hour_utc,
            price_eur_per_kwh=round(float(price), 3),
        ))
    return tuple(prices)


def _duplicate_or_non_monotonic(
    prices: tuple[_PricePoint, ...],
) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    previous: datetime | None = None
    non_monotonic: set[str] = set()
    for item in prices:
        current = _parse_utc(item.hour_utc, "hour_utc")
        if item.hour_utc in seen:
            duplicates.add(item.hour_utc)
        seen.add(item.hour_utc)
        if previous is not None and current <= previous:
            non_monotonic.add(item.hour_utc)
        previous = current
    return tuple(sorted(duplicates | non_monotonic))


def _missing_hours(
    prices: tuple[_PricePoint, ...],
    horizon_start: datetime,
    horizon_hours: int,
) -> tuple[str, ...]:
    actual = {item.hour_utc for item in prices}
    expected = {
        _format_utc_hour(horizon_start + timedelta(hours=offset))
        for offset in range(horizon_hours)
    }
    return tuple(sorted(expected - actual))


def _positive_int(raw: Any, label: str) -> int:
    if not isinstance(raw, int) or raw <= 0:
        raise Eng01SpotElectricityError(f"{label} must be a positive integer")
    return raw


def _parse_utc(value: str, label: str) -> datetime:
    if not value.endswith("Z"):
        raise Eng01SpotElectricityError(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise Eng01SpotElectricityError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise Eng01SpotElectricityError(f"{label} must be UTC")
    return parsed


def _format_utc_hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


__all__ = [
    "Eng01FirstSliceResult",
    "Eng01SpotElectricityError",
    "INVALID_PRICE_FEED_REFUSED",
    "MISSING_HOUR_REFUSED",
    "NON_MONOTONIC_HORIZON_REFUSED",
    "OK",
    "STALE_DATA_REFUSED",
    "recommend_top_3_cheapest_hours",
]
