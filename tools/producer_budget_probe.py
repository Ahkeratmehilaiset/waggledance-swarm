# SPDX-License-Identifier: BUSL-1.1
"""Read-only producer usage-budget telemetry probe.

Motivation (2026-06-09 incident): the producer agents (codex-lead-1,
codex-tools-1) burned their weekly provider usage limits ~2 days before
reset because nobody was watching the burn rate. This probe makes the
burn rate observable from the bridge event log so the RCO / operator can
throttle producers *before* a cap is hit.

What it does:
* Reads the bridge events.jsonl (read-only; the file is never written,
  locked, or truncated by this tool).
* For each producer identity, counts events inside one or more trailing
  time windows ending at --now, split into total / heartbeat /
  substantive (= non-heartbeat) events, and derives a substantive
  events-per-hour burn rate per window.
* Reports the minutes since each producer's most recent event (liveness)
  and surfaces data-quality counters (malformed lines, unparseable or
  future timestamps) instead of silently dropping them.
* Optionally flags producers whose substantive burn rate in the smallest
  window exceeds --warn-events-per-hour.

This is ADVISORY TELEMETRY ONLY. It is not a merge gate, grants no
authority, and must never be wired as an input to an autonomous-merge
decision. All claim gates are emitted false (leak_policy CLAIM_GATES).
Offline, deterministic (inject --now), no network.

Exit codes: 0 ok, 2 invalid arguments, 3 events file missing,
4 advisory warn threshold exceeded (only when --warn-events-per-hour set).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_PRODUCERS: tuple[str, ...] = ("codex-lead-1", "codex-tools-1")
DEFAULT_WINDOW_HOURS: tuple[float, ...] = (1.0, 6.0, 24.0, 168.0)
HEARTBEAT_TYPES = frozenset({"heartbeat"})

AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
# ts_utc in the live log carries 7 fractional digits + 'Z'; normalize to
# what datetime.fromisoformat accepts on all supported Pythons.
_FRACTION_RE = re.compile(r"\.(\d{7,})(?=$|[+-Z])")

# Hard rule shared with sibling bridge tools: every emitted artifact
# carries all claim gates as false.
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only producer usage-budget telemetry probe over the bridge "
            "event log. Advisory only; never a merge-gate input."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to bridge events.jsonl (default: .agent-bridge/shared/events.jsonl)",
    )
    parser.add_argument(
        "--producer",
        action="append",
        default=None,
        help=(
            "Producer agent identity to report on. Repeat to supply a set. "
            "Defaults to codex-lead-1 and codex-tools-1."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "ISO-8601 UTC instant the trailing windows end at "
            "(e.g. 2026-06-10T12:00:00Z). Defaults to the current UTC time; "
            "inject for deterministic output."
        ),
    )
    parser.add_argument(
        "--window-hours",
        action="append",
        type=float,
        default=None,
        help=(
            "Trailing window length in hours. Repeat for multiple windows. "
            "Defaults to 1, 6, 24 and 168."
        ),
    )
    parser.add_argument(
        "--warn-events-per-hour",
        type=float,
        default=None,
        help=(
            "Advisory threshold: flag producers whose substantive burn rate "
            "in the smallest window exceeds this many events/hour (exit 4)."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON result to stdout"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    producers = _normalize_producers(args.producer)
    if not producers:
        print("at least one --producer is required", file=sys.stderr)
        return 2
    invalid = [p for p in producers if not AGENT_ID_RE.fullmatch(p)]
    if invalid:
        print(f"invalid producer agent id(s): {invalid}", file=sys.stderr)
        return 2

    window_hours = tuple(args.window_hours or DEFAULT_WINDOW_HOURS)
    if any(not _is_finite_positive(h) for h in window_hours):
        print("--window-hours values must be finite and > 0", file=sys.stderr)
        return 2

    if args.warn_events_per_hour is not None and not _is_finite_positive(
        args.warn_events_per_hour
    ):
        print("--warn-events-per-hour must be finite and > 0", file=sys.stderr)
        return 2

    if args.now is not None:
        now = parse_ts(args.now)
        if now is None:
            print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    events_path: Path = args.events
    if not events_path.exists():
        print(f"bridge events file not found: {events_path}", file=sys.stderr)
        return 3

    events, malformed_lines = read_events_tolerant(events_path)
    result = probe_producer_budget(
        events=events,
        producers=producers,
        now=now,
        window_hours=window_hours,
        warn_events_per_hour=args.warn_events_per_hour,
        malformed_lines=malformed_lines,
    )
    _emit(result, args.json)
    return 4 if result["warned_producers"] else 0


def probe_producer_budget(
    *,
    events: Sequence[Mapping[str, Any]],
    producers: Sequence[str],
    now: datetime,
    window_hours: Sequence[float] = DEFAULT_WINDOW_HOURS,
    warn_events_per_hour: float | None = None,
    malformed_lines: int = 0,
) -> dict[str, Any]:
    """Compute per-producer budget telemetry from parsed bridge events.

    Pure and read-only: no I/O, no mutation of `events`.
    """
    windows = sorted(set(float(h) for h in window_hours))
    per_producer: dict[str, dict[str, Any]] = {}
    for producer in producers:
        per_producer[producer] = {
            "total_events": 0,
            "unparseable_ts_events": 0,
            "future_ts_events": 0,
            "last_event_ts_utc": None,
            "minutes_since_last_event": None,
            "windows": {
                _window_key(h): {
                    "window_hours": h,
                    "total_events": 0,
                    "heartbeat_events": 0,
                    "substantive_events": 0,
                    "substantive_events_per_hour": 0.0,
                }
                for h in windows
            },
        }

    for event in events:
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        stats = per_producer.get(agent)
        if stats is None:
            continue
        stats["total_events"] += 1
        ts = parse_ts(str(event.get("ts_utc", "")))
        if ts is None:
            stats["unparseable_ts_events"] += 1
            continue
        last = stats["last_event_ts_utc"]
        if last is None or ts > parse_ts(last):
            stats["last_event_ts_utc"] = _format_ts(ts)
        if ts > now:
            stats["future_ts_events"] += 1
            continue
        is_heartbeat = str(event.get("type", "")).lower() in HEARTBEAT_TYPES
        for h in windows:
            if ts > now - timedelta(hours=h):
                bucket = stats["windows"][_window_key(h)]
                bucket["total_events"] += 1
                if is_heartbeat:
                    bucket["heartbeat_events"] += 1
                else:
                    bucket["substantive_events"] += 1

    warned: list[str] = []
    smallest_key = _window_key(windows[0])
    for producer, stats in per_producer.items():
        for bucket in stats["windows"].values():
            bucket["substantive_events_per_hour"] = round(
                bucket["substantive_events"] / bucket["window_hours"], 4
            )
        last = stats["last_event_ts_utc"]
        if last is not None:
            delta = now - parse_ts(last)
            stats["minutes_since_last_event"] = round(delta.total_seconds() / 60.0, 2)
        if (
            warn_events_per_hour is not None
            and stats["windows"][smallest_key]["substantive_events_per_hour"]
            > warn_events_per_hour
        ):
            warned.append(producer)

    result: dict[str, Any] = {
        "ok": True,
        "advisory_only": True,
        "read_only": True,
        "now_utc": _format_ts(now),
        "producers": list(producers),
        "window_hours": windows,
        "warn_events_per_hour": warn_events_per_hour,
        "warned_producers": sorted(warned),
        "malformed_lines": int(malformed_lines),
        "per_producer": per_producer,
    }
    for key in CLAIM_GATES:
        result[key] = False
    return result


def parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 instant; naive values are taken as UTC.

    Returns None on anything unparseable (telemetry surfaces the count
    instead of crashing on one bad log line).
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_events_tolerant(events_path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read events.jsonl, skipping (but counting) malformed lines."""
    events: list[dict[str, Any]] = []
    malformed = 0
    text = events_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        events.append(event)
    return events, malformed


def _normalize_producers(value: Sequence[str] | None) -> tuple[str, ...]:
    raw = value if value is not None else DEFAULT_PRODUCERS
    normalized: list[str] = []
    for item in raw:
        producer = str(item or "").strip()
        if producer and producer not in normalized:
            normalized.append(producer)
    return tuple(normalized)


def _is_finite_positive(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf")) and value > 0


def _window_key(hours: float) -> str:
    return f"{hours:g}h"


def _format_ts(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True))
        return
    print(f"producer budget probe @ {result['now_utc']} (advisory, read-only)")
    if result["malformed_lines"]:
        print(f"  malformed lines skipped: {result['malformed_lines']}")
    for producer in result["producers"]:
        stats = result["per_producer"][producer]
        gap = stats["minutes_since_last_event"]
        gap_text = f"{gap} min ago" if gap is not None else "never seen"
        print(f"  {producer}: last event {gap_text}, total {stats['total_events']}")
        for key in (_window_key(h) for h in result["window_hours"]):
            bucket = stats["windows"][key]
            print(
                f"    {key}: {bucket['substantive_events']} substantive "
                f"(+{bucket['heartbeat_events']} heartbeat) "
                f"= {bucket['substantive_events_per_hour']}/h"
            )
    if result["warned_producers"]:
        print(
            "  WARN burn-rate over threshold: "
            + ", ".join(result["warned_producers"])
        )


if __name__ == "__main__":
    raise SystemExit(main())
