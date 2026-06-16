#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Read-only report for bridge nudge attempts that do not restore activity.

``Watch-AgentsBridgeNudge.ps1`` can successfully send repeated window nudges
while the target Claude/Codex session still does not emit a later bridge event.
This tool separates those cases from ordinary wake-file delivery:

* actual ``nudged`` attempts that did not produce target-origin bridge activity;
* ``no_window`` target-selection failures;
* operator-active guard skips that explain why only bridge wake files were sent.

The report is advisory only. It never sends keys, writes bridge events, restarts
processes, claims work, or changes gates.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402


NON_ACTIVITY_EVENT_TYPES = {"heartbeat", "liveness"}
ACTUAL_NUDGE_ACTIONS = {"nudged"}
WINDOW_TARGET_FAILURE_ACTIONS = {"no_window"}
OPERATOR_GUARD_ACTIONS = {"skip_operator_active"}
COOLDOWN_ACTIONS = {"skip_cooldown"}
DEFAULT_MIN_ACTUAL_NUDGES = 2
DEFAULT_MIN_ELAPSED_MINUTES = 5.0
DEFAULT_CSV_MAX_AGE_HOURS = 24.0


@dataclass(frozen=True)
class NudgeRow:
    ts_utc: datetime
    agent: str
    classification: str
    action: str
    open_incoming: int | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report whether bridge nudge attempts restore target activity.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--nudge-csv",
        type=Path,
        action="append",
        default=[],
        help=(
            "Watch-AgentsBridgeNudge CSV path. Repeatable. If omitted, recent "
            "watch-agents-bridgenudge-*.csv files are loaded from --nudge-log-dir."
        ),
    )
    parser.add_argument(
        "--nudge-log-dir",
        type=Path,
        default=Path(os.environ.get("WAGGLE_NUDGE_LOG_DIR", "C:/Python")),
        help="Directory used for default watch-agents-bridgenudge-*.csv discovery.",
    )
    parser.add_argument(
        "--csv-max-age-hours",
        type=float,
        default=DEFAULT_CSV_MAX_AGE_HOURS,
        help="When discovering CSVs, only include files modified within this window.",
    )
    parser.add_argument("--agent", action="append", default=[], help="Agent to inspect.")
    parser.add_argument(
        "--min-actual-nudges",
        type=int,
        default=DEFAULT_MIN_ACTUAL_NUDGES,
        help="Actual sent nudges after the last target activity required for an issue.",
    )
    parser.add_argument(
        "--min-elapsed-minutes",
        type=float,
        default=DEFAULT_MIN_ELAPSED_MINUTES,
        help="First unresolved actual nudge must be at least this old before it is ineffective.",
    )
    parser.add_argument("--now", default=None, help="UTC timestamp override.")
    parser.add_argument("--fail-on-issue", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    nudge_csvs = list(args.nudge_csv)
    if not nudge_csvs:
        nudge_csvs = default_nudge_csv_paths(
            args.nudge_log_dir,
            now_utc=now_utc,
            max_age_hours=args.csv_max_age_hours,
        )

    try:
        events = read_events(events_path)
        nudge_rows = read_nudge_csvs(nudge_csvs)
        report = report_nudge_effectiveness(
            events=events,
            nudge_rows=nudge_rows,
            now_utc=now_utc,
            agents=args.agent or None,
            min_actual_nudges=args.min_actual_nudges,
            min_elapsed_minutes=args.min_elapsed_minutes,
            nudge_csvs=nudge_csvs,
            events_path=events_path,
        )
    except (OSError, ValueError) as exc:
        report = {
            "ok": False,
            "decision": "nudge_effectiveness_error",
            "exit_code": 2,
            "reason": str(exc),
            "authority_boundary": authority_boundary(),
        }

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        for row in report.get("agents", []):
            print(
                "{agent}: {status} actual_nudges={actual_nudge_count} "
                "post_nudge_activity={post_nudge_activity_observed}".format(**row)
            )
        if report.get("safe_next_action"):
            print(f"safe_next_action: {report['safe_next_action']}")

    if report.get("exit_code"):
        return int(report["exit_code"])
    if args.fail_on_issue and report.get("issue_agent_count"):
        return 3
    return 0


def authority_boundary() -> dict[str, bool]:
    return {
        "read_only": True,
        "bridge_append_allowed": False,
        "keyboard_input_allowed": False,
        "process_restart_allowed": False,
        "process_termination_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "merge_allowed": False,
        "gate_skip_allowed": False,
    }


def default_nudge_csv_paths(
    log_dir: Path,
    *,
    now_utc: datetime,
    max_age_hours: float,
) -> list[Path]:
    if max_age_hours < 0:
        raise ValueError("csv-max-age-hours must be non-negative")
    if not log_dir.exists():
        return []
    cutoff_seconds = max_age_hours * 3600.0
    rows: list[tuple[float, Path]] = []
    for path in log_dir.glob("watch-agents-bridgenudge-*.csv"):
        try:
            age = now_utc.timestamp() - path.stat().st_mtime
        except OSError:
            continue
        if age < 0:
            age = 0.0
        if age <= cutoff_seconds:
            rows.append((path.stat().st_mtime, path))
    return [path for _, path in sorted(rows)]


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"events file not found: {path}")
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


def read_nudge_csvs(paths: Sequence[Path]) -> list[NudgeRow]:
    rows: list[NudgeRow] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"nudge csv not found: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"ts", "agent", "classification", "action"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError(f"nudge csv missing required columns: {path}")
            for raw in reader:
                try:
                    ts_utc = _parse_utc(str(raw.get("ts") or ""))
                except ValueError:
                    continue
                agent = str(raw.get("agent") or "").strip()
                action = str(raw.get("action") or "").strip()
                if not agent or not action:
                    continue
                rows.append(
                    NudgeRow(
                        ts_utc=ts_utc,
                        agent=agent,
                        classification=str(raw.get("classification") or "").strip(),
                        action=action,
                        open_incoming=_int_or_none(raw.get("open_incoming")),
                    )
                )
    return sorted(rows, key=lambda row: row.ts_utc)


def report_nudge_effectiveness(
    *,
    events: Sequence[Mapping[str, Any]],
    nudge_rows: Sequence[NudgeRow],
    now_utc: datetime,
    agents: Sequence[str] | None = None,
    min_actual_nudges: int = DEFAULT_MIN_ACTUAL_NUDGES,
    min_elapsed_minutes: float = DEFAULT_MIN_ELAPSED_MINUTES,
    nudge_csvs: Sequence[Path] | None = None,
    events_path: Path | None = None,
) -> dict[str, Any]:
    if min_actual_nudges < 1:
        raise ValueError("min_actual_nudges must be positive")
    if min_elapsed_minutes < 0:
        raise ValueError("min_elapsed_minutes must be non-negative")

    agent_filter = {agent for agent in (agents or []) if agent}
    candidate_agents = set(agent_filter)
    candidate_agents.update(row.agent for row in nudge_rows)
    if agent_filter:
        candidate_agents &= agent_filter

    activity_by_agent = _latest_activity_by_agent(events)
    rows_by_agent = _nudge_rows_by_agent(nudge_rows)
    agent_reports = [
        _agent_report(
            agent=agent,
            rows=rows_by_agent.get(agent, []),
            last_activity_ts=activity_by_agent.get(agent),
            now_utc=now_utc,
            min_actual_nudges=min_actual_nudges,
            min_elapsed_minutes=min_elapsed_minutes,
        )
        for agent in sorted(candidate_agents)
    ]

    issue_rows = [row for row in agent_reports if row["issue"]]
    ineffective = [
        row for row in issue_rows if row["issue_kind"] == "ineffective_nudge_sequence"
    ]
    window = [row for row in issue_rows if row["issue_kind"] == "window_targeting_issue"]
    if ineffective:
        decision = "nudge_effectiveness_issue"
        safe_next_action = (
            "restart_or_verify_target_session_poll_loop; require target-origin "
            "bridge activity after restart"
        )
    elif window:
        decision = "nudge_window_targeting_issue"
        safe_next_action = "fix_window_title_map_or_retitle_target_session"
    elif agent_reports:
        decision = "nudge_effectiveness_ok"
        safe_next_action = ""
    else:
        decision = "nudge_effectiveness_no_data"
        safe_next_action = ""

    return {
        "ok": True,
        "decision": decision,
        "issue_agent_count": len(issue_rows),
        "issue_agents": [str(row["agent"]) for row in issue_rows],
        "agent_count": len(agent_reports),
        "agents": agent_reports,
        "safe_next_action": safe_next_action,
        "min_actual_nudges": min_actual_nudges,
        "min_elapsed_minutes": min_elapsed_minutes,
        "events_checked": len(events),
        "nudge_rows_checked": len(nudge_rows),
        "nudge_csvs": [str(path) for path in (nudge_csvs or [])],
        "events_path": str(events_path) if events_path else "",
        "authority_boundary": authority_boundary(),
        "report_version": "wd.bridge_nudge_effectiveness_report.v0",
    }


def _agent_report(
    *,
    agent: str,
    rows: Sequence[NudgeRow],
    last_activity_ts: datetime | None,
    now_utc: datetime,
    min_actual_nudges: int,
    min_elapsed_minutes: float,
) -> dict[str, Any]:
    unresolved = [
        row for row in rows if last_activity_ts is None or row.ts_utc > last_activity_ts
    ]
    actual = [row for row in unresolved if row.action in ACTUAL_NUDGE_ACTIONS]
    no_window = [row for row in unresolved if row.action in WINDOW_TARGET_FAILURE_ACTIONS]
    operator_guard = [row for row in unresolved if row.action in OPERATOR_GUARD_ACTIONS]
    cooldown = [row for row in unresolved if row.action in COOLDOWN_ACTIONS]

    first_actual = actual[0].ts_utc if actual else None
    latest_actual = actual[-1].ts_utc if actual else None
    latest_row = unresolved[-1].ts_utc if unresolved else None
    post_nudge_activity = bool(
        latest_actual and last_activity_ts and last_activity_ts > latest_actual
    )
    first_actual_age = (
        round((now_utc - first_actual).total_seconds() / 60.0, 3)
        if first_actual
        else None
    )
    latest_actual_age = (
        round((now_utc - latest_actual).total_seconds() / 60.0, 3)
        if latest_actual
        else None
    )

    issue = False
    issue_kind = ""
    status = "ok"
    safe_next_action = ""
    if (
        len(actual) >= min_actual_nudges
        and not post_nudge_activity
        and first_actual_age is not None
        and first_actual_age >= min_elapsed_minutes
    ):
        issue = True
        issue_kind = "ineffective_nudge_sequence"
        status = "actual_nudges_without_target_activity"
        safe_next_action = "restart_or_verify_target_session_poll_loop"
    elif no_window:
        issue = True
        issue_kind = "window_targeting_issue"
        status = "window_targeting_failed"
        safe_next_action = "fix_window_title_map_or_retitle_target_session"
    elif operator_guard and not actual:
        status = "operator_guard_limited_nudges"
        safe_next_action = "wait_for_operator_idle_or_use_explicit_restart_path"

    return {
        "agent": agent,
        "status": status,
        "issue": issue,
        "issue_kind": issue_kind,
        "safe_next_action": safe_next_action,
        "actual_nudge_count": len(actual),
        "no_window_count": len(no_window),
        "operator_guard_skip_count": len(operator_guard),
        "cooldown_skip_count": len(cooldown),
        "unresolved_nudge_row_count": len(unresolved),
        "last_target_activity_ts_utc": _format_ts(last_activity_ts),
        "first_actual_nudge_ts_utc": _format_ts(first_actual),
        "latest_actual_nudge_ts_utc": _format_ts(latest_actual),
        "latest_nudge_row_ts_utc": _format_ts(latest_row),
        "first_actual_nudge_age_minutes": first_actual_age,
        "latest_actual_nudge_age_minutes": latest_actual_age,
        "post_nudge_activity_observed": post_nudge_activity,
    }


def _latest_activity_by_agent(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for event in events:
        agent = str(event.get("agent") or "").strip()
        if not agent:
            continue
        event_type = str(event.get("type") or "").strip().lower()
        if event_type in NON_ACTIVITY_EVENT_TYPES:
            continue
        try:
            ts_utc = _parse_utc(str(event.get("ts_utc") or ""))
        except ValueError:
            continue
        if agent not in latest or ts_utc > latest[agent]:
            latest[agent] = ts_utc
    return latest


def _nudge_rows_by_agent(rows: Sequence[NudgeRow]) -> dict[str, list[NudgeRow]]:
    grouped: dict[str, list[NudgeRow]] = {}
    for row in rows:
        grouped.setdefault(row.agent, []).append(row)
    return grouped


def _parse_utc(value: str) -> datetime:
    if not value:
        raise ValueError("empty UTC timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_ts(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
