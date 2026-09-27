#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Read-only capacity pacer (lane profile switching PR-5): pace each quota window to its reset.

A subscription quota that resets before it is used is capacity lost; one that
runs out before it resets stalls the lanes that share it. For every quota
window the pacer answers one question - at the measured burn rate, where will
usage stand when the window resets? - and turns the answers into a per-lane
shadow recommendation within the signed catalog:

* ``raise``: every long window of the lane's pool is forecast well under its
  limit and no short window blocks - a stronger allowed profile would use
  capacity that otherwise expires;
* ``lower``: some window of the pool is forecast to run out before it resets;
* ``same``: on pace;
* ``park`` with a reason: the pacer cannot or may not recommend a change (unknown
  or stale measurement, reviewer floor, lane at its floor, target not approved).

Rules that keep it honest:

* Measured, never assumed: the burn rate comes from the observer's stored
  samples inside the same window instance (same reset time). Too few samples or
  too short a span gives ``rate_unknown`` - never an estimate.
* Short windows (five hours) only block or lower; they never justify a raise.
* One step per pool per evaluation: only the highest-priority eligible lane of a
  pool is recommended to raise, so the next measurement shows its effect.
* Work modes change only the priority order: ``production`` (reviewer > lead >
  producer), ``planning`` (the same order, all lanes eligible to raise) and
  ``conserve`` (producers lower first; nothing raises).
* A pool the observer does not measure (Grok today) is reported as
  ``capacity_unobserved``; nothing is inferred for it.

Nothing here launches, stops, writes or signals anything. Every result carries
``execution_allowed: false``. The planner (D5) and the executor (D4) remain the
only path to a relaunch, and only in a signed, non-shadow mode.

See docs/BRIDGE_CAPACITY_PACING.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, load_catalog  # noqa: E402

SCHEMA = "wd.capacity-pacing.v1"
WORK_MODES = ("production", "planning", "conserve")
# Durations the observer does not supply for Claude windows, by window name.
KNOWN_WINDOW_MINUTES = {"five_hour": 300, "seven_day": 10080}
SHORT_WINDOW_MINUTES = 24 * 60          # a window shorter than a day only blocks or lowers
MIN_RATE_SPAN_SECONDS = 1800            # a burn rate needs samples at least 30 min apart
MAX_SAMPLE_AGE_SECONDS = 900            # the newest sample must be this fresh to pace at all
UNDERUSED_FORECAST_PERCENT = 70.0       # forecast at reset at or below this: capacity expires unused
OVERRUN_FORECAST_PERCENT = 95.0         # forecast at reset at or above this: the pool runs out
MAX_HISTORY_ROWS = 20000
ROLE_PRIORITY = {"reviewer": 0, "lead": 1, "producer": 2}


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _number(value: Any) -> bool:
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def read_samples(store: str | Path, *, limit: int = MAX_HISTORY_ROWS) -> list[dict]:
    """Quota-window samples from the observer store, read-only and without touching a WAL.

    Each sample is ``{provider, limit_id, window, used_percent, resets_at,
    duration_minutes, observed_at}``. Rows that do not parse are skipped; a store
    that cannot be read safely raises ``ValueError``.
    """
    from tools.bridge_capacity_collector import quota_details
    path = Path(store)
    with path.open("rb") as source:
        header = source.read(20)
    if header[:16] != b"SQLite format 3\x00":
        raise ValueError("not an SQLite observer store")
    if 2 in header[18:20]:
        raise ValueError("WAL store is unsupported without a read-only snapshot")
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "observations" not in tables:
                raise ValueError("not an observer store")
            rows = db.execute("SELECT data FROM observations ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error as exc:
        # claude-rco-1 B1: a malformed but real store is unreadable, reported like any other refusal.
        raise ValueError(f"observer store unreadable: {exc.__class__.__name__}") from None
    samples = []
    for (raw,) in rows:
        try:
            row = json.loads(raw)
            if not isinstance(row, dict) or row.get("reason") == "collection_failed":
                continue
            observed = _utc(row.get("observed_at"))
            if observed is None or row.get("provider") not in ("codex", "claude"):
                continue
            _, windows = quota_details(dict(row, freshness="fresh"), observed)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        for window in windows:
            used, reset = window.get("used_percent"), window.get("resets_at")
            if not (_number(used) and _number(reset)) or used < 0:
                continue
            duration = window.get("window_duration_minutes") or KNOWN_WINDOW_MINUTES.get(window.get("name"))
            samples.append({"provider": row["provider"], "limit_id": window.get("limit_id"),
                            "window": window.get("name"), "used_percent": float(used),
                            "resets_at": float(reset), "duration_minutes": duration,
                            "observed_at": observed})
    return samples


def pace_windows(samples: list[dict], *, now: datetime) -> dict:
    """Pace every (provider, limit_id, window) from its current window instance only."""
    groups: dict[tuple, list[dict]] = {}
    for sample in samples:
        key = (sample["provider"], sample["limit_id"], sample["window"])
        groups.setdefault(key, []).append(sample)
    paced = {}
    for key, rows in groups.items():
        newest = max(rows, key=lambda r: r["observed_at"])
        # The current instance is the newest sample's reset time; older instances are other windows.
        current = sorted((r for r in rows if r["resets_at"] == newest["resets_at"]), key=lambda r: r["observed_at"])
        entry = {"provider": key[0], "limit_id": key[1], "window": key[2],
                 "used_percent": newest["used_percent"], "resets_at": newest["resets_at"],
                 "duration_minutes": newest["duration_minutes"],
                 "observed_at": newest["observed_at"].isoformat(), "samples": len(current),
                 "rate_percent_per_hour": None, "hours_to_reset": None, "forecast_percent_at_reset": None,
                 "short": None, "verdict": "unknown", "reason": None}
        paced["/".join(str(k) for k in key)] = entry
        age = (now - newest["observed_at"]).total_seconds()
        hours_left = (newest["resets_at"] - now.timestamp()) / 3600
        duration = newest["duration_minutes"]
        entry["short"] = None if duration is None else duration < SHORT_WINDOW_MINUTES
        if not 0 <= age <= MAX_SAMPLE_AGE_SECONDS:
            entry["reason"] = "measurement_stale"
            continue
        if hours_left <= 0:
            entry["reason"] = "window_already_reset"
            continue
        if duration is None:
            entry["reason"] = "window_duration_unknown"
            continue
        entry["hours_to_reset"] = round(hours_left, 3)
        if newest["used_percent"] >= 100:
            entry.update(verdict="exhausted", forecast_percent_at_reset=newest["used_percent"])
            continue
        first, last = current[0], current[-1]
        span = (last["observed_at"] - first["observed_at"]).total_seconds()
        # Lead review of #1741: EVERY consecutive pair must be non-decreasing inside one
        # window instance. A dip anywhere (20 -> 80 -> 21) means the counter is not a
        # usage counter we can trust, so there is no rate - never a first/last average.
        falls = any(later["used_percent"] < earlier["used_percent"]
                    for earlier, later in zip(current, current[1:]))
        if span < MIN_RATE_SPAN_SECONDS or falls:
            entry["reason"] = "rate_unknown"
            continue
        rate = (last["used_percent"] - first["used_percent"]) / (span / 3600)
        forecast = newest["used_percent"] + rate * hours_left
        entry.update(rate_percent_per_hour=round(rate, 4), forecast_percent_at_reset=round(forecast, 2))
        if forecast >= OVERRUN_FORECAST_PERCENT:
            entry["verdict"] = "overrun"
        elif forecast <= UNDERUSED_FORECAST_PERCENT:
            entry["verdict"] = "underused"
        else:
            entry["verdict"] = "on_pace"
    return paced


def _role(catalog: dict, lane: str) -> str:
    if catalog["lanes"][lane]["reviewer"]:
        return "reviewer"
    return "lead" if lane == "codex-lead-1" else "producer"


def recommend(catalog: dict, paced: dict, current_profiles: dict, *, work_mode: str = "production") -> dict:
    """Per-lane shadow recommendation from the paced windows; see the module docstring."""
    if work_mode not in WORK_MODES:
        raise ValueError(f"work_mode must be one of {WORK_MODES}")
    profiles = catalog["capacity_policy"]["profiles"]
    lanes = sorted(catalog["lanes"], key=lambda lane: (ROLE_PRIORITY[_role(catalog, lane)], lane))
    if work_mode == "conserve":
        lanes.reverse()  # producers yield first
    result: dict[str, dict] = {}
    raised_pools: set[str] = set()
    for lane in lanes:
        spec = catalog["lanes"][lane]
        allowed = spec["allowed_profiles"]
        current = current_profiles.get(lane) if isinstance(current_profiles, dict) else None
        entry = {"lane": lane, "role": _role(catalog, lane), "current_profile": current, "target_profile": None,
                 "verdict": "park", "reasons": [], "blocked_by": [], "windows": []}
        result[lane] = entry
        if current not in allowed:
            entry["reasons"].append("current_profile_unverified")
            continue
        profile = profiles[current]
        pool = f"{profile['provider']}/{profile['account_pool']}"
        keys = [f"{profile['provider']}/{limit['id']}/{window}" for limit in profile["limits"]
                for window in limit["windows"]]
        windows = [paced.get(key) for key in keys]
        entry["windows"] = keys
        missing = [key for key, window in zip(keys, windows) if window is None]
        unknown = [key for key, window in zip(keys, windows)
                   if window is not None and window["verdict"] == "unknown"]
        if missing or unknown:
            entry["reasons"].append("capacity_unknown")
            entry["blocked_by"] = missing + unknown
            continue
        index = allowed.index(current)
        overrun = [w for w in windows if w["verdict"] in ("overrun", "exhausted")]
        if overrun or work_mode == "conserve" and entry["role"] == "producer":
            why = "pool_overrun_before_reset" if overrun else "work_mode_conserve"
            if spec["reviewer"]:
                entry["reasons"] += [why, "reviewer_never_lowered"]
            elif index >= spec["floor"]:
                entry["reasons"] += [why, "at_floor"]
            else:
                entry.update(verdict="lower", target_profile=allowed[index + 1], reasons=[why])
            entry["blocked_by"] = [f"{w['provider']}/{w['limit_id']}/{w['window']}" for w in overrun]
            continue
        long_windows = [w for w in windows if not w["short"]]
        if (work_mode != "conserve" and long_windows and all(w["verdict"] == "underused" for w in long_windows)
                and index > 0):
            if pool in raised_pools:
                entry.update(verdict="same", reasons=["raise_queued_one_step_per_pool"])
                continue
            target = allowed[index - 1]
            if profiles[target].get("approved") is not True:
                # Shown, but it does not take the pool's single raise slot: it cannot happen.
                entry.update(target_profile=target, reasons=["capacity_would_expire_unused", "target_not_approved"])
                continue
            entry.update(verdict="raise", target_profile=target, reasons=["capacity_would_expire_unused"])
            raised_pools.add(pool)
            continue
        entry.update(verdict="same", reasons=["at_strongest" if index == 0 else "on_pace"])
    return result


def pace(catalog: dict, catalog_sha256: str, samples: list[dict], current_profiles: dict, *,
         work_mode: str = "production", now: datetime | None = None,
         unobserved_providers: tuple[str, ...] = ("grok",)) -> dict:
    now = now or datetime.now(timezone.utc)
    paced = pace_windows(samples, now=now)
    return {"schema": SCHEMA, "decided_at": now.isoformat(), "catalog_sha256": catalog_sha256,
            "work_mode": work_mode, "execution_allowed": False,
            "windows": paced, "lanes": recommend(catalog, paced, current_profiles, work_mode=work_mode),
            "unpaced_providers": [{"provider": p, "reason": "capacity_unobserved"} for p in unobserved_providers],
            "limitations": ["per_lane_cost_not_attributed", "one_step_per_pool_per_evaluation"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", required=True, help="observer observations.sqlite (read-only)")
    parser.add_argument("--catalog", default=str(Path(__file__).resolve().parents[1] / "configs" /
                                                "lane_profile_catalog.json"))
    parser.add_argument("--current-profiles", default="{}",
                        help='JSON object lane -> verified current profile id (D3 binding), e.g. {"fable-5": "..."}')
    parser.add_argument("--work-mode", default="production", choices=WORK_MODES)
    args = parser.parse_args(argv)
    try:
        current = json.loads(args.current_profiles)
        if not isinstance(current, dict) or not all(k in LANES for k in current):
            raise ValueError("current profiles must map known lanes to profile ids")
        catalog, digest = load_catalog(args.catalog)
        report = pace(catalog, digest, read_samples(args.store), current, work_mode=args.work_mode)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": f"{exc.__class__.__name__}: {exc}",
                          "execution_allowed": False}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
