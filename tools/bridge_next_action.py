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
import hashlib
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
from waggledance.core.bridge_identity_registry import (  # noqa: E402
    load_bridge_identity_registry,
)
from waggledance.core.bridge_log_reader import (  # noqa: E402
    BridgeReadStatus,
    MAX_MAX_BYTES,
    MAX_MAX_ROWS,
    parse_bridge_json_object,
    read_bridge_log_tail_lines,
)
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
# The append-only bridge's LF row 44000 contains one historical bare-CR
# separator. Its full physical-row SHA-256 is
# 207a8e5cba836e2e3e63b777b537b18d397bc58f01613d2034fa7b899395c0bd;
# the tail reader removes the final CRLF, so compatibility binds the
# normalized digest below.
_LEGACY_BARE_CR_ROW_SHA256 = (
    "53f863ac93dd977504346feddc382ccd65bafceb4aeaad2bba1765712190a0d3"
)
_LEGACY_BARE_CR_EVENT_FINGERPRINTS = (
    (
        "codex-lead-1",
        "production-liveness-reactivation-scout-2026-07-01-"
        "codex-tools-1-since-20260701t161039z",
        "2026-07-01T16:45:30.4576368Z",
        "test",
        "attention",
    ),
    (
        "codex-lead-1",
        "production-liveness-reactivation-scout-2026-07-01-"
        "codex-tools-1-since-20260701t161039z",
        "2026-07-01T16:46:54.4324612Z",
        "message",
        "bridge_log_repair_note",
    ),
)
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
        "autonomous_merge_receipt",
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
REQUESTER_TERMINAL_EVENT_TYPES = frozenset(
    {"status", "done", "release", "decision"}
)
REQUESTER_TERMINAL_STATUS_STEMS = (
    "done",
    "closed",
    "superseded",
    "merged",
    "abandoned",
    "completed",
    "approved",
    "cancelled",
    "canceled",
    "resolved",
    "postmerge",
    "validated",
    "verified",
)
REQUESTER_MESSAGE_TERMINAL_STATUS_STEMS = (
    "closed",
    "superseded",
    "cancelled",
    "canceled",
)
REQUESTER_EXPLICIT_TERMINAL_STATUSES = frozenset(
    {
        "autonomous_merge_receipt",
        "changes_requested_retracted",
        "changes_requested_resolved",
        "changes_requested_withdrawn",
        "finding_retracted",
        "finding_withdrawn",
        "rco_closed_postmerge",
        "rco_finding_retracted",
        "rco_finding_withdrawn",
    }
)
REQUESTER_NONTERMINAL_STATUS_TOKENS = frozenset(
    {
        "ack",
        "acknowledged",
        "cannot",
        "failed",
        "failure",
        "incomplete",
        "missing",
        "needed",
        "never",
        "not",
        "notyet",
        "open",
        "pending",
        "progress",
        "received",
        "request",
        "requested",
        "required",
        "seen",
        "unresolved",
        "working",
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
BRIDGE_FOLLOW_NUDGE_TASK_PREFIX = "bridge-follow-nudge-"
WAKE_FILE_FRESHNESS_TOLERANCE_SECONDS = 2.0
PRODUCTION_LIVENESS_IGNORED_AGENTS = {"operator", "system", "unknown"}
WAKE_DELIVERY_IGNORED_TARGETS = {*PRODUCTION_LIVENESS_IGNORED_AGENTS, "driver"}
HEARTBEAT_ONLY_EVENT_TYPES = {"heartbeat"}
WAKE_SEND_FAILED_TARGET_PATTERN = re.compile(
    r"\bKeying\s+['\"](?P<agent>[a-z0-9][a-z0-9_.-]*)['\"]\s+failed\b",
    re.IGNORECASE,
)
RCO_AGENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*-rco-\d+$")
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
        help=(
            "Maximum event lines to read from the end of the JSONL file; "
            f"<=0 requests the bounded maximum of {MAX_MAX_ROWS} rows."
        ),
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
    """Read selected JSONL rows, skipping ASCII blanks/null; fail closed otherwise."""
    generation_path = path.with_name("events.generation.json")
    snapshot = read_bridge_log_tail_lines(
        path,
        tail_rows=tail if tail > 0 else MAX_MAX_ROWS,
        max_bytes=MAX_MAX_BYTES,
        generation_path=generation_path,
    )
    if snapshot.status is BridgeReadStatus.IDLE and snapshot.reason == "log_missing":
        return []
    if snapshot.status not in {BridgeReadStatus.OK, BridgeReadStatus.IDLE}:
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [f"bridge event snapshot unavailable: {snapshot.reason}"],
            }
        )
    lines = list(snapshot.lines)
    events: list[dict[str, Any]] = []
    for selected_row, raw in enumerate(lines, start=1):
        ascii_trimmed = raw.strip(" \t\r")
        if not ascii_trimmed:
            continue
        if ascii_trimmed == "null":
            continue
        try:
            row_events = _parse_selected_bridge_row(raw)
        except ValueError as exc:
            raise BridgeNextActionError(
                {
                    "ok": False,
                    "decision": "bridge_next_action_error",
                    "errors": [
                        (
                            "invalid JSON object in bridge events at selected "
                            f"tail row {selected_row}: {exc}"
                        )
                    ],
                }
            ) from exc
        events.extend(row_events)
    return events


def _parse_selected_bridge_row(raw: str) -> tuple[dict[str, Any], ...]:
    """Strictly parse one LF row, with one exact historical compatibility case."""

    if "\r" not in raw:
        return (parse_bridge_json_object(raw),)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest != _LEGACY_BARE_CR_ROW_SHA256:
        return (parse_bridge_json_object(raw),)

    fragments = raw.split("\r")
    if len(fragments) != 2 or any(not fragment for fragment in fragments):
        raise ValueError(
            "known historical bare-CR row must contain exactly two non-empty fragments"
        )
    events = tuple(parse_bridge_json_object(fragment) for fragment in fragments)
    fingerprints = tuple(
        (
            event.get("agent"),
            event.get("task_id"),
            event.get("ts_utc"),
            event.get("type"),
            event.get("status"),
        )
        for event in events
    )
    if fingerprints != _LEGACY_BARE_CR_EVENT_FINGERPRINTS:
        raise ValueError(
            "known historical bare-CR row event fingerprints do not match"
        )
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

    effective_now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
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
    all_open_requests = _open_requests_for_agent(
        agent=agent,
        events=events,
        now_utc=effective_now,
    )
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
    now_utc: datetime,
) -> list[Mapping[str, Any]]:
    closure_index = _build_request_closure_index(events)
    idle_progress_index = _build_idle_protocol_progress_index(events)
    current_utc = now_utc.astimezone(timezone.utc)
    requests: list[tuple[tuple[datetime, int], Mapping[str, Any]]] = []
    for event_index, event in enumerate(events):
        if not _is_request_like(event) or not _addressed_to(event, agent):
            continue
        request_ts = _parse_utc(_event_ts(event))
        if request_ts is None or request_ts > current_utc:
            continue
        requests.append(((request_ts, event_index), event))
    open_requests: list[Mapping[str, Any]] = []
    for request_moment, request in requests:
        if _is_direct_rco_pass_block_request(agent=agent, event=request):
            answered = _direct_rco_pass_block_request_closed(
                request=request,
                request_moment=request_moment,
                agent=agent,
                events=events,
            )
        else:
            answered = _request_closed_by_index(
                request=request,
                request_moment=request_moment,
                agent=agent,
                closure_index=closure_index,
            )
        if not answered and _idle_protocol_progressed_by_index(
            request,
            request_moment=request_moment,
            progress_index=idle_progress_index,
        ):
            answered = True
        if not answered:
            open_requests.append(request)
    return open_requests


def _build_request_closure_index(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return target answers and identity-bound requester closures by task.

    Timestamped closures are kept as undominated moments of parsed event
    time plus append index: a closure counts against a request only when it
    follows the request in BOTH orders (see _closure_occurs_after_request),
    and a single maximum under either order could be shadowed by a stale
    replay or a post-dated closure. Closures without a parseable timestamp
    keep the legacy append-order behavior as a maximum append index.
    """
    closure_index: dict[str, dict[str, dict[str, Any]]] = {}
    for event_index, event in enumerate(events):
        answer_like = _is_answer_like(event)
        requester_terminal = _is_requester_terminal_closure(event)
        if not answer_like and not requester_terminal:
            continue
        closure_ts = _parse_utc(_event_ts(event))
        event_agent = _event_agent(event)
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
            if answer_like:
                _record_closure_entry(
                    task_closures.setdefault(event_agent, _new_closure_entry()),
                    closure_ts=closure_ts,
                    closure_index=event_index,
                )
            if requester_terminal:
                for terminal_agent in _requester_terminal_closure_keys(event):
                    _record_closure_entry(
                        task_closures.setdefault(
                            terminal_agent, _new_closure_entry()
                        ),
                        closure_ts=closure_ts,
                        closure_index=event_index,
                    )
    return closure_index


def _new_closure_entry() -> dict[str, Any]:
    return {"moments": [], "tsless_max_index": -1}


def _record_closure_entry(
    entry: dict[str, Any],
    *,
    closure_ts: datetime | None,
    closure_index: int,
) -> None:
    if closure_ts is None:
        entry["tsless_max_index"] = max(
            int(entry["tsless_max_index"]), closure_index
        )
    else:
        _merge_closure_moment(entry["moments"], (closure_ts, closure_index))


def _entry_closes_request(
    entry: Mapping[str, Any] | None,
    *,
    request_ts: datetime,
    request_index: int,
) -> bool:
    if entry is None:
        return False
    if int(entry["tsless_max_index"]) > request_index:
        return True
    return _any_moment_closes_request(
        entry["moments"],
        request_ts=request_ts,
        request_index=request_index,
    )


def _merge_closure_moment(
    moments: list[tuple[datetime, int]],
    moment: tuple[datetime, int],
) -> None:
    """Keep only closure moments undominated in both time and append order."""
    moment_ts, moment_index = moment
    for existing_ts, existing_index in moments:
        if existing_ts >= moment_ts and existing_index >= moment_index:
            return
    moments[:] = [
        (existing_ts, existing_index)
        for existing_ts, existing_index in moments
        if not (moment_ts >= existing_ts and moment_index >= existing_index)
    ]
    moments.append(moment)


def _any_moment_closes_request(
    moments: Sequence[tuple[datetime, int]],
    *,
    request_ts: datetime,
    request_index: int,
) -> bool:
    return any(
        moment_index > request_index and moment_ts >= request_ts
        for moment_ts, moment_index in moments
    )


def _request_closed_by_index(
    *,
    request: Mapping[str, Any],
    request_moment: tuple[datetime, int],
    agent: str,
    closure_index: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> bool:
    task_id = _task_id(request)
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
        closure_keys.append(pr_closure_key)
    request_ts, request_index = request_moment
    closer_keys = (agent.lower(), _requester_terminal_request_key(request))
    for closure_key in closure_keys:
        task_closures = closure_index.get(closure_key, {})
        if not task_closures:
            continue
        for closer_key in closer_keys:
            if _entry_closes_request(
                task_closures.get(closer_key),
                request_ts=request_ts,
                request_index=request_index,
            ):
                return True
    return False


def _closure_occurs_after_request(
    *,
    closure_ts: datetime | None,
    closure_index: int,
    request_ts: datetime | None,
    request_index: int,
) -> bool:
    """Require a closure to follow the request in BOTH log orders.

    Append order alone would let a delayed WAL/spool replay of a stale
    closure (old timestamp, appended at the log tail) silently close a
    request renewed after it; timestamp order alone would let a post-dated
    closure appended before the request suppress it. When either side has
    no parseable timestamp the comparison falls back to append order,
    preserving the legacy behavior for malformed records.
    """
    if closure_index <= request_index:
        return False
    if closure_ts is None or request_ts is None:
        return True
    return closure_ts >= request_ts


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


def _pr_number_for_event(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    for key in ("pr", "pr_number"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return str(value)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdecimal() and int(normalized) > 0:
                return str(int(normalized))
    return None


def _pr_closure_key_for_event(event: Mapping[str, Any]) -> str | None:
    pr_number = _pr_number_for_event(event)
    return f"{PR_CLOSURE_KEY_PREFIX}{pr_number}" if pr_number else None


def _is_requester_terminal_closure(event: Mapping[str, Any]) -> bool:
    """Return whether an original requester explicitly closes its request."""
    event_type = _event_type(event)
    status = _event_status(event)
    if not status:
        return False
    if event_type == "message":
        stems = REQUESTER_MESSAGE_TERMINAL_STATUS_STEMS
    elif event_type in REQUESTER_TERMINAL_EVENT_TYPES:
        stems = REQUESTER_TERMINAL_STATUS_STEMS
    else:
        return False
    if event_type != "message" and status in REQUESTER_EXPLICIT_TERMINAL_STATUSES:
        return True
    status_tokens = _status_tokens(status)
    if status_tokens.intersection(REQUESTER_NONTERMINAL_STATUS_TOKENS):
        return False
    return any(status == stem or status.startswith(f"{stem}_") for stem in stems)


def _requester_identity_value(
    event: Mapping[str, Any],
    field: str,
    *,
    lowercase: bool = False,
) -> str:
    value = str(event.get(field, "") or "").strip()
    return value.lower() if lowercase else value


def _requester_terminal_agent_key(
    *,
    agent: str,
    agent_uuid: str = "",
    session_id: str = "",
) -> str:
    identity = {
        "agent": agent.lower(),
        "agent_uuid": agent_uuid.lower(),
        "session_id": session_id,
    }
    return PR_REQUESTER_TERMINAL_AGENT_PREFIX + json.dumps(
        identity,
        separators=(",", ":"),
        sort_keys=True,
    )


def _requester_terminal_closure_keys(event: Mapping[str, Any]) -> set[str]:
    """Return aliases able to close legacy or identity-bound requests."""
    agent = _event_agent(event)
    agent_uuid = _requester_identity_value(event, "agent_uuid", lowercase=True)
    session_id = _requester_identity_value(event, "session_id")
    identities = {("", "")}
    if agent_uuid:
        identities.add((agent_uuid, ""))
    if session_id:
        identities.add(("", session_id))
    if agent_uuid and session_id:
        identities.add((agent_uuid, session_id))
    return {
        _requester_terminal_agent_key(
            agent=agent,
            agent_uuid=identity_uuid,
            session_id=identity_session,
        )
        for identity_uuid, identity_session in identities
    }


def _requester_terminal_request_key(request: Mapping[str, Any]) -> str:
    """Return the most-specific requester identity required by a request."""
    return _requester_terminal_agent_key(
        agent=_event_agent(request),
        agent_uuid=_requester_identity_value(request, "agent_uuid", lowercase=True),
        session_id=_requester_identity_value(request, "session_id"),
    )


def _requester_identity_matches(
    request: Mapping[str, Any],
    closure: Mapping[str, Any],
) -> bool:
    """Bind requester closeout to every identity field present on the request."""
    if _event_agent(request) != _event_agent(closure):
        return False
    request_uuid = _requester_identity_value(request, "agent_uuid", lowercase=True)
    if request_uuid and request_uuid != _requester_identity_value(
        closure,
        "agent_uuid",
        lowercase=True,
    ):
        return False
    request_session = _requester_identity_value(request, "session_id")
    if request_session and request_session != _requester_identity_value(
        closure,
        "session_id",
    ):
        return False
    return True


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
            _requester_terminal_request_key(request),
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


def _build_idle_protocol_progress_index(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index idle-protocol progress by proposal id.

    Timestamped progress keeps undominated (time, append index) moments and
    counts only when it follows the proposal in both orders, mirroring the
    request-closure rule. Progress without a parseable timestamp keeps the
    legacy append-order behavior, tracked as a maximum append index.
    """
    progress_index: dict[str, dict[str, Any]] = {}
    for event_index, event in enumerate(events):
        payload = _payload(event)
        if payload.get("protocol_version") != "idle-protocol.v1":
            continue
        progress_ts = _parse_utc(_event_ts(event))
        for field in (
            "responds_to",
            "consensus_target_proposal_id",
            "violating_proposal_id",
            "rejected_event_id",
        ):
            proposal_id = str(payload.get(field) or "")
            if not proposal_id:
                continue
            _record_closure_entry(
                progress_index.setdefault(proposal_id, _new_closure_entry()),
                closure_ts=progress_ts,
                closure_index=event_index,
            )
    return progress_index


def _idle_protocol_progressed_by_index(
    request: Mapping[str, Any],
    *,
    request_moment: tuple[datetime, int],
    progress_index: Mapping[str, Mapping[str, Any]],
) -> bool:
    payload = _payload(request)
    if payload.get("protocol_version") != "idle-protocol.v1":
        return False
    proposal_id = str(payload.get("proposal_id") or "")
    if not proposal_id:
        return False
    request_ts, request_index = request_moment
    return _entry_closes_request(
        progress_index.get(proposal_id),
        request_ts=request_ts,
        request_index=request_index,
    )


def _is_request_like(event: Mapping[str, Any]) -> bool:
    if _is_bridge_follow_nudge(event):
        return False
    status = _event_status(event)
    if _is_closed_request_status(status):
        return False
    if _is_response_only_status(status):
        return False
    return _event_type(event) in REQUEST_TYPES and _status_has_any(
        status, OPEN_STATUS_FRAGMENTS
    )


def _is_bridge_follow_nudge(event: Mapping[str, Any]) -> bool:
    return _event_type(event) == "wake_request" and _task_id(event).lower().startswith(
        BRIDGE_FOLLOW_NUDGE_TASK_PREFIX
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
    request_moment: tuple[datetime, int],
    agent: str,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    request_task_id = _task_id(request)
    request_pr_key = _pr_closure_key_for_event(request)
    requester = _event_agent(request)
    target = agent.lower()
    request_ts, request_index = request_moment
    for event_index, event in enumerate(events):
        if not _closure_occurs_after_request(
            closure_ts=_parse_utc(_event_ts(event)),
            closure_index=event_index,
            request_ts=request_ts,
            request_index=request_index,
        ):
            continue
        same_task = bool(request_task_id and _task_id(event) == request_task_id)
        event_pr_key = _pr_closure_key_for_event(event)
        same_pr = bool(request_pr_key and event_pr_key == request_pr_key)
        if not same_task and not same_pr:
            continue
        event_agent = _event_agent(event)
        if event_agent == target and _is_substantive_rco_pass_block_response(event):
            return True
        if (
            event_agent == requester
            and _is_requester_terminal_closure(event)
            and _requester_identity_matches(request, event)
        ):
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
        or _is_response_only_status(status)
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


def _event_metadata(
    event: Mapping[str, Any],
    *,
    known_agents: Sequence[str] = (),
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("role", "agent_uuid", "session_id"):
        value = str(event.get(key) or "").strip()
        if value:
            if key == "session_id" and _session_id_conflicts_with_other_agent(
                session_id=value,
                event_agent=_event_agent(event),
                known_agents=known_agents,
            ):
                continue
            metadata[key] = value
    capabilities = _string_list(event.get("capabilities"))
    if capabilities:
        metadata["capabilities"] = capabilities
    return metadata


def _known_event_agents(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    agents: set[str] = set()
    for event in events:
        agent = _event_agent(event)
        if agent and agent != "unknown":
            agents.add(agent)
    return tuple(sorted(agents))


def _session_id_conflicts_with_other_agent(
    *,
    session_id: str,
    event_agent: str,
    known_agents: Sequence[str],
) -> bool:
    normalized_event_agent = event_agent.lower()
    owner = _session_id_owner_agent(
        session_id=session_id,
        known_agents=known_agents,
    )
    return owner is not None and owner != normalized_event_agent


def _session_id_owner_agent(
    *,
    session_id: str,
    known_agents: Sequence[str],
) -> str | None:
    normalized_session_id = session_id.lower()
    matches: list[str] = []
    for known_agent in known_agents:
        candidate = str(known_agent or "").strip().lower()
        if not candidate or candidate == "unknown":
            continue
        if normalized_session_id.startswith(f"{candidate}-"):
            matches.append(candidate)
    if not matches:
        return None
    return max(matches, key=len)


def _event_status(event: Mapping[str, Any]) -> str:
    return str(event.get("status") or "").lower()


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("type") or event.get("message_type") or "").lower()


def _event_ts(event: Mapping[str, Any]) -> str:
    return str(event.get("ts_utc") or event.get("timestamp") or "")


def _bounded_message(value: object) -> str:
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(message) > 240:
        return f"{message[:237]}..."
    return message


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


def _admitted_event_time(
    event: Mapping[str, Any],
    *,
    now_utc: datetime,
) -> datetime | None:
    event_ts = _parse_utc(_event_ts(event))
    if event_ts is None or event_ts > now_utc.astimezone(timezone.utc):
        return None
    return event_ts


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
    known_agents: Sequence[str] = (),
) -> dict[str, Any]:
    for event in reversed(events):
        if _event_agent(event) != agent:
            continue
        metadata = _event_metadata(event, known_agents=known_agents)
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

    current_utc = now_utc.astimezone(timezone.utc)
    suppressed_lookup = dict(suppressed_agents or {})
    identity_registry = _load_liveness_identity_registry()
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        event_ts = _admitted_event_time(event, now_utc=current_utc)
        if event_ts is None:
            continue
        event_agent = _event_agent(event)
        if event_agent in PRODUCTION_LIVENESS_IGNORED_AGENTS:
            continue
        if _event_has_registered_identity_mismatch(event, identity_registry):
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
        now_utc=current_utc,
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


def _load_liveness_identity_registry() -> dict[str, str]:
    try:
        return load_bridge_identity_registry(allow_missing=True)
    except ValueError as exc:
        raise BridgeNextActionError(
            {
                "ok": False,
                "decision": "bridge_next_action_error",
                "errors": [str(exc)],
            }
        ) from exc


def _event_has_registered_identity_mismatch(
    event: Mapping[str, Any], registry: Mapping[str, str]
) -> bool:
    if not registry:
        return False
    event_agent = _event_agent(event)
    expected_uuid = registry.get(event_agent)
    event_uuid = str(event.get("agent_uuid", "") or "").lower()
    if expected_uuid:
        return bool(event_uuid and event_uuid != expected_uuid.lower())
    if not event_uuid:
        return False
    for registered_agent, registered_uuid in registry.items():
        if registered_agent != event_agent and event_uuid == registered_uuid.lower():
            return True
    return False


def _wake_delivery_liveness_summary(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path | None,
    now_utc: datetime,
) -> dict[str, Any]:
    groups = _unresolved_wake_delivery_groups(events, now_utc=now_utc)
    stalled: list[dict[str, Any]] = []
    self_pacing: list[dict[str, Any]] = []
    max_age_minutes = DEFAULT_WAKE_DELIVERY_MAX_AGE_HOURS * 60.0
    self_liveness_by_agent = _latest_wake_delivery_self_liveness_by_agent(
        events,
        now_utc=now_utc,
    )
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
        single_wake_preflight = _rco_single_wake_preflight(
            group,
            bridge_root=bridge_root,
            now_utc=now_utc,
        )
        if (
            int(group["wake_request_count"]) < DEFAULT_WAKE_DELIVERY_MIN_REPEATS
            and not single_wake_preflight
        ):
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
                    now_utc=now_utc,
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
                now_utc=now_utc,
                classification=(
                    "rco_single_wake_preflight_stalled"
                    if single_wake_preflight
                    else "stalled_wake_delivery"
                ),
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
            _wake_delivery_escalation(stalled, by_agent)
            if stalled
            else _wake_delivery_no_escalation()
        ),
        "stalled_wakes": stalled,
        "single_wake_preflight_count": sum(
            1
            for row in stalled
            if row.get("classification") == "rco_single_wake_preflight_stalled"
        ),
        "self_liveness_window_minutes": (
            DEFAULT_WAKE_DELIVERY_SELF_LIVENESS_WINDOW_MINUTES
        ),
        "self_pacing_wake_count": len(self_pacing),
        "self_pacing_wakes": self_pacing,
    }


def _wake_delivery_escalation(
    stalled: Sequence[Mapping[str, Any]],
    by_agent: Mapping[str, int],
) -> dict[str, Any]:
    has_send_failure = any(
        int(row.get("wake_send_failed_count") or 0) for row in stalled
    )
    if has_send_failure:
        safe_next_action = "repair_operator_wake_routing_or_title_map"
        reason = "operator_wake_send_failed_for_unresolved_wake"
    else:
        safe_next_action = "restart_or_verify_target_agent_bridge_session_watcher"
        reason = "wake_request_visible_but_no_later_target_bridge_activity"
    return {
        "required": True,
        "target_agents": sorted(by_agent),
        "do_not_emit_additional_wake_requests": True,
        "safe_next_action": safe_next_action,
        "operator_action_required": True,
        "reason": reason,
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
    *,
    now_utc: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        event_agent = _event_agent(event)
        if event_agent and _is_wake_delivery_activity(event):
            _clear_wake_delivery_groups_for_target_activity(
                groups,
                event_agent=event_agent,
            )
        _clear_wake_delivery_groups_for_terminal_task(groups, event)
        admitted_ts = _admitted_event_time(event, now_utc=now_utc)
        if admitted_ts is None:
            continue
        _record_wake_send_failure_for_groups(
            groups,
            event,
            admitted_ts=admitted_ts,
        )
        if _event_type(event) != "wake_request":
            continue
        if _event_status(event) in CLOSED_REQUEST_STATUSES:
            continue
        task_id = _task_id(event)
        if not task_id:
            continue
        event_ts = _event_ts(event)
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
            existing["wake_request_count"] = int(existing["wake_request_count"]) + 1
            existing_last_ts = _parse_utc(str(existing["last_ts_utc"]))
            if existing_last_ts is None or admitted_ts >= existing_last_ts:
                existing["last_ts_utc"] = event_ts
                existing["last_status"] = _event_status(event)
            if event_agent:
                requesters = existing["requesters"]
                if isinstance(requesters, set):
                    requesters.add(event_agent)
    return groups


def _record_wake_send_failure_for_groups(
    groups: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
    *,
    admitted_ts: datetime,
) -> None:
    target = _wake_send_failed_target(event)
    if not target:
        return
    event_ts = _event_ts(event)
    for (group_target, _task_id_value), group in groups.items():
        if group_target != target:
            continue
        group["wake_send_failed_count"] = int(
            group.get("wake_send_failed_count") or 0
        ) + 1
        previous_ts = _parse_utc(
            str(group.get("latest_wake_send_failed_ts_utc") or "")
        )
        if previous_ts is None or admitted_ts >= previous_ts:
            group["latest_wake_send_failed_ts_utc"] = event_ts
            group["latest_wake_send_failed_message"] = _bounded_message(
                event.get("message")
            )


def _wake_send_failed_target(event: Mapping[str, Any]) -> str:
    if _event_status(event) != "wake_send_failed":
        return ""
    match = WAKE_SEND_FAILED_TARGET_PATTERN.search(str(event.get("message") or ""))
    if not match:
        return ""
    target = match.group("agent").strip().lower()
    return target if AGENT_ID_PATTERN.fullmatch(target) else ""


def _is_wake_delivery_activity(event: Mapping[str, Any]) -> bool:
    return _event_type(event) not in HEARTBEAT_ONLY_EVENT_TYPES


def _is_wake_delivery_self_liveness_activity(event: Mapping[str, Any]) -> bool:
    if _event_type(event) in HEARTBEAT_ONLY_EVENT_TYPES:
        return False
    return not (_event_type(event) == "message" and _event_status(event) == "received")


def _latest_wake_delivery_self_liveness_by_agent(
    events: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for event in events:
        agent = _event_agent(event)
        if not agent or not _is_wake_delivery_self_liveness_activity(event):
            continue
        event_ts = _admitted_event_time(event, now_utc=now_utc)
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
    if last_wake is None or last_self <= last_wake:
        return None
    reason = "target_self_activity_after_latest_wake"
    self_age_minutes = _elapsed_minutes(now_utc, last_self)
    if self_age_minutes >= DEFAULT_WAKE_DELIVERY_SELF_LIVENESS_WINDOW_MINUTES:
        return None
    return {
        "last_self_activity_ts_utc": _format_utc(last_self),
        "last_self_activity_age_minutes": _round_minutes(self_age_minutes),
        "self_liveness_reason": reason,
    }


def _rco_single_wake_preflight(
    group: Mapping[str, Any],
    *,
    bridge_root: Path | None,
    now_utc: datetime,
) -> bool:
    if int(group.get("wake_request_count") or 0) != 1:
        return False
    target = str(group.get("target_agent") or "")
    if not RCO_AGENT_PATTERN.fullmatch(target):
        return False
    wake_file = _wake_file_status(
        bridge_root,
        target,
        last_wake_ts_utc=str(group.get("last_ts_utc") or ""),
        now_utc=now_utc,
    )
    if wake_file.get("wake_file_present") is not True:
        return False
    try:
        wake_file_age_minutes = float(wake_file.get("wake_file_age_minutes"))
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(wake_file_age_minutes)
        and wake_file_age_minutes >= DEFAULT_WAKE_DELIVERY_MIN_AGE_MINUTES
    )


def _clear_wake_delivery_groups_for_target_activity(
    groups: dict[tuple[str, str], dict[str, Any]],
    *,
    event_agent: str,
) -> None:
    for key, group in list(groups.items()):
        target, _task_id_value = key
        if target != event_agent:
            continue
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
    now_utc: datetime,
    classification: str = "stalled_wake_delivery",
    self_liveness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = str(group["target_agent"])
    wake_file = _wake_file_status(
        bridge_root,
        target,
        last_wake_ts_utc=str(group["last_ts_utc"]),
        now_utc=now_utc,
    )
    requesters = group.get("requesters")
    requester_list = (
        sorted(str(item) for item in requesters) if isinstance(requesters, set) else []
    )
    wake_send_failed_count = int(group.get("wake_send_failed_count") or 0)
    if classification == "self_pacing_or_silent_by_design":
        diagnosis = (
            "target agent has self-authored bridge activity after the latest "
            "wake_request; treat as delivered and self-paced"
        )
        safe_next_action = (
            "wait for the target self-paced loop or recheck after the "
            "self-liveness window; do not restart solely from repeated wakes"
        )
    else:
        if wake_send_failed_count:
            diagnosis = (
                "operator wake send failed during this unresolved wake window; "
                "target session/tab routing may be unavailable"
            )
            safe_next_action = (
                "repair operator wake routing or TitleMap for the target session; "
                "do not emit more wake_request events until keying succeeds"
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
        "wake_send_failed_count": wake_send_failed_count,
        "latest_wake_send_failed_ts_utc": str(
            group.get("latest_wake_send_failed_ts_utc") or ""
        ),
        "latest_wake_send_failed_message": str(
            group.get("latest_wake_send_failed_message") or ""
        ),
        "wake_file_checked": bridge_root is not None,
        **wake_file,
        "diagnosis": diagnosis,
        "safe_next_action": safe_next_action,
    }
    if self_liveness:
        row.update(self_liveness)
    return row


def _wake_file_status(
    bridge_root: Path | None,
    target: str,
    *,
    last_wake_ts_utc: str,
    now_utc: datetime,
) -> dict[str, Any]:
    if bridge_root is None:
        return {
            "wake_file_present": False,
            "wake_file_mtime_utc": "",
            "wake_file_age_minutes": None,
            "wake_file_lag_seconds": None,
            "wake_file_fresh_after_last_wake": False,
        }
    wake_path = bridge_root / f"wake_{target}"
    if not wake_path.exists():
        return {
            "wake_file_present": False,
            "wake_file_mtime_utc": "",
            "wake_file_age_minutes": None,
            "wake_file_lag_seconds": None,
            "wake_file_fresh_after_last_wake": False,
        }
    try:
        mtime = datetime.fromtimestamp(wake_path.stat().st_mtime, tz=timezone.utc)
        last_wake_ts = _parse_utc(last_wake_ts_utc)
        lag_seconds = (
            (mtime - last_wake_ts).total_seconds()
            if last_wake_ts is not None
            else None
        )
        fresh_after_last_wake = (
            lag_seconds is not None
            and lag_seconds >= -WAKE_FILE_FRESHNESS_TOLERANCE_SECONDS
        )
        age_minutes = _elapsed_minutes(now_utc.astimezone(timezone.utc), mtime)
        return {
            "wake_file_present": True,
            "wake_file_mtime_utc": _format_utc(mtime),
            "wake_file_age_minutes": _round_minutes(age_minutes),
            "wake_file_lag_seconds": (
                round(lag_seconds, 3) if lag_seconds is not None else None
            ),
            "wake_file_fresh_after_last_wake": fresh_after_last_wake,
        }
    except OSError:
        return {
            "wake_file_present": True,
            "wake_file_mtime_utc": "",
            "wake_file_age_minutes": None,
            "wake_file_lag_seconds": None,
            "wake_file_fresh_after_last_wake": False,
        }


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
    known_agents = _known_event_agents(events)
    agent_profile = _latest_agent_metadata(
        agent=agent,
        events=events,
        known_agents=known_agents,
    )
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
        incoming.update(_event_metadata(request, known_agents=known_agents))
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
