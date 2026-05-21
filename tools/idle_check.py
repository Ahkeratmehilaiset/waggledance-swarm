# SPDX-License-Identifier: BUSL-1.1
"""Opt-in bridge idle detector for idle-protocol v1.

This is a read-only primitive: it reports idle/active/unknown and does not
emit bridge events, query GitHub, or start the protocol by itself.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import AGENT_ID_PATTERN


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_CLAIMS_DIR = Path(".agent-bridge") / "work_queue" / "claims"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether the bridge is idle enough for idle protocol v1.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--claims-dir", type=Path, default=DEFAULT_CLAIMS_DIR)
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--now", default=None)
    parser.add_argument("--operator-last-activity-utc", default=None)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument("--json", action="store_true")
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

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if report["blockers"]:
            print("blockers: " + ", ".join(report["blockers"]))
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
    if not events:
        raise ValueError(f"empty bridge events file: {events_path}")

    now_utc = now_utc.astimezone(timezone.utc)
    cutoff = now_utc - timedelta(minutes=idle_minutes)
    request_max_age = timedelta(hours=open_request_max_age_hours)
    event_requests, stale_requests = _fresh_and_stale_requests(
        _open_event_requests(events),
        now_utc,
        request_max_age,
    )
    claim_task_ids, claim_requests = _claim_state(claims_dir)
    requests = event_requests + claim_requests

    latest_operator = _latest(events, _is_operator_activity)
    if operator_last_activity_utc is not None:
        latest_operator = _max_dt(latest_operator, operator_last_activity_utc)

    criteria = {
        "pending_ci": {
            "ok": pending_ci_count == 0,
            "pending_ci_count": pending_ci_count,
        },
        "open_work_claims": {"ok": not claim_task_ids, "task_ids": claim_task_ids},
        "open_scout_requests": _request_criterion(requests, "scout"),
        "open_rco_requests": _request_criterion(requests, "rco"),
        "recent_merge": _quiet(_latest(events, _is_merge_event), cutoff),
        "recent_agent_message": _quiet(
            _latest(events, _is_substantive_agent_message),
            cutoff,
        ),
        "recent_operator_activity": _quiet(latest_operator, cutoff),
        "invalid_events": {"ok": invalid_lines == 0, "invalid_lines": invalid_lines},
        "stale_open_requests_ignored": {
            "ok": True,
            "task_ids": [request["task_id"] for request in stale_requests],
            "max_age_hours": open_request_max_age_hours,
        },
    }
    blockers = [name for name, value in criteria.items() if not value["ok"]]
    return {
        "decision": "idle" if not blockers else "active",
        "idle": not blockers,
        "checked_at_utc": _iso(now_utc),
        "idle_minutes": idle_minutes,
        "cutoff_utc": _iso(cutoff),
        "events_path": str(events_path),
        "claims_dir": str(claims_dir),
        "blockers": blockers,
        "criteria": criteria,
    }


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    invalid = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            event["_line_no"] = line_no
            event["_ts"] = _parse_utc(str(event["ts_utc"]))
            events.append(event)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            invalid += 1
    events.sort(key=lambda event: (event["_ts"], event["_line_no"]))
    return events, invalid


def _open_event_requests(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    open_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for event in events:
        task_id = str(event.get("task_id", ""))
        kind = _request_kind(event)
        if kind and task_id:
            open_by_key[(kind, task_id)] = {
                "kind": kind,
                "task_id": task_id,
                "opened_at_utc": _iso(event["_ts"]),
            }
            continue
        for key in list(open_by_key):
            if key[1] == task_id and _closes_request(key[0], event):
                del open_by_key[key]
    return list(open_by_key.values())


def _fresh_and_stale_requests(
    requests: list[dict[str, str]],
    now_utc: datetime,
    max_age: timedelta,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    fresh: list[dict[str, str]] = []
    stale: list[dict[str, str]] = []
    for request in requests:
        opened_at = _parse_utc(request["opened_at_utc"])
        (stale if now_utc - opened_at > max_age else fresh).append(request)
    return fresh, stale


def _claim_state(claims_dir: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not claims_dir.exists():
        return [], []
    task_ids: list[str] = []
    requests: list[dict[str, str]] = []
    for path in claims_dir.glob("*.json"):
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            claim = {"task_id": path.stem}
        task_id = str(claim.get("task_id", path.stem))
        task_ids.append(task_id)
        text = " ".join(
            str(claim.get(field, ""))
            for field in ("task_id", "summary", "release_status", "release_message")
        ).lower()
        kind = "scout" if "scout" in text else "rco" if "rco" in text else None
        if kind:
            requests.append(
                {
                    "kind": kind,
                    "task_id": task_id,
                    "opened_at_utc": str(claim.get("claimed_at_utc", "")),
                }
            )
    return sorted(task_ids), requests


def _request_kind(event: dict[str, Any]) -> str | None:
    status = str(event.get("status", "")).lower()
    if status in {"request_scout", "scout_requested"}:
        return "scout"
    if status == "rco_requested":
        return "rco"
    return None


def _closes_request(kind: str, event: dict[str, Any]) -> bool:
    status = str(event.get("status", "")).lower()
    if _request_kind(event) is not None:
        return False
    if str(event.get("type", "")).lower() in {"decision", "done", "blocked", "release"}:
        return True
    markers = {
        "scout": ("scout_", "answered", "recommend", "blocked", "done"),
        "rco": ("rco_", "source_review", "pass", "blocked", "done"),
    }
    return any(marker in status for marker in markers[kind])


def _request_criterion(
    requests: list[dict[str, str]],
    kind: str,
) -> dict[str, Any]:
    task_ids = [request["task_id"] for request in requests if request["kind"] == kind]
    return {"ok": not task_ids, "task_ids": task_ids}


def _latest(
    events: list[dict[str, Any]],
    predicate: Any,
) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        if predicate(event):
            latest = _max_dt(latest, event["_ts"])
    return latest


def _is_merge_event(event: dict[str, Any]) -> bool:
    if _is_retroactive_rco_closure(event):
        return False
    status = str(event.get("status", "")).lower()
    if "merged" in status or "merge_commit" in status or "mergecommit" in status:
        return True
    if str(event.get("type", "")).lower() != "done":
        return False
    message = str(event.get("message", "")).lower()
    return " merged " in f" {message} " or "merge commit" in message


def _is_retroactive_rco_closure(event: dict[str, Any]) -> bool:
    if str(event.get("type", "")).lower() != "done":
        return False
    text = (
        f"{event.get('status', '')} "
        f"{event.get('task_id', '')} "
        f"{event.get('message', '')}"
    ).lower()
    return "stale rco" in text and (
        "retroactive close" in text or "bookkeeping closure" in text
    )


def _is_substantive_agent_message(event: dict[str, Any]) -> bool:
    agent = str(event.get("agent", "")).lower()
    if agent in {"operator", "system"} or not re.fullmatch(AGENT_ID_PATTERN, agent):
        return False
    if str(event.get("type", "")).lower() != "message":
        return False
    message = str(event.get("message", "")).strip()
    if len(message) < 20:
        return False
    text = f"{event.get('status', '')} {event.get('task_id', '')} {message}".lower()
    return not (
        len(message) < 120
        and any(marker in text for marker in ("cron", "poll", "heartbeat", "liveness"))
    )


def _is_operator_activity(event: dict[str, Any]) -> bool:
    return (
        str(event.get("agent", "")).lower() == "operator"
        or str(event.get("to", "")).lower() == "operator"
    )


def _quiet(latest: datetime | None, cutoff: datetime) -> dict[str, Any]:
    return {
        "ok": latest is None or latest < cutoff,
        "latest_utc": _iso(latest) if latest is not None else None,
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
