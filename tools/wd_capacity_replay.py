#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Replay sanitized quota fixtures and estimate a pool's reserved headroom.

Run python -m tools.wd_capacity_replay FIXTURE.json. The fixture supplies an
explicit offset-aware as_of timestamp and a records array. Each record has
family, pool (a fixture label or null), window, used_percent, reset_at,
source, observed_at, confidence (verified, nominal, unknown, or suspect),
and optional binding_verified (defaults to false). Five-hour and seven-day
windows use five_hour and seven_day. Other window IDs need duration_minutes.

An optional reservations array supplies family, pool, window and separate
review_percent, renewal_percent and in_flight_percent values. All amounts
are percentage points of that window. A seven-day window has a 15-point
minimum for review plus renewal. Other windows require an explicit reserve.
An omitted reservation describes no in-flight work in the fixture.

The output reports each window's reserve and the tightest window's limits
and ramp per pool. A null verified_allocatable_percent means unknown.
Verification flags describe hypothetical fixture evidence. The tool reads
one JSON file, returns JSON to stdout, and grants no execution authority.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise
import json
from pathlib import Path
from typing import Any


WINDOW_MINUTES = {"five_hour": 300, "seven_day": 10080}
DEFAULT_MAX_AGE = timedelta(minutes=15)
WEEKLY_REVIEW_RESERVE = 15.0
RAMP_ORDER = ("normal", "filler-only", "start-nothing")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _instant(value: object, name: str) -> datetime:
    try:
        instant = datetime.fromisoformat(_text(value, name))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp with an offset") from exc
    if instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return instant.astimezone(timezone.utc)


def _percent(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise ValueError(f"{name} must be a number in [0, 100]")
    return float(value)


def _array(fixture: dict, name: str, *, required: bool = False) -> list[dict]:
    value = fixture.get(name) if required else fixture.get(name, [])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{name} must be an array of objects")
    return value


@dataclass(frozen=True)
class WindowKey:
    family: str
    pool: str | None
    window: str

    @classmethod
    def parse(cls, row: dict) -> WindowKey:
        pool = row.get("pool")
        return cls(
            family=_text(row.get("family"), "family"),
            pool=None if pool is None else _text(pool, "pool"),
            window=_text(row.get("window"), "window"),
        )


@dataclass(frozen=True)
class Sample:
    key: WindowKey
    duration: timedelta
    used: float
    reset: datetime
    observed: datetime
    source: str
    confidence: str
    binding_verified: bool

    @classmethod
    def parse(cls, row: dict) -> Sample:
        key = WindowKey.parse(row)
        minutes = row.get("duration_minutes", WINDOW_MINUTES.get(key.window))
        if type(minutes) is not int or not 1 <= minutes <= 525600:
            raise ValueError("duration_minutes must be an integer in [1, 525600]")
        confidence = _text(row.get("confidence"), "confidence")
        if confidence not in ("verified", "nominal", "unknown", "suspect"):
            raise ValueError("invalid confidence")
        binding = row.get("binding_verified", False)
        if type(binding) is not bool:
            raise ValueError("binding_verified must be a boolean")
        return cls(
            key=key,
            duration=timedelta(minutes=minutes),
            used=_percent(row.get("used_percent"), "used_percent"),
            reset=_instant(row.get("reset_at"), "reset_at"),
            observed=_instant(row.get("observed_at"), "observed_at"),
            source=_text(row.get("source"), "source"),
            confidence=confidence,
            binding_verified=binding,
        )


@dataclass(frozen=True)
class Reservation:
    review_percent: float
    renewal_percent: float
    in_flight_percent: float

    @classmethod
    def parse(cls, row: dict) -> Reservation:
        return cls(
            review_percent=_percent(row.get("review_percent"), "review_percent"),
            renewal_percent=_percent(row.get("renewal_percent"), "renewal_percent"),
            in_flight_percent=_percent(row.get("in_flight_percent"), "in_flight_percent"),
        )


def _history_issues(samples: list[Sample]) -> set[str]:
    # Retain anomalies in the current reset window. A later on-time rollover
    # can provide a new baseline without inheriting earlier meter problems.
    issues = set()
    for previous, current in pairwise(samples):
        if previous.reset != current.reset:
            if current.observed >= previous.reset:
                issues.clear()
            else:
                issues.add("unannounced_reset")
        if current.observed == previous.observed and (
            current.reset != previous.reset or current.used != previous.used
        ):
            issues.add("conflicting_observations")
        if previous.reset == current.reset and current.used < previous.used:
            issues.add("meter_regression")
    return issues


def _window(
    samples: list[Sample], reservation: Reservation | None, as_of: datetime,
    unverified_family_binding: bool,
) -> dict[str, Any]:
    samples.sort(key=lambda sample: (sample.observed, sample.reset, sample.used, sample.source))
    latest = samples[-1]
    # At a tied timestamp use the tightest reported cap for the nominal view.
    latest_peers = [sample for sample in samples if sample.observed == latest.observed]
    nominal = 100.0 - max(sample.used for sample in latest_peers)
    remaining_time = latest.reset - as_of
    issues = _history_issues(samples)
    if unverified_family_binding:
        issues.add("unverified_pool_binding")
    if any(sample.confidence != "verified" for sample in latest_peers):
        issues.add("unverified_observation")
    if as_of - latest.observed > DEFAULT_MAX_AGE:
        issues.add("stale_observation")
    if remaining_time <= timedelta(0):
        issues.add("reset_passed")
    if any(
        sample.duration != latest.duration or not timedelta(0) < sample.reset - sample.observed <= sample.duration
        for sample in latest_peers
    ):
        issues.add("invalid_window_bounds")

    in_flight = 0.0 if reservation is None else reservation.in_flight_percent
    review_and_renewal = None if reservation is None else reservation.review_percent + reservation.renewal_percent
    if latest.duration == timedelta(minutes=WINDOW_MINUTES["seven_day"]):
        review_and_renewal = max(WEEKLY_REVIEW_RESERVE, review_and_renewal or 0.0)
    reserve = None if review_and_renewal is None else review_and_renewal + in_flight
    if reserve is None:
        issues.add("review_reservation_unknown")

    # The label is a time policy. Eligibility additionally requires the
    # numerical allocatable budget, and unknown budgets always stop admission.
    allocatable = None if issues else max(0.0, nominal - reserve)
    if allocatable is None or allocatable == 0 or remaining_time <= timedelta(minutes=20):
        ramp = "start-nothing"
    elif remaining_time <= latest.duration / 4:
        ramp = "filler-only"
    else:
        ramp = "normal"
    return {
        "window": latest.key.window,
        "duration_minutes": latest.duration // timedelta(minutes=1),
        "reset_at": latest.reset.isoformat(),
        "observed_at": latest.observed.isoformat(),
        "sources": sorted({sample.source for sample in latest_peers}),
        "nominal_remaining_percent": nominal,
        "verified_allocatable_percent": allocatable,
        "review_and_renewal_percent": review_and_renewal,
        "in_flight_percent": in_flight,
        "reserve_percent": reserve,
        "ramp": ramp,
        "issues": sorted(issues),
    }


def replay(fixture: dict) -> dict[str, Any]:
    """Replay only samples at or before the fixture's as_of timestamp."""
    if not isinstance(fixture, dict):
        raise ValueError("fixture must be an object")
    as_of = _instant(fixture.get("as_of"), "as_of")
    reservations = {}
    for row in _array(fixture, "reservations"):
        key = WindowKey.parse(row)
        if key in reservations:
            raise ValueError(f"duplicate reservation for {key}")
        reservations[key] = Reservation.parse(row)

    windows: dict[WindowKey, list[Sample]] = defaultdict(list)
    unverified_families = set()
    ignored = 0
    for row in _array(fixture, "records", required=True):
        sample = Sample.parse(row)
        if sample.observed > as_of:
            ignored += 1
            continue
        windows[sample.key].append(sample)
        if sample.key.pool is None or not sample.binding_verified:
            # An unbound subject can consume the same family-wide pool. The
            # fixture cannot prove independence by assigning different labels.
            unverified_families.add(sample.key.family)

    pools = defaultdict(list)
    for key, samples in windows.items():
        pools[key.family, key.pool].append(
            _window(samples, reservations.get(key), as_of, key.family in unverified_families)
        )
    estimates = []
    for (family, pool), reports in sorted(pools.items(), key=lambda row: (row[0][0], row[0][1] or "")):
        reports.sort(key=lambda report: report["window"])
        allocatable = [report["verified_allocatable_percent"] for report in reports]
        estimates.append({
            "family": family,
            "pool": pool,
            "nominal_remaining_percent": min(report["nominal_remaining_percent"] for report in reports),
            "verified_allocatable_percent": None if None in allocatable else min(allocatable),
            "ramp": max((report["ramp"] for report in reports), key=RAMP_ORDER.index),
            "windows": reports,
        })
    return {
        "schema": "wd.capacity-replay.v1",
        "as_of": as_of.isoformat(),
        "execution_allowed": False,
        "future_records_ignored": ignored,
        "pools": estimates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    try:
        report = replay(json.loads(args.fixture.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
