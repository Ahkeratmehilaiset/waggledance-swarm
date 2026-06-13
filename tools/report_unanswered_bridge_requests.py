# SPDX-License-Identifier: BUSL-1.1
"""Report visible bridge requests that the addressed agent has not answered.

This is a read-only diagnostic for the "nudge is visible but nothing happens"
failure mode. It does not write bridge events, enqueue scheduler work, close
requests, or grant merge authority. It only scans the bridge JSONL and reports
requests that are still open for their target agent after a minimum age.
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

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    PRIVATE_MARKERS,
    _event_agent,
    _event_recipients,
    _event_status,
    _event_ts,
    _event_type,
    _is_answer_like,
    _is_request_like,
    _latest_event_time,
    _parse_utc,
    _task_id,
    read_events,
)
from waggledance.core.work_queue import AGENT_ID_PATTERN  # noqa: E402


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_MIN_AGE_MINUTES = 12.0
DEFAULT_MAX_AGE_HOURS = 12.0
DEFAULT_TAIL = 50000
DEFAULT_MAX_ITEMS = 50


class UnansweredRequestError(ValueError):
    """Raised when the report cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read bridge events and report requests addressed to agents that "
            "remain unanswered by those target agents."
        ),
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument(
        "--agent",
        action="append",
        default=None,
        help="Target agent to include. Repeat to filter to multiple agents.",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="Only report unanswered requests at least this old.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=(
            "Ignore unanswered requests older than this many hours; use <=0 "
            "to include the full selected event tail."
        ),
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help="Maximum unanswered requests to include in the report.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for request-age evaluation.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = report_unanswered_requests(
            events=read_events(args.events, tail=args.tail),
            agents=args.agent,
            min_age_minutes=args.min_age_minutes,
            max_age_hours=args.max_age_hours,
            max_items=args.max_items,
            now_utc=_parse_now(args.now),
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "unanswered_bridge_requests_error",
            "errors": exc.report.get("errors", [str(exc)]),
        }
        exit_code = 2
    except UnansweredRequestError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "unanswered_bridge_requests_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def report_unanswered_requests(
    *,
    events: Sequence[Mapping[str, Any]],
    agents: Sequence[str] | None = None,
    min_age_minutes: float = DEFAULT_MIN_AGE_MINUTES,
    max_age_hours: float | None = DEFAULT_MAX_AGE_HOURS,
    max_items: int = DEFAULT_MAX_ITEMS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return unanswered incoming bridge requests grouped by target agent."""
    if min_age_minutes < 0:
        raise UnansweredRequestError(
            {
                "ok": False,
                "decision": "unanswered_bridge_requests_error",
                "errors": ["min_age_minutes must be non-negative"],
            }
        )
    if max_age_hours is not None and max_age_hours > 0:
        max_age_minutes = max_age_hours * 60.0
    else:
        max_age_hours = None
        max_age_minutes = None
    if max_items <= 0:
        raise UnansweredRequestError(
            {
                "ok": False,
                "decision": "unanswered_bridge_requests_error",
                "errors": ["max_items must be positive"],
            }
        )

    agent_filter = _normalize_agent_filter(agents)
    effective_now = (
        now_utc
        or _latest_event_time(events)
        or datetime.now(timezone.utc).astimezone(timezone.utc)
    )
    open_requests = _open_requests_by_target(events=events, agent_filter=agent_filter)

    rows: list[dict[str, Any]] = []
    for state in open_requests.values():
        request_ts = _parse_utc(str(state["ts_utc"]))
        if request_ts is None:
            continue
        age_minutes = (
            effective_now.astimezone(timezone.utc) - request_ts
        ).total_seconds() / 60.0
        if age_minutes < min_age_minutes:
            continue
        if max_age_minutes is not None and age_minutes > max_age_minutes:
            continue
        rows.append(_request_row(state, age_minutes=age_minutes))

    rows.sort(
        key=lambda row: (
            -float(row["age_minutes"]),
            str(row["target_agent"]),
            str(row["task_id"]),
        )
    )
    rows = rows[:max_items]

    by_agent: dict[str, int] = {}
    for row in rows:
        target = str(row["target_agent"])
        by_agent[target] = by_agent.get(target, 0) + 1

    return {
        "ok": True,
        "decision": "unanswered_bridge_requests_report",
        "events_checked": len(events),
        "events_path_recorded": False,
        "local_paths_recorded": False,
        "min_age_minutes": min_age_minutes,
        "max_age_hours": max_age_hours,
        "max_items": max_items,
        "agent_filter": sorted(agent_filter) if agent_filter else [],
        "unanswered_count": len(rows),
        "by_agent": dict(sorted(by_agent.items())),
        "requests": rows,
    }


def _open_requests_by_target(
    *,
    events: Sequence[Mapping[str, Any]],
    agent_filter: set[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    known_agents = _known_bridge_agents(events)
    open_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, event in enumerate(events):
        _close_answered_requests(open_by_key, event, known_agents=known_agents)
        if not _is_request_like(event):
            continue
        requester = _event_agent(event)
        for target in _event_recipients(event):
            if not target or target == requester:
                continue
            if agent_filter and target not in agent_filter:
                continue
            task_id = _task_id(event)
            key = (target, _task_key(task_id, known_agents=known_agents, requester=requester))
            open_by_key[key] = {
                "target_agent": target,
                "requester": requester,
                "task_id": task_id,
                "type": _event_type(event),
                "status": _event_status(event),
                "ts_utc": _event_ts(event),
                "message": _safe_message(event.get("message")),
                "event_index": index,
                "payload_head": _payload_scalar(event, "head"),
                "payload_pr": _payload_scalar(event, "pr")
                or _payload_scalar(event, "pr_number"),
            }
    return open_by_key


def _close_answered_requests(
    open_by_key: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
    *,
    known_agents: Sequence[str],
) -> None:
    if not _is_answer_like(event):
        return
    event_agent = _event_agent(event)
    event_task_key = _task_key(
        _task_id(event),
        known_agents=known_agents,
        requester=event_agent,
    )
    event_ts = _event_ts(event)
    for key, state in list(open_by_key.items()):
        target, state_task_key = key
        requester = str(state["requester"])
        if state_task_key != event_task_key:
            continue
        if event_ts <= str(state["ts_utc"]):
            continue
        if event_agent in {target, requester}:
            del open_by_key[key]


def _known_bridge_agents(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    agents: set[str] = set()
    for event in events:
        event_agent = _event_agent(event)
        if AGENT_ID_PATTERN.fullmatch(event_agent):
            agents.add(event_agent)
        for recipient in _event_recipients(event):
            if AGENT_ID_PATTERN.fullmatch(recipient):
                agents.add(recipient)
    return tuple(sorted(agents, key=lambda item: (-len(item), item)))


def _task_key(
    task_id: str,
    *,
    known_agents: Sequence[str],
    requester: str,
) -> str:
    normalized = str(task_id or "").strip()
    if not normalized:
        return f"empty:{requester}"
    for agent in known_agents:
        slash_prefix = f"{agent}/"
        hyphen_prefix = f"{agent}-"
        if normalized.startswith(slash_prefix):
            rest = normalized[len(slash_prefix):]
            if rest:
                return f"{agent}-{rest}"
        if normalized.startswith(hyphen_prefix):
            rest = normalized[len(hyphen_prefix):]
            if rest:
                return f"{agent}-{rest}"
    return normalized


def _request_row(state: Mapping[str, Any], *, age_minutes: float) -> dict[str, Any]:
    row = {
        "target_agent": state["target_agent"],
        "requester": state["requester"],
        "task_id": state["task_id"],
        "type": state["type"],
        "status": state["status"],
        "ts_utc": state["ts_utc"],
        "age_minutes": round(age_minutes, 3),
        "message": state["message"],
        "bridge_visible": True,
        "response_expected_from": state["target_agent"],
    }
    if state.get("payload_head"):
        row["head"] = state["payload_head"]
    if state.get("payload_pr"):
        row["pr"] = state["payload_pr"]
    return row


def _normalize_agent_filter(agents: Sequence[str] | None) -> set[str]:
    normalized: set[str] = set()
    for raw in agents or []:
        agent = str(raw or "").strip().lower()
        if not agent:
            continue
        if not AGENT_ID_PATTERN.fullmatch(agent):
            raise UnansweredRequestError(
                {
                    "ok": False,
                    "decision": "unanswered_bridge_requests_error",
                    "errors": [
                        f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"
                    ],
                }
            )
        normalized.add(agent)
    return normalized


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise UnansweredRequestError(
            {
                "ok": False,
                "decision": "unanswered_bridge_requests_error",
                "errors": ["now must be an ISO-8601 timestamp"],
            }
        )
    return parsed


def _payload_scalar(event: Mapping[str, Any], key: str) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get(key)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _safe_message(value: object) -> str:
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if any(marker in message for marker in PRIVATE_MARKERS):
        return "<redacted-private-marker>"
    if len(message) > 240:
        return f"{message[:237]}..."
    return message


def _print_human(report: Mapping[str, Any]) -> None:
    if not report.get("ok"):
        print("unanswered bridge request report failed", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    count = int(report.get("unanswered_count") or 0)
    print(f"unanswered bridge requests: {count}")
    for row in report.get("requests", []):
        if not isinstance(row, Mapping):
            continue
        print(
            "- "
            f"{row.get('target_agent')} <- {row.get('requester')} "
            f"{row.get('task_id')} "
            f"{row.get('status')} age={row.get('age_minutes')}m"
        )


if __name__ == "__main__":
    raise SystemExit(main())
