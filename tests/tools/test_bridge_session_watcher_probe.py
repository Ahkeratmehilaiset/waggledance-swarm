# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import tools.bridge_session_watcher_probe as probe_module
from tools.bridge_session_watcher_probe import (
    CLAIM_GATES,
    collect_live_process_snapshot,
    main,
    probe_bridge_session_watchers,
    read_active_claim_counts,
    read_process_snapshot,
)


WATCHER_COMMAND = (
    r'"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" '
    r"-NoProfile -ExecutionPolicy Bypass -File "
    r"C:\Python\project2-master\.agent-bridge\bin\Watch-Bridge.ps1 "
    r"-Agent codex-lead-1 -RuntimeRoot C:\Python\project2-master\.agent-bridge"
)
HEARTBEAT_COMMAND = (
    r'"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" '
    r"-NoProfile -ExecutionPolicy Bypass -File "
    r"C:\Python\project2-master\.agent-bridge\bin\Start-BridgeHeartbeat.ps1 "
    r"-Agent codex-lead-1 -RuntimeRoot C:\Python\project2-master\.agent-bridge"
)


def _process(command_line: str, pid: int = 1001) -> dict[str, object]:
    return {
        "ProcessId": pid,
        "ParentProcessId": 900,
        "CreationDate": "2026-06-14T12:00:00Z",
        "CommandLine": command_line,
    }


def test_probe_reports_visible_watcher_and_expected_heartbeat() -> None:
    report = probe_bridge_session_watchers(
        processes=[
            _process(WATCHER_COMMAND, pid=11),
            _process(HEARTBEAT_COMMAND, pid=12),
        ],
        agents=["codex-lead-1"],
        active_claim_counts={"codex-lead-1": 1},
    )

    assert report["decision"] == "bridge_session_watchers_observed"
    assert report["nudge_retry_recommended"] is True
    assert report["missing_watcher_count"] == 0
    assert report["missing_heartbeat_count"] == 0
    row = report["agents"][0]
    assert row["status"] == "watcher_and_expected_heartbeat_present"
    assert row["watcher_process_count"] == 1
    assert row["heartbeat_process_count"] == 1


def test_missing_watcher_blocks_treating_more_nudges_as_delivery() -> None:
    report = probe_bridge_session_watchers(
        processes=[],
        agents=["codex-lead-1"],
        active_claim_counts={"codex-lead-1": 1},
    )

    assert report["decision"] == "bridge_session_watcher_missing"
    assert report["operator_action"] == "restart_or_dot_source_target_bridge_sessions"
    assert report["nudge_retry_recommended"] is False
    row = report["agents"][0]
    assert row["status"] == "missing_watcher_and_expected_heartbeat"
    assert row["missing_watcher"] is True
    assert row["missing_heartbeat"] is True
    assert "do not emit more wake_request" in row["safe_next_action"]


def test_explicit_agent_filter_does_not_add_other_active_claimants() -> None:
    report = probe_bridge_session_watchers(
        processes=[],
        agents=["codex-lead-1"],
        active_claim_counts={"codex-tools-1": 1},
    )

    assert report["target_agents"] == ["codex-lead-1"]
    assert [row["agent"] for row in report["agents"]] == ["codex-lead-1"]


def test_missing_heartbeat_only_matters_when_agent_has_active_claim() -> None:
    report = probe_bridge_session_watchers(
        processes=[_process(WATCHER_COMMAND)],
        agents=["codex-lead-1"],
        active_claim_counts={"codex-lead-1": 1},
    )

    assert report["decision"] == "bridge_session_heartbeat_missing"
    row = report["agents"][0]
    assert row["watcher_present"] is True
    assert row["heartbeat_expected"] is True
    assert row["missing_heartbeat"] is True


def test_heartbeat_is_optional_without_active_claim() -> None:
    report = probe_bridge_session_watchers(
        processes=[_process(WATCHER_COMMAND)],
        agents=["codex-lead-1"],
        active_claim_counts={},
    )

    assert report["decision"] == "bridge_session_watchers_observed"
    row = report["agents"][0]
    assert row["status"] == "watcher_present_heartbeat_optional"
    assert row["heartbeat_expected"] is False
    assert row["missing_heartbeat"] is False


def test_command_lines_are_redacted_from_report() -> None:
    report = probe_bridge_session_watchers(
        processes=[_process(WATCHER_COMMAND)],
        agents=["codex-lead-1"],
        active_claim_counts={},
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\Python" not in encoded
    assert "Watch-Bridge.ps1" not in encoded
    assert "CommandLine" not in encoded
    assert "command_digest" in encoded


def test_job_name_fallback_extracts_agent() -> None:
    report = probe_bridge_session_watchers(
        processes=[
            _process(
                "powershell -Command Start-Job -Name agent-bridge-watcher-codex-lead-1",
                pid=20,
            )
        ],
        agents=["codex-lead-1"],
        active_claim_counts={},
    )

    assert report["agents"][0]["watcher_present"] is True


def test_unknown_helper_without_agent_is_reported_separately() -> None:
    report = probe_bridge_session_watchers(
        processes=[_process("powershell -File Watch-Bridge.ps1", pid=21)],
        agents=["codex-lead-1"],
        active_claim_counts={},
    )

    assert report["missing_watcher_count"] == 1
    assert len(report["unknown_helper_processes"]) == 1
    assert report["unknown_helper_processes"][0]["process_kind"] == "watcher"


def test_all_claim_gates_are_false() -> None:
    report = probe_bridge_session_watchers(
        processes=[_process(WATCHER_COMMAND)],
        agents=["codex-lead-1"],
        active_claim_counts={},
    )

    assert CLAIM_GATES
    for gate in CLAIM_GATES:
        assert report[gate] is False
    assert report["authority_boundary"]["bridge_append_allowed"] is False
    assert report["authority_boundary"]["process_restart_allowed"] is False


def test_read_process_snapshot_accepts_wrapped_processes(tmp_path: Path) -> None:
    path = tmp_path / "processes.json"
    path.write_text(
        json.dumps({"processes": [_process(WATCHER_COMMAND)]}),
        encoding="utf-8",
    )

    assert len(read_process_snapshot(str(path))) == 1


def test_read_process_snapshot_rejects_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('[{"CommandLine": NaN}]', encoding="utf-8")

    assert main(["--processes-json", str(path), "--json"]) == 2


def test_read_active_claim_counts_reads_bridge_claims(tmp_path: Path) -> None:
    claims = tmp_path / "work_queue" / "claims"
    claims.mkdir(parents=True)
    (claims / "a.json").write_text(
        json.dumps({"agent": "codex-lead-1"}),
        encoding="utf-8",
    )
    (claims / "b.json").write_text("{not-json", encoding="utf-8")

    counts, errors = read_active_claim_counts(tmp_path)

    assert counts == {"codex-lead-1": 1}
    assert errors == ["b.json"]


def test_collect_live_process_snapshot_runs_read_only_process_inventory(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([_process(WATCHER_COMMAND)]),
            stderr="",
        )

    monkeypatch.setattr(sys, "platform", "win32")

    processes = collect_live_process_snapshot(runner=runner)

    assert processes == [_process(WATCHER_COMMAND)]
    command, kwargs = calls[0]
    assert command[:3] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert "Get-CimInstance Win32_Process" in command[-1]
    assert "Watch-Bridge.ps1" not in command[-1]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 10
    assert kwargs["check"] is False


def test_cli_returns_warn_code_when_missing_with_fail_flag(tmp_path: Path, capsys) -> None:
    path = tmp_path / "processes.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    rc = main(
        [
            "--processes-json",
            str(path),
            "--bridge-root",
            str(tmp_path),
            "--agent",
            "codex-lead-1",
            "--fail-on-missing",
            "--json",
        ]
    )

    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "bridge_session_watcher_missing"


def test_cli_live_reports_visible_watcher(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        probe_module,
        "collect_live_process_snapshot",
        lambda: [_process(WATCHER_COMMAND)],
    )

    rc = main(
        [
            "--live",
            "--bridge-root",
            str(tmp_path),
            "--agent",
            "codex-lead-1",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "bridge_session_watchers_observed"
