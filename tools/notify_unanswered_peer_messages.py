# SPDX-License-Identifier: BUSL-1.1
"""Surface peer-addressed bridge messages that an agent has not yet answered.

Some agent sessions (notably Claude Code interactive sessions) do not
auto-poll the bridge for incoming peer messages. A peer's
``handoff status=rco_requested`` or ``message status=status_query`` can
sit on the bridge unanswered while the agent only emits heartbeats. This
tool detects that condition and reports it so an operator dispatcher
can surface a marker / wake notification.

Approach: scan ``events.jsonl`` for events whose ``to`` field includes
the target agent and whose ``type`` is in the response-expected set
(handoff, message, decision, finding). For each, check whether the
target agent has emitted any event on the SAME ``task_id`` with a
``ts_utc`` strictly later than the peer event. If not, the message is
"unanswered" and reported.

Heartbeat / claim / done / release events do not require a response and
are excluded from the response-expected set.

Usage:

    python tools/notify_unanswered_peer_messages.py \\
        --agent claude --bridge-root .agent-bridge \\
        --window-minutes 60 --json

Exit codes:
  0  no unanswered peer messages
  4  one or more unanswered peer messages (non-fatal, advisory)
  2  argument error / unreadable events file
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_BRIDGE_ROOT = Path(".agent-bridge")
DEFAULT_WINDOW_MINUTES = 60
RESPONSE_EXPECTED_TYPES = frozenset({"handoff", "message", "decision", "finding"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report bridge peer-addressed messages an agent has not yet "
            "answered. Designed for the operator-side dispatcher to "
            "surface them as inbox markers."
        ),
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Target agent whose unanswered peer messages should be listed.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=DEFAULT_BRIDGE_ROOT,
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=DEFAULT_WINDOW_MINUTES,
        help="Look back this many minutes for unanswered peer messages.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-UTC timestamp override (tests).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.agent or not args.agent.strip():
        print("--agent must not be empty", file=sys.stderr)
        return 2
    if args.window_minutes <= 0:
        print("--window-minutes must be positive", file=sys.stderr)
        return 2

    events_path = args.bridge_root / "shared" / "events.jsonl"
    if not events_path.exists():
        result = {
            "ok": False,
            "decision": "missing_events_file",
            "error": f"bridge events file not found: {events_path}",
        }
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(result["error"], file=sys.stderr)
        return 2

    now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    events = _read_events(events_path)
    report = find_unanswered_peer_messages(
        events=events,
        agent=args.agent,
        now_utc=now_utc,
        window_minutes=args.window_minutes,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        if report["unanswered_count"] == 0:
            print(f"{args.agent}: 0 unanswered peer messages in last {args.window_minutes} min")
        else:
            print(
                f"{args.agent}: {report['unanswered_count']} unanswered peer "
                f"message(s) in last {args.window_minutes} min"
            )
            for entry in report["unanswered"]:
                ts = entry.get("ts_utc", "")
                agent = entry.get("agent", "")
                etype = entry.get("type", "")
                status = entry.get("status", "")
                task_id = entry.get("task_id", "")
                short = (entry.get("message") or "")[:120]
                print(f"  - {ts} {agent} {etype}/{status} task={task_id}")
                print(f"    msg: {short}")
    return 4 if report["unanswered_count"] > 0 else 0


def find_unanswered_peer_messages(
    *,
    events: Sequence[Mapping[str, Any]],
    agent: str,
    now_utc: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> dict[str, Any]:
    cutoff = now_utc - timedelta(minutes=window_minutes)

    agent_response_ts_by_task: dict[str, datetime] = {}
    candidates: list[tuple[datetime, Mapping[str, Any]]] = []

    for event in events:
        ts_raw = str(event.get("ts_utc", ""))
        if not ts_raw:
            continue
        try:
            ts = _parse_utc(ts_raw)
        except ValueError:
            continue
        task_id = str(event.get("task_id", ""))
        ev_agent = str(event.get("agent", ""))
        if ev_agent == agent and task_id:
            existing = agent_response_ts_by_task.get(task_id)
            if existing is None or existing < ts:
                agent_response_ts_by_task[task_id] = ts
            continue
        if ts < cutoff:
            continue
        if ev_agent == agent:
            continue
        etype = str(event.get("type", "")).lower()
        if etype not in RESPONSE_EXPECTED_TYPES:
            continue
        to_field = str(event.get("to", ""))
        if agent not in _split_to_field(to_field):
            continue
        candidates.append((ts, event))

    unanswered: list[dict[str, Any]] = []
    for ts, event in candidates:
        task_id = str(event.get("task_id", ""))
        response_ts = agent_response_ts_by_task.get(task_id)
        if response_ts is not None and response_ts > ts:
            continue
        unanswered.append(_summarize_event(event))

    return {
        "ok": True,
        "agent": agent,
        "window_minutes": window_minutes,
        "checked_at_utc": _iso(now_utc),
        "cutoff_utc": _iso(cutoff),
        "unanswered_count": len(unanswered),
        "unanswered": unanswered,
    }


def _split_to_field(to_field: str) -> Iterable[str]:
    return {part.strip() for part in to_field.split(",") if part.strip()}


def _summarize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ts_utc": str(event.get("ts_utc", "")),
        "agent": str(event.get("agent", "")),
        "type": str(event.get("type", "")),
        "status": str(event.get("status", "")),
        "task_id": str(event.get("task_id", "")),
        "message": str(event.get("message", "")),
        "to": str(event.get("to", "")),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
