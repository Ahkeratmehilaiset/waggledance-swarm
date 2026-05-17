# SPDX-License-Identifier: BUSL-1.1
"""Opt-in bridge idle-state detector for idle-protocol v1.

The tool is intentionally a detection primitive only. It reads local bridge
state, accepts an explicit pending-CI count from the caller, and reports
``idle`` only when every v1 predicate is quiet for the configured window.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Sequence


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_CLAIMS_DIR = Path(".agent-bridge") / "work_queue" / "claims"
DEFAULT_IDLE_MINUTES = 60


@dataclass(frozen=True)
class BridgeEvent:
    line_no: int
    ts_utc: datetime
    agent: str
    type: str
    task_id: str
    status: str
    to: str
    message: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class OpenRequest:
    kind: str
    task_id: str
    opened_at_utc: str
    status: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether the bridge is idle enough to start idle protocol v1.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to .agent-bridge/shared/events.jsonl.",
    )
    parser.add_argument(
        "--claims-dir",
        type=Path,
        default=DEFAULT_CLAIMS_DIR,
        help="Path to active bridge claim JSON files.",
    )
    parser.add_argument(
        "--pending-ci-count",
        type=int,
        default=0,
        help="External count of PRs with CI still pending. v1 does not call GitHub.",
    )
    parser.add_argument(
        "--idle-minutes",
        type=int,
        default=DEFAULT_IDLE_MINUTES,
        help="Required quiet window in minutes.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="UTC timestamp override for deterministic tests.",
    )
    parser.add_argument(
        "--operator-last-activity-utc",
        default=None,
        help="Optional operator activity timestamp from outside the bridge event log.",
    )
    parser.add_argument(
        "--open-request-max-age-hours",
        type=float,
        default=12.0,
        help=(
            "Ignore unclosed event-stream scout/RCO requests older than this "
            "as stale bridge hygiene artifacts. Active claim files still block."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_idle_state(
            events_path=args.events,
            claims_dir=args.claims_dir,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            idle_minutes=args.idle_minutes,
            pending_ci_count=args.pending_ci_count,
            open_request_max_age_hours=args.open_request_max_age_hours,
            operator_last_activity_utc=(
                _parse_utc(args.operator_last_activity_utc)
                if args.operator_last_activity_utc
                else None
            ),
        )
    except ValueError as exc:
        print(f"idle check FAILED: {exc}", file=sys.stderr)
        return 2

    _print_report(report, json_output=args.json)
    return 0


def evaluate_idle_state(
    *,
    events_path: Path,
    claims_dir: Path,
    now_utc: datetime,
    idle_minutes: int,
    pending_ci_count: int,
    open_request_max_age_hours: float,
    operator_last_activity_utc: datetime | None = None,
) -> dict[str, Any]:
    if idle_minutes <= 0:
        raise ValueError("--idle-minutes must be positive")
    if pending_ci_count < 0:
        raise ValueError("--pending-ci-count cannot be negative")
    if open_request_max_age_hours <= 0:
        raise ValueError("--open-request-max-age-hours must be positive")
    if not events_path.exists():
        raise ValueError(f"missing bridge events file: {events_path}")

    events, invalid_lines = _read_events(events_path)
    cutoff = now_utc - timedelta(minutes=idle_minutes)
    if not events:
        raise ValueError(f"empty bridge events file: {events_path}")

    open_work_claims = _open_claim_task_ids(claims_dir)
    open_requests, stale_event_requests = _partition_stale_requests(
        _open_requests(events),
        now_utc,
        max_age=timedelta(hours=open_request_max_age_hours),
    )
    open_claim_requests = _open_requests_from_claims(claims_dir)
    open_scout = [
        request for request in [*open_requests, *open_claim_requests]
        if request.kind == "scout"
    ]
    open_rco = [
        request for request in [*open_requests, *open_claim_requests]
        if request.kind == "rco"
    ]

    latest_merge = _latest(events, _is_merge_event)
    latest_agent_message = _latest(events, _is_substantive_agent_message)
    latest_operator_activity = _latest(events, _is_operator_activity)
    if operator_last_activity_utc is not None:
        latest_operator_activity = _max_dt(
            latest_operator_activity,
            operator_last_activity_utc,
        )

    criteria = {
        "pending_ci": {
            "ok": pending_ci_count == 0,
            "pending_ci_count": pending_ci_count,
        },
        "open_work_claims": {
            "ok": not open_work_claims,
            "task_ids": open_work_claims,
        },
        "open_scout_requests": {
            "ok": not open_scout,
            "task_ids": [request.task_id for request in open_scout],
        },
        "open_rco_requests": {
            "ok": not open_rco,
            "task_ids": [request.task_id for request in open_rco],
        },
        "recent_merge": _quiet_criterion(latest_merge, cutoff),
        "recent_agent_message": _quiet_criterion(latest_agent_message, cutoff),
        "recent_operator_activity": _quiet_criterion(
            latest_operator_activity,
            cutoff,
        ),
        "invalid_events": {
            "ok": invalid_lines == 0,
            "invalid_lines": invalid_lines,
        },
        "stale_open_requests_ignored": {
            "ok": True,
            "task_ids": [request.task_id for request in stale_event_requests],
            "max_age_hours": open_request_max_age_hours,
        },
    }
    blockers = [name for name, value in criteria.items() if not value["ok"]]
    idle = not blockers
    return {
        "decision": "idle" if idle else "active",
        "idle": idle,
        "checked_at_utc": _iso(now_utc),
        "idle_minutes": idle_minutes,
        "cutoff_utc": _iso(cutoff),
        "events_path": str(events_path),
        "claims_dir": str(claims_dir),
        "blockers": blockers,
        "criteria": criteria,
    }


def _read_events(path: Path) -> tuple[list[BridgeEvent], int]:
    events: list[BridgeEvent] = []
    invalid_lines = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            events.append(_event_from_raw(line_no, raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            invalid_lines += 1
    events.sort(key=lambda event: (event.ts_utc, event.line_no))
    return events, invalid_lines


def _event_from_raw(line_no: int, raw: dict[str, Any]) -> BridgeEvent:
    return BridgeEvent(
        line_no=line_no,
        ts_utc=_parse_utc(str(raw["ts_utc"])),
        agent=str(raw.get("agent", "")),
        type=str(raw.get("type", "")),
        task_id=str(raw.get("task_id", "")),
        status=str(raw.get("status", "")),
        to=str(raw.get("to", "")),
        message=str(raw.get("message", "")),
        raw=raw,
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _open_requests(events: Iterable[BridgeEvent]) -> list[OpenRequest]:
    open_by_task: dict[tuple[str, str], OpenRequest] = {}
    for event in events:
        kind = _request_kind(event)
        if kind is not None:
            open_by_task[(kind, event.task_id)] = OpenRequest(
                kind=kind,
                task_id=event.task_id,
                opened_at_utc=_iso(event.ts_utc),
                status=event.status,
            )
            continue

        for key in list(open_by_task):
            request_kind, task_id = key
            if event.task_id == task_id and _closes_request(request_kind, event):
                del open_by_task[key]
    return list(open_by_task.values())


def _partition_stale_requests(
    requests: Iterable[OpenRequest],
    now_utc: datetime,
    *,
    max_age: timedelta,
) -> tuple[list[OpenRequest], list[OpenRequest]]:
    fresh: list[OpenRequest] = []
    stale: list[OpenRequest] = []
    for request in requests:
        try:
            opened_at = _parse_utc(request.opened_at_utc)
        except ValueError:
            fresh.append(request)
            continue
        if now_utc - opened_at > max_age:
            stale.append(request)
        else:
            fresh.append(request)
    return fresh, stale


def _open_claim_task_ids(claims_dir: Path) -> list[str]:
    if not claims_dir.exists():
        return []
    task_ids: list[str] = []
    for path in claims_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            task_ids.append(str(raw.get("task_id", path.stem)))
        except (OSError, json.JSONDecodeError):
            task_ids.append(path.stem)
    return sorted(task_ids)


def _open_requests_from_claims(claims_dir: Path) -> list[OpenRequest]:
    if not claims_dir.exists():
        return []
    requests: list[OpenRequest] = []
    for path in claims_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        text = " ".join(
            str(raw.get(field, ""))
            for field in ("task_id", "summary", "release_status", "release_message")
        ).lower()
        task_id = str(raw.get("task_id", path.stem))
        opened_at = str(raw.get("claimed_at_utc", ""))
        if "scout" in text:
            requests.append(OpenRequest("scout", task_id, opened_at, "claim"))
        elif "rco" in text:
            requests.append(OpenRequest("rco", task_id, opened_at, "claim"))
    return requests


def _request_kind(event: BridgeEvent) -> str | None:
    status = event.status.lower()
    if status in {"request_scout", "scout_requested"}:
        return "scout"
    if status == "rco_requested":
        return "rco"
    return None


def _closes_request(kind: str, event: BridgeEvent) -> bool:
    status = event.status.lower()
    if _request_kind(event) is not None:
        return False
    if event.type.lower() in {"decision", "done", "blocked", "release"}:
        return True
    if kind == "scout":
        return any(
            marker in status
            for marker in ("scout_", "answered", "recommend", "blocked", "done")
        )
    if kind == "rco":
        return any(
            marker in status
            for marker in ("rco_", "source_review", "pass", "blocked", "done")
        )
    return False


def _latest(
    events: Iterable[BridgeEvent],
    predicate: Callable[[BridgeEvent], bool],
) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        if predicate(event):
            latest = _max_dt(latest, event.ts_utc)
    return latest


def _is_merge_event(event: BridgeEvent) -> bool:
    status = event.status.lower()
    if "merged" in status or "merge_commit" in status or "mergecommit" in status:
        return True
    if event.type.lower() != "done":
        return False
    message = event.message.lower()
    return " merged " in f" {message} " or "merge commit" in message


def _is_substantive_agent_message(event: BridgeEvent) -> bool:
    if event.agent.lower() not in {"claude", "codex"}:
        return False
    if event.type.lower() != "message":
        return False
    message = event.message.strip()
    if len(message) < 20:
        return False
    return not _is_short_cron_poll(event)


def _is_short_cron_poll(event: BridgeEvent) -> bool:
    text = f"{event.status} {event.task_id} {event.message}".lower()
    if event.type.lower() in {"heartbeat", "liveness"}:
        return True
    return len(event.message.strip()) < 120 and any(
        marker in text for marker in ("cron", "poll", "heartbeat", "liveness")
    )


def _is_operator_activity(event: BridgeEvent) -> bool:
    return event.agent.lower() == "operator" or event.to.lower() == "operator"


def _quiet_criterion(latest: datetime | None, cutoff: datetime) -> dict[str, Any]:
    return {
        "ok": latest is None or latest < cutoff,
        "latest_utc": _iso(latest) if latest is not None else None,
    }


def _max_dt(left: datetime | None, right: datetime) -> datetime:
    if left is None or right > left:
        return right
    return left


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, sort_keys=True))
        return
    print(report["decision"])
    if report["blockers"]:
        print("blockers: " + ", ".join(report["blockers"]))


if __name__ == "__main__":
    raise SystemExit(main())
