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
import json
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
ANSWER_TYPES = {"message", "done", "decision", "blocked", "finding", "test", "release"}
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
    "closed",
    "done",
    "merged",
    "pass",
    "resolved",
    "superseded",
)


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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        events_path = args.events or (Path(args.bridge_root) / "shared" / "events.jsonl")
        events = read_events(events_path, tail=args.tail)
        claims = list_claims(bridge_root=Path(args.bridge_root))
        report = recommend_next_action(
            agent=args.agent,
            events=events,
            claims=claims,
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
    """Read bridge JSONL events, ignoring malformed non-object lines."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if tail > 0:
        lines = lines[-tail:]
    events: list[dict[str, Any]] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def recommend_next_action(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Claim],
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

    own_claims = [claim for claim in claims if claim.agent == agent]
    foreign_write_claims = [
        claim for claim in claims if claim.agent != agent and claim.mode == "write"
    ]
    open_requests = _open_requests_for_agent(agent=agent, events=events)

    if own_claims:
        claim = own_claims[0]
        return _report(
            agent=agent,
            action="continue_claim",
            task_id=claim.task_id,
            safe_mode=claim.mode,
            summary=f"continue active claim {claim.task_id}",
            claims=claims,
            open_requests=open_requests,
            foreign_write_claims=foreign_write_claims,
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
            claims=claims,
            open_requests=open_requests,
            foreign_write_claims=foreign_write_claims,
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
            claims=claims,
            open_requests=open_requests,
            foreign_write_claims=foreign_write_claims,
        )
    return _report(
        agent=agent,
        action="claim_unblocked_work",
        task_id="next-unclaimed-scout-or-implementation",
        safe_mode="write-or-read-only",
        summary="no active claim or incoming blocker; claim the highest-value unblocked work",
        claims=claims,
        open_requests=open_requests,
        foreign_write_claims=foreign_write_claims,
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
        request_ts = _event_ts(request)
        task_id = _task_id(request)
        answered = any(
            _event_agent(event) == agent
            and _task_id(event) == task_id
            and _event_ts(event) > request_ts
            and _is_answer_like(event)
            for event in events
        )
        if not answered and _idle_protocol_progressed(request, events):
            answered = True
        if not answered:
            open_requests.append(request)
    return open_requests


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
    status = _event_status(event)
    return _event_type(event) in ANSWER_TYPES and _status_has_any(
        status, ANSWER_STATUS_FRAGMENTS
    )


def _status_has_any(status: str, candidates: Sequence[str]) -> bool:
    tokens = {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}
    return any(candidate in tokens for candidate in candidates)


def _addressed_to(event: Mapping[str, Any], agent: str) -> bool:
    return str(event.get("to") or "").lower() == agent


def _task_id(event: Mapping[str, Any]) -> str:
    return str(event.get("task_id") or event.get("id") or "")


def _event_agent(event: Mapping[str, Any]) -> str:
    return str(event.get("agent") or event.get("author") or "unknown").lower()


def _event_status(event: Mapping[str, Any]) -> str:
    return str(event.get("status") or "").lower()


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("type") or event.get("message_type") or "").lower()


def _event_ts(event: Mapping[str, Any]) -> str:
    return str(event.get("ts_utc") or event.get("timestamp") or "")


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _message(event: Mapping[str, Any], *, limit: int = 220) -> str:
    text = " ".join(str(event.get("message") or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _report(
    *,
    agent: str,
    action: str,
    task_id: str,
    safe_mode: str,
    summary: str,
    claims: Sequence[Claim],
    open_requests: Sequence[Mapping[str, Any]],
    foreign_write_claims: Sequence[Claim],
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
        "foreign_write_claim_count": len(foreign_write_claims),
    }
    if request is not None:
        payload["incoming"] = {
            "agent": _event_agent(request),
            "type": _event_type(request),
            "status": _event_status(request),
            "ts_utc": _event_ts(request),
            "message": _message(request),
        }
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
    print(f"action: {report.get('action', '')}")
    print(f"task_id: {report.get('task_id', '')}")
    print(f"safe_mode: {report.get('safe_mode', '')}")
    print(f"summary: {report.get('summary', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
