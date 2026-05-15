# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""ENG-06 cottage fireplace first-slice solver core.

Pure deterministic logic for the first operator-facing slice:
"summarize the recent cottage fireplace burn log without weather forecast".
This module does not open SQLite, read credentials, write state, or call a
network. Callers provide already-fetched daily burn-log rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping


CASE_ID = "ENG-06__cottage_fireplace_advisor__cottage"

OK = "OK"
NO_FIRES_IN_HORIZON_REFUSED = "NO_FIRES_IN_HORIZON_REFUSED"
STALE_LOG_REFUSED = "STALE_LOG_REFUSED"
MISSING_DAY_REFUSED = "MISSING_DAY_REFUSED"
INVALID_LOG_FEED_REFUSED = "INVALID_LOG_FEED_REFUSED"
NON_MONOTONIC_HORIZON_REFUSED = "NON_MONOTONIC_HORIZON_REFUSED"


@dataclass(frozen=True)
class Eng06FireplaceSummary:
    """Operator-facing ENG-06 burn-log summary or explicit refusal."""

    result_marker: str
    fire_event_count_30d: int = 0
    days_with_fire: int = 0
    average_chimney_temp_c: float | None = None
    peak_chimney_temp_c: float | None = None
    burn_log_first_day_utc: str | None = None
    burn_log_last_day_utc: str | None = None
    recommendation_freshness_ttl_minutes: int = 60
    refusal_reason: str | None = None
    missing_days_utc: tuple[str, ...] = ()
    duplicate_days_utc: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "fire_event_count_30d": self.fire_event_count_30d,
            "days_with_fire": self.days_with_fire,
            "recommendation_freshness_ttl_minutes":
                self.recommendation_freshness_ttl_minutes,
        }
        if self.average_chimney_temp_c is not None:
            payload["average_chimney_temp_c"] = self.average_chimney_temp_c
        if self.peak_chimney_temp_c is not None:
            payload["peak_chimney_temp_c"] = self.peak_chimney_temp_c
        if self.burn_log_first_day_utc is not None:
            payload["burn_log_first_day_utc"] = self.burn_log_first_day_utc
        if self.burn_log_last_day_utc is not None:
            payload["burn_log_last_day_utc"] = self.burn_log_last_day_utc
        if self.refusal_reason is not None:
            payload["refusal_reason"] = self.refusal_reason
        if self.missing_days_utc:
            payload["missing_days_utc"] = list(self.missing_days_utc)
        if self.duplicate_days_utc:
            payload["duplicate_days_utc"] = list(self.duplicate_days_utc)
        return payload


@dataclass(frozen=True)
class _BurnLogDay:
    day_utc: str
    fire_event_count: int
    peak_chimney_temp_c: float
    average_chimney_temp_c: float


class _InvalidLogFeed(ValueError):
    """Internal parse refusal for malformed burn-log rows."""


def summarize_burn_log(
    burn_log: Iterable[Mapping[str, Any]],
    *,
    horizon_start_utc: str,
    horizon_end_utc: str,
    stale_threshold_hours: int | None = None,
) -> Eng06FireplaceSummary:
    """Return a deterministic fireplace burn-log summary or refusal."""
    try:
        horizon_start = _parse_utc_day(
            horizon_start_utc,
            "horizon_start_utc",
        )
        horizon_end = _parse_utc_day(horizon_end_utc, "horizon_end_utc")
        threshold = _optional_positive_int(
            stale_threshold_hours,
            "stale_threshold_hours",
        )
    except _InvalidLogFeed as exc:
        return _invalid(str(exc))

    if horizon_end < horizon_start:
        return Eng06FireplaceSummary(
            result_marker=NON_MONOTONIC_HORIZON_REFUSED,
            refusal_reason="horizon_end_before_horizon_start",
        )

    try:
        days = _parse_burn_log(burn_log)
    except _InvalidLogFeed as exc:
        return _invalid(str(exc))

    if not days:
        return Eng06FireplaceSummary(
            result_marker=NO_FIRES_IN_HORIZON_REFUSED,
            refusal_reason="no_burn_log_rows_in_horizon",
        )

    duplicate_or_non_monotonic = _duplicate_or_non_monotonic(days)
    if duplicate_or_non_monotonic:
        return Eng06FireplaceSummary(
            result_marker=NON_MONOTONIC_HORIZON_REFUSED,
            refusal_reason="non_monotonic_or_duplicate_day",
            duplicate_days_utc=duplicate_or_non_monotonic,
            burn_log_first_day_utc=days[0].day_utc,
            burn_log_last_day_utc=days[-1].day_utc,
        )

    outside_horizon = tuple(
        item.day_utc
        for item in days
        if _parse_utc_day(item.day_utc, "day_utc") < horizon_start
        or _parse_utc_day(item.day_utc, "day_utc") > horizon_end
    )
    if outside_horizon:
        return _invalid(
            "burn_log contains days outside requested horizon: "
            + ",".join(outside_horizon)
        )

    if threshold is not None:
        age_hours = (horizon_end - _parse_utc_day(
            days[-1].day_utc,
            "last_day_utc",
        )).total_seconds() / 3600
        if age_hours > threshold:
            return Eng06FireplaceSummary(
                result_marker=STALE_LOG_REFUSED,
                refusal_reason=(
                    f"log_age_{int(age_hours)}h_exceeds_threshold_"
                    f"{threshold}h"
                ),
                burn_log_first_day_utc=days[0].day_utc,
                burn_log_last_day_utc=days[-1].day_utc,
            )

    missing_days = _missing_days(days, horizon_start, horizon_end)
    if missing_days:
        return Eng06FireplaceSummary(
            result_marker=MISSING_DAY_REFUSED,
            refusal_reason="missing_day_in_horizon",
            missing_days_utc=missing_days,
            burn_log_first_day_utc=days[0].day_utc,
            burn_log_last_day_utc=days[-1].day_utc,
        )

    fire_days = tuple(item for item in days if item.fire_event_count > 0)
    if not fire_days:
        return Eng06FireplaceSummary(
            result_marker=NO_FIRES_IN_HORIZON_REFUSED,
            refusal_reason="no_fire_events_in_horizon",
            burn_log_first_day_utc=days[0].day_utc,
            burn_log_last_day_utc=days[-1].day_utc,
        )

    fire_event_count = sum(item.fire_event_count for item in fire_days)
    average_temp = sum(
        item.average_chimney_temp_c for item in fire_days
    ) / len(fire_days)

    return Eng06FireplaceSummary(
        result_marker=OK,
        fire_event_count_30d=fire_event_count,
        days_with_fire=len(fire_days),
        average_chimney_temp_c=round(average_temp, 1),
        peak_chimney_temp_c=round(
            max(item.peak_chimney_temp_c for item in fire_days),
            1,
        ),
        burn_log_first_day_utc=days[0].day_utc,
        burn_log_last_day_utc=days[-1].day_utc,
    )


def _parse_burn_log(
    burn_log: Iterable[Mapping[str, Any]],
) -> tuple[_BurnLogDay, ...]:
    try:
        raw_days = tuple(burn_log)
    except TypeError as exc:
        raise _InvalidLogFeed("burn_log must be an iterable of objects") from exc

    days: list[_BurnLogDay] = []
    for index, item in enumerate(raw_days):
        if not isinstance(item, Mapping):
            raise _InvalidLogFeed(f"burn_log[{index}] must be an object")
        day_utc = item.get("day_utc")
        if not isinstance(day_utc, str) or not day_utc:
            raise _InvalidLogFeed(f"burn_log[{index}].day_utc must be a string")
        _parse_utc_day(day_utc, f"burn_log[{index}].day_utc")

        days.append(_BurnLogDay(
            day_utc=day_utc,
            fire_event_count=_non_negative_int(
                item.get("fire_event_count"),
                f"burn_log[{index}].fire_event_count",
            ),
            peak_chimney_temp_c=_finite_number(
                item.get("peak_chimney_temp_c"),
                f"burn_log[{index}].peak_chimney_temp_c",
            ),
            average_chimney_temp_c=_finite_number(
                item.get("average_chimney_temp_c"),
                f"burn_log[{index}].average_chimney_temp_c",
            ),
        ))
    return tuple(days)


def _duplicate_or_non_monotonic(days: tuple[_BurnLogDay, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates_or_non_monotonic: set[str] = set()
    previous: datetime | None = None
    for item in days:
        current = _parse_utc_day(item.day_utc, "day_utc")
        if item.day_utc in seen:
            duplicates_or_non_monotonic.add(item.day_utc)
        seen.add(item.day_utc)
        if previous is not None and current <= previous:
            duplicates_or_non_monotonic.add(item.day_utc)
        previous = current
    return tuple(sorted(duplicates_or_non_monotonic))


def _missing_days(
    days: tuple[_BurnLogDay, ...],
    horizon_start: datetime,
    horizon_end: datetime,
) -> tuple[str, ...]:
    actual = {item.day_utc for item in days}
    expected = {
        _format_utc_day(horizon_start + timedelta(days=offset))
        for offset in range((horizon_end - horizon_start).days + 1)
    }
    return tuple(sorted(expected - actual))


def _non_negative_int(raw: Any, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise _InvalidLogFeed(f"{label} must be a non-negative integer")
    return raw


def _finite_number(raw: Any, label: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _InvalidLogFeed(f"{label} must be numeric")
    value = float(raw)
    if not math.isfinite(value):
        raise _InvalidLogFeed(f"{label} must be finite")
    return round(value, 1)


def _optional_positive_int(raw: Any, label: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise _InvalidLogFeed(f"{label} must be a positive integer")
    return raw


def _parse_utc_day(value: str, label: str) -> datetime:
    if not value.endswith("Z"):
        raise _InvalidLogFeed(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _InvalidLogFeed(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise _InvalidLogFeed(f"{label} must be UTC")
    if (
        parsed.hour != 0
        or parsed.minute != 0
        or parsed.second != 0
        or parsed.microsecond != 0
    ):
        raise _InvalidLogFeed(f"{label} must be day-aligned at 00:00:00Z")
    return parsed


def _format_utc_day(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")


def _invalid(reason: str) -> Eng06FireplaceSummary:
    return Eng06FireplaceSummary(
        result_marker=INVALID_LOG_FEED_REFUSED,
        refusal_reason=reason,
    )


__all__ = [
    "CASE_ID",
    "Eng06FireplaceSummary",
    "INVALID_LOG_FEED_REFUSED",
    "MISSING_DAY_REFUSED",
    "NON_MONOTONIC_HORIZON_REFUSED",
    "NO_FIRES_IN_HORIZON_REFUSED",
    "OK",
    "STALE_LOG_REFUSED",
    "summarize_burn_log",
]
