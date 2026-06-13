# SPDX-License-Identifier: BUSL-1.1
"""Diagnose Claude Code agent stalls caused by invalid pinned model ids.

This probe is intentionally read-only and path-free. It consumes a process
snapshot supplied by the operator or a watcher, detects Claude Code processes
that were launched with known-unavailable models, and emits an advisory report.

It does not restart sessions, kill processes, append bridge events, claim work,
enqueue scheduler items, or grant any merge/runtime authority. The purpose is
to distinguish "bridge nudge did not work" from "the agent CLI is wedged before
it can answer the bridge".
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


DEFAULT_UNAVAILABLE_MODELS: tuple[str, ...] = (
    "claude-fable-5",
    "claude-fable-5[1m]",
    "claude-mythos-5",
    "claude-mythos-5[1m]",
)
DEFAULT_REPLACEMENT_MODEL = "claude-opus-4-8"
MODEL_ARG_RE = re.compile(
    r"""(?:^|\s)--model(?:=|\s+)(?:"([^"]+)"|'([^']+)'|([^\s]+))""",
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
            "Read-only Claude Code process/model probe. Detects known "
            "unavailable --model pins that wedge agent sessions before bridge "
            "nudge delivery can matter."
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
        help=(
            "Collect a read-only local Win32_Process snapshot in memory before "
            "running the same redacted model-pin probe."
        ),
    )
    parser.add_argument(
        "--unavailable-model",
        action="append",
        default=None,
        help=(
            "Known-unavailable model id. Repeatable. Defaults to Fable 5 and "
            "Mythos 5 aliases known to wedge Claude Code sessions."
        ),
    )
    parser.add_argument(
        "--replacement-model",
        default=DEFAULT_REPLACEMENT_MODEL,
        help=f"Recommended restart model (default: {DEFAULT_REPLACEMENT_MODEL}).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    unavailable = tuple(args.unavailable_model or DEFAULT_UNAVAILABLE_MODELS)
    try:
        if args.live:
            processes = collect_live_process_snapshot()
        else:
            processes = read_process_snapshot(args.processes_json)
        report = probe_claude_code_models(
            processes=processes,
            unavailable_models=unavailable,
            replacement_model=args.replacement_model,
        )
    except ValueError as exc:
        report = _blocked_report(str(exc), replacement_model=args.replacement_model)

    _emit(report, as_json=args.json)
    if report["ok"] is False:
        return 2
    return 4 if report["invalid_model_process_count"] else 0


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


def _decode_process_snapshot_text(text: str) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
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


def probe_claude_code_models(
    *,
    processes: Sequence[Mapping[str, Any]],
    unavailable_models: Sequence[str] = DEFAULT_UNAVAILABLE_MODELS,
    replacement_model: str = DEFAULT_REPLACEMENT_MODEL,
) -> dict[str, Any]:
    unavailable = {model for model in unavailable_models if model}
    claude_processes: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    missing_model: list[dict[str, Any]] = []
    observed_models: set[str] = set()

    for process in processes:
        command_line = _command_line(process)
        if not _looks_like_claude_code_process(command_line):
            continue
        model = _extract_model_arg(command_line)
        item = _redacted_process_record(process, command_line=command_line, model=model)
        claude_processes.append(item)
        if model:
            observed_models.add(model)
        else:
            missing_model.append(item)
            continue
        if model in unavailable:
            invalid.append(
                {
                    **item,
                    "reason": "unavailable_model",
                    "restart_required": True,
                    "replacement_model": replacement_model,
                }
            )

    if invalid:
        decision = "restart_required_invalid_model"
        nudge_retry_recommended = False
        operator_action = (
            "restart_affected_claude_code_sessions_with_replacement_model"
        )
    elif claude_processes:
        decision = "no_invalid_model_processes_observed"
        nudge_retry_recommended = True
        operator_action = "continue_bridge_liveness_diagnosis"
    else:
        decision = "no_claude_code_processes_observed"
        nudge_retry_recommended = False
        operator_action = "start_or_restart_agent_session"

    report: dict[str, Any] = {
        "ok": True,
        "report_version": "wd.agent_cli_model_probe.v0",
        "advisory_only": True,
        "read_only": True,
        "path_free": True,
        "command_lines_redacted": True,
        "decision": decision,
        "operator_action": operator_action,
        "nudge_retry_recommended": nudge_retry_recommended,
        "replacement_model": replacement_model,
        "unavailable_models": sorted(unavailable),
        "observed_model_ids": sorted(observed_models),
        "claude_code_process_count": len(claude_processes),
        "invalid_model_process_count": len(invalid),
        "missing_model_process_count": len(missing_model),
        "invalid_model_processes": invalid,
        "missing_model_processes": missing_model,
        "authority_boundary": _authority_boundary(),
    }
    for gate in CLAIM_GATES:
        report[gate] = False
    return report


def _blocked_report(reason: str, *, replacement_model: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "report_version": "wd.agent_cli_model_probe.v0",
        "advisory_only": True,
        "read_only": True,
        "path_free": True,
        "command_lines_redacted": True,
        "decision": "input_refused",
        "blockers": [reason],
        "operator_action": "fix_process_snapshot_input",
        "nudge_retry_recommended": False,
        "replacement_model": replacement_model,
        "unavailable_models": list(DEFAULT_UNAVAILABLE_MODELS),
        "observed_model_ids": [],
        "claude_code_process_count": 0,
        "invalid_model_process_count": 0,
        "missing_model_process_count": 0,
        "invalid_model_processes": [],
        "missing_model_processes": [],
        "authority_boundary": _authority_boundary(),
    }
    for gate in CLAIM_GATES:
        report[gate] = False
    return report


def _extract_model_arg(command_line: str) -> str:
    match = MODEL_ARG_RE.search(command_line)
    if not match:
        return ""
    return next(group for group in match.groups() if group).strip("\"'")


def _looks_like_claude_code_process(command_line: str) -> bool:
    text = command_line.lower()
    return (
        "claude.cmd" in text
        or "claude.exe" in text
        or "@anthropic-ai" in text and "claude-code" in text
    )


def _redacted_process_record(
    process: Mapping[str, Any], *, command_line: str, model: str
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
        "process_kind": _process_kind(command_line),
        "model": model,
        "command_digest": sha256(command_line.encode("utf-8")).hexdigest(),
    }


def _process_kind(command_line: str) -> str:
    text = command_line.lower()
    if "claude.cmd" in text:
        return "claude.cmd"
    if "claude.exe" in text:
        return "claude.exe"
    if "claude-code" in text:
        return "claude-code"
    return "claude"


def _command_line(process: Mapping[str, Any]) -> str:
    return _string(
        process.get("CommandLine")
        or process.get("commandLine")
        or process.get("command_line")
        or process.get("cmd")
    )


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
    print(f"agent CLI model probe: {report['decision']}")
    print(f"  Claude Code processes: {report['claude_code_process_count']}")
    print(f"  invalid model processes: {report['invalid_model_process_count']}")
    print(f"  operator action: {report['operator_action']}")
    if report.get("observed_model_ids"):
        print("  observed models: " + ", ".join(report["observed_model_ids"]))


if __name__ == "__main__":
    raise SystemExit(main())
