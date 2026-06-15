# SPDX-License-Identifier: BUSL-1.1
"""Report stale RCO wake bits without consuming or closing anything.

The wake substrate is file-backed: a local watcher writes ``wake_<agent>`` and
the target session must consume that bit. A present, old RCO wake file with no
later non-heartbeat bridge activity means another wake_request is not delivery
proof; the safe action is to verify or restart the target RCO poll loop.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
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
    read_events,
)
from waggledance.core.work_queue import AGENT_ID_PATTERN, resolve_bridge_root  # noqa: E402


DEFAULT_RCO_AGENTS = ("claude-rco-1", "claude-rco-2")
DEFAULT_MIN_AGE_MINUTES = 12.0
DEFAULT_TAIL = 50000
RCO_AGENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*-rco-\d+$")


class StaleRcoWakeError(ValueError):
    """Raised when the stale wake report cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read bridge wake files and events, then report stale RCO wake bits "
            "that require watcher/poll-loop verification."
        ),
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
    parser.add_argument(
        "--agent",
        action="append",
        default=None,
        help=(
            "RCO agent to check. Repeat to check multiple agents. Defaults to "
            "claude-rco-1 and claude-rco-2."
        ),
    )
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="Only report present wake files at least this old.",
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
        "--fail-on-stale",
        action="store_true",
        help="Return exit code 3 when a stale RCO wake is detected.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    try:
        report = report_stale_rco_wakes(
            events=read_events(events_path, tail=args.tail),
            bridge_root=bridge_root,
            agents=args.agent,
            min_age_minutes=args.min_age_minutes,
            now_utc=_parse_now(args.now),
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "stale_rco_wake_error",
            "errors": exc.report.get("errors", [str(exc)]),
        }
        exit_code = 2
    except StaleRcoWakeError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "stale_rco_wake_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 3 if args.fail_on_stale and report["stale_count"] else 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def report_stale_rco_wakes(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    agents: Sequence[str] | None = None,
    min_age_minutes: float = DEFAULT_MIN_AGE_MINUTES,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only report for stale RCO wake files."""
    if min_age_minutes < 0:
        raise StaleRcoWakeError(
            {
                "ok": False,
                "decision": "stale_rco_wake_error",
                "errors": ["min_age_minutes must be non-negative"],
            }
        )

    checked_agents = _normalize_rco_agents(agents)
    effective_now = now_utc or datetime.now(timezone.utc).astimezone(timezone.utc)

    checked_rows: list[dict[str, Any]] = []
    stale_rows: list[dict[str, Any]] = []
    for agent in checked_agents:
        row = _rco_wake_row(
            agent=agent,
            events=events,
            bridge_root=bridge_root,
            min_age_minutes=min_age_minutes,
            now_utc=effective_now,
        )
        checked_rows.append(row)
        if row["stale"]:
            stale_rows.append(row)

    stale_rows.sort(
        key=lambda row: (
            -float(row.get("wake_age_minutes") or 0.0),
            str(row.get("target_agent") or ""),
        )
    )
    checked_rows.sort(key=lambda row: str(row.get("target_agent") or ""))
    stale_agents = [str(row["target_agent"]) for row in stale_rows]
    return {
        "ok": True,
        "decision": (
            "stale_rco_wake_detected" if stale_rows else "rco_wake_ok"
        ),
        "events_checked": len(events),
        "min_age_minutes": min_age_minutes,
        "agent_filter": checked_agents,
        "stale_count": len(stale_rows),
        "by_agent": {agent: 1 for agent in stale_agents},
        "authority_boundary": {
            "read_only": True,
            "does_not_consume_wake": True,
            "does_not_emit_rco_pass": True,
            "does_not_skip_gate": True,
        },
        "delivery_escalation": {
            "required": bool(stale_rows),
            "target_agents": stale_agents,
            "do_not_emit_additional_wake_requests": bool(stale_rows),
            "safe_next_action": (
                "restart_or_verify_target_rco_bridge_session_watcher"
                if stale_rows
                else ""
            ),
            "operator_action_required": bool(stale_rows),
            "reason": (
                "stale_rco_wake_file_without_later_target_activity"
                if stale_rows
                else ""
            ),
        },
        "checked_rco_wakes": checked_rows,
        "stale_rco_wakes": stale_rows,
    }


def _rco_wake_row(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    min_age_minutes: float,
    now_utc: datetime,
) -> dict[str, Any]:
    wake_path = Path(bridge_root) / f"wake_{agent}"
    last_activity = _last_non_heartbeat_activity(events, agent)
    base = {
        "target_agent": agent,
        "wake_file_checked": True,
        "last_target_activity_ts_utc": _format_utc(
            last_activity["ts"] if last_activity else None
        ),
        "last_target_activity_type": str(last_activity["type"]) if last_activity else "",
        "last_target_activity_status": (
            str(last_activity["status"]) if last_activity else ""
        ),
    }
    if not wake_path.exists():
        return {
            **base,
            "wake_file_present": False,
            "wake_file_mtime_utc": "",
            "wake_age_minutes": None,
            "stale": False,
            "diagnosis": "wake_file_absent",
            "safe_next_action": "",
        }

    mtime = datetime.fromtimestamp(wake_path.stat().st_mtime, tz=timezone.utc)
    wake_age_minutes = max(0.0, (now_utc - mtime).total_seconds() / 60.0)
    later_activity = (
        last_activity is not None
        and isinstance(last_activity["ts"], datetime)
        and last_activity["ts"] > mtime
    )
    stale = wake_age_minutes >= min_age_minutes and not later_activity
    diagnosis = (
        "stale wake file exists but target RCO has no later non-heartbeat bridge activity"
        if stale
        else "wake file present but not stale"
    )
    if later_activity:
        diagnosis = "target RCO emitted non-heartbeat bridge activity after wake file mtime"
    return {
        **base,
        "wake_file_present": True,
        "wake_file_mtime_utc": _format_utc(mtime),
        "wake_age_minutes": round(wake_age_minutes, 3),
        "stale": stale,
        "diagnosis": diagnosis,
        "safe_next_action": (
            "restart or verify the target RCO bridge session watcher/poll loop; "
            "do not treat additional wake_request events as delivery proof"
            if stale
            else ""
        ),
    }


def _last_non_heartbeat_activity(
    events: Sequence[Mapping[str, Any]], agent: str
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for event in events:
        if _event_agent(event) != agent:
            continue
        if _event_type(event) in HEARTBEAT_ONLY_EVENT_TYPES:
            continue
        event_ts = _parse_utc(_event_ts(event))
        if event_ts is None:
            continue
        if latest is None or event_ts > latest["ts"]:
            latest = {
                "ts": event_ts,
                "type": _event_type(event),
                "status": _event_status(event),
            }
    return latest


def _normalize_rco_agents(agents: Sequence[str] | None) -> list[str]:
    raw_agents = list(agents or DEFAULT_RCO_AGENTS)
    normalized: list[str] = []
    for raw in raw_agents:
        agent = str(raw or "").strip().lower()
        if not agent:
            continue
        if not AGENT_ID_PATTERN.fullmatch(agent):
            raise StaleRcoWakeError(
                {
                    "ok": False,
                    "decision": "stale_rco_wake_error",
                    "errors": [
                        f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}"
                    ],
                }
            )
        if not RCO_AGENT_PATTERN.fullmatch(agent):
            raise StaleRcoWakeError(
                {
                    "ok": False,
                    "decision": "stale_rco_wake_error",
                    "errors": [f"agent must be an RCO lane: {agent!r}"],
                }
            )
        if agent not in normalized:
            normalized.append(agent)
    return normalized


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise StaleRcoWakeError(
            {
                "ok": False,
                "decision": "stale_rco_wake_error",
                "errors": ["now must be an ISO-8601 timestamp"],
            }
        )
    return parsed


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _print_human(report: Mapping[str, Any]) -> None:
    if not report.get("ok"):
        print("stale RCO wake report failed", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    count = int(report.get("stale_count") or 0)
    print(f"stale RCO wake files: {count}")
    for row in report.get("stale_rco_wakes", []):
        if not isinstance(row, Mapping):
            continue
        print(
            "- "
            f"{row.get('target_agent')} "
            f"age={row.get('wake_age_minutes')}m "
            f"last_activity={row.get('last_target_activity_ts_utc') or 'none'}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
