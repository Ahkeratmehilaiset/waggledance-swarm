# SPDX-License-Identifier: BUSL-1.1
"""Read-only RCO readiness report for bridge wake/pass-block queues.

RCO sessions need a cheap step-0 check before general review work: is a wake
bit present, and is there a direct pass/block request that should be answered
before content review? This tool only reports that state. It does not consume
wake files, append bridge events, emit RCO_PASS, close requests, enqueue work,
or change merge authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    CLOSED_REQUEST_STATUSES,
    PRIVATE_MARKERS,
    _event_agent,
    _event_recipients,
    _event_status,
    _event_ts,
    _event_type,
    _is_request_like,
    _latest_event_time,
    _parse_utc,
    _task_id,
    read_events,
    recommend_next_action,
)
from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    WorkQueueError,
    list_claims,
    resolve_bridge_root,
)


DEFAULT_TAIL = 50000
DEFAULT_MAX_AGE_HOURS = 12.0
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)


class RcoReadinessError(ValueError):
    """Raised when the readiness report cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only RCO readiness report: wake-bit presence plus highest "
            "priority direct pass/block request for one RCO lane."
        ),
    )
    parser.add_argument("--agent", required=True, help="RCO agent id, e.g. claude-rco-1.")
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to runtime .agent-bridge. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of JSONL; <=0 reads all.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=(
            "Ignore direct pass/block requests older than this many hours; "
            "use <=0 to include the selected event tail."
        ),
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
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    try:
        agent = _normalize_rco_agent(args.agent)
        events = read_events(events_path, tail=args.tail)
        claims = list_claims(bridge_root)
        report = build_rco_readiness_report(
            agent=agent,
            events=events,
            bridge_root=bridge_root,
            claims=claims,
            max_age_hours=args.max_age_hours,
            now_utc=_parse_now(args.now),
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "rco_readiness_report_error",
            "errors": exc.report.get("errors", [str(exc)]),
        }
        exit_code = 2
    except RcoReadinessError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except WorkQueueError as exc:
        report = {
            "ok": False,
            "decision": "rco_readiness_report_error",
            "errors": [str(exc)],
        }
        exit_code = 2
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "rco_readiness_report_error",
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


def build_rco_readiness_report(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    claims: Sequence[Any] = (),
    max_age_hours: float | None = DEFAULT_MAX_AGE_HOURS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return wake/pass-block readiness for one RCO lane."""
    target = _normalize_rco_agent(agent)
    effective_now = (
        now_utc
        or _latest_event_time(events)
        or datetime.now(timezone.utc).astimezone(timezone.utc)
    )
    direct_requests = _open_direct_rco_pass_block_requests(
        events=events,
        agent=target,
        now_utc=effective_now,
        max_age_hours=max_age_hours,
    )
    next_action = recommend_next_action(
        agent=target,
        events=events,
        claims=claims,
        bridge_root=bridge_root,
        now_utc=effective_now,
    )
    highest = direct_requests[0] if direct_requests else None
    wake_file = _wake_file_status(bridge_root=bridge_root, agent=target)
    if highest is not None:
        decision = "direct_rco_pass_block_request_ready"
        operator_action = "answer_highest_priority_direct_pass_block_request"
    elif wake_file["present"]:
        decision = "wake_bit_present_no_direct_pass_block_request"
        operator_action = "consume_wake_then_run_bridge_next_action"
    else:
        decision = "rco_ready_no_direct_pass_block_request"
        operator_action = "continue_bridge_next_action"

    report: dict[str, Any] = {
        "ok": True,
        "report_version": "wd.rco_readiness_report.v0",
        "advisory_only": True,
        "read_only": True,
        "wake_consumed": False,
        "bridge_append_allowed": False,
        "merge_allowed": False,
        "decision": decision,
        "operator_action": operator_action,
        "agent": target,
        "events_checked": len(events),
        "max_age_hours": None
        if max_age_hours is not None and max_age_hours <= 0
        else max_age_hours,
        "local_paths_recorded": False,
        "wake_file": wake_file,
        "wake_no_consume_command": (
            f".agent-bridge\\bin\\Test-BridgeWake.ps1 -Agent {target} -NoConsume"
        ),
        "wake_consume_step0_command": (
            f".agent-bridge\\bin\\Test-BridgeWake.ps1 -Agent {target}"
        ),
        "direct_pass_block_request_count": len(direct_requests),
        "highest_priority_request": highest or {},
        "direct_pass_block_requests": direct_requests,
        "bridge_next_action": _next_action_summary(next_action),
        "request_closure_rule": (
            "wake acknowledgements do not resolve direct RCO pass/block requests; "
            "only substantive RCO pass/block/finding/changes responses or "
            "requester terminal closure do"
        ),
        "authority_boundary": _authority_boundary(),
    }
    for gate in CLAIM_GATES:
        report[gate] = False
    _assert_no_private_markers(report)
    return report


def _open_direct_rco_pass_block_requests(
    *,
    events: Sequence[Mapping[str, Any]],
    agent: str,
    now_utc: datetime,
    max_age_hours: float | None,
) -> list[dict[str, Any]]:
    if max_age_hours is not None and max_age_hours > 0:
        max_age_minutes = max_age_hours * 60.0
    else:
        max_age_minutes = None
    open_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, event in enumerate(events):
        _close_direct_requests(open_by_key, event, target_agent=agent)
        if not _is_direct_rco_pass_block_request(agent=agent, event=event):
            continue
        task_id = _task_id(event)
        key = (task_id, _payload_scalar(event, "pr") or _payload_scalar(event, "pr_number"))
        open_by_key[key] = {
            "target_agent": agent,
            "requester": _event_agent(event),
            "task_id": task_id,
            "type": _event_type(event),
            "status": _event_status(event),
            "ts_utc": _event_ts(event),
            "message": _safe_message(event.get("message")),
            "event_index": index,
            "head": _payload_scalar(event, "head"),
            "pr": _payload_scalar(event, "pr") or _payload_scalar(event, "pr_number"),
        }

    rows: list[dict[str, Any]] = []
    for state in open_by_key.values():
        request_ts = _parse_utc(str(state["ts_utc"]))
        age_minutes = 0.0
        if request_ts is not None:
            age_minutes = (
                now_utc.astimezone(timezone.utc) - request_ts
            ).total_seconds() / 60.0
        if max_age_minutes is not None and age_minutes > max_age_minutes:
            continue
        row = dict(state)
        row["age_minutes"] = round(age_minutes, 3)
        row["priority"] = "direct_rco_pass_block"
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -int(row["event_index"]),
            str(row.get("target_agent") or ""),
            str(row.get("task_id") or ""),
        )
    )
    for row in rows:
        row.pop("event_index", None)
    return rows


def _close_direct_requests(
    open_by_key: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
    *,
    target_agent: str,
) -> None:
    event_ts = _parse_utc(_event_ts(event))
    event_agent = _event_agent(event)
    event_task = _task_id(event)
    event_pr = _payload_scalar(event, "pr") or _payload_scalar(event, "pr_number")
    for key, state in list(open_by_key.items()):
        task_id, pr = key
        request_ts = _parse_utc(str(state["ts_utc"]))
        if event_ts is None or request_ts is None or event_ts <= request_ts:
            continue
        same_task = bool(task_id and event_task == task_id)
        same_pr = bool(pr and event_pr == pr)
        if not same_task and not same_pr:
            continue
        requester = str(state["requester"])
        if event_agent == target_agent and _is_substantive_rco_response(event):
            del open_by_key[key]
            continue
        if event_agent == requester and _is_terminal_closure(event):
            del open_by_key[key]


def _is_direct_rco_pass_block_request(
    *,
    agent: str,
    event: Mapping[str, Any],
) -> bool:
    if agent not in _event_recipients(event):
        return False
    if not _is_request_like(event):
        return False
    tokens = _request_signal_tokens(event)
    wants_decision = bool(tokens.intersection({"pass", "block"}))
    asks_for_decision = bool(
        tokens.intersection(
            {
                "request",
                "requested",
                "required",
                "needed",
                "missing",
                "ready",
                "blocking",
                "blocker",
            }
        )
    )
    return wants_decision and asks_for_decision


def _is_substantive_rco_response(event: Mapping[str, Any]) -> bool:
    status_tokens = _tokens(_event_status(event))
    if status_tokens.intersection({"ack", "acknowledged", "received", "seen"}):
        return False
    if {"wake", "ack"}.issubset(status_tokens):
        return False
    if _event_type(event) == "finding":
        return True
    tokens = _event_tokens(event)
    if {"rco", "pass"}.issubset(tokens):
        return True
    if "block" in tokens or "blocked" in tokens:
        return True
    if {"changes", "requested"}.issubset(tokens):
        return True
    return False


def _is_terminal_closure(event: Mapping[str, Any]) -> bool:
    return _event_type(event) == "done" or _event_status(event) in CLOSED_REQUEST_STATUSES


def _event_tokens(event: Mapping[str, Any]) -> set[str]:
    fields = [
        _event_type(event),
        _event_status(event),
        _safe_message(event.get("message")),
    ]
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in {
                "current_blocker",
                "decision",
                "required_action",
                "request",
                "status",
            } and isinstance(value, (str, int, float, bool)):
                fields.append(str(value))
    return _tokens(" ".join(fields))


def _request_signal_tokens(event: Mapping[str, Any]) -> set[str]:
    fields = [
        _event_type(event),
        _event_status(event),
    ]
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in {
                "current_blocker",
                "decision",
                "required_action",
                "request",
                "status",
            } and isinstance(value, (str, int, float, bool)):
                fields.append(str(value))
    return _tokens(" ".join(fields))


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


def _next_action_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        "action": report.get("action", ""),
        "task_id": report.get("task_id", ""),
        "safe_mode": report.get("safe_mode", ""),
        "summary": report.get("summary", ""),
        "open_incoming_count": report.get("open_incoming_count", 0),
    }
    incoming = report.get("incoming")
    if isinstance(incoming, Mapping):
        summary["incoming"] = {
            "agent": incoming.get("agent", ""),
            "type": incoming.get("type", ""),
            "status": incoming.get("status", ""),
            "ts_utc": incoming.get("ts_utc", ""),
        }
    return summary


def _wake_file_status(*, bridge_root: Path, agent: str) -> dict[str, Any]:
    wake_path = bridge_root / f"wake_{agent}"
    if not wake_path.exists():
        return {"checked": True, "present": False, "mtime_utc": ""}
    try:
        mtime = datetime.fromtimestamp(wake_path.stat().st_mtime, tz=timezone.utc)
        return {
            "checked": True,
            "present": True,
            "mtime_utc": mtime.isoformat().replace("+00:00", "Z"),
        }
    except OSError:
        return {"checked": True, "present": True, "mtime_utc": ""}


def _normalize_rco_agent(agent: str) -> str:
    normalized = str(agent or "").strip().lower()
    if not normalized or not AGENT_ID_PATTERN.fullmatch(normalized):
        raise RcoReadinessError(
            {
                "ok": False,
                "decision": "rco_readiness_report_error",
                "errors": [f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"],
            }
        )
    if "rco" not in _tokens(normalized):
        raise RcoReadinessError(
            {
                "ok": False,
                "decision": "rco_readiness_report_error",
                "errors": ["agent must be an RCO lane"],
            }
        )
    return normalized


def _payload_scalar(event: Mapping[str, Any], key: str) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get(key)
    if isinstance(value, (str, int, float, bool)) and not isinstance(value, bool):
        return str(value)
    return ""


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise RcoReadinessError(
            {
                "ok": False,
                "decision": "rco_readiness_report_error",
                "errors": ["now must be an ISO-8601 timestamp"],
            }
        )
    return parsed


def _safe_message(value: object) -> str:
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if any(marker in message for marker in PRIVATE_MARKERS):
        return "<redacted-private-marker>"
    if len(message) > 240:
        return f"{message[:237]}..."
    return message


def _authority_boundary() -> dict[str, bool]:
    return {
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "runtime_activation_allowed": False,
        "wake_file_consume_allowed": False,
        "rco_pass_emit_allowed": False,
        "merge_allowed": False,
        "network_required": False,
    }


def _assert_no_private_markers(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    if any(marker in text for marker in PRIVATE_MARKERS):
        raise RcoReadinessError(
            {
                "ok": False,
                "decision": "rco_readiness_report_refused",
                "errors": ["private marker present in selected readiness output"],
            }
        )


def _print_human(report: Mapping[str, Any]) -> None:
    if not report.get("ok"):
        print("RCO readiness report failed", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    print(f"RCO readiness: {report['decision']}")
    print(f"  agent: {report['agent']}")
    print(f"  wake present: {report['wake_file']['present']}")
    print(
        "  direct pass/block requests: "
        f"{report['direct_pass_block_request_count']}"
    )
    highest = report.get("highest_priority_request")
    if isinstance(highest, Mapping) and highest:
        print(f"  highest priority: {highest.get('task_id')} {highest.get('status')}")


if __name__ == "__main__":
    raise SystemExit(main())
