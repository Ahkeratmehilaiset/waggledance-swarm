# SPDX-License-Identifier: BUSL-1.1
"""Daily producer usage-budget burn-report artifact (read-only, advisory).

Sprint S5. Wraps the merged read-only producer budget probe (#1031) and
its reset projection (#1032) into a single digest-bound daily artifact
the RCO / operator can read each day to decide whether to throttle a
producer BEFORE it hits its weekly provider cap (the 2026-06-09 burn).

Per producer it classifies a status from the probe telemetry:

* ``ok``                — burn rate under the warn threshold (or no cap in view);
* ``approaching_cap``   — projected substantive events to reset exceed the
  soft budget but stay under the hard one;
* ``over_budget``       — projected substantive events to reset exceed the
  hard budget, OR burn rate in the smallest window exceeds the warn rate;
* ``idle``              — no substantive events in the largest window (only
  heartbeats), e.g. a throttled producer;
* ``never_seen``        — no events at all for that producer.

The artifact carries an overall ``worst_status`` and a re-derivable
``canonical_digest`` over its core fields. It is ADVISORY TELEMETRY ONLY:
not a merge gate, no authority, never an autonomous-merge input, and it
performs no startup-script change. All claim gates are emitted false.
Offline, deterministic via --now; reuses the probe's tolerant reader so a
malformed event line is counted, not fatal.

Exit codes: 0 ok (all producers ok/idle/never_seen), 2 invalid arguments,
3 events file missing, 4 any producer approaching_cap or over_budget
(advisory escalation signal for a daily cron).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402
from tools.producer_budget_probe import (  # noqa: E402
    DEFAULT_PRODUCERS,
    DEFAULT_WINDOW_HOURS,
    _is_finite_positive,
    _window_key,
    parse_ts,
    probe_producer_budget,
    read_events_tolerant,
)

REPORT_VERSION = "wd.producer_burn_report.v0"
CLAIM_LABEL = "MEASURED_LOCAL_ADVISORY"

STATUS_OK = "ok"
STATUS_IDLE = "idle"
STATUS_NEVER_SEEN = "never_seen"
STATUS_APPROACHING = "approaching_cap"
STATUS_OVER = "over_budget"
# Severity order for worst_status rollup (higher = worse).
_STATUS_RANK = {
    STATUS_NEVER_SEEN: 0,
    STATUS_IDLE: 0,
    STATUS_OK: 1,
    STATUS_APPROACHING: 2,
    STATUS_OVER: 3,
}
_ESCALATION_STATUSES = frozenset({STATUS_APPROACHING, STATUS_OVER})

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
            "Build a digest-bound daily producer burn-report artifact from "
            "the read-only budget probe. Advisory only; never a merge-gate "
            "input."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Path to bridge events.jsonl (default: <bridge-root>/shared/events.jsonl).",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument(
        "--producer",
        action="append",
        default=None,
        help="Producer agent id (repeatable). Defaults to the producer pair.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC instant the trailing windows end at (deterministic).",
    )
    parser.add_argument(
        "--reset-at",
        default=None,
        help="ISO-8601 UTC instant of the next provider usage reset.",
    )
    parser.add_argument(
        "--projection-window-hours",
        type=float,
        default=24.0,
        help="Trailing window whose burn rate drives the reset projection.",
    )
    parser.add_argument(
        "--warn-events-per-hour",
        type=float,
        default=6.0,
        help=(
            "Substantive events/hour in the smallest window that flags "
            "over_budget on rate alone (default 6)."
        ),
    )
    parser.add_argument(
        "--soft-budget",
        type=float,
        default=None,
        help=(
            "Projected substantive events to reset above which a producer is "
            "approaching_cap (requires --reset-at)."
        ),
    )
    parser.add_argument(
        "--hard-budget",
        type=float,
        default=None,
        help=(
            "Projected substantive events to reset above which a producer is "
            "over_budget (requires --reset-at; must exceed --soft-budget)."
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="Also write JSON here.")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"

    producers = _normalize(args.producer, DEFAULT_PRODUCERS)
    if not producers:
        print("at least one --producer is required", file=sys.stderr)
        return 2

    for label, value in (
        ("--projection-window-hours", args.projection_window_hours),
        ("--warn-events-per-hour", args.warn_events_per_hour),
    ):
        if not _is_finite_positive(value):
            print(f"{label} must be finite and > 0", file=sys.stderr)
            return 2
    for label, value in (("--soft-budget", args.soft_budget), ("--hard-budget", args.hard_budget)):
        if value is not None and not _is_finite_positive(value):
            print(f"{label} must be finite and > 0", file=sys.stderr)
            return 2
    if (
        args.soft_budget is not None
        and args.hard_budget is not None
        and args.hard_budget <= args.soft_budget
    ):
        print("--hard-budget must exceed --soft-budget", file=sys.stderr)
        return 2

    if args.now is not None:
        now = parse_ts(args.now)
        if now is None:
            print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    reset_at = None
    if args.reset_at is not None:
        reset_at = parse_ts(args.reset_at)
        if reset_at is None:
            print(f"--reset-at is not a valid ISO-8601 instant: {args.reset_at!r}", file=sys.stderr)
            return 2

    if not events_path.exists():
        print(f"bridge events file not found: {events_path}", file=sys.stderr)
        return 3

    events, malformed_lines = read_events_tolerant(events_path)
    artifact = build_burn_report(
        events=events,
        producers=producers,
        now=now,
        reset_at=reset_at,
        projection_window_hours=float(args.projection_window_hours),
        warn_events_per_hour=float(args.warn_events_per_hour),
        soft_budget=args.soft_budget,
        hard_budget=args.hard_budget,
        malformed_lines=malformed_lines,
    )

    payload = json.dumps(artifact, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        _print_summary(artifact)
    return 4 if artifact["escalated_producers"] else 0


def build_burn_report(
    *,
    events: Sequence[Mapping[str, Any]],
    producers: Sequence[str],
    now: datetime,
    reset_at: datetime | None = None,
    projection_window_hours: float = 24.0,
    warn_events_per_hour: float = 6.0,
    soft_budget: float | None = None,
    hard_budget: float | None = None,
    malformed_lines: int = 0,
) -> dict[str, Any]:
    """Classify per-producer burn status from the read-only probe telemetry."""
    probe = probe_producer_budget(
        events=events,
        producers=producers,
        now=now,
        window_hours=DEFAULT_WINDOW_HOURS,
        warn_events_per_hour=warn_events_per_hour,
        malformed_lines=malformed_lines,
        reset_at=reset_at,
        projection_window_hours=projection_window_hours,
    )

    smallest_key = _window_key(sorted(DEFAULT_WINDOW_HOURS)[0])
    largest_key = _window_key(sorted(DEFAULT_WINDOW_HOURS)[-1])
    per_producer: dict[str, dict[str, Any]] = {}
    escalated: list[str] = []
    for producer in producers:
        stats = probe["per_producer"][producer]
        status, reasons = _classify(
            stats=stats,
            smallest_key=smallest_key,
            largest_key=largest_key,
            warn_events_per_hour=warn_events_per_hour,
            soft_budget=soft_budget,
            hard_budget=hard_budget,
            reset_present=reset_at is not None,
        )
        per_producer[producer] = {
            "status": status,
            "status_reasons": reasons,
            "minutes_since_last_event": stats["minutes_since_last_event"],
            "smallest_window_burn_per_hour": stats["windows"][smallest_key][
                "substantive_events_per_hour"
            ],
            "projected_substantive_events_to_reset": stats.get(
                "projected_substantive_events_to_reset"
            ),
        }
        if status in _ESCALATION_STATUSES:
            escalated.append(producer)

    statuses = [entry["status"] for entry in per_producer.values()]
    worst_status = (
        max(statuses, key=lambda status: _STATUS_RANK[status])
        if statuses
        else STATUS_NEVER_SEEN
    )

    core: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "claim_label": CLAIM_LABEL,
        "advisory_only": True,
        "read_only": True,
        "generated_at_utc": _fmt(now),
        "reset_at_utc": _fmt(reset_at) if reset_at is not None else None,
        "hours_until_reset": probe.get("hours_until_reset"),
        "producers": list(producers),
        "warn_events_per_hour": warn_events_per_hour,
        "soft_budget": soft_budget,
        "hard_budget": hard_budget,
        "projection_window_hours": (
            projection_window_hours if reset_at is not None else None
        ),
        "malformed_lines": int(malformed_lines),
        "per_producer": per_producer,
        "worst_status": worst_status,
        "escalated_producers": sorted(escalated),
    }
    for gate in CLAIM_GATES:
        core[gate] = False
    return {**core, "canonical_digest": sha256_digest(core)}


def _classify(
    *,
    stats: Mapping[str, Any],
    smallest_key: str,
    largest_key: str,
    warn_events_per_hour: float,
    soft_budget: float | None,
    hard_budget: float | None,
    reset_present: bool,
) -> tuple[str, list[str]]:
    if stats["total_events"] == 0:
        return STATUS_NEVER_SEEN, ["no_events_for_producer"]

    reasons: list[str] = []
    rate = stats["windows"][smallest_key]["substantive_events_per_hour"]
    projected = stats.get("projected_substantive_events_to_reset")

    over = False
    approaching = False
    if rate > warn_events_per_hour:
        over = True
        reasons.append(f"burn_rate_over_warn:{rate}>{warn_events_per_hour}")
    if reset_present and projected is not None:
        if hard_budget is not None and projected > hard_budget:
            over = True
            reasons.append(f"projection_over_hard:{projected}>{hard_budget}")
        elif soft_budget is not None and projected > soft_budget:
            approaching = True
            reasons.append(f"projection_over_soft:{projected}>{soft_budget}")
    if over:
        return STATUS_OVER, reasons
    if approaching:
        return STATUS_APPROACHING, reasons

    largest = stats["windows"][largest_key]
    if largest["substantive_events"] == 0:
        return STATUS_IDLE, ["only_heartbeats_in_largest_window"]
    return STATUS_OK, ["within_budget"]


def _normalize(value: Sequence[str] | None, default: Sequence[str]) -> tuple[str, ...]:
    raw = value if value is not None else default
    out: list[str] = []
    for item in raw:
        agent = str(item or "").strip()
        if agent and agent not in out:
            out.append(agent)
    return tuple(out)


def _fmt(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _print_summary(artifact: Mapping[str, Any]) -> None:
    print(
        f"producer burn report @ {artifact['generated_at_utc']} "
        f"({artifact['claim_label']}, advisory) worst={artifact['worst_status']}"
    )
    if artifact["reset_at_utc"] is not None:
        print(
            f"  reset at {artifact['reset_at_utc']} "
            f"(in {artifact['hours_until_reset']} h)"
        )
    for producer in artifact["producers"]:
        entry = artifact["per_producer"][producer]
        proj = entry["projected_substantive_events_to_reset"]
        proj_text = f", proj {proj} to reset" if proj is not None else ""
        print(
            f"  {producer}: {entry['status']} "
            f"({entry['smallest_window_burn_per_hour']}/h{proj_text}) "
            f"- {', '.join(entry['status_reasons'])}"
        )
    if artifact["escalated_producers"]:
        print("  ESCALATE: " + ", ".join(artifact["escalated_producers"]))


if __name__ == "__main__":
    raise SystemExit(main())
