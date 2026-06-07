# SPDX-License-Identifier: BUSL-1.1
"""Surface unanswered bridge peer requests into an agent inbox.

Some bridge participants can keep emitting heartbeat events while their
interactive session does not automatically poll ``events.jsonl``. This helper
turns fresh unanswered peer requests into deterministic inbox ``.md`` markers,
so a UI/operator pump that already watches ``.agent-bridge/inbox/<agent>/`` can
surface missed ``status_query`` / ``handoff`` / ``rco_requested`` work.

The tool is intentionally narrow:

* read bridge events,
* identify unanswered incoming request-like events,
* optionally write one idempotent marker per task id.

It does not emit bridge events, mutate claims, touch git, or call the network.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
    PRIVATE_MARKERS,
    _event_agent,
    _event_status,
    _event_ts,
    _event_type,
    _idle_protocol_progressed,
    _is_answer_like,
    _is_request_like,
    _message,
    _parse_utc,
    _split_fresh_and_stale_requests,
    _task_id,
    read_events,
)
from waggledance.core.work_queue import AGENT_ID_PATTERN, DEFAULT_BRIDGE_ROOT  # noqa: E402


DEFAULT_TAIL = 50000
MESSAGE_LIMIT = 900


class PeerNotificationError(ValueError):
    """Raised when peer notifications cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Surface unanswered bridge peer requests into an agent inbox.",
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file; <=0 reads all.",
    )
    parser.add_argument(
        "--open-request-max-age-hours",
        type=float,
        default=DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
        help="Do not surface unanswered requests older than this many hours.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for request-age evaluation.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write marker files. Without this flag the tool is read-only.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        events_path = args.events or (Path(args.bridge_root) / "shared" / "events.jsonl")
        out_dir = args.out_dir or (Path(args.bridge_root) / "inbox" / args.agent)
        now_utc = datetime.now(timezone.utc)
        if args.now:
            parsed_now = _parse_utc(args.now)
            if parsed_now is None:
                raise PeerNotificationError(
                    _error_report("now must be an ISO-8601 timestamp")
                )
            now_utc = parsed_now
        events = read_events(events_path, tail=args.tail)
        report = surface_unanswered_peer_messages(
            agent=args.agent,
            events=events,
            out_dir=out_dir,
            now_utc=now_utc,
            open_request_max_age_hours=args.open_request_max_age_hours,
            apply=args.apply,
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "notify_unanswered_peer_messages_error",
            "errors": list(exc.report.get("errors", [])),
        }
        exit_code = 2
    except PeerNotificationError as exc:
        report = exc.report
        exit_code = 2
    except OSError as exc:
        report = _error_report(exc.__class__.__name__)
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def surface_unanswered_peer_messages(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    out_dir: Path,
    now_utc: datetime,
    open_request_max_age_hours: float | None = DEFAULT_OPEN_REQUEST_MAX_AGE_HOURS,
    apply: bool = False,
) -> dict[str, Any]:
    """Return and optionally write inbox markers for fresh unanswered requests."""
    if not AGENT_ID_PATTERN.fullmatch(agent):
        raise PeerNotificationError(
            _error_report(f"agent must match {AGENT_ID_PATTERN.pattern}")
        )
    if open_request_max_age_hours is not None and (
        not math.isfinite(open_request_max_age_hours)
        or open_request_max_age_hours <= 0
    ):
        raise PeerNotificationError(
            _error_report("open_request_max_age_hours must be positive")
        )

    open_requests = _open_requests_for_agent(agent=agent, events=events)
    fresh, stale = _split_fresh_and_stale_requests(
        open_requests,
        now_utc=now_utc,
        max_age_hours=open_request_max_age_hours,
    )
    latest_by_task = _latest_request_by_task(fresh)
    markers = [
        _build_marker(agent=agent, request=request, out_dir=out_dir)
        for request in latest_by_task
    ]
    report: dict[str, Any] = {
        "ok": True,
        "decision": "notify_unanswered_peer_messages",
        "agent": agent,
        "applied": bool(apply),
        "marker_count": len(markers),
        "stale_request_count": len(stale),
        "markers": [
            {
                "task_id": marker["task_id"],
                "path": str(marker["path"]),
                "request_agent": marker["request_agent"],
                "status": marker["status"],
                "type": marker["type"],
                "ts_utc": marker["ts_utc"],
            }
            for marker in markers
        ],
    }
    _assert_no_private_markers(report)
    for marker in markers:
        _assert_no_private_markers(marker["content"])

    if apply and markers:
        out_dir.mkdir(parents=True, exist_ok=True)
        for marker in markers:
            marker["path"].write_text(marker["content"], encoding="utf-8")

    return report


def _open_requests_for_agent(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    requests = [
        event
        for event in events
        if _is_peer_request_like(event)
        and _event_agent(event) != agent
        and _addressed_to(event, agent)
        and _task_id(event)
    ]
    open_requests: list[Mapping[str, Any]] = []
    for request in requests:
        request_ts = _event_ts(request)
        task_id = _task_id(request)
        answered = any(
            _event_agent(event) == agent
            and _task_id(event) == task_id
            and _event_ts(event) > request_ts
            and _is_substantive_answer_like(event)
            for event in events
        )
        if not answered:
            answered = any(
                _task_id(event) == task_id
                and _event_ts(event) > request_ts
                and _is_closing_event_like(event)
                for event in events
            )
        if not answered and _idle_protocol_progressed(request, events):
            answered = True
        if not answered:
            open_requests.append(request)
    return open_requests


def _is_peer_request_like(event: Mapping[str, Any]) -> bool:
    if _is_request_like(event):
        return True
    status_tokens = _status_tokens(_event_status(event))
    return _event_type(event) == "message" and {"status", "query"}.issubset(
        status_tokens
    )


def _is_substantive_answer_like(event: Mapping[str, Any]) -> bool:
    event_type = _event_type(event)
    status = _event_status(event)
    if event_type in {"heartbeat", "liveness", "wake_request"}:
        return False
    if event_type == "message" and status in {"received", "seen", "acknowledged"}:
        return False
    if _is_answer_like(event):
        return True
    return event_type in {
        "message",
        "claim",
        "done",
        "decision",
        "blocked",
        "finding",
        "test",
        "handoff",
        "release",
    }


def _is_closing_event_like(event: Mapping[str, Any]) -> bool:
    event_type = _event_type(event)
    if event_type not in {"done", "decision", "blocked", "release", "handoff"}:
        return False
    tokens = _status_tokens(_event_status(event))
    return bool(
        tokens
        & {
            "blocked",
            "closed",
            "done",
            "merged",
            "postmerge",
            "resolved",
            "superseded",
            "validated",
            "verified",
        }
    )


def _latest_request_by_task(
    requests: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    by_task: dict[str, Mapping[str, Any]] = {}
    for request in requests:
        by_task[_task_id(request)] = request
    return sorted(by_task.values(), key=lambda event: _event_ts(event))


def _build_marker(
    *,
    agent: str,
    request: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    task_id = _task_id(request)
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    path = out_dir / f"peer_request_{_safe_name(task_id)}_{digest}.md"
    request_agent = _event_agent(request)
    request_type = _event_type(request)
    request_status = _event_status(request)
    ts_utc = _event_ts(request)
    message = _message(request, limit=MESSAGE_LIMIT)
    content = "\n".join(
        [
            "# Unanswered Bridge Peer Request",
            "",
            f"- target_agent: {agent}",
            f"- request_agent: {request_agent}",
            f"- task_id: {task_id}",
            f"- type: {request_type}",
            f"- status: {request_status}",
            f"- ts_utc: {ts_utc}",
            "",
            "## Message",
            "",
            message or "(no message)",
            "",
            "## Next Step",
            "",
            (
                "Read the bridge event and answer it with a substantive event "
                "using the same task_id. A message/received ACK is not enough."
            ),
            "",
        ]
    )
    return {
        "task_id": task_id,
        "path": path,
        "request_agent": request_agent,
        "type": request_type,
        "status": request_status,
        "ts_utc": ts_utc,
        "content": content,
    }


def _addressed_to(event: Mapping[str, Any], agent: str) -> bool:
    target = agent.lower()
    recipients = [
        item.strip().lower()
        for item in re.split(r"[,;\s]+", str(event.get("to") or ""))
        if item.strip()
    ]
    return target in recipients


def _status_tokens(status: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return safe[:100] or "untitled"


def _assert_no_private_markers(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    if any(marker in text for marker in PRIVATE_MARKERS):
        raise PeerNotificationError(
            {
                "ok": False,
                "decision": "notify_unanswered_peer_messages_refused",
                "errors": ["private marker present in selected peer notification output"],
            }
        )


def _error_report(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "notify_unanswered_peer_messages_error",
        "errors": [message],
    }


def _print_human(report: Mapping[str, Any]) -> None:
    print(report.get("decision", "unknown"))
    if not report.get("ok", False):
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    print(f"agent: {report.get('agent', '')}")
    print(f"applied: {str(report.get('applied', False)).lower()}")
    print(f"marker_count: {report.get('marker_count', 0)}")
    print(f"stale_request_count: {report.get('stale_request_count', 0)}")
    for marker in report.get("markers", []):
        if isinstance(marker, Mapping):
            print(f"- {marker.get('task_id', '')}: {marker.get('path', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
