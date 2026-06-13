# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import tools.agent_cli_model_probe as probe_module
from tools.agent_cli_model_probe import (
    CLAIM_GATES,
    collect_live_process_snapshot,
    main,
    probe_claude_code_models,
    read_process_snapshot,
)


BAD_COMMAND = (
    r'C:\WINDOWS\system32\cmd.exe /c ""C:\Users\janik\AppData\Roaming\npm'
    r'\claude.cmd" --dangerously-skip-permissions --model claude-fable-5"'
)
BAD_MYTHOS_COMMAND = (
    r'"C:\Users\janik\AppData\Roaming\npm\\node_modules\@anthropic-ai'
    r'\claude-code\bin\claude.exe" --model claude-mythos-5'
)
GOOD_COMMAND = (
    r'"C:\Users\janik\AppData\Roaming\npm\\node_modules\@anthropic-ai'
    r'\claude-code\bin\claude.exe" --model claude-opus-4-8'
)


def _process(command_line: str, pid: int = 43064) -> dict[str, object]:
    return {
        "ProcessId": pid,
        "ParentProcessId": 29152,
        "CreationDate": "2026-06-13T05:51:22Z",
        "CommandLine": command_line,
    }


def test_probe_flags_unavailable_claude_model_without_leaking_command_line() -> None:
    report = probe_claude_code_models(processes=[_process(BAD_COMMAND)])

    assert report["ok"] is True
    assert report["decision"] == "restart_required_invalid_model"
    assert report["operator_action"] == (
        "restart_affected_claude_code_sessions_with_replacement_model"
    )
    assert report["nudge_retry_recommended"] is False
    assert report["invalid_model_process_count"] == 1
    finding = report["invalid_model_processes"][0]
    assert finding["model"] == "claude-fable-5"
    assert finding["replacement_model"] == "claude-opus-4-8"
    assert finding["restart_required"] is True

    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\Users" not in encoded
    assert "AppData" not in encoded
    assert "CommandLine" not in encoded
    assert "claude.cmd" in encoded


def test_probe_flags_mythos_5_as_unavailable_model() -> None:
    report = probe_claude_code_models(processes=[_process(BAD_MYTHOS_COMMAND)])

    assert report["decision"] == "restart_required_invalid_model"
    assert report["invalid_model_process_count"] == 1
    assert report["invalid_model_processes"][0]["model"] == "claude-mythos-5"
    assert report["invalid_model_processes"][0]["replacement_model"] == "claude-opus-4-8"


def test_probe_accepts_valid_replacement_model() -> None:
    report = probe_claude_code_models(processes=[_process(GOOD_COMMAND)])

    assert report["decision"] == "no_invalid_model_processes_observed"
    assert report["invalid_model_process_count"] == 0
    assert report["observed_model_ids"] == ["claude-opus-4-8"]
    assert report["nudge_retry_recommended"] is True


def test_probe_reports_no_claude_processes_as_start_or_restart_needed() -> None:
    report = probe_claude_code_models(
        processes=[_process("python tools/bridge_next_action.py", pid=10)]
    )

    assert report["decision"] == "no_claude_code_processes_observed"
    assert report["operator_action"] == "start_or_restart_agent_session"
    assert report["nudge_retry_recommended"] is False


def test_probe_surfaces_claude_process_missing_model() -> None:
    report = probe_claude_code_models(
        processes=[_process(r"C:\Users\janik\AppData\Roaming\npm\claude.cmd")]
    )

    assert report["decision"] == "no_invalid_model_processes_observed"
    assert report["missing_model_process_count"] == 1
    assert report["missing_model_processes"][0]["process_kind"] == "claude.cmd"


def test_all_claim_gates_are_false() -> None:
    report = probe_claude_code_models(processes=[_process(BAD_COMMAND)])

    assert CLAIM_GATES
    for gate in CLAIM_GATES:
        assert report[gate] is False
    assert report["authority_boundary"]["bridge_append_allowed"] is False
    assert report["authority_boundary"]["process_restart_allowed"] is False


def test_read_process_snapshot_accepts_wrapped_processes(tmp_path: Path) -> None:
    path = tmp_path / "processes.json"
    path.write_text(json.dumps({"processes": [_process(GOOD_COMMAND)]}), encoding="utf-8")

    assert len(read_process_snapshot(str(path))) == 1


def test_read_process_snapshot_rejects_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('[{"CommandLine": NaN}]', encoding="utf-8")

    assert main(["--processes-json", str(path), "--json"]) == 2


def test_collect_live_process_snapshot_runs_redaction_probe_source(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([_process(GOOD_COMMAND)]),
            stderr="",
        )

    monkeypatch.setattr(sys, "platform", "win32")

    processes = collect_live_process_snapshot(runner=runner)

    assert processes == [_process(GOOD_COMMAND)]
    command, kwargs = calls[0]
    assert command[:3] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert "Get-CimInstance Win32_Process" in command[-1]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 10
    assert kwargs["check"] is False


def test_cli_live_returns_warn_code_for_invalid_model(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        probe_module,
        "collect_live_process_snapshot",
        lambda: [_process(BAD_COMMAND)],
    )

    rc = main(["--live", "--json"])

    assert rc == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "restart_required_invalid_model"
    assert payload["invalid_model_process_count"] == 1
    encoded = json.dumps(payload, sort_keys=True)
    assert "C:\\Users" not in encoded
    assert "CommandLine" not in encoded


def test_cli_returns_warn_code_for_invalid_model(tmp_path: Path, capsys) -> None:
    path = tmp_path / "processes.json"
    path.write_text(json.dumps([_process(BAD_COMMAND)]), encoding="utf-8")

    rc = main(["--processes-json", str(path), "--json"])

    assert rc == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "restart_required_invalid_model"


def test_cli_returns_zero_for_valid_model(tmp_path: Path, capsys) -> None:
    path = tmp_path / "processes.json"
    path.write_text(json.dumps([_process(GOOD_COMMAND)]), encoding="utf-8")

    rc = main(["--processes-json", str(path), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "no_invalid_model_processes_observed"
