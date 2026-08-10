# SPDX-License-Identifier: BUSL-1.1
"""Emit a bridge event that closes a stale open-RCO request after a merge.

When an agent emits a `type=handoff status=rco_requested` bridge event, the
idle-protocol substrate tracks an "open RCO request" against the named
``task_id``. The request stays open until a matching closing event arrives.

The autonomous-merge path (``gh pr merge --squash --match-head-commit``)
does NOT itself emit a closing bridge event. If the merge lands the PR but
no agent follows up with an RCO PASS or done event, the open RCO blocks
the auto-emit idle-protocol dispatcher's ``open_rco_requests`` criterion
indefinitely.

This helper closes that gap. Run it after a successful merge:

    python tools/close_bridge_rco_request.py \\
        --task-id <task_id-of-rco_requested-handoff> \\
        --pr <merged PR number> \\
        --from-agent claude

The emitted event uses ``type=decision status=rco_closed_postmerge`` so it:

* Closes the RCO via the canonical target/requester closure reducer shared by
  next-action, idle, reporting, notification, and this writer gate.
* Does NOT match ``_is_merge_event`` (which requires status containing
  ``"merged"`` or ``type=done`` plus a merge-context message). This avoids
  pushing the ``recent_merge`` quiet window forward by an extra 60 minutes
  every time a close event is emitted.

The helper is intentionally read+append only. It does not call ``gh`` to
verify merge state; the caller passes the PR number for citation. It does
verify that the bridge events file already contains an open
``rco_requested`` handoff for the given task_id, refusing if there is no
open request to close.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_event_writer import (  # noqa: E402
    AppendV1Backend,
    BridgeEventWriteError,
    validate_v1_replayer_event,
    write_bridge_event,
)
from tools.bridge_next_action import (  # noqa: E402
    _build_idle_protocol_progress_index,
    _build_request_closure_index,
    _chronological_events,
    _event_recipients,
    _event_status,
    _event_ts,
    _is_request_like,
    _request_closed_for_agent,
    _task_id,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

DEFAULT_BRIDGE_ROOT = Path(".agent-bridge")
RCO_CLOSE_STATUS = "rco_closed_postmerge"
RCO_OPEN_STATUS = "rco_requested"
DEFAULT_TO_AGENTS = "codex,claude"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a bridge event that closes a stale open-RCO request "
            "for the given task_id after the corresponding PR was merged."
        ),
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument(
        "--from-agent",
        required=True,
        help="Agent id emitting the close (claude / codex / operator).",
    )
    parser.add_argument(
        "--agent-uuid",
        default="",
        help="Optional UUID of the emitting requester identity.",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Optional session id of the emitting requester identity.",
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
        "--merge-commit",
        default="",
        help="Optional merge commit SHA for citation in the close message.",
    )
    parser.add_argument(
        "--merged-at",
        default="",
        help="Optional ISO-UTC timestamp when the PR was merged.",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional free-text note appended to the close message.",
    )
    parser.add_argument(
        "--to",
        default=DEFAULT_TO_AGENTS,
        help=(
            "Comma-separated bridge audience. Defaults to peer agents, not "
            "operator; pass operator explicitly when a human-facing close is "
            "intended."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-UTC timestamp override for the emitted event (tests).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", "--emit", dest="emit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    try:
        result = close_bridge_rco_request(
            task_id=args.task_id,
            pr_number=args.pr,
            from_agent=args.from_agent,
            from_agent_uuid=args.agent_uuid,
            from_session_id=args.session_id,
            bridge_root=bridge_root,
            merge_commit=args.merge_commit,
            merged_at=args.merged_at,
            to_agents=args.to,
            note=args.note,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            emit=args.emit,
        )
    except CloseRcoError as exc:
        report = {"ok": False, "error": str(exc), "decision": exc.decision}
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"close_bridge_rco_request FAILED: {exc}", file=sys.stderr)
        return int(exc.exit_code)

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["decision"])
        if result.get("emitted"):
            print(f"event written to: {result['events_path']}")
        else:
            print("dry-run: pass --apply (or --emit) to write the close event")
    return 0


class CloseRcoError(ValueError):
    def __init__(self, message: str, decision: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.decision = decision
        self.exit_code = exit_code


def close_bridge_rco_request(
    *,
    task_id: str,
    pr_number: int,
    from_agent: str,
    from_agent_uuid: str = "",
    from_session_id: str = "",
    bridge_root: Path,
    merge_commit: str = "",
    merged_at: str = "",
    to_agents: str = DEFAULT_TO_AGENTS,
    note: str = "",
    now_utc: datetime,
    emit: bool,
    writer_backend: AppendV1Backend | None = None,
) -> dict[str, Any]:
    if not task_id or not task_id.strip():
        raise CloseRcoError("task_id must not be empty", "invalid_args")
    if pr_number <= 0:
        raise CloseRcoError("--pr must be a positive integer", "invalid_args")
    if not from_agent or not from_agent.strip():
        raise CloseRcoError("--from-agent must not be empty", "invalid_args")

    events_path = bridge_root / "shared" / "events.jsonl"
    if not events_path.exists():
        raise CloseRcoError(
            f"bridge events file missing: {events_path}",
            "missing_events_file",
        )

    events = _read_events(events_path)
    open_state = _open_rco_for(events, task_id)
    if not open_state["has_open_rco"]:
        raise CloseRcoError(
            (
                f"no open rco_requested handoff found for task_id "
                f"'{task_id}' in {events_path}"
            ),
            "no_open_rco",
            exit_code=3,
        )

    event = _build_close_event(
        task_id=task_id,
        pr_number=pr_number,
        from_agent=from_agent,
        from_agent_uuid=from_agent_uuid,
        from_session_id=from_session_id,
        to_agents=to_agents,
        merge_commit=merge_commit,
        merged_at=merged_at,
        note=note,
        now_utc=now_utc,
    )
    try:
        validate_v1_replayer_event(event)
    except BridgeEventWriteError as exc:
        raise CloseRcoError(str(exc), "invalid_args") from exc

    candidate_state = _open_rco_for([*events, event], task_id)
    if candidate_state["has_open_rco"]:
        raise CloseRcoError(
            (
                "proposed close event would not close the open RCO under the "
                "canonical requester/target, identity, target-set, and "
                "event-order rules"
            ),
            "close_event_would_not_close",
            exit_code=4,
        )

    report: dict[str, Any] = {
        "ok": True,
        "decision": "ready",
        "emitted": False,
        "task_id": task_id,
        "pr": pr_number,
        "from_agent": from_agent,
        "proposed_event": event,
        "open_rco_opened_at_utc": open_state["opened_at_utc"],
    }
    if emit:
        try:
            write_bridge_event(
                bridge_root=bridge_root,
                events_path=events_path,
                event=event,
                # This close helper historically writes only the canonical
                # stream.  Keep its sidecars deliberately disabled.
                write_sidecars=False,
                backend=writer_backend,
            )
        except BridgeEventWriteError as exc:
            raise CloseRcoError(str(exc), exc.decision) from exc
        report["emitted"] = True
        report["decision"] = "closed"
        report["events_path"] = str(events_path)
    return report


def _build_close_event(
    *,
    task_id: str,
    pr_number: int,
    from_agent: str,
    from_agent_uuid: str,
    from_session_id: str,
    to_agents: str,
    merge_commit: str,
    merged_at: str,
    note: str,
    now_utc: datetime,
) -> dict[str, Any]:
    parts: list[str] = []
    parts.append(
        f"Close open rco_requested handoff for task '{task_id}' "
        f"after PR #{pr_number} merged."
    )
    if merged_at:
        parts.append(f"Merged at: {merged_at}.")
    if merge_commit:
        parts.append(f"Merge commit: {merge_commit}.")
    parts.append(
        "Status 'rco_closed_postmerge' closes the open RCO via the canonical "
        "requester-closure rule without triggering "
        "_is_merge_event (which would extend the recent_merge window by "
        "60 minutes)."
    )
    if note:
        parts.append(f"Note: {note}")
    event: dict[str, Any] = {
        "ts_utc": _iso(now_utc),
        "agent": from_agent,
        "type": "decision",
        "task_id": task_id,
        "status": RCO_CLOSE_STATUS,
        "severity": "",
        "to": to_agents,
        "message": " ".join(parts),
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
    }
    if from_agent_uuid:
        event["agent_uuid"] = from_agent_uuid
    if from_session_id:
        event["session_id"] = from_session_id
    return event


def _open_rco_for(
    events: Sequence[Mapping[str, Any]],
    task_id: str,
) -> dict[str, Any]:
    ordered_events = _chronological_events(events)
    closure_index = _build_request_closure_index(ordered_events)
    idle_progress_index = _build_idle_protocol_progress_index(ordered_events)
    open_requests: list[Mapping[str, Any]] = []
    for request in ordered_events:
        if (
            _task_id(request) != task_id
            or _event_status(request) != RCO_OPEN_STATUS
            or not _is_request_like(request)
        ):
            continue
        for target in _event_recipients(request):
            if not _request_closed_for_agent(
                request=request,
                agent=target,
                events=ordered_events,
                closure_index=closure_index,
                idle_progress_index=idle_progress_index,
            ):
                open_requests.append(request)
                break
    if not open_requests:
        return {"has_open_rco": False, "opened_at_utc": ""}
    latest_open = open_requests[-1]
    return {
        "has_open_rco": True,
        "opened_at_utc": _event_ts(latest_open),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CloseRcoError(
                (
                    f"invalid JSON in bridge events file {events_path} "
                    f"at line {line_no}: {exc.msg}"
                ),
                "invalid_events_file",
            ) from exc
        if event is None:
            continue
        if not isinstance(event, dict):
            raise CloseRcoError(
                (
                    f"invalid bridge event in {events_path} at line {line_no}: "
                    "event must be a JSON object"
                ),
                "invalid_events_file",
            )
        events.append(event)
    return events


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
