# SPDX-License-Identifier: BUSL-1.1
"""Recommend the next safe bridge action for one local agent.

This is the Python parity primitive for
``.agent-bridge/bin/Get-BridgeNextAction.ps1``. It is intentionally read-only:
it reads bridge events plus active work-queue claims and emits a compact
recommendation that a fresh agent session can use before deciding whether to
continue a claim, answer an incoming request, or claim new work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import KNOWN_ACK_STATUSES  # noqa: E402
from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    DEFAULT_BRIDGE_ROOT,
    Claim,
    WorkQueueError,
    list_claims,
    resolve_bridge_root,
)


DEFAULT_EVENTS_PATH = DEFAULT_BRIDGE_ROOT / "shared" / "events.jsonl"
DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS = 12.0
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
REQUEST_TYPES = {
    "message",
    "finding",
    "handoff",
    "wake_request",
    "peer_review_request",
    "simulation_open",
    "sandbox_drop",
    "decision",
}
ANSWER_TYPES = {
    "message",
    "done",
    "decision",
    "blocked",
    "finding",
    "test",
    "release",
    "handoff",
}
OPEN_STATUS_FRAGMENTS = (
    "open",
    "proposal",
    "request",
    "requested",
    "required",
    "needed",
    "missing",
    "ready",
    "pushed",
    "active",
    "blocked",
)
CLOSED_REQUEST_STATUSES = frozenset(
    {
        "accepted",
        *KNOWN_ACK_STATUSES,
        "answered",
        "approved",
        "changes_requested_retracted",
        "changes_requested_resolved",
        "changes_requested_withdrawn",
        "closed",
        "done",
        "finding_retracted",
        "finding_withdrawn",
        "merged",
        "reported",
        "resolved",
        "retracted",
        "rco_finding_retracted",
        "rco_finding_withdrawn",
        "superseded",
        "validated",
        "verified",
        "withdrawn",
    }
)
ANSWER_STATUS_FRAGMENTS = (
    "accepted",
    "ack",
    *tuple(sorted(KNOWN_ACK_STATUSES)),
    "answered",
    "approved",
    "block",
    "blocked",
    "changes",
    "closed",
    "done",
    "merged",
    "pass",
    "resolved",
    "reported",
    "superseded",
    "validated",
    "verified",
)
RESPONSE_ONLY_STATUS_FRAGMENTS = (
    "accepted",
    "ack",
    *tuple(sorted(KNOWN_ACK_STATUSES)),
    "answered",
    "approved",
    "closed",
    "done",
    "merged",
    "observed",
    "pass",
    "received",
    "reported",
    "resolved",
    "seen",
    "superseded",
    "validated",
    "verified",
)
DEFAULT_STALE_REQUEST_REPORT_MAX_AGE_HOURS = 72.0
DEFAULT_PRODUCTION_IDLE_WARN_MINUTES = 12.0
DEFAULT_WAKE_DELIVERY_MIN_AGE_MINUTES = 12.0
DEFAULT_WAKE_DELIVERY_MIN_REPEATS = 2
DEFAULT_WAKE_DELIVERY_MAX_AGE_HOURS = 12.0
DEFAULT_WAKE_DELIVERY_SELF_LIVENESS_WINDOW_MINUTES = 40.0
PRODUCTION_LIVENESS_IGNORED_AGENTS = {"operator", "system", "unknown"}
WAKE_DELIVERY_IGNORED_TARGETS = {*PRODUCTION_LIVENESS_IGNORED_AGENTS, "driver"}
HEARTBEAT_ONLY_EVENT_TYPES = {"heartbeat"}
PRODUCTION_LIVENESS_SUPPRESSION_FILENAME = "production_liveness_suppression.json"
TASK_CLOSURE_KEY_PREFIX = "task:"
EMPTY_TASK_CLOSURE_KEY_PREFIX = "empty-task:"
PR_CLOSURE_KEY_PREFIX = "pr:"
PR_REQUESTER_TERMINAL_AGENT_PREFIX = "requester-terminal:"


class BridgeNextActionError(ValueError):
    """Raised when a next-action recommendation cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recommend the next bridge action.")
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to the runtime .agent-bridge directory. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT / AGENT_BRIDGE_ROOT when set, then "
            "repo-local .agent-bridge."
        ),
    )
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument(
        "--tail",
        type=int,
        default=50000,
        help="Maximum event lines to read from the end of the JSONL file; <=0 reads all.",
    )
    parser.add_argument(
        "--open-request-max-age-hours",
        type=float,
        default=DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
        help=(
            "Ignore unanswered incoming requests older than this many hours "
            "when choosing the next action."
        ),
    )
    parser.add_argument(
        "--stale-report-max-age-hours",
        type=float,
        default=DEFAULT_STALE_REQUEST_REPORT_MAX_AGE_HOURS,
        help=(
            "Keep stale incoming task IDs in the normal stale report for this "
            "many hours; older stale requests are counted as archived "
            "historical noise instead of current follow-up."
        ),
    )
    parser.add_argument(
        "--production-idle-warn-minutes",
        type=float,
        default=DEFAULT_PRODUCTION_IDLE_WARN_MINUTES,
        help=(
            "Report agents whose latest non-heartbeat bridge activity is older "
            "than this many minutes while the selected event tail shows no "
            "new production activity."
        ),
    )
    parser.add_argument(
        "--production-liveness-suppression-config",
        type=Path,
        default=None,
        help=(
            "Optional JSON config listing intentionally unavailable bridge "
            "agents to separate from actionable production-liveness stalls. "
            "Defaults to bridge-root/shared/production_liveness_suppression.json "
            "when that runtime file exists."
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
    try:
        bridge_root = resolve_bridge_root(args.bridge_root)
        events_path = args.events or (bridge_root / "shared" / "events.jsonl")
        events = read_events(events_path, tail=args.tail)
        claims = list_claims(bridge_root=bridge_root)
        now_utc = datetime.now(timezone.utc)
        if args.now:
            now_utc = _parse_utc(args.now)
            if now_utc is None:
                raise BridgeNextActionError(
                    {
                        "ok": False,
                        "decision": "bridge_next_action_error",
                        "errors": ["now must be an ISO-8601 timestamp"],
                    }
                )
        suppression_config = _default_production_liveness_suppression_config(
            bridge_root
        )
        if args.production_liveness_suppression_config is not None:
            suppression_config = Path(args.production_liveness_suppression_config)
        production_liveness_suppressed_agents = (
            _load_production_liveness_suppression_config(suppression_config)
            if suppression_config.exists()
            else {}
        )
        report = recommend_next_action(
            agent=args.agent,
            events=events,
            claims=claims,
            bridge_root=bridge_root,
            now_utc=now_utc,
            open_request_max_age_hours=args.open_request_max_age_hours,
            stale_report_max_age_hours=args.stale_report_max_age_hours,
            production_idle_warn_minutes=args.production_idle_warn_minutes,
            production_liveness_suppressed_agents=(
                production_liveness_suppressed_agents
            ),
        )
    except (BridgeNextActionError, WorkQueueError) as exc:
        if isinstance(exc, BridgeNextActionError):
            report = exc.report
        else:
            report = {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [str(exc)],
            }
        exit_code = 2
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "bridge_next_action_error",
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


def read_events(path: Path, *, tail: int = 50000) -> list[dict[str, Any]]:
    """Read bridge JSONL events, failing closed on malformed selected lines."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    start_line = 1
    if tail > 0:
        start_line = max(1, len(lines) - tail + 1)
        lines = lines[-tail:]
    events: list[dict[str, Any]] = []
    for line_no, raw in enumerate(lines, start=start_line):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeNextActionError(
                {
                    "ok": False,
                    "decision": "bridge_next_action_error",
                    "errors": [
                        (
                            f"invalid JSON in bridge events at line "
                            f"{line_no}: {exc.msg}"
                        )
                    ],
                }
            ) from exc
        if not isinstance(event, dict):
            raise BridgeNextActionError(
                {
                    "ok": False,
                    "decision": "bridge_next_action_error",
                    "errors": [
                        (
                            f"invalid bridge event at line {line_no}: "
                            "event must be a JSON object"
                        )
                    ],
                }
            )
        events.append(event)
    return events


def _load_production_liveness_suppression_config(path: Path) -> dict[str, str]:
    """Load optional liveness suppression config for unavailable agent lanes."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [
                    (
                        "invalid production liveness suppression config JSON: "
                        f"{exc.msg}"
                    )
                ],
            }
        ) from exc
    if not isinstance(raw, Mapping):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [
                    "production liveness suppression config must be a JSON object"
                ],
            }
        )
    agents = raw.get("suppressed_agents", {})
    if not isinstance(agents, Mapping):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": ["suppressed_agents must be an object"],
            }
        )

    suppressed: dict[str, str] = {}
    for agent, metadata in agents.items():
        agent_id = str(agent)
        if not AGENT_ID_PATTERN.fullmatch(agent_id):
            raise BridgeNextActionError(
                {
                    "ok": False,
                    "decision": "bridge_next_action_error",
                    "errors": [
                        (
                            "suppressed agent id must match "
                            f"{AGENT_ID_PATTERN.pattern}: {agent_id!r}"
                        )
                    ],
                }
            )
        if isinstance(metadata, Mapping):
            reason = str(metadata.get("reason") or "")
        else:
            reason = str(metadata or "")
        suppressed[agent_id] = reason
    _assert_no_private_markers(suppressed)
    return dict(sorted(suppressed.items()))


def _default_production_liveness_suppression_config(bridge_root: Path) -> Path:
    return Path(bridge_root) / "shared" / PRODUCTION_LIVENESS_SUPPRESSION_FILENAME


def _production_liveness_suppression_map(
    extra_suppressed_agents: Mapping[str, str] | None,
) -> dict[str, str]:
    suppressed = dict(extra_suppressed_agents or {})
    _assert_no_private_markers(suppressed)
    return dict(sorted(suppressed.items()))


def recommend_next_action(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim],
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    open_request_max_age_hours: float | None = DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
    stale_report_max_age_hours: float | None = (
        DEFAULT_STALE_REQUEST_REPORT_MAX_AGE_HOURS
    ),
    production_idle_warn_minutes: float | None = (
        DEFAULT_PRODUCTION_IDLE_WARN_MINUTES
    ),
    production_liveness_suppressed_agents: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic next-action recommendation for ``agent``."""
    if not AGENT_ID_PATTERN.fullmatch(agent):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [f"agent must match {AGENT_ID_PATTERN.pattern}"],
            }
        )
    if (
        open_request_max_age_hours is not None
        and (
            not math.isfinite(open_request_max_age_hours)
            or open_request_max_age_hours <= 0
        )
    ):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": ["open_request_max_age_hours must be positive"],
            }
        )
    if (
        stale_report_max_age_hours is not None
        and (
            not math.isfinite(stale_report_max_age_hours)
            or stale_report_max_age_hours <= 0
        )
    ):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": ["stale_report_max_age_hours must be positive"],
            }
        )
    if (
        production_idle_warn_minutes is not None
        and (
            not math.isfinite(production_idle_warn_minutes)
            or production_idle_warn_minutes <= 0
        )
    ):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": ["production_idle_warn_minutes must be positive"],
            }
        )

    effective_now = now_utc or _latest_event_time(events) or datetime.now(timezone.utc)
    active_claims, stale_claims = _split_active_and_stale_claims(
        claims,
        now_utc=effective_now,
    )
    own_claims = [claim for claim in active_claims if claim.agent == agent]
    foreign_write_claims = [
        claim
        for claim in active_claims
        if claim.agent != agent and claim.mode == "write"
    ]
    all_open_requests = _open_requests_for_agent(agent=agent, events=events)
    open_request_events, stale_open_requests = _split_fresh_and_stale_requests(
        all_open_requests,
        now_utc=effective_now,
        max_age_hours=open_request_max_age_hours,
    )
    stale_suppression_index = _build_stale_incoming_suppression_index(
        events,
        agent=agent,
    )
    stale_open_requests = [
        request
        for request in stale_open_requests
        if not _stale_request_suppressed_by_index(
            request,
            stale_suppression_index=stale_suppression_index,
        )
    ]
    open_requests = _deduplicate_repeated_wake_requests(
        open_request_events,
        agent=agent,
    )
    reported_stale_open_requests, archived_stale_open_requests = (
        _split_reported_and_archived_stale_requests(
            stale_open_requests,
            now_utc=effective_now,
            max_age_hours=stale_report_max_age_hours,
        )
    )
    suppressed_agents = _production_liveness_suppression_map(
        production_liveness_suppressed_agents
    )
    production_liveness = _production_liveness_report(
        events=events,
        bridge_root=bridge_root,
        now_utc=effective_now,
        idle_warn_minutes=production_idle_warn_minutes,
        suppressed_agents=suppressed_agents,
    )
    suppression_reason = suppressed_agents.get(agent)
    merge_blocking_request = _latest_merge_blocking_request(open_requests)

    if own_claims and merge_blocking_request is not None:
        request = merge_blocking_request
        requester = _event_agent(request)
        kind = f"{_event_type(request)}/{_event_status(request)}".strip("/")
        summary = f"answer merge-blocking incoming {kind} from {requester}"
        return _report(
            agent=agent,
            action="answer_incoming",
            task_id=_task_id(request),
            safe_mode="read-only",
            summary=summary,
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
            request=request,
        )
    if own_claims:
        claim = own_claims[0]
        return _report(
            agent=agent,
            action="continue_claim",
            task_id=claim.task_id,
            safe_mode=claim.mode,
            summary=f"continue active claim {claim.task_id}",
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
        )
    if suppression_reason is not None:
        report = _report(
            agent=agent,
            action="agent_suppressed_unavailable",
            task_id="agent-suppressed-unavailable",
            safe_mode="read-only",
            summary=f"agent {agent} is suppressed unavailable: {suppression_reason}",
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
        )
        report["suppression_reason"] = suppression_reason
        _assert_no_private_markers(report)
        return report
    if open_requests:
        priority_request = _latest_direct_rco_pass_block_request(
            agent=agent,
            requests=open_requests,
        )
        request = priority_request or open_requests[-1]
        requester = _event_agent(request)
        kind = f"{_event_type(request)}/{_event_status(request)}".strip("/")
        if priority_request is not None:
            summary = f"answer direct RCO pass/block incoming {kind} from {requester}"
        else:
            summary = f"answer incoming {kind} from {requester}"
        return _report(
            agent=agent,
            action="answer_incoming",
            task_id=_task_id(request),
            safe_mode="read-only",
            summary=summary,
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
            request=request,
        )
    wake_delivery_escalation = _wake_delivery_escalation_from_liveness(
        production_liveness
    )
    if wake_delivery_escalation is not None:
        target_agents = _string_list(wake_delivery_escalation.get("target_agents"))
        target_text = ",".join(target_agents) if target_agents else "unknown"
        safe_next_action = str(
            wake_delivery_escalation.get("safe_next_action") or ""
        )
        return _report(
            agent=agent,
            action="escalate_wake_delivery_stall",
            task_id="bridge-wake-delivery-stalled",
            safe_mode="read-only",
            summary=(
                "operator action required for stalled wake delivery "
                f"to {target_text}: {safe_next_action}"
            ),
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
        )
    if foreign_write_claims:
        claim = foreign_write_claims[0]
        scope = ",".join(claim.write_scope)
        return _report(
            agent=agent,
            action="parallel_read_only",
            task_id="bridge-review-or-scout",
            safe_mode="read-only",
            summary=f"foreign write claim active; take read-only work outside scope: {scope}",
            events=events,
            claims=active_claims,
            stale_claims=stale_claims,
            open_requests=open_requests,
            open_request_event_count=len(open_request_events),
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
        )
    return _report(
        agent=agent,
        action="claim_unblocked_work",
        task_id="next-unclaimed-scout-or-implementation",
        safe_mode="write-or-read-only",
        summary="no active claim or incoming blocker; claim the highest-value unblocked work",
        events=events,
        claims=active_claims,
        stale_claims=stale_claims,
        open_requests=open_requests,
        open_request_event_count=len(open_request_events),
        stale_open_requests=reported_stale_open_requests,
        archived_stale_open_requests=archived_stale_open_requests,
        foreign_write_claims=foreign_write_claims,
        production_liveness=production_liveness,
    )


def _open_requests_for_agent(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    closure_index = _build_request_closure_index(events)
    idle_progress_index = _build_idle_protocol_progress_index(events)
    requests = [
        event
        for event in events
        if _is_request_like(event) and _addressed_to(event, agent)
    ]
    open_requests: list[Mapping[str, Any]] = []
    for request in requests:
        if _is_direct_rco_pass_block_request(agent=agent, event=request):
            answered = _direct_rco_pass_block_request_closed(
                request=request,
                agent=agent,
                events=events,
            )
        else:
            answered = _request_closed_by_index(
                request=request,
                agent=agent,
                closure_index=closure_index,
            )
        if not answered and _idle_protocol_progressed_by_index(
            request,
            idle_progress_index,
        ):
            answered = True
        if not answered:
            open_requests.append(request)
    return open_requests


def _build_request_closure_index(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Return latest answer-like event timestamps by task and closing agent."""
    closure_index: dict[str, dict[str, str]] = {}
    for event in events:
        if not _is_answer_like(event):
            continue
        event_agent = _event_agent(event)
        event_ts = _event_ts(event)
        task_id = _task_id(event)
        if task_id:
            closure_keys = {_task_closure_key(task_id)}
        else:
            closure_keys = _empty_task_closure_keys_for_answer(event)
        pr_closure_key = _pr_closure_key_for_event(event)
        if pr_closure_key:
            closure_keys.add(pr_closure_key)
        for closure_key in closure_keys:
            task_closures = closure_index.setdefault(closure_key, {})
            if event_ts > task_closures.get(event_agent, ""):
                task_closures[event_agent] = event_ts
            if closure_key.startswith(
                PR_CLOSURE_KEY_PREFIX
            ) and _is_explicit_terminal_pr_closure(event):
                terminal_agent = _pr_requester_terminal_agent_key(event_agent)
                if event_ts > task_closures.get(terminal_agent, ""):
                    task_closures[terminal_agent] = event_ts
    return closure_index


def _request_closed_by_index(
    *,
    request: Mapping[str, Any],
    agent: str,
    closure_index: Mapping[str, Mapping[str, str]],
) -> bool:
    task_id = _task_id(request)
    request_ts = _event_ts(request)
    closure_keys = []
    if task_id:
        closure_keys.append(_task_closure_key(task_id))
    else:
        closure_keys.append(
            _empty_task_closure_key(
                requester=_event_agent(request),
                target=agent.lower(),
            )
        )
    pr_closure_key = _pr_closure_key_for_event(request)
    if pr_closure_key:
        task_closures = closure_index.get(pr_closure_key, {})
        if task_closures:
            target_agent = agent.lower()
            if task_closures.get(target_agent, "") > request_ts:
                return True
            requester_terminal_agent = _pr_requester_terminal_agent_key(
                _event_agent(request)
            )
            if task_closures.get(requester_terminal_agent, "") > request_ts:
                return True
    for closure_key in closure_keys:
        task_closures = closure_index.get(closure_key, {})
        if not task_closures:
            continue
        for closing_agent in {agent.lower(), _event_agent(request)}:
            if task_closures.get(closing_agent, "") > request_ts:
                return True
    return False


def _task_closure_key(task_id: str) -> str:
    return f"{TASK_CLOSURE_KEY_PREFIX}{task_id}"


def _empty_task_closure_key(*, requester: str, target: str) -> str:
    return f"{EMPTY_TASK_CLOSURE_KEY_PREFIX}{requester.lower()}->{target.lower()}"


def _empty_task_closure_keys_for_answer(event: Mapping[str, Any]) -> set[str]:
    event_agent = _event_agent(event)
    keys: set[str] = set()
    for recipient in _event_recipients(event):
        keys.add(_empty_task_closure_key(requester=recipient, target=event_agent))
        keys.add(_empty_task_closure_key(requester=event_agent, target=recipient))
    return keys


def _pr_closure_key_for_event(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    for key in ("pr", "pr_number"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return f"{PR_CLOSURE_KEY_PREFIX}{value}"
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdecimal():
                return f"{PR_CLOSURE_KEY_PREFIX}{int(normalized)}"
    return None


def _is_explicit_terminal_pr_closure(event: Mapping[str, Any]) -> bool:
    return (
        _event_type(event) == "done"
        or _event_status(event) in CLOSED_REQUEST_STATUSES
    )


def _pr_requester_terminal_agent_key(agent: str) -> str:
    return f"{PR_REQUESTER_TERMINAL_AGENT_PREFIX}{agent}"


def _deduplicate_repeated_wake_requests(
    requests: Sequence[Mapping[str, Any]],
    *,
    agent: str,
) -> list[Mapping[str, Any]]:
    """Collapse wake-request storms without hiding other request semantics."""
    deduped: list[Mapping[str, Any]] = []
    wake_request_indexes: dict[tuple[str, str, str, str], int] = {}
    target = agent.lower()
    for request in requests:
        if _event_type(request) != "wake_request":
            deduped.append(request)
            continue
        key = (
            _event_agent(request),
            _task_id(request),
            _event_status(request),
            target,
        )
        index = wake_request_indexes.get(key)
        if index is None:
            wake_request_indexes[key] = len(deduped)
            deduped.append(request)
        else:
            deduped[index] = request
    return deduped


def _closes_request_for_agent(
    *,
    event: Mapping[str, Any],
    request: Mapping[str, Any],
    agent: str,
) -> bool:
    if _task_id(event) != _task_id(request):
        return False
    if _event_ts(event) <= _event_ts(request):
        return False
    if not _is_answer_like(event):
        return False
    event_agent = _event_agent(event)
    return event_agent == agent or event_agent == _event_agent(request)


def _split_fresh_and_stale_requests(
    requests: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
    max_age_hours: float | None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if max_age_hours is None:
        return list(requests), []
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    fresh: list[Mapping[str, Any]] = []
    stale: list[Mapping[str, Any]] = []
    for request in requests:
        request_ts = _parse_utc(_event_ts(request))
        if request_ts is not None and request_ts < cutoff:
            stale.append(request)
        else:
            fresh.append(request)
    return fresh, stale


def _split_reported_and_archived_stale_requests(
    requests: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
    max_age_hours: float | None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if max_age_hours is None:
        return list(requests), []
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    reported: list[Mapping[str, Any]] = []
    archived: list[Mapping[str, Any]] = []
    for request in requests:
        request_ts = _parse_utc(_event_ts(request))
        if request_ts is not None and request_ts < cutoff:
            archived.append(request)
        else:
            reported.append(request)
    return reported, archived


def _build_stale_incoming_suppression_index(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
) -> dict[str, str]:
    """Return target-authored stale-sweep finding timestamps by stale task."""
    suppression_index: dict[str, str] = {}
    for event in events:
        if _event_agent(event) != agent:
            continue
        if _event_type(event) != "finding":
            continue
        if "stale_incoming" not in _event_status(event):
            continue
        event_ts = _event_ts(event)
        for task_id in _stale_finding_task_ids(event):
            if event_ts > suppression_index.get(task_id, ""):
                suppression_index[task_id] = event_ts
    return suppression_index


def _stale_finding_task_ids(event: Mapping[str, Any]) -> set[str]:
    payload = _payload(event)
    task_ids: set[str] = set(_string_list(payload.get("stale_task_ids")))
    evidence = payload.get("evidence")
    if isinstance(evidence, Sequence) and not isinstance(
        evidence,
        (str, bytes, bytearray),
    ):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if task_id:
                task_ids.add(task_id)
    return task_ids


def _stale_request_suppressed_by_index(
    request: Mapping[str, Any],
    *,
    stale_suppression_index: Mapping[str, str],
) -> bool:
    task_id = _task_id(request)
    if not task_id:
        return False
    return stale_suppression_index.get(task_id, "") > _event_ts(request)


def _split_active_and_stale_claims(
    claims: Sequence[Claim],
    *,
    now_utc: datetime,
) -> tuple[list[Claim], list[Claim]]:
    active: list[Claim] = []
    stale: list[Claim] = []
    for claim in claims:
        if _is_stale_claim(claim, now_utc=now_utc):
            stale.append(claim)
        else:
            active.append(claim)
    return active, stale


def _is_stale_claim(claim: Claim, *, now_utc: datetime) -> bool:
    heartbeat = _parse_utc(claim.last_heartbeat_utc) or _parse_utc(
        claim.claimed_at_utc
    )
    if heartbeat is None:
        return False

    lease_seconds = max(1, int(claim.lease_seconds or 0))
    effective_expiry = heartbeat + timedelta(seconds=lease_seconds)
    explicit_expiry = _parse_utc(claim.claim_lease_expires_utc)
    if explicit_expiry is not None and explicit_expiry > effective_expiry:
        effective_expiry = explicit_expiry
    return now_utc.astimezone(timezone.utc) >= effective_expiry


def _idle_protocol_progressed(
    request: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> bool:
    return _idle_protocol_progressed_by_index(
        request,
        _build_idle_protocol_progress_index(events),
    )


def _build_idle_protocol_progress_index(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    progress_index: dict[str, str] = {}
    for event in events:
        payload = _payload(event)
        if payload.get("protocol_version") != "idle-protocol.v1":
            continue
        event_ts = _event_ts(event)
        for field in (
            "responds_to",
            "consensus_target_proposal_id",
            "violating_proposal_id",
            "rejected_event_id",
        ):
            proposal_id = str(payload.get(field) or "")
            if proposal_id and event_ts > progress_index.get(proposal_id, ""):
                progress_index[proposal_id] = event_ts
    return progress_index


def _idle_protocol_progressed_by_index(
    request: Mapping[str, Any],
    progress_index: Mapping[str, str],
) -> bool:
    payload = _payload(request)
    if payload.get("protocol_version") != "idle-protocol.v1":
        return False
    proposal_id = str(payload.get("proposal_id") or "")
    if not proposal_id:
        return False
    request_ts = _event_ts(request)
    return progress_index.get(proposal_id, "") > request_ts


def _is_request_like(event: Mapping[str, Any]) -> bool:
    status = _event_status(event)
    if _is_closed_request_status(status):
        return False
    if _is_response_only_status(status):
        return False
    return _event_type(event) in REQUEST_TYPES and _status_has_any(
        status, OPEN_STATUS_FRAGMENTS
    )


def _latest_merge_blocking_request(
    requests: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for request in reversed(requests):
        if _is_merge_blocking_request(request):
            return request
    return None


def _latest_direct_rco_pass_block_request(
    *,
    agent: str,
    requests: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for request in reversed(requests):
        if _is_direct_rco_pass_block_request(agent=agent, event=request):
            return request
    return None


def _is_direct_rco_pass_block_request(
    *,
    agent: str,
    event: Mapping[str, Any],
) -> bool:
    if not _is_rco_agent(agent):
        return False
    tokens = _merge_blocking_signal_tokens(event)
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


def _is_rco_agent(agent: str) -> bool:
    return "rco" in _status_tokens(agent)


def _direct_rco_pass_block_request_closed(
    *,
    request: Mapping[str, Any],
    agent: str,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    request_ts = _event_ts(request)
    request_task_id = _task_id(request)
    request_pr_key = _pr_closure_key_for_event(request)
    requester = _event_agent(request)
    target = agent.lower()
    for event in events:
        event_ts = _event_ts(event)
        if event_ts <= request_ts:
            continue
        same_task = bool(request_task_id and _task_id(event) == request_task_id)
        event_pr_key = _pr_closure_key_for_event(event)
        same_pr = bool(request_pr_key and event_pr_key == request_pr_key)
        if not same_task and not same_pr:
            continue
        event_agent = _event_agent(event)
        if event_agent == target and _is_substantive_rco_pass_block_response(event):
            return True
        if event_agent == requester and _is_explicit_terminal_pr_closure(event):
            return True
    return False


def _is_substantive_rco_pass_block_response(event: Mapping[str, Any]) -> bool:
    status_tokens = _status_tokens(_event_status(event))
    if status_tokens.intersection(KNOWN_ACK_STATUSES) or {"wake", "ack"}.issubset(
        status_tokens
    ):
        return False
    if _event_type(event) == "finding":
        return True
    tokens = _merge_blocking_signal_tokens(event)
    if {"rco", "pass"}.issubset(tokens):
        return True
    if "block" in tokens or "blocked" in tokens:
        return True
    if {"changes", "requested"}.issubset(tokens):
        return True
    return False


def _is_merge_blocking_request(event: Mapping[str, Any]) -> bool:
    tokens = _merge_blocking_signal_tokens(event)
    if {"build", "consensus"}.issubset(tokens) and tokens.intersection(
        {"request", "requested", "required", "needed", "missing"}
    ):
        return True
    if {"rco", "pass"}.issubset(tokens) and tokens.intersection(
        {"request", "requested", "required", "needed", "missing"}
    ):
        return True
    if {"rco", "reemit"}.issubset(tokens) and tokens.intersection(
        {"request", "requested", "required", "needed", "missing"}
    ):
        return True
    if "merge" in tokens and tokens.intersection(
        {"ready", "eligible", "blocking", "blocker", "required", "needed"}
    ):
        return True
    return False


def _merge_blocking_signal_tokens(event: Mapping[str, Any]) -> set[str]:
    fields: list[str] = [
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
    text = " ".join(fields)
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


def _is_answer_like(event: Mapping[str, Any]) -> bool:
    if _event_type(event) == "done":
        return True
    status = _event_status(event)
    return _event_type(event) in ANSWER_TYPES and (
        status in CLOSED_REQUEST_STATUSES
        or _status_has_any(status, ANSWER_STATUS_FRAGMENTS)
    )


def _is_closed_request_status(status: str) -> bool:
    return status in CLOSED_REQUEST_STATUSES


def _status_has_any(status: str, candidates: Sequence[str]) -> bool:
    tokens = _status_tokens(status)
    return any(candidate in tokens for candidate in candidates)


def _is_response_only_status(status: str) -> bool:
    tokens = _status_tokens(status)
    if "not" in tokens or tokens.intersection({"required", "needed", "missing"}):
        return False
    if tokens.intersection({"request", "requested"}) and not tokens.intersection(
        set(RESPONSE_ONLY_STATUS_FRAGMENTS) - {"pass"}
    ):
        return False
    return any(candidate in tokens for candidate in RESPONSE_ONLY_STATUS_FRAGMENTS)


def _status_tokens(status: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}


def _addressed_to(event: Mapping[str, Any], agent: str) -> bool:
    target = agent.lower()
    return target in _event_recipients(event)


def _event_recipients(event: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        item.strip().lower()
        for item in re.split(r"[,;\s]+", str(event.get("to") or ""))
        if item.strip()
    )


def _task_id(event: Mapping[str, Any]) -> str:
    return str(event.get("task_id") or event.get("id") or "")


def _event_agent(event: Mapping[str, Any]) -> str:
    return str(event.get("agent") or event.get("author") or "unknown").lower()


def _event_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("role", "agent_uuid", "session_id"):
        value = str(event.get(key) or "").strip()
        if value:
            metadata[key] = value
    capabilities = _string_list(event.get("capabilities"))
    if capabilities:
        metadata["capabilities"] = capabilities
    return metadata


def _event_status(event: Mapping[str, Any]) -> str:
    return str(event.get("status") or "").lower()


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("type") or event.get("message_type") or "").lower()


def _event_ts(event: Mapping[str, Any]) -> str:
    return str(event.get("ts_utc") or event.get("timestamp") or "")


def _latest_event_time(events: Sequence[Mapping[str, Any]]) -> datetime | None:
    parsed = [
        value
        for value in (_parse_utc(_event_ts(event)) for event in events)
        if value is not None
    ]
    return max(parsed) if parsed else None


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    normalized = _trim_fractional_seconds(normalized)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trim_fractional_seconds(value: str) -> str:
    return re.sub(
        r"(\.\d{6})\d+((?:[+-]\d{2}:\d{2})?)$",
        r"\1\2",
        value,
    )


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _latest_agent_metadata(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    for event in reversed(events):
        if _event_agent(event) != agent:
            continue
        metadata = _event_metadata(event)
        if metadata:
            return metadata
    return {}


def _production_liveness_report(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path | None = None,
    now_utc: datetime,
    idle_warn_minutes: float | None,
    suppressed_agents: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if idle_warn_minutes is None:
        return {}

    suppressed_lookup = dict(suppressed_agents or {})
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        event_ts = _parse_utc(_event_ts(event))
        if event_ts is None:
            continue
        event_agent = _event_agent(event)
        if event_agent in PRODUCTION_LIVENESS_IGNORED_AGENTS:
            continue
        state = states.setdefault(
            event_agent,
            {
                "first_event_ts": event_ts,
                "last_event_ts": event_ts,
                "last_heartbeat_ts": None,
                "last_activity_ts": None,
                "last_activity_type": "",
                "last_activity_status": "",
                "last_activity_task_id": "",
            },
        )
        if event_ts < state["first_event_ts"]:
            state["first_event_ts"] = event_ts
        if event_ts > state["last_event_ts"]:
            state["last_event_ts"] = event_ts
        if _event_type(event) in HEARTBEAT_ONLY_EVENT_TYPES:
            if (
                state["last_heartbeat_ts"] is None
                or event_ts > state["last_heartbeat_ts"]
            ):
                state["last_heartbeat_ts"] = event_ts
            continue
        if state["last_activity_ts"] is None or event_ts > state["last_activity_ts"]:
            state["last_activity_ts"] = event_ts
            state["last_activity_type"] = _event_type(event)
            state["last_activity_status"] = _event_status(event)
            state["last_activity_task_id"] = _task_id(event)

    stalled: list[dict[str, Any]] = []
    suppressed_stalled: list[dict[str, Any]] = []
    for event_agent, state in states.items():
        first_event_ts = state["first_event_ts"]
        last_heartbeat_ts = state["last_heartbeat_ts"]
        last_activity_ts = state["last_activity_ts"]
        if last_activity_ts is None:
            observed_minutes = _elapsed_minutes(now_utc, first_event_ts)
            if observed_minutes < idle_warn_minutes:
                continue
            record = {
                "agent": event_agent,
                "reason": "heartbeat_only_in_selected_events",
                "first_observed_ts_utc": _format_utc(first_event_ts),
                "last_heartbeat_ts_utc": _format_utc(last_heartbeat_ts),
                "observed_minutes": _round_minutes(observed_minutes),
            }
            _append_liveness_record(
                event_agent=event_agent,
                record=record,
                stalled=stalled,
                suppressed_stalled=suppressed_stalled,
                suppressed_lookup=suppressed_lookup,
            )
            continue

        idle_minutes = _elapsed_minutes(now_utc, last_activity_ts)
        if idle_minutes < idle_warn_minutes:
            continue
        heartbeat_only = (
            last_heartbeat_ts is not None and last_heartbeat_ts > last_activity_ts
        )
        reason = (
            "heartbeat_only_since_activity"
            if heartbeat_only
            else "no_activity_since_last_event"
        )
        _append_liveness_record(
            event_agent=event_agent,
            record={
                "agent": event_agent,
                "reason": reason,
                "last_activity_ts_utc": _format_utc(last_activity_ts),
                "last_activity_type": state["last_activity_type"],
                "last_activity_status": state["last_activity_status"],
                "last_activity_task_id": state["last_activity_task_id"],
                "last_heartbeat_ts_utc": _format_utc(last_heartbeat_ts),
                "idle_minutes": _round_minutes(idle_minutes),
                "heartbeat_only_since_activity": heartbeat_only,
            },
            stalled=stalled,
            suppressed_stalled=suppressed_stalled,
            suppressed_lookup=suppressed_lookup,
        )

    wake_delivery = _wake_delivery_liveness_summary(
        events=events,
        bridge_root=bridge_root,
        now_utc=now_utc,
    )
    if not stalled and not suppressed_stalled and not wake_delivery:
        return {}
    for records in (stalled, suppressed_stalled):
        records.sort(
            key=lambda item: (
                float(item.get("idle_minutes") or item.get("observed_minutes") or 0.0),
                str(item.get("agent") or ""),
            ),
            reverse=True,
        )
    report: dict[str, Any] = {
        "idle_warn_minutes": float(idle_warn_minutes),
        "stalled_agent_count": len(stalled),
        "stalled_agents": stalled,
    }
    if suppressed_stalled:
        report["suppressed_stalled_agent_count"] = len(suppressed_stalled)
        report["suppressed_stalled_agents"] = suppressed_stalled
    if wake_delivery:
        report["wake_delivery"] = wake_delivery
    return report


def _wake_delivery_liveness_summary(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path | None,
    now_utc: datetime,
) -> dict[str, Any]:
    groups = _unresolved_wake_delivery_groups(events)
    stalled: list[dict[str, Any]] = []
    self_pacing: list[dict[str, Any]] = []
    max_age_minutes = DEFAULT_WAKE_DELIVERY_MAX_AGE_HOURS * 60.0
    self_liveness_by_agent = _latest_wake_delivery_self_liveness_by_agent(events)
    for group in groups.values():
        first_ts = _parse_utc(str(group["first_ts_utc"]))
        last_ts = _parse_utc(str(group["last_ts_utc"]))
        if first_ts is None or last_ts is None:
            continue
        age_minutes = _elapsed_minutes(now_utc, first_ts)
        latest_wake_age_minutes = _elapsed_minutes(now_utc, last_ts)
        if age_minutes < DEFAULT_WAKE_DELIVERY_MIN_AGE_MINUTES:
            continue
        if latest_wake_age_minutes > max_age_minutes:
            continue
        if int(group["wake_request_count"]) < DEFAULT_WAKE_DELIVERY_MIN_REPEATS:
            continue
        self_liveness = _wake_delivery_self_liveness_suppression(
            group,
            self_liveness_by_agent=self_liveness_by_agent,
            now_utc=now_utc,
        )
        if self_liveness is not None:
            self_pacing.append(
                _wake_delivery_row(
                    group,
                    bridge_root=bridge_root,
                    age_minutes=age_minutes,
                    latest_wake_age_minutes=latest_wake_age_minutes,
                    classification="self_pacing_or_silent_by_design",
                    self_liveness=self_liveness,
                )
            )
            continue
        stalled.append(
            _wake_delivery_row(
                group,
                bridge_root=bridge_root,
                age_minutes=age_minutes,
                latest_wake_age_minutes=latest_wake_age_minutes,
            )
        )
    if not stalled and not self_pacing:
        return {}
    stalled.sort(
        key=lambda row: (
            -int(row["wake_request_count"]),
            -float(row["age_minutes"]),
            str(row["target_agent"]),
            str(row["task_id"]),
        )
    )
    self_pacing.sort(
        key=lambda row: (
            -int(row["wake_request_count"]),
            -float(row["age_minutes"]),
            str(row["target_agent"]),
            str(row["task_id"]),
        )
    )
    by_agent: dict[str, int] = {}
    for row in stalled:
        target = str(row["target_agent"])
        by_agent[target] = by_agent.get(target, 0) + 1
    return {
        "decision": "wake_delivery_stalled" if stalled else "wake_delivery_ok",
        "stalled_wake_count": len(stalled),
        "by_agent": dict(sorted(by_agent.items())),
        "delivery_escalation": (
            _wake_delivery_escalation(by_agent)
            if stalled
            else _wake_delivery_no_escalation()
        ),
        "stalled_wakes": stalled,
        "self_liveness_window_minutes": (
            DEFAULT_WAKE_DELIVERY_SELF_LIVENESS_WINDOW_MINUTES
        ),
        "self_pacing_wake_count": len(self_pacing),
        "self_pacing_wakes": self_pacing,
    }


def _wake_delivery_escalation(by_agent: Mapping[str, int]) -> dict[str, Any]:
    return {
        "required": True,
        "target_agents": sorted(by_agent),
        "do_not_emit_additional_wake_requests": True,
        "safe_next_action": "restart_or_verify_target_agent_bridge_session_watcher",
        "operator_action_required": True,
        "reason": "wake_request_visible_but_no_later_target_bridge_activity",
    }


def _wake_delivery_no_escalation() -> dict[str, Any]:
    return {
        "required": False,
        "target_agents": [],
        "do_not_emit_additional_wake_requests": False,
        "safe_next_action": "",
        "operator_action_required": False,
        "reason": "",
    }


def _wake_delivery_escalation_from_liveness(
    production_liveness: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    wake_delivery = production_liveness.get("wake_delivery")
    if not isinstance(wake_delivery, Mapping):
        return None
    escalation = wake_delivery.get("delivery_escalation")
    if not isinstance(escalation, Mapping):
        return None
    if escalation.get("operator_action_required") is not True:
        return None
    return escalation


def _unresolved_wake_delivery_groups(
    events: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        event_agent = _event_agent(event)
        event_ts = _event_ts(event)
        if event_agent and _is_wake_delivery_activity(event):
            _clear_wake_delivery_groups_for_target_activity(
                groups,
                event_agent=event_agent,
                event_ts=event_ts,
            )
        _clear_wake_delivery_groups_for_terminal_task(groups, event)
        if _event_type(event) != "wake_request":
            continue
        if _event_status(event) in CLOSED_REQUEST_STATUSES:
            continue
        task_id = _task_id(event)
        if not task_id:
            continue
        for target in _event_recipients(event):
            if not target or target == event_agent:
                continue
            if target in WAKE_DELIVERY_IGNORED_TARGETS:
                continue
            key = (target, task_id)
            existing = groups.get(key)
            if existing is None:
                groups[key] = {
                    "target_agent": target,
                    "task_id": task_id,
                    "first_ts_utc": event_ts,
                    "last_ts_utc": event_ts,
                    "requesters": {event_agent} if event_agent else set(),
                    "wake_request_count": 1,
                    "last_status": _event_status(event),
                }
                continue
            existing["last_ts_utc"] = event_ts
            existing["wake_request_count"] = int(existing["wake_request_count"]) + 1
            existing["last_status"] = _event_status(event)
            if event_agent:
                requesters = existing["requesters"]
                if isinstance(requesters, set):
                    requesters.add(event_agent)
    return groups


def _is_wake_delivery_activity(event: Mapping[str, Any]) -> bool:
    return _event_type(event) not in HEARTBEAT_ONLY_EVENT_TYPES


def _is_wake_delivery_self_liveness_activity(event: Mapping[str, Any]) -> bool:
    if _event_type(event) in HEARTBEAT_ONLY_EVENT_TYPES:
        return False
    return not (_event_type(event) == "message" and _event_status(event) == "received")


def _latest_wake_delivery_self_liveness_by_agent(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for event in events:
        agent = _event_agent(event)
        if not agent or not _is_wake_delivery_self_liveness_activity(event):
            continue
        event_ts = _parse_utc(_event_ts(event))
        if event_ts is None:
            continue
        existing = latest.get(agent)
        if existing is None or event_ts > existing:
            latest[agent] = event_ts
    return latest


def _wake_delivery_self_liveness_suppression(
    group: Mapping[str, Any],
    *,
    self_liveness_by_agent: Mapping[str, datetime],
    now_utc: datetime,
) -> dict[str, Any] | None:
    target = str(group["target_agent"])
    last_self = self_liveness_by_agent.get(target)
    if last_self is None:
        return None
    last_wake = _parse_utc(str(group["last_ts_utc"]))
    if last_wake is not None and last_self > last_wake:
        reason = "target_self_activity_after_latest_wake"
    else:
        reason = "target_self_activity_within_liveness_window"
    self_age_minutes = _elapsed_minutes(now_utc, last_self)
    if (
        reason != "target_self_activity_after_latest_wake"
        and self_age_minutes >= DEFAULT_WAKE_DELIVERY_SELF_LIVENESS_WINDOW_MINUTES
    ):
        return None
    return {
        "last_self_activity_ts_utc": _format_utc(last_self),
        "last_self_activity_age_minutes": _round_minutes(self_age_minutes),
        "self_liveness_reason": reason,
    }


def _clear_wake_delivery_groups_for_target_activity(
    groups: dict[tuple[str, str], dict[str, Any]],
    *,
    event_agent: str,
    event_ts: str,
) -> None:
    for key, group in list(groups.items()):
        target, _task_id_value = key
        if target != event_agent:
            continue
        last_ts = str(group["last_ts_utc"])
        if event_ts and event_ts > last_ts:
            del groups[key]


def _clear_wake_delivery_groups_for_terminal_task(
    groups: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
) -> None:
    if _event_type(event) != "done" and _event_status(event) not in CLOSED_REQUEST_STATUSES:
        return
    task_id = _task_id(event)
    if not task_id:
        return
    for key in list(groups):
        _target, group_task_id = key
        if group_task_id == task_id:
            del groups[key]


def _wake_delivery_row(
    group: Mapping[str, Any],
    *,
    bridge_root: Path | None,
    age_minutes: float,
    latest_wake_age_minutes: float,
    classification: str = "stalled_wake_delivery",
    self_liveness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = str(group["target_agent"])
    wake_file = _wake_file_status(bridge_root, target)
    requesters = group.get("requesters")
    requester_list = (
        sorted(str(item) for item in requesters) if isinstance(requesters, set) else []
    )
    if classification == "self_pacing_or_silent_by_design":
        diagnosis = (
            "target agent has recent self-authored bridge activity inside "
            "the self-liveness window; treat as self-paced or silent by design"
        )
        safe_next_action = (
            "wait for the target self-paced loop or recheck after the "
            "self-liveness window; do not restart solely from repeated wakes"
        )
    else:
        diagnosis = (
            "wake file exists but target agent has not emitted bridge activity"
            if wake_file["wake_file_present"]
            else (
                "no target activity after repeated wake_request; watcher may "
                "be absent or target may not be polling"
            )
        )
        safe_next_action = (
            "restart or verify the target agent bridge session watcher/poll loop; "
            "do not treat additional wake_request events as delivery proof"
        )
    row = {
        "classification": classification,
        "target_agent": target,
        "task_id": group["task_id"],
        "requesters": requester_list,
        "first_ts_utc": group["first_ts_utc"],
        "last_ts_utc": group["last_ts_utc"],
        "age_minutes": _round_minutes(age_minutes),
        "latest_wake_age_minutes": _round_minutes(latest_wake_age_minutes),
        "wake_request_count": group["wake_request_count"],
        "last_status": group["last_status"],
        "wake_file_checked": bridge_root is not None,
        **wake_file,
        "diagnosis": diagnosis,
        "safe_next_action": safe_next_action,
    }
    if self_liveness:
        row.update(self_liveness)
    return row


def _wake_file_status(bridge_root: Path | None, target: str) -> dict[str, Any]:
    if bridge_root is None:
        return {"wake_file_present": False, "wake_file_mtime_utc": ""}
    wake_path = bridge_root / f"wake_{target}"
    if not wake_path.exists():
        return {"wake_file_present": False, "wake_file_mtime_utc": ""}
    try:
        mtime = datetime.fromtimestamp(wake_path.stat().st_mtime, tz=timezone.utc)
        return {
            "wake_file_present": True,
            "wake_file_mtime_utc": _format_utc(mtime),
        }
    except OSError:
        return {"wake_file_present": True, "wake_file_mtime_utc": ""}


def _append_liveness_record(
    *,
    event_agent: str,
    record: dict[str, Any],
    stalled: list[dict[str, Any]],
    suppressed_stalled: list[dict[str, Any]],
    suppressed_lookup: Mapping[str, str],
) -> None:
    suppressed_reason = suppressed_lookup.get(event_agent)
    if suppressed_reason is None:
        stalled.append(record)
        return
    suppressed_record = dict(record)
    suppressed_record["suppressed_reason"] = suppressed_reason
    suppressed_stalled.append(suppressed_record)


def _elapsed_minutes(later: datetime, earlier: datetime) -> float:
    return max(0.0, (later - earlier).total_seconds() / 60.0)


def _round_minutes(value: float) -> float:
    return round(value, 3)


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _claim_metadata(claim: Claim) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "agent": claim.agent,
        "task_id": claim.task_id,
        "mode": claim.mode,
        "write_scope": list(claim.write_scope),
        "lease_seconds": claim.lease_seconds,
    }
    if claim.claim_lease_expires_utc:
        metadata["claim_lease_expires_utc"] = claim.claim_lease_expires_utc
    if claim.role:
        metadata["role"] = claim.role
    if claim.agent_uuid:
        metadata["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        metadata["capabilities"] = list(claim.capabilities)
    return metadata


def _claim_snapshot(
    *,
    agent: str,
    claims: Sequence[Claim],
    foreign_write_claims: Sequence[Claim],
) -> dict[str, list[dict[str, Any]]]:
    own_claims = [claim for claim in claims if claim.agent == agent]
    snapshot: dict[str, list[dict[str, Any]]] = {
        "own": [_claim_metadata(claim) for claim in own_claims],
        "foreign_write": [
            _claim_metadata(claim) for claim in foreign_write_claims
        ],
    }
    return snapshot


def _message(event: Mapping[str, Any], *, limit: int = 220) -> str:
    text = " ".join(str(event.get("message") or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _unique_task_ids(events: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: set[str] = set()
    task_ids: list[str] = []
    for event in events:
        task_id = _task_id(event)
        if task_id in seen:
            continue
        seen.add(task_id)
        task_ids.append(task_id)
    return task_ids


def _report(
    *,
    agent: str,
    action: str,
    task_id: str,
    safe_mode: str,
    summary: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim],
    open_requests: Sequence[Mapping[str, Any]],
    open_request_event_count: int,
    stale_open_requests: Sequence[Mapping[str, Any]],
    archived_stale_open_requests: Sequence[Mapping[str, Any]],
    foreign_write_claims: Sequence[Claim],
    stale_claims: Sequence[Claim],
    production_liveness: Mapping[str, Any],
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stale_task_ids = _unique_task_ids(stale_open_requests)
    archived_stale_task_ids = _unique_task_ids(archived_stale_open_requests)
    payload: dict[str, Any] = {
        "ok": True,
        "decision": "bridge_next_action",
        "agent": agent,
        "action": action,
        "task_id": task_id,
        "safe_mode": safe_mode,
        "summary": summary,
        "active_claim_count": len(claims),
        "open_incoming_count": len(open_requests),
        "stale_incoming_count": len(stale_task_ids),
        "foreign_write_claim_count": len(foreign_write_claims),
    }
    if open_request_event_count != len(open_requests):
        payload["open_incoming_event_count"] = open_request_event_count
        payload["deduplicated_open_incoming_count"] = len(open_requests)
        payload["open_incoming_duplicate_count"] = (
            open_request_event_count - len(open_requests)
        )
    agent_profile = _latest_agent_metadata(agent=agent, events=events)
    if agent_profile:
        payload["agent_profile"] = agent_profile
    claim_snapshot = _claim_snapshot(
        agent=agent,
        claims=claims,
        foreign_write_claims=foreign_write_claims,
    )
    if claim_snapshot["own"] or claim_snapshot["foreign_write"]:
        payload["claim_snapshot"] = claim_snapshot
    if stale_claims:
        payload["ignored_stale_claim_count"] = len(stale_claims)
        payload["ignored_stale_claims"] = [
            _claim_metadata(claim) for claim in stale_claims
        ]
    if stale_open_requests:
        payload["stale_incoming_task_ids"] = stale_task_ids
        payload["stale_incoming_event_count"] = len(stale_open_requests)
    if archived_stale_open_requests:
        payload["archived_stale_incoming_count"] = len(archived_stale_task_ids)
        payload["archived_stale_incoming_task_ids"] = archived_stale_task_ids
        payload["archived_stale_incoming_event_count"] = len(
            archived_stale_open_requests
        )
    if production_liveness:
        payload["production_liveness"] = dict(production_liveness)
        wake_delivery_escalation = _wake_delivery_escalation_from_liveness(
            production_liveness
        )
        if wake_delivery_escalation is not None:
            payload["operator_action_required"] = True
            payload["operator_action"] = str(
                wake_delivery_escalation.get("safe_next_action") or ""
            )
            payload["operator_action_reason"] = str(
                wake_delivery_escalation.get("reason") or ""
            )
            payload["operator_action_target_agents"] = _string_list(
                wake_delivery_escalation.get("target_agents")
            )
    if request is not None:
        incoming = {
            "agent": _event_agent(request),
            "type": _event_type(request),
            "status": _event_status(request),
            "ts_utc": _event_ts(request),
            "message": _message(request),
        }
        incoming.update(_event_metadata(request))
        payload["incoming"] = incoming
    _assert_no_private_markers(payload)
    return payload


def _assert_no_private_markers(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    if any(marker in text for marker in PRIVATE_MARKERS):
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_refused",
                "errors": ["private marker present in selected bridge output"],
            }
        )


def _print_human(report: Mapping[str, Any]) -> None:
    print(report.get("decision", "unknown"))
    if not report.get("ok", False):
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    print(f"agent: {report.get('agent', '')}")
    profile = report.get("agent_profile")
    if isinstance(profile, Mapping):
        print(f"agent_profile: {_format_metadata(profile)}")
    print(f"action: {report.get('action', '')}")
    print(f"task_id: {report.get('task_id', '')}")
    print(f"safe_mode: {report.get('safe_mode', '')}")
    snapshot = report.get("claim_snapshot")
    if isinstance(snapshot, Mapping):
        own_count = len(snapshot.get("own", []))
        foreign_count = len(snapshot.get("foreign_write", []))
        print(f"claim_snapshot: own={own_count} foreign_write={foreign_count}")
    open_event_count = int(report.get("open_incoming_event_count", 0) or 0)
    if open_event_count:
        print(f"open_incoming_event_count: {open_event_count}")
        print(
            "open_incoming_duplicate_count: "
            f"{int(report.get('open_incoming_duplicate_count', 0) or 0)}"
        )
    stale_count = int(report.get("stale_incoming_count", 0) or 0)
    if stale_count:
        print(f"stale_incoming_count: {stale_count}")
        task_ids = ", ".join(
            str(item) for item in report.get("stale_incoming_task_ids", [])
        )
        if task_ids:
            print(f"stale_incoming_task_ids: {task_ids}")
    archived_count = int(report.get("archived_stale_incoming_count", 0) or 0)
    if archived_count:
        print(f"archived_stale_incoming_count: {archived_count}")
        task_ids = ", ".join(
            str(item)
            for item in report.get("archived_stale_incoming_task_ids", [])
        )
        if task_ids:
            print(f"archived_stale_incoming_task_ids: {task_ids}")
    liveness = report.get("production_liveness")
    if isinstance(liveness, Mapping):
        stalled_count = int(liveness.get("stalled_agent_count", 0) or 0)
        if stalled_count:
            print(f"production_liveness_stalled_agent_count: {stalled_count}")
        suppressed_count = int(
            liveness.get("suppressed_stalled_agent_count", 0) or 0
        )
        if suppressed_count:
            print(
                "production_liveness_suppressed_stalled_agent_count: "
                f"{suppressed_count}"
            )
        wake_delivery = liveness.get("wake_delivery")
        if isinstance(wake_delivery, Mapping):
            escalation = wake_delivery.get("delivery_escalation")
            if isinstance(escalation, Mapping) and escalation.get(
                "operator_action_required"
            ):
                targets = ", ".join(_string_list(escalation.get("target_agents")))
                print("wake_delivery_operator_action_required: true")
                if targets:
                    print(f"wake_delivery_target_agents: {targets}")
                safe_next_action = str(escalation.get("safe_next_action") or "")
                if safe_next_action:
                    print(f"wake_delivery_safe_next_action: {safe_next_action}")
    print(f"summary: {report.get('summary', '')}")


def _format_metadata(metadata: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("role", "agent_uuid", "session_id"):
        value = str(metadata.get(key) or "")
        if value:
            parts.append(f"{key}={value}")
    capabilities = _string_list(metadata.get("capabilities"))
    if capabilities:
        parts.append(f"capabilities={','.join(capabilities)}")
    return " ".join(parts) if parts else "none"


if __name__ == "__main__":
    raise SystemExit(main())
