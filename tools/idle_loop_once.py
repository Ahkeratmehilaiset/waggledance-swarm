# SPDX-License-Identifier: BUSL-1.1
"""One-tick read-only orchestrator for the idle-protocol continuous loop.

This tool combines ``tools/idle_check`` and
``waggledance.core.idle_protocol_session.summarize_idle_session`` into a
single decision-token report that a scheduler or peer agent can consume to
decide the next concrete action.

It is intentionally read-only:

* never emits bridge events;
* never opens, drafts, or merges pull requests;
* never claims work-queue tasks.

External effects belong to the existing primitives this tool points at
(``run_idle_protocol_once``, ``idle_protocol_activate``,
``idle_consensus_artifact``, ``idle_consensus_draft_pr``,
``idle_consensus_auto_merge``). Auto-conversion from consensus to
implementation work is explicitly deferred in
``docs/architecture/IDLE_PROTOCOL_V1.md``; this tool reports the
deferral as an operator/implementer escalation rather than performing it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import (  # noqa: E402
    DEFAULT_CLAIMS_DIR,
    DEFAULT_EVENTS_PATH,
    evaluate_idle_state,
)
from waggledance.core.idle_protocol_session import (  # noqa: E402
    summarize_idle_session,
)


CONVERGED_STATUSES = {"soft_convergence", "hard_convergence"}
OPERATOR_REVIEW_STATUSES = {
    "charter_violation",
    "invalid_event",
    "operator_escalation",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report the next idle-loop action for a scheduler or peer agent."
        ),
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--claims-dir", type=Path, default=DEFAULT_CLAIMS_DIR)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument("--operator-last-activity-utc", default=None)
    parser.add_argument(
        "--agent",
        default=None,
        help=(
            "Optional agent id used only to make stale-terminal-session "
            "recommendations concrete."
        ),
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_idle_loop_tick(
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
        agent=args.agent,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        next_action = report.get("next_action")
        if next_action:
            print(f"next_action: {next_action}")
        recommended = report.get("recommended_command")
        if recommended:
            print(f"recommended_command: {recommended}")
        for note in report.get("notes", []):
            print(f"note: {note}")
    return int(report.get("exit_code", 0))


def evaluate_idle_loop_tick(
    *,
    events_path: Path,
    claims_dir: Path,
    now_utc: datetime,
    idle_minutes: int,
    pending_ci_count: int,
    open_request_max_age_hours: float,
    operator_last_activity_utc: datetime | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return one read-only decision token for the idle-loop tick."""
    try:
        idle_report = evaluate_idle_state(
            events_path=events_path,
            claims_dir=claims_dir,
            now_utc=now_utc,
            idle_minutes=idle_minutes,
            pending_ci_count=pending_ci_count,
            open_request_max_age_hours=open_request_max_age_hours,
            operator_last_activity_utc=operator_last_activity_utc,
        )
    except ValueError as exc:
        return {
            "decision": "unknown",
            "next_action": "operator_handles",
            "exit_code": 2,
            "reason": str(exc),
            "notes": [
                "idle_check could not prove the bridge state; "
                "the scheduler should leave the bridge alone until the "
                "events file is healthy"
            ],
        }

    if not idle_report["idle"]:
        return {
            "decision": "not_idle",
            "next_action": "wait_for_quiet",
            "exit_code": 0,
            "blockers": list(idle_report["blockers"]),
            "idle_report": idle_report,
            "notes": [
                "the bridge is active; the scheduler should not start a new "
                "idle-protocol round in this tick"
            ],
        }

    events = _read_events(events_path)
    summary = summarize_idle_session(events)
    status = str(summary.get("status", ""))

    if status == "no_session":
        return _report(
            decision="no_session",
            next_action="emit_round_1",
            recommended_command=(
                "python tools/run_idle_protocol_once.py --emit --json"
            ),
            idle_report=idle_report,
            session_summary=summary,
            notes=[
                "the bridge is quiet and there is no open idle-protocol "
                "instance; an agent (or scheduler-driven session) should "
                "emit one round-1 proposal"
            ],
        )

    if status == "active_session":
        next_required = summary.get("next_required_event") or {}
        next_event_type = str(next_required.get("event_type") or "")
        next_round = next_required.get("round_number")
        return _report(
            decision="mid_protocol",
            next_action="generate_next_round_payload",
            idle_report=idle_report,
            session_summary=summary,
            notes=[
                (
                    f"open idle-protocol instance at round "
                    f"{summary.get('latest_round')}; the next required event "
                    f"is {next_event_type or 'unknown'}"
                    + (f" at round {next_round}" if next_round else "")
                ),
                (
                    "an agent must compose the next-round payload "
                    "(idle-protocol payload generation is intentionally "
                    "deferred from tooling; LLM-in-the-loop is required)"
                ),
            ],
        )

    if status in CONVERGED_STATUSES:
        convergence = summary.get("convergence") or {}
        target = convergence.get("target_proposal_id") or convergence.get(
            "finalist_proposal_ids"
        )
        return _report(
            decision="convergence_reached",
            next_action="route_to_implementer_chain",
            idle_report=idle_report,
            session_summary=summary,
            notes=[
                f"idle-protocol reached {status}; target/finalists: {target}",
                (
                    "auto-conversion from consensus to implementation work "
                    "is intentionally deferred in IDLE_PROTOCOL_V1.md; an "
                    "implementer agent must turn the consensus into a "
                    "candidate diff before idle_consensus_to_pr / draft_pr "
                    "/ auto_merge can run"
                ),
                (
                    "once a candidate diff exists, the chain is: "
                    "idle_consensus_artifact -> idle_consensus_draft_pr -> "
                    "pr_status_snapshot -> idle_consensus_auto_merge --apply"
                ),
            ],
        )

    if status in OPERATOR_REVIEW_STATUSES:
        stale_terminal = _stale_terminal_session(
            events,
            now_utc=now_utc,
            max_age_hours=open_request_max_age_hours,
        )
        if stale_terminal["stale"]:
            recommended = (
                f"python tools/agent_next_task.py --agent {agent} --json"
                if agent
                else "python tools/agent_next_task.py --agent <agent> --json"
            )
            return _report(
                decision="stale_terminal_session",
                next_action="run_agent_next_task",
                recommended_command=recommended,
                idle_report=idle_report,
                session_summary={
                    **summary,
                    "stale_terminal_session": stale_terminal,
                },
                notes=[
                    f"idle-protocol terminal status is stale: {status}",
                    (
                        "operator review remains recorded for that old "
                        "idle-protocol instance, but it no longer blocks "
                        "new safe work selection while idle_check has no "
                        "blockers"
                    ),
                    (
                        "the next live agent should use agent_next_task.py "
                        "to claim deterministic unblocked work"
                    ),
                ],
            )
        reason_codes = summary.get("reason_codes") or []
        return _report(
            decision="operator_review_required",
            next_action="operator_handles",
            idle_report=idle_report,
            session_summary=summary,
            notes=[
                f"idle-protocol terminal status: {status}",
                (
                    f"reason_codes: {reason_codes}"
                    if reason_codes
                    else "see session_summary for details"
                ),
            ],
        )

    return _report(
        decision="unknown_state",
        next_action="operator_handles",
        idle_report=idle_report,
        session_summary=summary,
        notes=[
            f"idle session summary returned an unrecognized status: {status!r}",
        ],
    )


def _report(
    *,
    decision: str,
    next_action: str,
    idle_report: Mapping[str, Any],
    session_summary: Mapping[str, Any],
    notes: Sequence[str],
    recommended_command: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision": decision,
        "next_action": next_action,
        "exit_code": 0,
        "idle_report": dict(idle_report),
        "session_summary": dict(session_summary),
        "notes": list(notes),
    }
    if recommended_command:
        payload["recommended_command"] = recommended_command
    return payload


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _stale_terminal_session(
    events: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
    max_age_hours: float,
) -> dict[str, Any]:
    latest = _latest_idle_payload_ts(events)
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    stale = latest is not None and latest < cutoff
    return {
        "stale": stale,
        "latest_idle_payload_utc": _iso(latest) if latest is not None else None,
        "cutoff_utc": _iso(cutoff),
        "max_age_hours": max_age_hours,
    }


def _latest_idle_payload_ts(events: Sequence[Mapping[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        payload = event.get("payload")
        is_idle_payload = (
            event.get("protocol_version") == "idle-protocol.v1"
            or (
                isinstance(payload, Mapping)
                and payload.get("protocol_version") == "idle-protocol.v1"
            )
        )
        if not is_idle_payload:
            continue
        ts_raw = str(event.get("ts_utc", "")).strip()
        if not ts_raw:
            continue
        try:
            ts = _parse_utc(ts_raw)
        except ValueError:
            continue
        latest = ts if latest is None or ts > latest else latest
    return latest


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
