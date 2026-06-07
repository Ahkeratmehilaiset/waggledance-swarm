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

from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    DEFAULT_BRIDGE_ROOT,
    Claim,
    WorkQueueError,
    list_claims,
)


DEFAULT_EVENTS_PATH = DEFAULT_BRIDGE_ROOT / "shared" / "events.jsonl"
DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS = 12.0
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
REQUEST_TYPES = {
    "message",
    "finding",
    "handoff",
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
    "ready",
    "pushed",
    "active",
    "blocked",
)
ANSWER_STATUS_FRAGMENTS = (
    "accepted",
    "ack",
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
DEFAULT_STALE_REQUEST_REPORT_MAX_AGE_HOURS = 72.0
DEFAULT_PRODUCTION_IDLE_WARN_MINUTES = 12.0
PRODUCTION_LIVENESS_IGNORED_AGENTS = {"operator", "system", "unknown"}
HEARTBEAT_ONLY_EVENT_TYPES = {"heartbeat"}


class BridgeNextActionError(ValueError):
    """Raised when a next-action recommendation cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recommend the next bridge action.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
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
        "--now",
        default=None,
        help="Override current UTC time for request-age evaluation.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        events_path = args.events or (Path(args.bridge_root) / "shared" / "events.jsonl")
        events = read_events(events_path, tail=args.tail)
        claims = list_claims(bridge_root=Path(args.bridge_root))
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
        report = recommend_next_action(
            agent=args.agent,
            events=events,
            claims=claims,
            now_utc=now_utc,
            open_request_max_age_hours=args.open_request_max_age_hours,
            stale_report_max_age_hours=args.stale_report_max_age_hours,
            production_idle_warn_minutes=args.production_idle_warn_minutes,
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


def recommend_next_action(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim],
    now_utc: datetime | None = None,
    open_request_max_age_hours: float | None = DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
    stale_report_max_age_hours: float | None = (
        DEFAULT_STALE_REQUEST_REPORT_MAX_AGE_HOURS
    ),
    production_idle_warn_minutes: float | None = (
        DEFAULT_PRODUCTION_IDLE_WARN_MINUTES
    ),
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
    open_requests, stale_open_requests = _split_fresh_and_stale_requests(
        all_open_requests,
        now_utc=effective_now,
        max_age_hours=open_request_max_age_hours,
    )
    reported_stale_open_requests, archived_stale_open_requests = (
        _split_reported_and_archived_stale_requests(
            stale_open_requests,
            now_utc=effective_now,
            max_age_hours=stale_report_max_age_hours,
        )
    )
    production_liveness = _production_liveness_report(
        events=events,
        now_utc=effective_now,
        idle_warn_minutes=production_idle_warn_minutes,
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
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
        )
    if open_requests:
        request = open_requests[-1]
        requester = _event_agent(request)
        kind = f"{_event_type(request)}/{_event_status(request)}".strip("/")
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
            stale_open_requests=reported_stale_open_requests,
            archived_stale_open_requests=archived_stale_open_requests,
            foreign_write_claims=foreign_write_claims,
            production_liveness=production_liveness,
            request=request,
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
    requests = [
        event
        for event in events
        if _is_request_like(event) and _addressed_to(event, agent)
    ]
    open_requests: list[Mapping[str, Any]] = []
    for request in requests:
        answered = any(
            _closes_request_for_agent(event=event, request=request, agent=agent)
            for event in events
        )
        if not answered and _idle_protocol_progressed(request, events):
            answered = True
        if not answered:
            open_requests.append(request)
    return open_requests


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
    payload = _payload(request)
    if payload.get("protocol_version") != "idle-protocol.v1":
        return False
    proposal_id = str(payload.get("proposal_id") or "")
    if not proposal_id:
        return False
    request_ts = _event_ts(request)
    for event in events:
        if _event_ts(event) <= request_ts:
            continue
        later = _payload(event)
        if later.get("protocol_version") != "idle-protocol.v1":
            continue
        if str(later.get("responds_to") or "") == proposal_id:
            return True
        if str(later.get("consensus_target_proposal_id") or "") == proposal_id:
            return True
        if str(later.get("violating_proposal_id") or "") == proposal_id:
            return True
        if str(later.get("rejected_event_id") or "") == proposal_id:
            return True
    return False


def _is_request_like(event: Mapping[str, Any]) -> bool:
    status = _event_status(event)
    return _event_type(event) in REQUEST_TYPES and _status_has_any(
        status, OPEN_STATUS_FRAGMENTS
    )


def _is_answer_like(event: Mapping[str, Any]) -> bool:
    if _event_type(event) == "done":
        return True
    status = _event_status(event)
    return _event_type(event) in ANSWER_TYPES and _status_has_any(
        status, ANSWER_STATUS_FRAGMENTS
    )


def _status_has_any(status: str, candidates: Sequence[str]) -> bool:
    tokens = {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}
    return any(candidate in tokens for candidate in candidates)


def _addressed_to(event: Mapping[str, Any], agent: str) -> bool:
    target = agent.lower()
    recipients = [
        item.strip().lower()
        for item in re.split(r"[,;\s]+", str(event.get("to") or ""))
        if item.strip()
    ]
    return target in recipients


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
    now_utc: datetime,
    idle_warn_minutes: float | None,
) -> dict[str, Any]:
    if idle_warn_minutes is None:
        return {}

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
    for event_agent, state in states.items():
        first_event_ts = state["first_event_ts"]
        last_heartbeat_ts = state["last_heartbeat_ts"]
        last_activity_ts = state["last_activity_ts"]
        if last_activity_ts is None:
            observed_minutes = _elapsed_minutes(now_utc, first_event_ts)
            if observed_minutes < idle_warn_minutes:
                continue
            stalled.append(
                {
                    "agent": event_agent,
                    "reason": "heartbeat_only_in_selected_events",
                    "first_observed_ts_utc": _format_utc(first_event_ts),
                    "last_heartbeat_ts_utc": _format_utc(last_heartbeat_ts),
                    "observed_minutes": _round_minutes(observed_minutes),
                }
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
        stalled.append(
            {
                "agent": event_agent,
                "reason": reason,
                "last_activity_ts_utc": _format_utc(last_activity_ts),
                "last_activity_type": state["last_activity_type"],
                "last_activity_status": state["last_activity_status"],
                "last_activity_task_id": state["last_activity_task_id"],
                "last_heartbeat_ts_utc": _format_utc(last_heartbeat_ts),
                "idle_minutes": _round_minutes(idle_minutes),
                "heartbeat_only_since_activity": heartbeat_only,
            }
        )

    if not stalled:
        return {}
    stalled.sort(
        key=lambda item: (
            float(item.get("idle_minutes") or item.get("observed_minutes") or 0.0),
            str(item.get("agent") or ""),
        ),
        reverse=True,
    )
    return {
        "idle_warn_minutes": float(idle_warn_minutes),
        "stalled_agent_count": len(stalled),
        "stalled_agents": stalled,
    }


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
