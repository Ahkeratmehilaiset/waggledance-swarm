# SPDX-License-Identifier: BUSL-1.1
"""Read-only bridge session watcher process probe.

Wake delivery has two local pieces: ``Watch-Bridge.ps1`` must create the
``wake_<agent>`` sentinel, and the target agent session must poll/consume it.
This probe does not wake, restart, enqueue, merge, or write bridge state. It
only inspects a supplied or live process snapshot and reports whether the
expected watcher/heartbeat helpers are visible for each target agent.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    ALLOWED_MODES,
    resolve_bridge_root,
)


DEFAULT_TARGET_AGENTS: tuple[str, ...] = (
    "codex-lead-1",
    "codex-tools-1",
    "claude-rco-1",
    "claude-rco-2",
)
AGENT_ARG_RE = re.compile(
    r"""(?:^|\s)-Agent(?:=|\s+)(?:"([^"]+)"|'([^']+)'|([a-z][a-z0-9_-]{1,32}))""",
    re.IGNORECASE,
)
JOB_AGENT_RE = re.compile(
    r"""agent-bridge-(?:watcher|heartbeat)-([a-z][a-z0-9_-]{1,32})""",
    re.IGNORECASE,
)
REDACTION_SENTINELS = ("PRIVATE" + "_MARKER", "_DO" + "_NOT" + "_LEAK")
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only bridge watcher/heartbeat process probe. Use it when "
            "wake_request events are visible but target agents do not answer."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--processes-json",
        help=(
            "JSON process snapshot path, or '-' for stdin. Accepts either a "
            "list of process objects or an object with a 'processes' list."
        ),
    )
    source.add_argument(
        "--live",
        action="store_true",
        help="Collect a read-only local Win32_Process snapshot in memory.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge for active-claim context. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local."
        ),
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=None,
        help="Agent id to inspect. Repeatable. Defaults to active WD lanes.",
    )
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Return exit code 3 when a watcher or expected heartbeat is missing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        agents = _normalize_agents(args.agent)
        if args.live:
            processes = collect_live_process_snapshot()
        else:
            processes = read_process_snapshot(args.processes_json)
        bridge_root = resolve_bridge_root(args.bridge_root)
        active_claim_counts, claim_read_errors = read_active_claim_counts(bridge_root)
        report = probe_bridge_session_watchers(
            processes=processes,
            agents=agents,
            active_claim_counts=active_claim_counts,
            claim_read_errors=claim_read_errors,
        )
    except ValueError as exc:
        report = _blocked_report(str(exc))

    _emit(report, as_json=args.json)
    if report["ok"] is False:
        return 2
    missing = int(report.get("missing_watcher_count") or 0) + int(
        report.get("missing_heartbeat_count") or 0
    )
    return 3 if args.fail_on_missing and missing else 0


def read_process_snapshot(source: str) -> list[Mapping[str, Any]]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return _decode_process_snapshot_text(text)


def collect_live_process_snapshot(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: int = 10,
) -> list[Mapping[str, Any]]:
    if sys.platform != "win32":
        raise ValueError("live_process_snapshot_requires_windows")
    script = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | "
        "ConvertTo-Json -Compress -Depth 4"
    )
    try:
        result = runner(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("live_process_snapshot_unavailable") from exc
    if result.returncode != 0:
        raise ValueError("live_process_snapshot_command_failed")
    return _decode_process_snapshot_text(result.stdout)


def read_active_claim_counts(bridge_root: Path) -> tuple[dict[str, int], list[str]]:
    records, errors = read_active_claim_records(bridge_root)
    counts: dict[str, int] = {}
    for record in records:
        agent = str(record.get("agent") or "")
        if agent:
            counts[agent] = counts.get(agent, 0) + 1
    return counts, errors


def read_active_claim_records(
    bridge_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    claims_dir = bridge_root / "work_queue" / "claims"
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not claims_dir.exists():
        return records, errors
    for path in sorted(claims_dir.glob("*.json")):
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, ValueError):
            errors.append(path.name)
            continue
        if not isinstance(payload, Mapping):
            errors.append(path.name)
            continue
        agent = str(payload.get("agent") or "").strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            continue
        raw_mode = str(payload.get("mode") or "read-only").strip().lower()
        mode = raw_mode if raw_mode in ALLOWED_MODES else "unknown"
        task_id = str(payload.get("task_id") or "").strip()
        write_scope = _claim_write_scope(payload.get("write_scope"))
        records.append(
            {
                "agent": agent,
                "task_id": task_id,
                "mode": mode,
                "write_scope": write_scope,
                "claimed_at_utc": str(payload.get("claimed_at_utc") or ""),
                "last_heartbeat_utc": str(payload.get("last_heartbeat_utc") or ""),
                "claim_lease_expires_utc": str(
                    payload.get("claim_lease_expires_utc") or ""
                ),
                "malformed": bool(not task_id or mode == "unknown"),
            }
        )
    return records, errors


def _claim_write_scope(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def probe_bridge_session_watchers(
    *,
    processes: Sequence[Mapping[str, Any]],
    agents: Sequence[str] | None = None,
    active_claim_counts: Mapping[str, int] | None = None,
    claim_read_errors: Sequence[str] = (),
) -> dict[str, Any]:
    active_counts = dict(active_claim_counts or {})
    watchers: dict[str, list[dict[str, Any]]] = {}
    heartbeats: dict[str, list[dict[str, Any]]] = {}
    unknown_helpers: list[dict[str, Any]] = []

    for process in processes:
        command_line = _command_line(process)
        helper_kind = _helper_kind(command_line)
        if not helper_kind:
            continue
        agent = _extract_agent(command_line)
        record = _redacted_process_record(
            process,
            command_line=command_line,
            helper_kind=helper_kind,
        )
        if not agent:
            unknown_helpers.append(record)
            continue
        bucket = watchers if helper_kind == "watcher" else heartbeats
        bucket.setdefault(agent, []).append(record)

    target_agents = _target_agents(
        requested=agents,
        active_claim_counts=active_counts,
        watchers=watchers,
        heartbeats=heartbeats,
    )
    rows = [
        _agent_row(
            agent=agent,
            watcher_processes=watchers.get(agent, []),
            heartbeat_processes=heartbeats.get(agent, []),
            active_claim_count=int(active_counts.get(agent, 0)),
        )
        for agent in target_agents
    ]
    by_status: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1

    missing_watcher_count = sum(1 for row in rows if row["missing_watcher"])
    missing_heartbeat_count = sum(1 for row in rows if row["missing_heartbeat"])
    if missing_watcher_count:
        decision = "bridge_session_watcher_missing"
        operator_action = "restart_or_dot_source_target_bridge_sessions"
        nudge_retry_recommended = False
    elif missing_heartbeat_count:
        decision = "bridge_session_heartbeat_missing"
        operator_action = "restart_target_bridge_heartbeat_jobs"
        nudge_retry_recommended = False
    else:
        decision = "bridge_session_watchers_observed"
        operator_action = "continue_wake_delivery_or_cli_liveness_diagnosis"
        nudge_retry_recommended = True

    report: dict[str, Any] = {
        "ok": True,
        "report_version": "wd.bridge_session_watcher_probe.v0",
        "advisory_only": True,
        "read_only": True,
        "command_lines_redacted": True,
        "decision": decision,
        "operator_action": operator_action,
        "nudge_retry_recommended": nudge_retry_recommended,
        "target_agents": target_agents,
        "agent_count": len(rows),
        "missing_watcher_count": missing_watcher_count,
        "missing_heartbeat_count": missing_heartbeat_count,
        "by_status": dict(sorted(by_status.items())),
        "agents": rows,
        "unknown_helper_processes": unknown_helpers,
        "claim_read_error_count": len(claim_read_errors),
        "claim_read_errors": sorted(str(item) for item in claim_read_errors),
        "wake_delivery_interpretation": (
            "A missing watcher means additional wake_request events are not "
            "delivery proof. A visible watcher plus an unconsumed wake file "
            "points at the target agent poll loop or CLI/session health."
        ),
        "authority_boundary": _authority_boundary(),
    }
    for gate in CLAIM_GATES:
        report[gate] = False
    return report


def _agent_row(
    *,
    agent: str,
    watcher_processes: Sequence[Mapping[str, Any]],
    heartbeat_processes: Sequence[Mapping[str, Any]],
    active_claim_count: int,
) -> dict[str, Any]:
    watcher_present = bool(watcher_processes)
    heartbeat_present = bool(heartbeat_processes)
    heartbeat_expected = active_claim_count > 0
    missing_watcher = not watcher_present
    missing_heartbeat = heartbeat_expected and not heartbeat_present
    if missing_watcher and missing_heartbeat:
        status = "missing_watcher_and_expected_heartbeat"
        safe_next_action = (
            "restart or dot-source Start-AgentBridgeSession.ps1 for this "
            "agent; do not emit more wake_request events as delivery proof"
        )
    elif missing_watcher:
        status = "missing_watcher"
        safe_next_action = (
            "restart or verify the target bridge watcher before retrying "
            "wake_request nudges"
        )
    elif missing_heartbeat:
        status = "missing_expected_heartbeat"
        safe_next_action = (
            "restart the bridge heartbeat job for the active claimant or "
            "refresh the session bootstrap"
        )
    elif not heartbeat_expected and not heartbeat_present:
        status = "watcher_present_heartbeat_optional"
        safe_next_action = (
            "watcher is visible; continue target poll-loop and CLI liveness "
            "diagnosis if wake files remain unconsumed"
        )
    else:
        status = "watcher_and_expected_heartbeat_present"
        safe_next_action = (
            "bridge helper processes are visible; continue target poll-loop "
            "and CLI liveness diagnosis"
        )
    return {
        "agent": agent,
        "status": status,
        "watcher_present": watcher_present,
        "watcher_process_count": len(watcher_processes),
        "heartbeat_present": heartbeat_present,
        "heartbeat_process_count": len(heartbeat_processes),
        "active_claim_count": active_claim_count,
        "heartbeat_expected": heartbeat_expected,
        "missing_watcher": missing_watcher,
        "missing_heartbeat": missing_heartbeat,
        "safe_next_action": safe_next_action,
        "watcher_processes": list(watcher_processes),
        "heartbeat_processes": list(heartbeat_processes),
    }


def _target_agents(
    *,
    requested: Sequence[str],
    active_claim_counts: Mapping[str, int],
    watchers: Mapping[str, Sequence[Mapping[str, Any]]],
    heartbeats: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[str]:
    if requested:
        return sorted(set(requested))
    agents = set(DEFAULT_TARGET_AGENTS)
    agents.update(active_claim_counts)
    agents.update(watchers)
    agents.update(heartbeats)
    return sorted(agents)


def _normalize_agents(agents: Sequence[str] | None) -> tuple[str, ...]:
    if not agents:
        return ()
    normalized: list[str] = []
    for raw in agents:
        agent = str(raw or "").strip().lower()
        if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
            raise ValueError(f"agent must match {AGENT_ID_PATTERN.pattern}: {agent!r}")
        normalized.append(agent)
    return tuple(dict.fromkeys(normalized))


def _decode_process_snapshot_text(text: str) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("process_snapshot_json_error") from exc
    if isinstance(payload, Mapping):
        if "processes" in payload:
            payload = payload["processes"]
        else:
            payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("process_snapshot_not_list")
    _assert_finite(payload, path="processes")
    _assert_no_redaction_sentinels(payload)
    processes: list[Mapping[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"process_not_object:{index}")
        processes.append(item)
    return processes


def _helper_kind(command_line: str) -> str:
    text = command_line.lower()
    if "watch-bridge.ps1" in text or "agent-bridge-watcher-" in text:
        return "watcher"
    if "start-bridgeheartbeat.ps1" in text or "agent-bridge-heartbeat-" in text:
        return "heartbeat"
    return ""


def _extract_agent(command_line: str) -> str:
    match = AGENT_ARG_RE.search(command_line)
    if match:
        return next(group for group in match.groups() if group).lower()
    match = JOB_AGENT_RE.search(command_line)
    if match:
        return match.group(1).lower()
    return ""


def _redacted_process_record(
    process: Mapping[str, Any], *, command_line: str, helper_kind: str
) -> dict[str, Any]:
    return {
        "pid": _string(
            process.get("ProcessId")
            or process.get("processId")
            or process.get("pid")
            or process.get("PID")
        ),
        "parent_pid": _string(
            process.get("ParentProcessId")
            or process.get("parentProcessId")
            or process.get("ppid")
        ),
        "created_at": _string(
            process.get("CreationDate")
            or process.get("creationDate")
            or process.get("created_at")
        ),
        "process_kind": helper_kind,
        "command_digest": sha256(command_line.encode("utf-8")).hexdigest(),
    }


def _command_line(process: Mapping[str, Any]) -> str:
    return _string(
        process.get("CommandLine")
        or process.get("commandLine")
        or process.get("command_line")
        or process.get("cmd")
    )


def _blocked_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "report_version": "wd.bridge_session_watcher_probe.v0",
        "advisory_only": True,
        "read_only": True,
        "command_lines_redacted": True,
        "decision": "input_refused",
        "blockers": [reason],
        "operator_action": "fix_process_snapshot_input",
        "nudge_retry_recommended": False,
        "target_agents": [],
        "agent_count": 0,
        "missing_watcher_count": 0,
        "missing_heartbeat_count": 0,
        "by_status": {},
        "agents": [],
        "unknown_helper_processes": [],
        "claim_read_error_count": 0,
        "claim_read_errors": [],
        "authority_boundary": _authority_boundary(),
    }
    for gate in CLAIM_GATES:
        report[gate] = False
    return report


def _authority_boundary() -> dict[str, bool]:
    return {
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "runtime_activation_allowed": False,
        "process_termination_allowed": False,
        "process_restart_allowed": False,
        "merge_allowed": False,
        "network_required": False,
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


def _assert_finite(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
        raise ValueError(f"non_finite_json:{path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, path=f"{path}[{index}]")


def _assert_no_redaction_sentinels(value: Any) -> None:
    if isinstance(value, str):
        for marker in REDACTION_SENTINELS:
            if marker.lower() in value.lower():
                raise ValueError("redaction_sentinel_present")
    elif isinstance(value, Mapping):
        for item in value.values():
            _assert_no_redaction_sentinels(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_redaction_sentinels(item)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _emit(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, sort_keys=True))
        return
    print(f"bridge session watcher probe: {report['decision']}")
    print(f"  agents checked: {report.get('agent_count', 0)}")
    print(f"  missing watchers: {report.get('missing_watcher_count', 0)}")
    print(f"  missing expected heartbeats: {report.get('missing_heartbeat_count', 0)}")
    print(f"  operator action: {report.get('operator_action', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
