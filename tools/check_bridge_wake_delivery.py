# SPDX-License-Identifier: BUSL-1.1
"""Diagnose wake requests that are visible on the bridge but not delivered.

The bridge wake substrate is intentionally local: a watcher writes
``wake_<agent>`` and the target session must consume it. This read-only tool
turns the silent failure mode into a concrete report: repeated wake_request
events for a target with no later target activity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    CLOSED_REQUEST_STATUSES,
    HEARTBEAT_ONLY_EVENT_TYPES,
    _event_agent,
    _event_recipients,
    _event_status,
    _event_ts,
    _event_type,
    _admitted_event_time,
    _parse_utc,
    _task_id,
    read_events,
)
from waggledance.core.work_queue import AGENT_ID_PATTERN, resolve_bridge_root  # noqa: E402


DEFAULT_MIN_AGE_MINUTES = 12.0
DEFAULT_MIN_REPEATS = 2
DEFAULT_MAX_AGE_HOURS = 12.0
DEFAULT_SELF_LIVENESS_WINDOW_MINUTES = 40.0
DEFAULT_TAIL = 50000
WAKE_FILE_FRESHNESS_TOLERANCE_SECONDS = 2.0
WAKE_SEND_FAILED_TARGET_PATTERN = re.compile(
    r"\bKeying\s+['\"](?P<agent>[a-z0-9][a-z0-9_.-]*)['\"]\s+failed\b",
    re.IGNORECASE,
)
RCO_AGENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*-rco-\d+$")


class WakeDeliveryError(ValueError):
    """Raised when the wake-delivery report cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read bridge events and report wake_request traffic that has not "
            "resulted in later target-agent bridge activity."
        ),
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
        help="Target agent to include. Repeat to filter to multiple agents.",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="Only report unresolved wake groups at least this old.",
    )
    parser.add_argument(
        "--min-repeats",
        type=int,
        default=DEFAULT_MIN_REPEATS,
        help="Minimum unresolved wake_request count per target/task.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=(
            "Ignore unresolved wake groups older than this many hours; use "
            "<=0 to include the full selected event tail."
        ),
    )
    parser.add_argument(
        "--self-liveness-window-minutes",
        type=float,
        default=DEFAULT_SELF_LIVENESS_WINDOW_MINUTES,
        help=(
            "Treat a target agent with self-authored non-heartbeat activity "
            "after the latest wake_request and inside this window as delivered "
            "and self-paced instead of a wake-delivery stall."
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
        help="Override current UTC time for wake-age evaluation.",
    )
    parser.add_argument(
        "--fail-on-stalled",
        action="store_true",
        help="Return exit code 3 when stalled wake delivery is detected.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    try:
        report = check_wake_delivery(
            events=read_events(events_path, tail=args.tail),
            bridge_root=bridge_root,
            agents=args.agent,
            min_age_minutes=args.min_age_minutes,
            min_repeats=args.min_repeats,
            max_age_hours=args.max_age_hours,
            self_liveness_window_minutes=args.self_liveness_window_minutes,
            now_utc=_parse_now(args.now),
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "wake_delivery_error",
            "errors": exc.report.get("errors", [str(exc)]),
        }
        exit_code = 2
    except WakeDeliveryError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "wake_delivery_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 3 if args.fail_on_stalled and report["stalled_count"] else 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def check_wake_delivery(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path | None = None,
    agents: Sequence[str] | None = None,
    min_age_minutes: float = DEFAULT_MIN_AGE_MINUTES,
    min_repeats: int = DEFAULT_MIN_REPEATS,
    max_age_hours: float | None = DEFAULT_MAX_AGE_HOURS,
    self_liveness_window_minutes: float = DEFAULT_SELF_LIVENESS_WINDOW_MINUTES,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return unresolved wake_request groups with no later target activity."""
    if min_age_minutes < 0:
        raise WakeDeliveryError(
            {
                "ok": False,
                "decision": "wake_delivery_error",
                "errors": ["min_age_minutes must be non-negative"],
            }
        )
    if min_repeats <= 0:
        raise WakeDeliveryError(
            {
                "ok": False,
                "decision": "wake_delivery_error",
                "errors": ["min_repeats must be positive"],
            }
        )
    if (
        not math.isfinite(self_liveness_window_minutes)
        or self_liveness_window_minutes <= 0
    ):
        raise WakeDeliveryError(
            {
                "ok": False,
                "decision": "wake_delivery_error",
                "errors": ["self_liveness_window_minutes must be positive"],
            }
        )
    if max_age_hours is not None and max_age_hours > 0:
        max_age_minutes = max_age_hours * 60.0
    else:
        max_age_hours = None
        max_age_minutes = None

    agent_filter = _normalize_agent_filter(agents)
    effective_now = (now_utc or _utc_now()).astimezone(timezone.utc)
    unresolved = _unresolved_wake_groups(
        events=events,
        agent_filter=agent_filter,
        now_utc=effective_now,
    )
    self_liveness_by_agent = _latest_self_liveness_by_agent(
        events,
        now_utc=effective_now,
    )

    stalled: list[dict[str, Any]] = []
    self_pacing: list[dict[str, Any]] = []
    for group in unresolved.values():
        first_ts = _parse_utc(str(group["first_ts_utc"]))
        last_ts = _parse_utc(str(group["last_ts_utc"]))
        if first_ts is None or last_ts is None:
            continue
        unresolved_age_minutes = (
            effective_now.astimezone(timezone.utc) - first_ts
        ).total_seconds() / 60.0
        latest_wake_age_minutes = (
            effective_now.astimezone(timezone.utc) - last_ts
        ).total_seconds() / 60.0
        if unresolved_age_minutes < min_age_minutes:
            continue
        if max_age_minutes is not None and latest_wake_age_minutes > max_age_minutes:
            continue
        single_wake_preflight = _rco_single_wake_preflight(
            group,
            bridge_root=bridge_root,
            now_utc=effective_now,
            min_age_minutes=min_age_minutes,
        )
        if int(group["wake_request_count"]) < min_repeats and not single_wake_preflight:
            continue
        self_liveness = _self_liveness_suppression(
            group,
            self_liveness_by_agent=self_liveness_by_agent,
            now_utc=effective_now,
            self_liveness_window_minutes=self_liveness_window_minutes,
        )
        if self_liveness is not None:
            self_pacing.append(
                _wake_row(
                    group,
                    age_minutes=unresolved_age_minutes,
                    latest_wake_age_minutes=latest_wake_age_minutes,
                    now_utc=effective_now,
                    bridge_root=bridge_root,
                    classification="self_pacing_or_silent_by_design",
                    self_liveness=self_liveness,
                )
            )
            continue
        stalled.append(
            _wake_row(
                group,
                age_minutes=unresolved_age_minutes,
                latest_wake_age_minutes=latest_wake_age_minutes,
                now_utc=effective_now,
                bridge_root=bridge_root,
                classification=(
                    "rco_single_wake_preflight_stalled"
                    if single_wake_preflight
                    else "stalled_wake_delivery"
                ),
            )
        )

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

    delivery_escalation = _delivery_escalation(
        stalled,
        by_agent,
        bridge_root=bridge_root,
    )
    return {
        "ok": True,
        "decision": "wake_delivery_stalled" if stalled else "wake_delivery_ok",
        "events_checked": len(events),
        "min_age_minutes": min_age_minutes,
        "min_repeats": min_repeats,
        "max_age_hours": max_age_hours,
        "self_liveness_window_minutes": self_liveness_window_minutes,
        "agent_filter": sorted(agent_filter) if agent_filter else [],
        "stalled_count": len(stalled),
        "by_agent": dict(sorted(by_agent.items())),
        "delivery_escalation": delivery_escalation,
        "stalled_wakes": stalled,
        "single_wake_preflight_count": sum(
            1
            for row in stalled
            if row.get("classification") == "rco_single_wake_preflight_stalled"
        ),
        "self_pacing_wake_count": len(self_pacing),
        "self_pacing_wakes": self_pacing,
    }


def _delivery_escalation(
    stalled: Sequence[Mapping[str, Any]],
    by_agent: Mapping[str, int],
    *,
    bridge_root: Path | None,
) -> dict[str, Any]:
    stalled_present = bool(stalled)
    has_send_failure = any(
        int(row.get("wake_send_failed_count") or 0) for row in stalled
    )
    safe_next_action = ""
    reason = ""
    if stalled_present:
        if has_send_failure:
            safe_next_action = "repair_operator_wake_routing_or_title_map"
            reason = "operator_wake_send_failed_for_unresolved_wake"
        else:
            safe_next_action = "restart_or_verify_target_agent_bridge_session_watcher"
            reason = "wake_request_visible_but_no_later_target_bridge_activity"
    return {
        "required": stalled_present,
        "target_agents": sorted(by_agent),
        "do_not_emit_additional_wake_requests": stalled_present,
        "safe_next_action": safe_next_action,
        "operator_action_required": stalled_present,
        "reason": reason,
        "diagnostic_next_action": (
            "run session_liveness_supervisor_report before writing more wake_requests"
            if stalled_present
            else ""
        ),
        "diagnostic_commands": _session_liveness_supervisor_commands(
            targets=by_agent,
            bridge_root=bridge_root,
        ),
    }


def _session_liveness_supervisor_commands(
    *,
    targets: Mapping[str, int],
    bridge_root: Path | None,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for target in sorted(targets):
        argv = [
            "python",
            "tools/session_liveness_supervisor_report.py",
        ]
        if bridge_root is not None:
            argv.extend(["--bridge-root", str(bridge_root)])
        argv.extend(["--agent", target, "--json"])
        commands.append(
            {
                "target_agent": target,
                "authority": "read_only_report_no_restart_no_gate_skip",
                "argv": argv,
                "command": subprocess.list2cmdline(argv),
            }
        )
    return commands


def _unresolved_wake_groups(
    *,
    events: Sequence[Mapping[str, Any]],
    agent_filter: set[str],
    now_utc: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for index, event in enumerate(events):
        event_agent = _event_agent(event)
        event_ts = _event_ts(event)
        if event_agent and _is_target_delivery_activity(event):
            _clear_for_target_activity(groups, event_agent=event_agent)
        _clear_for_terminal_task(groups, event)
        _record_wake_send_failure(groups, event, now_utc=now_utc)

        if _event_type(event) != "wake_request":
            continue
        if _event_status(event) in CLOSED_REQUEST_STATUSES:
            continue
        task_id = _task_id(event)
        if not task_id:
            continue
        admitted_ts = _admitted_event_time(event, now_utc=now_utc)
        if admitted_ts is None:
            continue
        for target in _event_recipients(event):
            if not target or target == event_agent:
                continue
            if agent_filter and target not in agent_filter:
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
                    "last_message": _safe_message(event.get("message")),
                    "last_event_index": index,
                }
                continue
            existing["wake_request_count"] = int(existing["wake_request_count"]) + 1
            existing["last_event_index"] = index
            existing_last_ts = _parse_utc(str(existing.get("last_ts_utc") or ""))
            if existing_last_ts is None or admitted_ts >= existing_last_ts:
                existing["last_ts_utc"] = event_ts
                existing["last_status"] = _event_status(event)
                existing["last_message"] = _safe_message(event.get("message"))
            if event_agent:
                requesters = existing["requesters"]
                if isinstance(requesters, set):
                    requesters.add(event_agent)
    return groups


def _record_wake_send_failure(
    groups: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
    *,
    now_utc: datetime,
) -> None:
    target = _wake_send_failed_target(event)
    if not target:
        return
    admitted_ts = _admitted_event_time(event, now_utc=now_utc)
    if admitted_ts is None:
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
            group["latest_wake_send_failed_message"] = _safe_message(
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


def _is_target_delivery_activity(event: Mapping[str, Any]) -> bool:
    return _event_type(event) not in HEARTBEAT_ONLY_EVENT_TYPES


def _is_self_liveness_activity(event: Mapping[str, Any]) -> bool:
    if _event_type(event) in HEARTBEAT_ONLY_EVENT_TYPES:
        return False
    return not (_event_type(event) == "message" and _event_status(event) == "received")


def _latest_self_liveness_by_agent(
    events: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for event in events:
        agent = _event_agent(event)
        if not agent or not _is_self_liveness_activity(event):
            continue
        event_ts = _admitted_event_time(event, now_utc=now_utc)
        if event_ts is None:
            continue
        existing = latest.get(agent)
        if existing is None or event_ts > existing:
            latest[agent] = event_ts
    return latest


def _self_liveness_suppression(
    group: Mapping[str, Any],
    *,
    self_liveness_by_agent: Mapping[str, datetime],
    now_utc: datetime,
    self_liveness_window_minutes: float,
) -> dict[str, Any] | None:
    target = str(group["target_agent"])
    last_self = self_liveness_by_agent.get(target)
    if last_self is None:
        return None
    last_wake = _parse_utc(str(group["last_ts_utc"]))
    if last_wake is None or last_self <= last_wake:
        return None
    reason = "target_self_activity_after_latest_wake"
    self_age_minutes = max(
        0.0,
        (now_utc.astimezone(timezone.utc) - last_self).total_seconds() / 60.0,
    )
    if self_age_minutes >= self_liveness_window_minutes:
        return None
    return {
        "last_self_activity_ts_utc": _format_utc(last_self),
        "last_self_activity_age_minutes": round(self_age_minutes, 3),
        "self_liveness_reason": reason,
    }


def _rco_single_wake_preflight(
    group: Mapping[str, Any],
    *,
    bridge_root: Path | None,
    now_utc: datetime,
    min_age_minutes: float,
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
        and wake_file_age_minutes >= min_age_minutes
    )


def _clear_for_target_activity(
    groups: dict[tuple[str, str], dict[str, Any]],
    *,
    event_agent: str,
) -> None:
    for key, group in list(groups.items()):
        target, _task_id_value = key
        if target != event_agent:
            continue
        del groups[key]


def _clear_for_terminal_task(
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


def _wake_row(
    group: Mapping[str, Any],
    *,
    age_minutes: float,
    latest_wake_age_minutes: float,
    now_utc: datetime,
    bridge_root: Path | None,
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
    requester_list = sorted(str(item) for item in requesters) if isinstance(requesters, set) else []
    wake_send_failed_count = int(group.get("wake_send_failed_count") or 0)
    if classification == "self_pacing_or_silent_by_design":
        reason = (
            "target agent has self-authored bridge activity after the latest "
            "wake_request; treat as delivered and self-paced"
        )
        safe_next_action = (
            "wait for the target self-paced loop or recheck after the "
            "self-liveness window; do not restart solely from repeated wakes"
        )
    else:
        if wake_send_failed_count:
            reason = (
                "operator wake send failed during this unresolved wake window; "
                "target session/tab routing may be unavailable"
            )
            safe_next_action = (
                "repair operator wake routing or TitleMap for the target session; "
                "do not emit more wake_request events until keying succeeds"
            )
        elif wake_file["wake_file_present"] and wake_file["wake_file_fresh_after_last_wake"]:
            reason = "wake file exists but target agent has not emitted bridge activity"
            safe_next_action = (
                "restart or verify the target agent bridge session watcher/poll loop; "
                "do not treat additional wake_request events as delivery proof"
            )
        elif wake_file["wake_file_present"]:
            reason = (
                "wake file is stale relative to latest wake_request; watcher "
                "may not be updating the target wake signal"
            )
            safe_next_action = (
                "restart or verify the target agent bridge session watcher/poll loop; "
                "do not treat additional wake_request events as delivery proof"
            )
        else:
            reason = (
                "no target activity after repeated wake_request; watcher may "
                "be absent or target may not be polling"
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
        "age_minutes": round(age_minutes, 3),
        "latest_wake_age_minutes": round(latest_wake_age_minutes, 3),
        "wake_request_count": group["wake_request_count"],
        "last_status": group["last_status"],
        "last_message": group["last_message"],
        "wake_send_failed_count": wake_send_failed_count,
        "latest_wake_send_failed_ts_utc": str(
            group.get("latest_wake_send_failed_ts_utc") or ""
        ),
        "latest_wake_send_failed_message": str(
            group.get("latest_wake_send_failed_message") or ""
        ),
        "wake_file_checked": bridge_root is not None,
        **wake_file,
        "diagnosis": reason,
        "safe_next_action": safe_next_action,
    }
    if self_liveness:
        row.update(self_liveness)
    return row


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone.utc)


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
        age_minutes = (
            now_utc.astimezone(timezone.utc) - mtime
        ).total_seconds() / 60.0
        return {
            "wake_file_present": True,
            "wake_file_mtime_utc": mtime.isoformat().replace("+00:00", "Z"),
            "wake_file_age_minutes": round(age_minutes, 3),
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


def _normalize_agent_filter(agents: Sequence[str] | None) -> set[str]:
    normalized: set[str] = set()
    for raw in agents or []:
        agent = str(raw or "").strip().lower()
        if not agent:
            continue
        if not AGENT_ID_PATTERN.fullmatch(agent):
            raise WakeDeliveryError(
                {
                    "ok": False,
                    "decision": "wake_delivery_error",
                    "errors": [
                        f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"
                    ],
                }
            )
        normalized.add(agent)
    return normalized


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise WakeDeliveryError(
            {
                "ok": False,
                "decision": "wake_delivery_error",
                "errors": ["now must be an ISO-8601 timestamp"],
            }
        )
    return parsed


def _safe_message(value: object) -> str:
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(message) > 240:
        return f"{message[:237]}..."
    return message


def _print_human(report: Mapping[str, Any]) -> None:
    if not report.get("ok"):
        print("wake delivery report failed", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    count = int(report.get("stalled_count") or 0)
    print(f"wake delivery stalled groups: {count}")
    for row in report.get("stalled_wakes", []):
        if not isinstance(row, Mapping):
            continue
        print(
            "- "
            f"{row.get('target_agent')} {row.get('task_id')} "
            f"repeats={row.get('wake_request_count')} "
            f"age={row.get('age_minutes')}m "
            f"wake_file={row.get('wake_file_present')}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
