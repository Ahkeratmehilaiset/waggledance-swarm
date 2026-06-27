# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only self-drive queue planner from bridge state.

The planner ranks visible bridge work without taking authority over it. It
does not append bridge events, claim/release work, call GitHub, enqueue
scheduler tasks, or decide merge eligibility. Its output is a deterministic
queue of safe next actions for agents and the lead sprint board.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.report_unanswered_bridge_requests import (  # noqa: E402
    report_unanswered_requests,
)
from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    Claim,
    WorkQueueError,
    list_claims,
    resolve_bridge_root,
)


REPORT_VERSION = "wd.self_drive_queue_planner.v0"
DEFAULT_TAIL = 50000
DEFAULT_MAX_ITEMS = 25
DEFAULT_STALE_CLAIM_MINUTES = 15.0
DEFAULT_REQUEST_MAX_AGE_HOURS = 48.0
REDACTION_SENTINELS = ("PRIVATE" + "_MARKER", "_DO" + "_NOT" + "_LEAK")
KEEPALIVE_TYPES = {"heartbeat", "liveness"}
READY_REVIEW_STATUSES = (
    "rco_requested",
    "review_requested",
    "exact_head_review_requested",
    "ci_green",
    "checks_green",
    "build_consensus_pass",
    "driver_ready",
    "full_consensus_driver_ready",
)
OPERATOR_GATE_STATUS_FRAGMENTS = (
    "operator_required",
    "operator_review",
    "operator_sign",
    "operator_signed",
    "approval_required",
    "manual",
    "permission",
    "signature",
    "signed_",
    "_gate_",
    "gate_required",
)


class SelfDriveQueuePlannerError(ValueError):
    """Raised when the planner cannot safely produce a report."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a read-only self-drive queue planner report.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
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
        "--agent",
        action="append",
        default=None,
        help="Agent lane to include in open-request analysis. Repeatable.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file; <=0 reads all.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help="Maximum next-action rows to include.",
    )
    parser.add_argument(
        "--stale-claim-minutes",
        type=float,
        default=DEFAULT_STALE_CLAIM_MINUTES,
        help="Claim heartbeat/lease age threshold for stale-claim rows.",
    )
    parser.add_argument(
        "--request-max-age-hours",
        type=float,
        default=DEFAULT_REQUEST_MAX_AGE_HOURS,
        help=(
            "Ignore unanswered bridge requests older than this many hours; "
            "use <=0 to include the full selected event tail."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bridge_root = resolve_bridge_root(args.bridge_root)
        events_path = args.events or bridge_root / "shared" / "events.jsonl"
        events = read_bridge_events(events_path, tail=args.tail)
        claims = list_claims(bridge_root=bridge_root)
        report = build_self_drive_queue_planner(
            events=events,
            claims=claims,
            agents=args.agent,
            max_items=args.max_items,
            stale_claim_minutes=args.stale_claim_minutes,
            request_max_age_hours=args.request_max_age_hours,
            now_utc=_parse_now(args.now),
        )
    except SelfDriveQueuePlannerError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except (OSError, WorkQueueError) as exc:
        report = {
            "ok": False,
            "decision": "self_drive_queue_planner_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 0 if report["ok"] else 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return exit_code


def build_self_drive_queue_planner(
    *,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim | Mapping[str, Any]] = (),
    agents: Sequence[str] | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    stale_claim_minutes: float = DEFAULT_STALE_CLAIM_MINUTES,
    request_max_age_hours: float | None = DEFAULT_REQUEST_MAX_AGE_HOURS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic read-only queue planner report."""
    if max_items <= 0:
        raise SelfDriveQueuePlannerError(
            _error_report("max_items must be positive"),
        )
    if not math.isfinite(stale_claim_minutes) or stale_claim_minutes <= 0:
        raise SelfDriveQueuePlannerError(
            _error_report("stale_claim_minutes must be positive"),
        )
    if (
        request_max_age_hours is not None
        and request_max_age_hours > 0
        and not math.isfinite(request_max_age_hours)
    ):
        raise SelfDriveQueuePlannerError(
            _error_report("request_max_age_hours must be finite"),
        )
    agent_filter = _normalize_agents(agents)
    loaded_events = [dict(event) for event in events]
    _assert_finite(loaded_events, path="events")
    _assert_no_redaction_sentinels(loaded_events)
    effective_now = (
        now_utc
        or _latest_event_time(loaded_events)
        or datetime.now(timezone.utc).astimezone(timezone.utc)
    )

    actions: list[dict[str, Any]] = []
    actions.extend(
        _operator_gate_actions(
            events=loaded_events,
            now_utc=effective_now,
            max_items=max_items,
            max_age_hours=request_max_age_hours,
        )
    )
    actions.extend(
        _open_request_actions(
            events=loaded_events,
            agents=agent_filter or None,
            now_utc=effective_now,
            max_items=max_items,
            max_age_hours=request_max_age_hours,
        )
    )
    actions.extend(
        _claim_actions(
            claims=claims,
            now_utc=effective_now,
            stale_claim_minutes=stale_claim_minutes,
        )
    )
    actions.extend(_ready_review_actions(events=loaded_events, now_utc=effective_now))

    actions = _dedupe_actions(actions)
    actions.sort(
        key=lambda item: (
            -int(item["priority"]),
            str(item.get("owner_agent") or ""),
            str(item.get("task_id") or ""),
            str(item.get("classification") or ""),
        )
    )
    truncated = len(actions) > max_items
    actions = actions[:max_items]

    report = {
        "report_version": REPORT_VERSION,
        "ok": True,
        "decision": "self_drive_queue_planner",
        "generated_at_utc": _format_utc(effective_now),
        "source": {
            "event_count": len(loaded_events),
            "events_digest": _events_digest(loaded_events),
            "path_free": True,
            "messages_redacted": True,
            "payloads_redacted": True,
            "events_path_recorded": False,
        },
        "queue": {
            "next_action_count": len(actions),
            "next_actions_truncated": truncated,
            "classification_counts": _classification_counts(actions),
            "next_actions": actions,
        },
        "lanes": _lane_summary(events=loaded_events, claims=claims, now_utc=effective_now),
        "authority_boundary": _authority_boundary(),
    }
    _assert_no_redaction_sentinels(report)
    return report


def read_bridge_events(path: Path, *, tail: int = DEFAULT_TAIL) -> list[dict[str, Any]]:
    if not path.exists():
        raise SelfDriveQueuePlannerError(
            _error_report("events_file_missing"),
            exit_code=1,
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = lines if tail <= 0 else lines[-tail:]
    start_line = 1 if tail <= 0 else max(1, len(lines) - len(selected) + 1)
    events: list[dict[str, Any]] = []
    for line_no, raw in enumerate(selected, start=start_line):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as exc:
            raise SelfDriveQueuePlannerError(
                _error_report(f"events_json_error:line_{line_no}:{exc.msg}"),
            ) from exc
        if not isinstance(event, dict):
            raise SelfDriveQueuePlannerError(
                _error_report(f"event_not_object:line_{line_no}"),
            )
        _assert_finite(event, path=f"line_{line_no}")
        events.append(event)
    return events


def render_markdown(report: Mapping[str, Any]) -> str:
    queue = _mapping(report.get("queue"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# WD Self-Drive Queue Planner",
        "",
        f"- report version: `{report.get('report_version')}`",
        f"- input ok: `{_bool_text(report.get('ok') is True)}`",
        f"- events observed: `{_mapping(report.get('source')).get('event_count', 0)}`",
        f"- next actions: `{queue.get('next_action_count', 0)}`",
        "",
        "## Next Actions",
        "",
    ]
    for item in queue.get("next_actions", []):
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "- "
            f"`P{item.get('priority')}` "
            f"`{item.get('classification')}` "
            f"`{item.get('owner_agent')}` "
            f"`{item.get('task_id')}`"
        )
    if not queue.get("next_actions"):
        lines.append("- `none`")
    lines.extend(
        [
            "",
            "## Authority Boundary",
            "",
            f"- read-only report: `{_bool_text(authority.get('read_only_report') is True)}`",
            f"- bridge append allowed: `{_bool_text(authority.get('bridge_append_allowed') is True)}`",
            f"- queue write allowed: `{_bool_text(authority.get('queue_write_allowed') is True)}`",
            f"- github write allowed: `{_bool_text(authority.get('github_write_allowed') is True)}`",
            f"- merge allowed: `{_bool_text(authority.get('merge_allowed') is True)}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _operator_gate_actions(
    *,
    events: Sequence[Mapping[str, Any]],
    now_utc: datetime,
    max_items: int,
    max_age_hours: float | None,
) -> list[dict[str, Any]]:
    report = report_unanswered_requests(
        events=events,
        agents=["operator"],
        min_age_minutes=0,
        max_age_hours=max_age_hours,
        max_items=max_items,
        now_utc=now_utc,
    )
    actions: list[dict[str, Any]] = []
    for row in report.get("requests", []):
        if not isinstance(row, Mapping):
            continue
        if not _is_operator_gate_status(_string(row.get("status"))):
            continue
        actions.append(
            _action(
                priority=100,
                classification="operator_gate",
                action="surface_operator_gate_do_not_bypass",
                owner_agent="operator",
                task_id=_string(row.get("task_id")),
                reason=f"open_operator_request:{_string(row.get('status'))}",
                age_minutes=_float(row.get("age_minutes")),
                requester=_string(row.get("requester")),
                pr=_string(row.get("pr")),
                head=_head_prefix(row.get("head")),
            )
        )
    return actions


def _is_operator_gate_status(status: str) -> bool:
    lowered = status.lower()
    return any(fragment in lowered for fragment in OPERATOR_GATE_STATUS_FRAGMENTS)


def _open_request_actions(
    *,
    events: Sequence[Mapping[str, Any]],
    agents: Sequence[str] | None,
    now_utc: datetime,
    max_items: int,
    max_age_hours: float | None,
) -> list[dict[str, Any]]:
    report = report_unanswered_requests(
        events=events,
        agents=agents,
        min_age_minutes=0,
        max_age_hours=max_age_hours,
        max_items=max_items,
        now_utc=now_utc,
    )
    actions: list[dict[str, Any]] = []
    for row in report.get("requests", []):
        if not isinstance(row, Mapping):
            continue
        owner = _string(row.get("target_agent"))
        if owner == "operator":
            continue
        actions.append(
            _action(
                priority=90,
                classification="answer_open_bridge_request",
                action="target_agent_answers_same_task_id",
                owner_agent=owner,
                task_id=_string(row.get("task_id")),
                reason=f"open_request:{_string(row.get('status'))}",
                age_minutes=_float(row.get("age_minutes")),
                requester=_string(row.get("requester")),
                pr=_string(row.get("pr")),
                head=_head_prefix(row.get("head")),
            )
        )
    return actions


def _claim_actions(
    *,
    claims: Sequence[Claim | Mapping[str, Any]],
    now_utc: datetime,
    stale_claim_minutes: float,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for claim in claims:
        row = _claim_row(claim)
        age_minutes = _claim_age_minutes(row, now_utc=now_utc)
        lease_expired = _claim_lease_expired(row, now_utc=now_utc)
        stale = lease_expired or age_minutes is None or age_minutes > stale_claim_minutes
        actions.append(
            _action(
                priority=80 if stale else 50,
                classification="stale_active_claim" if stale else "continue_active_claim",
                action=(
                    "verify_or_release_stale_claim"
                    if stale
                    else "owner_continues_claim"
                ),
                owner_agent=_string(row.get("agent")),
                task_id=_string(row.get("task_id")),
                reason="claim_lease_expired" if lease_expired else "active_claim",
                age_minutes=age_minutes,
                write_scope=list(row.get("write_scope") or []),
            )
        )
    return actions


def _ready_review_actions(
    *,
    events: Sequence[Mapping[str, Any]],
    now_utc: datetime,
) -> list[dict[str, Any]]:
    latest_by_task: dict[str, Mapping[str, Any]] = {}
    for event in events:
        task_id = _string(event.get("task_id"))
        if not task_id:
            continue
        latest_by_task[task_id] = event

    actions: list[dict[str, Any]] = []
    for task_id, event in latest_by_task.items():
        if _terminal_event(event):
            continue
        status = _string(event.get("status")).lower()
        if not any(fragment in status for fragment in READY_REVIEW_STATUSES):
            continue
        target = _first_non_operator_recipient(event) or _string(event.get("agent"))
        event_ts = _parse_utc(_string(event.get("ts_utc")))
        actions.append(
            _action(
                priority=70,
                classification="ready_for_review_or_lead_action",
                action="review_or_advance_exact_head_item",
                owner_agent=target,
                task_id=task_id,
                reason=f"latest_status:{status}",
                age_minutes=(
                    _elapsed_minutes(now_utc, event_ts) if event_ts is not None else None
                ),
                requester=_string(event.get("agent")),
                pr=_payload_scalar(event, "pr") or _payload_scalar(event, "pr_number"),
                head=_head_prefix(_payload_scalar(event, "head")),
            )
        )
    return actions


def _lane_summary(
    *,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim | Mapping[str, Any]],
    now_utc: datetime,
) -> dict[str, Any]:
    lanes: dict[str, dict[str, Any]] = {}
    for event in events:
        if _string(event.get("type")) in KEEPALIVE_TYPES:
            continue
        agent = _string(event.get("agent"))
        if not agent:
            continue
        lanes[agent] = {
            "last_substantive_ts_utc": _string(event.get("ts_utc")),
            "last_substantive_type": _string(event.get("type")),
            "last_substantive_status": _string(event.get("status")),
        }
    for lane in lanes.values():
        ts = _parse_utc(_string(lane.get("last_substantive_ts_utc")))
        lane["substantive_gap_minutes"] = (
            _elapsed_minutes(now_utc, ts) if ts is not None else None
        )
    active_claims_by_agent: dict[str, int] = {}
    for claim in claims:
        row = _claim_row(claim)
        agent = _string(row.get("agent"))
        if agent:
            active_claims_by_agent[agent] = active_claims_by_agent.get(agent, 0) + 1
    return {
        "agent_count": len(lanes),
        "agents": {agent: lanes[agent] for agent in sorted(lanes)},
        "active_claims_by_agent": dict(sorted(active_claims_by_agent.items())),
    }


def _action(
    *,
    priority: int,
    classification: str,
    action: str,
    owner_agent: str,
    task_id: str,
    reason: str,
    age_minutes: float | None,
    requester: str = "",
    pr: str = "",
    head: str = "",
    write_scope: Sequence[str] = (),
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "priority": int(priority),
        "classification": classification,
        "action": action,
        "owner_agent": owner_agent,
        "task_id": task_id,
        "reason": reason,
        "age_minutes": None if age_minutes is None else round(float(age_minutes), 3),
    }
    if requester:
        row["requester"] = requester
    if pr:
        row["pr"] = pr
    if head:
        row["head_prefix"] = head
    if write_scope:
        row["write_scope"] = sorted(_safe_scope_label(item) for item in write_scope)
    return row


def _dedupe_actions(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for action in actions:
        key = (
            _string(action.get("classification")),
            _string(action.get("owner_agent")),
            _string(action.get("task_id")),
        )
        previous = by_key.get(key)
        current = dict(action)
        if previous is None or int(current["priority"]) > int(previous["priority"]):
            by_key[key] = current
    return list(by_key.values())


def _classification_counts(actions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        classification = _string(action.get("classification"))
        if classification:
            counts[classification] = counts.get(classification, 0) + 1
    return dict(sorted(counts.items()))


def _claim_row(claim: Claim | Mapping[str, Any]) -> dict[str, Any]:
    if is_dataclass(claim) and not isinstance(claim, type):
        return dict(asdict(claim))
    return dict(claim)


def _claim_age_minutes(row: Mapping[str, Any], *, now_utc: datetime) -> float | None:
    ts = _parse_utc(
        _string(row.get("last_heartbeat_utc")) or _string(row.get("claimed_at_utc"))
    )
    return _elapsed_minutes(now_utc, ts) if ts is not None else None


def _claim_lease_expired(row: Mapping[str, Any], *, now_utc: datetime) -> bool:
    expires = _parse_utc(_string(row.get("claim_lease_expires_utc")))
    return expires is not None and expires <= now_utc


def _terminal_event(event: Mapping[str, Any]) -> bool:
    event_type = _string(event.get("type")).lower()
    status = _string(event.get("status")).lower()
    return event_type in {"done", "release"} or status.startswith(
        ("done", "merged", "closed", "superseded", "cancelled", "canceled")
    )


def _first_non_operator_recipient(event: Mapping[str, Any]) -> str:
    for raw in _string(event.get("to")).split(","):
        agent = raw.strip()
        if agent and agent != "operator":
            return agent
    return ""


def _payload_scalar(event: Mapping[str, Any], key: str) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get(key)
    if isinstance(value, str | int | float | bool):
        return str(value)
    return ""


def _head_prefix(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 12 and all(char in "0123456789abcdefABCDEF" for char in text[:12]):
        return text[:12].lower()
    return ""


def _safe_scope_label(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return "<empty-scope>"
    if ":" in text or text.startswith(("/", "~")):
        return "<absolute-path-redacted>"
    return text


def _normalize_agents(agents: Sequence[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in agents or []:
        agent = str(raw or "").strip().lower()
        if not agent:
            continue
        if not AGENT_ID_PATTERN.fullmatch(agent):
            raise SelfDriveQueuePlannerError(
                _error_report(f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"),
            )
        if agent not in seen:
            seen.add(agent)
            normalized.append(agent)
    return normalized


def _latest_event_time(events: Sequence[Mapping[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        parsed = _parse_utc(_string(event.get("ts_utc")))
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise SelfDriveQueuePlannerError(_error_report("now must be an ISO-8601 timestamp"))
    return parsed


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed_minutes(later: datetime, earlier: datetime) -> float:
    return max(0.0, (later.astimezone(timezone.utc) - earlier).total_seconds() / 60.0)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _events_digest(events: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(events, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "github_write_allowed": False,
        "merge_allowed": False,
        "consensus_verdict_allowed": False,
        "network_required": False,
    }


def _error_report(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "self_drive_queue_planner_error",
        "errors": [message],
        "authority_boundary": _authority_boundary(),
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


def _assert_finite(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SelfDriveQueuePlannerError(_error_report(f"non_finite_json:{path}"))
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_finite(item, path=f"{path}[{index}]")


def _assert_no_redaction_sentinels(value: Any) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    lowered = text.lower()
    for marker in REDACTION_SENTINELS:
        if marker.lower() in lowered:
            raise SelfDriveQueuePlannerError(_error_report("redaction_sentinel_present"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    raise SystemExit(main())
