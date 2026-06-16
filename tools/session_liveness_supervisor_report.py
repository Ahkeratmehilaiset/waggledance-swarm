# SPDX-License-Identifier: BUSL-1.1
"""Read-only session liveness supervisor report.

This tool correlates bridge activity, wake delivery, watcher visibility,
optional screen-state snapshots, and active write/checkpoint claims. It
recommends when a target agent session should be restarted or verified, but it
never performs a restart, sends keystrokes, writes the bridge, emits RCO
decisions, or skips gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    HEARTBEAT_ONLY_EVENT_TYPES,
    _event_agent,
    _event_status,
    _event_ts,
    _event_type,
    _parse_utc,
    _task_id,
    read_events,
)
from tools.bridge_session_watcher_probe import (  # noqa: E402
    collect_live_process_snapshot,
    probe_bridge_session_watchers,
    read_active_claim_counts,
    read_active_claim_records,
    read_process_snapshot,
)
from tools.check_bridge_wake_delivery import (  # noqa: E402
    WakeDeliveryError,
    check_wake_delivery,
)
from waggledance.core.work_queue import AGENT_ID_PATTERN, resolve_bridge_root  # noqa: E402


DEFAULT_TARGET_AGENTS = (
    "codex-lead-1",
    "codex-tools-1",
    "claude-rco-1",
    "claude-rco-2",
)
DEFAULT_ACTIVITY_GAP_MINUTES = 45.0
DEFAULT_CYCLE_BUDGET_MINUTES = 90.0
DEFAULT_ACTIVE_CLAIM_MAX_AGE_HOURS = 24.0
DEFAULT_WAKE_MIN_AGE_MINUTES = 12.0
DEFAULT_WAKE_MIN_REPEATS = 2
DEFAULT_TAIL = 50000

WORKING_SCREEN_STATES = {"working", "streaming", "spinner", "running", "busy"}
IDLE_SCREEN_STATES = {"idle", "idle_prompt", "prompt", "ready", "waiting"}
WEDGED_SCREEN_STATES = {"wedged", "frozen", "unresponsive", "model_error", "error"}
ACTIVE_CLAIM_STATUS = {"active", "claimed", "in_progress"}
TERMINAL_EVENT_TYPES = {"done", "release", "handoff", "blocked"}
TERMINAL_STATUSES = {
    "done",
    "released",
    "handoff",
    "blocked",
    "merged",
    "merged_observed",
    "merged_with_magma_receipt",
}
SAME_TASK_RECEIPT_STATUSES = {
    "autonomous_merge_receipt",
    "merged_observed",
    "merged_with_magma_receipt",
}
SAME_TASK_RECEIPT_EVENT_TYPES = {"decision", "done", "handoff", "release"}


class SessionLivenessSupervisorError(ValueError):
    """Raised when the supervisor report cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only session liveness supervisor report. Correlates bridge "
            "events, wake delivery, watcher process snapshots, optional screen "
            "state, active write claims, and cycle budget into restart "
            "recommendations."
        )
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
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    process_source = parser.add_mutually_exclusive_group()
    process_source.add_argument(
        "--processes-json",
        help=(
            "Optional JSON process snapshot path, or '-' for stdin. Accepts the "
            "same format as bridge_session_watcher_probe."
        ),
    )
    process_source.add_argument(
        "--live-processes",
        action="store_true",
        help="Collect a read-only local Win32_Process snapshot in memory.",
    )
    parser.add_argument(
        "--screen-state-json",
        help=(
            "Optional screen-state snapshot path. Accepts a list, an object with "
            "'agents', or an object keyed by agent id."
        ),
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=None,
        help="Agent id to inspect. Repeatable. Defaults to active WD lanes.",
    )
    parser.add_argument(
        "--activity-gap-minutes",
        type=float,
        default=DEFAULT_ACTIVITY_GAP_MINUTES,
        help="Recommend attention when target-origin activity is older than this.",
    )
    parser.add_argument(
        "--cycle-budget-minutes",
        type=float,
        default=DEFAULT_CYCLE_BUDGET_MINUTES,
        help=(
            "Recommend a fresh handoff-bootstrapped session when the supplied "
            "screen-state cycle age exceeds this budget."
        ),
    )
    parser.add_argument(
        "--wake-min-age-minutes",
        type=float,
        default=DEFAULT_WAKE_MIN_AGE_MINUTES,
        help="Wake-delivery groups must be at least this old to count.",
    )
    parser.add_argument(
        "--wake-min-repeats",
        type=int,
        default=DEFAULT_WAKE_MIN_REPEATS,
        help="Minimum unresolved wake_request count per target/task.",
    )
    parser.add_argument(
        "--active-claim-max-age-hours",
        type=float,
        default=DEFAULT_ACTIVE_CLAIM_MAX_AGE_HOURS,
        help=(
            "Ignore event-derived active claims older than this many hours; "
            "use <=0 to include the full selected event tail."
        ),
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for deterministic reports.",
    )
    parser.add_argument(
        "--fail-on-restart-recommended",
        action="store_true",
        help="Return exit code 3 when an unblocked restart is recommended.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    try:
        events = read_events(events_path, tail=args.tail)
        processes = _load_processes(args)
        screen_states = read_screen_state_snapshot(args.screen_state_json)
        active_claim_file_records, claim_read_errors = read_active_claim_records(
            bridge_root
        )
        active_claim_file_counts = _claim_file_counts_from_records(
            active_claim_file_records
        )
        report = build_session_liveness_supervisor_report(
            events=events,
            bridge_root=bridge_root,
            processes=processes,
            process_snapshot_checked=args.processes_json is not None
            or bool(args.live_processes),
            screen_states=screen_states,
            active_claim_file_counts=active_claim_file_counts,
            active_claim_file_records=active_claim_file_records,
            claim_read_errors=claim_read_errors,
            agents=args.agent,
            activity_gap_minutes=args.activity_gap_minutes,
            cycle_budget_minutes=args.cycle_budget_minutes,
            active_claim_max_age_hours=args.active_claim_max_age_hours,
            wake_min_age_minutes=args.wake_min_age_minutes,
            wake_min_repeats=args.wake_min_repeats,
            now_utc=_parse_now(args.now),
        )
    except (BridgeNextActionError, WakeDeliveryError) as exc:
        report = {
            "ok": False,
            "decision": "session_liveness_supervisor_error",
            "errors": exc.report.get("errors", [str(exc)]),
            "authority_boundary": _authority_boundary(),
        }
        exit_code = getattr(exc, "exit_code", 2)
    except SessionLivenessSupervisorError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except (OSError, ValueError) as exc:
        report = {
            "ok": False,
            "decision": "session_liveness_supervisor_error",
            "errors": [str(exc) or exc.__class__.__name__],
            "authority_boundary": _authority_boundary(),
        }
        exit_code = 2
    else:
        exit_code = (
            3
            if args.fail_on_restart_recommended
            and report["restart_recommended_count"]
            else 0
        )

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def build_session_liveness_supervisor_report(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path | None = None,
    processes: Sequence[Mapping[str, Any]] = (),
    process_snapshot_checked: bool = False,
    screen_states: Mapping[str, Mapping[str, Any]] | None = None,
    active_claim_file_counts: Mapping[str, int] | None = None,
    active_claim_file_records: Sequence[Mapping[str, Any]] = (),
    claim_read_errors: Sequence[str] = (),
    agents: Sequence[str] | None = None,
    activity_gap_minutes: float = DEFAULT_ACTIVITY_GAP_MINUTES,
    cycle_budget_minutes: float = DEFAULT_CYCLE_BUDGET_MINUTES,
    active_claim_max_age_hours: float | None = DEFAULT_ACTIVE_CLAIM_MAX_AGE_HOURS,
    wake_min_age_minutes: float = DEFAULT_WAKE_MIN_AGE_MINUTES,
    wake_min_repeats: int = DEFAULT_WAKE_MIN_REPEATS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only supervisor report for target agent sessions."""
    _validate_thresholds(
        activity_gap_minutes=activity_gap_minutes,
        cycle_budget_minutes=cycle_budget_minutes,
        active_claim_max_age_hours=active_claim_max_age_hours,
        wake_min_age_minutes=wake_min_age_minutes,
        wake_min_repeats=wake_min_repeats,
    )
    target_agents = _normalize_agents(agents)
    screens = dict(screen_states or {})
    effective_now = (now_utc or _utc_now()).astimezone(timezone.utc)
    active_claims = _active_claims_by_agent(
        events,
        now_utc=effective_now,
        max_age_hours=active_claim_max_age_hours,
    )
    claim_file_records = _claim_file_records_by_agent(active_claim_file_records)
    claim_file_counts = _claim_file_counts(active_claim_file_counts or {})
    for agent, records in claim_file_records.items():
        claim_file_counts[agent] = max(claim_file_counts.get(agent, 0), len(records))
    claim_counts = {
        agent: len(claims) for agent, claims in active_claims.items()
    }
    for agent, count in claim_file_counts.items():
        claim_counts[agent] = max(claim_counts.get(agent, 0), count)

    agent_filter = target_agents or _infer_target_agents(
        events=events,
            active_claims=active_claims,
            active_claim_file_counts=claim_file_counts,
            screen_states=screens,
        )
    watcher_report = (
        probe_bridge_session_watchers(
            processes=processes,
            agents=agent_filter,
            active_claim_counts=claim_counts,
            claim_read_errors=claim_read_errors,
        )
        if process_snapshot_checked
        else _unchecked_watcher_report(agent_filter, claim_read_errors)
    )
    wake_report = check_wake_delivery(
        events=events,
        bridge_root=bridge_root,
        agents=agent_filter,
        min_age_minutes=wake_min_age_minutes,
        min_repeats=wake_min_repeats,
        now_utc=effective_now,
    )
    latest_activity = _latest_activity_by_agent(events)
    wake_by_agent = _wake_stalls_by_agent(wake_report)
    watcher_by_agent = {
        str(row.get("agent") or ""): row
        for row in watcher_report.get("agents", [])
        if isinstance(row, Mapping)
    }

    rows = [
        _agent_row(
            agent=agent,
            now_utc=effective_now,
            last_activity=latest_activity.get(agent),
            watcher_row=watcher_by_agent.get(agent),
            watcher_checked=process_snapshot_checked,
            wake_stalls=wake_by_agent.get(agent, []),
            screen_state=screens.get(agent),
            active_claims=active_claims.get(agent, []),
            active_claim_count=claim_counts.get(agent, 0),
            active_claim_file_count=claim_file_counts.get(agent, 0),
            active_claim_file_records=claim_file_records.get(agent, []),
            activity_gap_minutes=activity_gap_minutes,
            cycle_budget_minutes=cycle_budget_minutes,
        )
        for agent in sorted(agent_filter)
    ]
    by_status: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1

    restart_recommended = [row for row in rows if row["restart_recommended"]]
    restart_blocked = [row for row in rows if row["restart_blocked"]]
    watcher_repair = [row for row in rows if row["watcher_repair_recommended"]]
    checkpoint_blocked = [
        row for row in rows if not row["restart_checkpoint_ready"]
    ]
    checkpoint_unknown_count = sum(
        int(row["active_unknown_scope_claim_count"]) for row in rows
    )
    decision = _decision(
        restart_recommended_count=len(restart_recommended),
        restart_blocked_count=len(restart_blocked),
        watcher_repair_count=len(watcher_repair),
    )
    return {
        "ok": True,
        "report_version": "wd.session_liveness_supervisor_report.v0",
        "decision": decision,
        "events_checked": len(events),
        "target_agents": sorted(agent_filter),
        "agent_count": len(rows),
        "activity_gap_minutes": activity_gap_minutes,
        "cycle_budget_minutes": cycle_budget_minutes,
        "active_claim_max_age_hours": active_claim_max_age_hours,
        "wake_min_age_minutes": wake_min_age_minutes,
        "wake_min_repeats": wake_min_repeats,
        "process_snapshot_checked": process_snapshot_checked,
        "screen_state_checked": bool(screens),
        "restart_recommended_count": len(restart_recommended),
        "restart_blocked_count": len(restart_blocked),
        "restart_checkpoint_ready_count": sum(
            1 for row in rows if row["restart_checkpoint_ready"]
        ),
        "restart_checkpoint_blocked_count": len(checkpoint_blocked),
        "active_unknown_scope_claim_count": checkpoint_unknown_count,
        "restart_recommended_agents": [
            str(row["agent"]) for row in restart_recommended
        ],
        "restart_blocked_agents": [str(row["agent"]) for row in restart_blocked],
        "watcher_repair_recommended_count": len(watcher_repair),
        "watcher_repair_recommended_agents": [
            str(row["agent"]) for row in watcher_repair
        ],
        "by_status": dict(sorted(by_status.items())),
        "claim_read_error_count": len(claim_read_errors),
        "claim_read_errors": sorted(str(item) for item in claim_read_errors),
        "wake_delivery": {
            "decision": wake_report["decision"],
            "stalled_count": wake_report["stalled_count"],
            "by_agent": wake_report["by_agent"],
        },
        "watcher_probe": {
            "decision": watcher_report["decision"],
            "missing_watcher_count": watcher_report["missing_watcher_count"],
            "missing_heartbeat_count": watcher_report["missing_heartbeat_count"],
        },
        "agents": rows,
        "authority_boundary": _authority_boundary(),
    }


def read_screen_state_snapshot(
    source: str | None,
) -> dict[str, Mapping[str, Any]]:
    if not source:
        return {}
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("screen_state_json_error") from exc
    _assert_finite(payload, path="screen_state")
    records: list[Mapping[str, Any]]
    if isinstance(payload, Mapping):
        if isinstance(payload.get("agents"), list):
            records = [item for item in payload["agents"] if isinstance(item, Mapping)]
        else:
            records = []
            for key, value in payload.items():
                if not isinstance(value, Mapping):
                    continue
                item = dict(value)
                item.setdefault("agent", key)
                records.append(item)
    elif isinstance(payload, list):
        records = [item for item in payload if isinstance(item, Mapping)]
    else:
        raise ValueError("screen_state_snapshot_not_object_or_list")

    by_agent: dict[str, Mapping[str, Any]] = {}
    for record in records:
        agent = str(record.get("agent") or "").strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            raise ValueError(f"screen_state_agent_invalid:{agent!r}")
        by_agent[agent] = dict(record)
    return by_agent


def _agent_row(
    *,
    agent: str,
    now_utc: datetime,
    last_activity: Mapping[str, Any] | None,
    watcher_row: Mapping[str, Any] | None,
    watcher_checked: bool,
    wake_stalls: Sequence[Mapping[str, Any]],
    screen_state: Mapping[str, Any] | None,
    active_claims: Sequence[Mapping[str, Any]],
    active_claim_count: int,
    active_claim_file_count: int,
    active_claim_file_records: Sequence[Mapping[str, Any]],
    activity_gap_minutes: float,
    cycle_budget_minutes: float,
) -> dict[str, Any]:
    last_activity_ts = _parse_utc(str(last_activity.get("ts_utc") or "")) if last_activity else None
    last_activity_age = _age_minutes(now_utc, last_activity_ts)
    activity_gap_exceeded = (
        last_activity_age is None or last_activity_age >= activity_gap_minutes
    )
    screen = _screen_summary(screen_state, now_utc=now_utc)
    active_write_claims = [
        claim for claim in active_claims if claim["write_scope"]
    ]
    event_claim_identities = {
        _event_claim_identity(claim) for claim in active_claims
    }
    file_claim_contract = _file_claim_contract(
        active_claim_file_records,
        active_claim_file_count=active_claim_file_count,
        event_claim_identities=event_claim_identities,
    )
    active_unknown_scope_claim_count = int(
        file_claim_contract["unmatched_count"]
    ) + int(file_claim_contract["malformed_count"])
    active_file_write_claim_count = int(file_claim_contract["write_count"])
    watcher_status = (
        str(watcher_row.get("status") or "unknown")
        if watcher_row
        else ("not_checked" if not watcher_checked else "missing_watcher")
    )
    watcher_missing = bool(watcher_row.get("missing_watcher")) if watcher_row else False
    heartbeat_missing = bool(watcher_row.get("missing_heartbeat")) if watcher_row else False
    wake_delivery_stalled = bool(wake_stalls)
    cycle_budget_exceeded = screen["cycle_budget_exceeded"] or (
        screen["cycle_age_minutes"] is not None
        and screen["cycle_age_minutes"] >= cycle_budget_minutes
    )
    triggers: list[str] = []
    if watcher_missing:
        triggers.append("watcher_missing")
    if heartbeat_missing:
        triggers.append("heartbeat_missing")
    if wake_delivery_stalled and not screen["working"]:
        triggers.append("wake_delivery_stalled")
    if screen["wedged"]:
        triggers.append("screen_state_wedged")
    if cycle_budget_exceeded:
        triggers.append("cycle_budget_exceeded")
    if (
        activity_gap_exceeded
        and screen["idle"]
        and not wake_delivery_stalled
        and not triggers
    ):
        triggers.append("idle_activity_gap_exceeded")

    restart_checkpoint_ready = not (
        active_write_claims
        or active_unknown_scope_claim_count
        or active_file_write_claim_count
    )
    restart_blocked = bool(not restart_checkpoint_ready and triggers)
    restart_recommended = bool(triggers and not restart_blocked)
    watcher_repair_recommended = bool(
        (watcher_missing or heartbeat_missing) and not restart_blocked
    )
    status = _row_status(
        restart_recommended=restart_recommended,
        restart_blocked=restart_blocked,
        watcher_repair_recommended=watcher_repair_recommended,
        triggers=triggers,
        screen=screen,
    )
    return {
        "agent": agent,
        "status": status,
        "last_activity_ts_utc": _format_utc(last_activity_ts),
        "last_activity_type": str(last_activity.get("type") or "") if last_activity else "",
        "last_activity_status": str(last_activity.get("status") or "") if last_activity else "",
        "last_activity_age_minutes": last_activity_age,
        "activity_gap_exceeded": activity_gap_exceeded,
        "activity_gap_minutes": activity_gap_minutes,
        "watcher_status": watcher_status,
        "watcher_checked": watcher_checked,
        "watcher_missing": watcher_missing,
        "heartbeat_missing": heartbeat_missing,
        "wake_delivery_stalled": wake_delivery_stalled,
        "wake_stall_count": len(wake_stalls),
        "screen_state": screen["state"],
        "screen_state_checked": screen_state is not None,
        "screen_observed_at_utc": screen["observed_at_utc"],
        "screen_age_minutes": screen["screen_age_minutes"],
        "screen_working": screen["working"],
        "screen_idle": screen["idle"],
        "screen_wedged": screen["wedged"],
        "cycle_age_minutes": screen["cycle_age_minutes"],
        "cycle_budget_exceeded": cycle_budget_exceeded,
        "cycle_budget_minutes": cycle_budget_minutes,
        "active_claim_count": active_claim_count,
        "active_claim_file_count": active_claim_file_count,
        "active_write_claim_count": len(active_write_claims),
        "active_write_claims": list(active_write_claims),
        "active_unknown_scope_claim_count": active_unknown_scope_claim_count,
        "active_claim_file_write_count": active_file_write_claim_count,
        "active_claim_file_unmatched_count": file_claim_contract["unmatched_count"],
        "active_claim_file_malformed_count": file_claim_contract["malformed_count"],
        "active_claim_file_records": file_claim_contract["records"],
        "restart_checkpoint_ready": restart_checkpoint_ready,
        "restart_checkpoint_contract": {
            "version": "wd.session_restart_checkpoint_contract.v0",
            "ready": restart_checkpoint_ready,
            "event_claim_count": len(active_claims),
            "file_claim_count": active_claim_file_count,
            "active_claim_count": active_claim_count,
            "active_write_claim_count": len(active_write_claims),
            "active_unknown_scope_claim_count": active_unknown_scope_claim_count,
            "active_claim_file_write_count": active_file_write_claim_count,
            "active_claim_file_unmatched_count": file_claim_contract[
                "unmatched_count"
            ],
            "active_claim_file_malformed_count": file_claim_contract[
                "malformed_count"
            ],
            "required_before_restart": True,
        },
        "restart_triggers": triggers,
        "restart_recommended": restart_recommended,
        "restart_blocked": restart_blocked,
        "restart_recommended_after_checkpoint": restart_blocked,
        "watcher_repair_recommended": watcher_repair_recommended,
        "safe_next_action": _safe_next_action(
            restart_recommended=restart_recommended,
            restart_blocked=restart_blocked,
            watcher_repair_recommended=watcher_repair_recommended,
            triggers=triggers,
            screen=screen,
            active_unknown_scope_claim_count=active_unknown_scope_claim_count,
            active_file_write_claim_count=active_file_write_claim_count,
        ),
        "diagnosis": _diagnosis(
            restart_recommended=restart_recommended,
            restart_blocked=restart_blocked,
            triggers=triggers,
            screen=screen,
            active_unknown_scope_claim_count=active_unknown_scope_claim_count,
            active_file_write_claim_count=active_file_write_claim_count,
        ),
    }


def _active_claims_by_agent(
    events: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
    max_age_hours: float | None,
) -> dict[str, list[dict[str, Any]]]:
    active: dict[tuple[str, str], dict[str, Any]] = {}
    for index, event in enumerate(events):
        agent = _event_agent(event)
        task_id = _task_id(event)
        if not agent or not task_id or not AGENT_ID_PATTERN.fullmatch(agent):
            continue
        key = (agent, task_id)
        event_type = _event_type(event)
        status = _event_status(event)
        if event_type == "claim" and (not status or status in ACTIVE_CLAIM_STATUS):
            active[key] = {
                "agent": agent,
                "task_id": task_id,
                "claim_index": index,
                "claim_ts_utc": _event_ts(event),
                "status": status or "active",
                "write_scope": _write_scope(event),
            }
            continue
        if _is_same_task_terminal_receipt(event_type=event_type, status=status):
            for active_key in list(active):
                _active_agent, active_task_id = active_key
                if active_task_id == task_id:
                    del active[active_key]
            continue
        if key in active and (
            event_type in TERMINAL_EVENT_TYPES or status in TERMINAL_STATUSES
        ):
            del active[key]
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for (agent, _task_id_value), claim in active.items():
        if not _claim_within_age_window(
            claim,
            now_utc=now_utc,
            max_age_hours=max_age_hours,
        ):
            continue
        by_agent.setdefault(agent, []).append(claim)
    for claims in by_agent.values():
        claims.sort(key=lambda claim: str(claim["claim_ts_utc"]))
    return by_agent


def _is_same_task_terminal_receipt(*, event_type: str, status: str) -> bool:
    return (
        event_type in SAME_TASK_RECEIPT_EVENT_TYPES
        and status in SAME_TASK_RECEIPT_STATUSES
    )


def _claim_file_counts(
    active_claim_file_counts: Mapping[str, int],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_agent, raw_count in active_claim_file_counts.items():
        agent = str(raw_agent).strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            continue
        counts[agent] = max(counts.get(agent, 0), max(0, int(raw_count)))
    return counts


def _claim_file_counts_from_records(
    active_claim_file_records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in active_claim_file_records:
        agent = str(record.get("agent") or "").strip().lower()
        if agent and AGENT_ID_PATTERN.fullmatch(agent):
            counts[agent] = counts.get(agent, 0) + 1
    return counts


def _claim_file_records_by_agent(
    active_claim_file_records: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for record in active_claim_file_records:
        agent = str(record.get("agent") or "").strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            continue
        normalized = {
            "agent": agent,
            "task_id": str(record.get("task_id") or "").strip(),
            "mode": str(record.get("mode") or "unknown").strip().lower(),
            "write_scope": _write_scope(record),
            "claimed_at_utc": str(record.get("claimed_at_utc") or ""),
            "last_heartbeat_utc": str(record.get("last_heartbeat_utc") or ""),
            "claim_lease_expires_utc": str(
                record.get("claim_lease_expires_utc") or ""
            ),
            "malformed": bool(record.get("malformed")),
        }
        if not normalized["task_id"] or normalized["mode"] not in {"read-only", "write"}:
            normalized["malformed"] = True
        by_agent.setdefault(agent, []).append(normalized)
    return by_agent


def _event_claim_identity(claim: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    write_scope = tuple(sorted(_normalize_scope(scope) for scope in claim["write_scope"]))
    mode = "write" if write_scope else "read-only"
    return str(claim.get("task_id") or ""), mode, write_scope


def _file_claim_identity(record: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    write_scope = tuple(sorted(_normalize_scope(scope) for scope in record["write_scope"]))
    return str(record.get("task_id") or ""), str(record.get("mode") or ""), write_scope


def _file_claim_contract(
    records: Sequence[Mapping[str, Any]],
    *,
    active_claim_file_count: int,
    event_claim_identities: set[tuple[str, str, tuple[str, ...]]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    write_count = 0
    unmatched_count = max(0, active_claim_file_count - len(records))
    malformed_count = 0
    for record in records:
        identity = _file_claim_identity(record)
        is_write = identity[1] == "write" or bool(identity[2])
        malformed = bool(record.get("malformed"))
        matched = identity in event_claim_identities
        if is_write:
            write_count += 1
        if malformed:
            malformed_count += 1
        elif not matched:
            unmatched_count += 1
        rows.append(
            {
                "task_id": identity[0],
                "mode": identity[1],
                "write_scope": list(identity[2]),
                "matched_event_claim": matched,
                "write_claim": is_write,
                "malformed": malformed,
                "checkpoint_blocks_restart": bool(is_write or malformed or not matched),
                "claimed_at_utc": str(record.get("claimed_at_utc") or ""),
                "last_heartbeat_utc": str(record.get("last_heartbeat_utc") or ""),
                "claim_lease_expires_utc": str(
                    record.get("claim_lease_expires_utc") or ""
                ),
            }
        )
    return {
        "records": rows,
        "write_count": write_count,
        "unmatched_count": unmatched_count,
        "malformed_count": malformed_count,
    }


def _normalize_scope(value: str) -> str:
    return str(value).replace("\\", "/").strip("/").lower()


def _claim_within_age_window(
    claim: Mapping[str, Any],
    *,
    now_utc: datetime,
    max_age_hours: float | None,
) -> bool:
    if max_age_hours is None or max_age_hours <= 0:
        return True
    claim_ts = _parse_utc(str(claim.get("claim_ts_utc") or ""))
    if claim_ts is None:
        return False
    age_hours = (
        now_utc.astimezone(timezone.utc) - claim_ts.astimezone(timezone.utc)
    ).total_seconds() / 3600.0
    return age_hours <= max_age_hours


def _write_scope(event: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (event.get("write_scope"), _payload_value(event, "write_scope")):
        if isinstance(source, str):
            values.extend(_split_scope_value(source))
        elif isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
            for item in source:
                values.extend(_split_scope_value(str(item)))
    return [item for item in dict.fromkeys(values) if item]


def _payload_value(event: Mapping[str, Any], key: str) -> Any:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return payload.get(key)
    return None


def _split_scope_value(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def _latest_activity_by_agent(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    latest_ts: dict[str, datetime] = {}
    for event in events:
        event_type = _event_type(event)
        if event_type in HEARTBEAT_ONLY_EVENT_TYPES:
            continue
        agent = _event_agent(event)
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            continue
        parsed_ts = _parse_utc(_event_ts(event))
        if parsed_ts is None:
            continue
        existing = latest_ts.get(agent)
        if existing is None or parsed_ts > existing:
            latest_ts[agent] = parsed_ts
            latest[agent] = {
                "ts_utc": _event_ts(event),
                "type": event_type,
                "status": _event_status(event),
            }
    return latest


def _wake_stalls_by_agent(report: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    by_agent: dict[str, list[Mapping[str, Any]]] = {}
    for row in report.get("stalled_wakes", []):
        if not isinstance(row, Mapping):
            continue
        agent = str(row.get("target_agent") or "")
        if agent:
            by_agent.setdefault(agent, []).append(row)
    return by_agent


def _screen_summary(
    screen_state: Mapping[str, Any] | None,
    *,
    now_utc: datetime,
) -> dict[str, Any]:
    if not screen_state:
        return {
            "state": "unknown",
            "observed_at_utc": "",
            "screen_age_minutes": None,
            "cycle_age_minutes": None,
            "working": False,
            "idle": False,
            "wedged": False,
            "cycle_budget_exceeded": False,
        }
    state = str(
        screen_state.get("screen_state")
        or screen_state.get("state")
        or screen_state.get("status")
        or "unknown"
    ).strip().lower()
    observed_at = _parse_utc(
        str(screen_state.get("observed_at_utc") or screen_state.get("ts_utc") or "")
    )
    cycle_started = _parse_utc(str(screen_state.get("cycle_started_at_utc") or ""))
    cycle_age = _number_or_none(screen_state.get("cycle_age_minutes"))
    if cycle_age is None and cycle_started is not None:
        cycle_age = _age_minutes(now_utc, cycle_started)
    return {
        "state": state,
        "observed_at_utc": _format_utc(observed_at),
        "screen_age_minutes": _age_minutes(now_utc, observed_at),
        "cycle_age_minutes": cycle_age,
        "working": state in WORKING_SCREEN_STATES,
        "idle": state in IDLE_SCREEN_STATES,
        "wedged": state in WEDGED_SCREEN_STATES,
        "cycle_budget_exceeded": any(
            _bool_value(screen_state.get(key))
            for key in (
                "cycle_budget_exceeded",
                "context_budget_exceeded",
                "token_budget_exceeded",
            )
        ),
    }


def _row_status(
    *,
    restart_recommended: bool,
    restart_blocked: bool,
    watcher_repair_recommended: bool,
    triggers: Sequence[str],
    screen: Mapping[str, Any],
) -> str:
    if restart_blocked:
        return "restart_blocked_active_write_claim"
    if watcher_repair_recommended:
        return "watcher_repair_recommended"
    if restart_recommended:
        return "session_restart_recommended"
    if screen["working"]:
        return "working_no_restart"
    if screen["idle"]:
        return "idle_no_restart"
    if triggers:
        return "attention_required"
    return "session_liveness_ok"


def _safe_next_action(
    *,
    restart_recommended: bool,
    restart_blocked: bool,
    watcher_repair_recommended: bool,
    triggers: Sequence[str],
    screen: Mapping[str, Any],
    active_unknown_scope_claim_count: int,
    active_file_write_claim_count: int,
) -> str:
    if restart_blocked:
        if active_file_write_claim_count:
            return (
                "write durable handoff/checkpoint and release active write-mode "
                "claim files before any restart"
            )
        if active_unknown_scope_claim_count:
            return (
                "resolve unmatched or malformed active claim-file identity "
                "before any restart"
            )
        return (
            "write durable handoff/checkpoint and release the active write "
            "claim before any restart"
        )
    if watcher_repair_recommended:
        return (
            "restart or verify the bridge watcher/heartbeat helper; require "
            "target-origin bridge activity before declaring recovery"
        )
    if restart_recommended:
        if "cycle_budget_exceeded" in triggers:
            return (
                "start a fresh handoff-bootstrapped session after confirming "
                "there is no uncheckpointed write claim"
            )
        return (
            "restart or verify the target session poll loop; require "
            "target-origin bridge activity after restart"
        )
    if screen["working"]:
        return "wait for the visible working cycle and recheck before restart"
    if screen["idle"]:
        return "continue normal bridge polling; no restart evidence"
    return "continue monitoring; provide screen-state input for stronger classification"


def _diagnosis(
    *,
    restart_recommended: bool,
    restart_blocked: bool,
    triggers: Sequence[str],
    screen: Mapping[str, Any],
    active_unknown_scope_claim_count: int,
    active_file_write_claim_count: int,
) -> str:
    if restart_blocked:
        if active_file_write_claim_count:
            return (
                "restart evidence is present, but an active write-mode claim "
                "file makes restart unsafe until checkpointed"
            )
        if active_unknown_scope_claim_count:
            return (
                "restart evidence is present, but active claim-file identity "
                "is unmatched or malformed"
            )
        return (
            "restart evidence is present, but an active write claim makes "
            "restart unsafe until checkpointed"
        )
    if restart_recommended:
        return "restart evidence present: " + ",".join(triggers)
    if screen["working"]:
        return "screen state says the target is actively working"
    if screen["idle"]:
        return "screen state is idle but no restart trigger is present"
    return "no restart trigger is present"


def _decision(
    *,
    restart_recommended_count: int,
    restart_blocked_count: int,
    watcher_repair_count: int,
) -> str:
    if restart_blocked_count:
        return "session_restart_blocked_by_active_write_claim"
    if watcher_repair_count and watcher_repair_count == restart_recommended_count:
        return "session_watcher_repair_recommended"
    if restart_recommended_count:
        return "session_restart_recommended"
    if watcher_repair_count:
        return "session_watcher_repair_recommended"
    return "session_liveness_ok"


def _infer_target_agents(
    *,
    events: Sequence[Mapping[str, Any]],
    active_claims: Mapping[str, Sequence[Mapping[str, Any]]],
    active_claim_file_counts: Mapping[str, int],
    screen_states: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    agents = set(DEFAULT_TARGET_AGENTS)
    agents.update(active_claims)
    agents.update(str(agent).strip().lower() for agent in active_claim_file_counts)
    agents.update(screen_states)
    for event in events:
        agent = _event_agent(event)
        if agent in DEFAULT_TARGET_AGENTS:
            agents.add(agent)
    return sorted(agent for agent in agents if AGENT_ID_PATTERN.fullmatch(agent))


def _unchecked_watcher_report(
    agents: Sequence[str], claim_read_errors: Sequence[str]
) -> dict[str, Any]:
    return {
        "decision": "watcher_process_snapshot_not_checked",
        "missing_watcher_count": 0,
        "missing_heartbeat_count": 0,
        "agents": [
            {
                "agent": agent,
                "status": "not_checked",
                "missing_watcher": False,
                "missing_heartbeat": False,
            }
            for agent in sorted(agents)
        ],
        "claim_read_errors": list(claim_read_errors),
    }


def _load_processes(args: argparse.Namespace) -> list[Mapping[str, Any]]:
    if args.live_processes:
        return collect_live_process_snapshot()
    if args.processes_json is not None:
        return read_process_snapshot(args.processes_json)
    return []


def _normalize_agents(agents: Sequence[str] | None) -> tuple[str, ...]:
    if not agents:
        return ()
    normalized: list[str] = []
    for raw in agents:
        agent = str(raw or "").strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            raise SessionLivenessSupervisorError(
                {
                    "ok": False,
                    "decision": "session_liveness_supervisor_error",
                    "errors": [f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"],
                    "authority_boundary": _authority_boundary(),
                }
            )
        normalized.append(agent)
    return tuple(dict.fromkeys(normalized))


def _validate_thresholds(
    *,
    activity_gap_minutes: float,
    cycle_budget_minutes: float,
    active_claim_max_age_hours: float | None,
    wake_min_age_minutes: float,
    wake_min_repeats: int,
) -> None:
    errors: list[str] = []
    if not math.isfinite(activity_gap_minutes) or activity_gap_minutes < 0:
        errors.append("activity_gap_minutes must be non-negative")
    if not math.isfinite(cycle_budget_minutes) or cycle_budget_minutes <= 0:
        errors.append("cycle_budget_minutes must be positive")
    if active_claim_max_age_hours is not None and not math.isfinite(
        active_claim_max_age_hours
    ):
        errors.append("active_claim_max_age_hours must be finite or <=0")
    if not math.isfinite(wake_min_age_minutes) or wake_min_age_minutes < 0:
        errors.append("wake_min_age_minutes must be non-negative")
    if wake_min_repeats <= 0:
        errors.append("wake_min_repeats must be positive")
    if errors:
        raise SessionLivenessSupervisorError(
            {
                "ok": False,
                "decision": "session_liveness_supervisor_error",
                "errors": errors,
                "authority_boundary": _authority_boundary(),
            }
        )


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise ValueError("now must be an ISO-8601 UTC timestamp")
    return parsed


def _age_minutes(now_utc: datetime, then_utc: datetime | None) -> float | None:
    if then_utc is None:
        return None
    age = (
        now_utc.astimezone(timezone.utc) - then_utc.astimezone(timezone.utc)
    ).total_seconds() / 60.0
    return round(max(0.0, age), 3)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone.utc)


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(max(0.0, number), 3)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


def _assert_finite(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non_finite_json:{path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, path=f"{path}[{index}]")


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "keyboard_input_allowed": False,
        "process_termination_allowed": False,
        "process_restart_allowed": False,
        "rco_pass_emit_allowed": False,
        "gate_skip_allowed": False,
        "merge_allowed": False,
        "network_required": False,
        "restart_requires_checkpoint_contract": True,
    }


def _print_human(report: Mapping[str, Any]) -> None:
    print(f"decision: {report.get('decision')}")
    print(f"restart_recommended_count: {report.get('restart_recommended_count', 0)}")
    print(f"restart_blocked_count: {report.get('restart_blocked_count', 0)}")
    for row in report.get("agents", []):
        if not isinstance(row, Mapping):
            continue
        print(
            "- "
            f"{row.get('agent')}: {row.get('status')} "
            f"triggers={','.join(row.get('restart_triggers', [])) or '-'} "
            f"next={row.get('safe_next_action')}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
