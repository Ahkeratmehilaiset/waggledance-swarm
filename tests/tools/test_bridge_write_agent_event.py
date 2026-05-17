# SPDX-License-Identifier: BUSL-1.1
"""Tests for .agent-bridge/bin/Write-AgentEvent.ps1."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from waggledance.core.bridge_event_schema import validate_event_line


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for Write-AgentEvent smoke tests")
    return executable


def _run_writer(
    root: Path,
    runtime_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / ".agent-bridge" / "bin" / "Write-AgentEvent.ps1"),
            *args,
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_handoff_without_task_id_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "handoff",
        "-Message",
        "missing task id",
    )

    assert completed.returncode != 0
    assert "handoff requires -TaskId" in completed.stderr
    assert not runtime_root.exists()


def test_ack_message_without_task_id_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Status",
        "acknowledged",
        "-To",
        "claude",
        "-Message",
        "missing task id",
    )

    assert completed.returncode != 0
    assert "ack message requires -TaskId" in completed.stderr
    assert not runtime_root.exists()


def test_task_scoped_event_with_task_id_writes_valid_event(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "handoff",
        "-TaskId",
        "bridge-writer-smoke",
        "-To",
        "claude",
        "-Message",
        "valid handoff",
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["task_id"] == "bridge-writer-smoke"
    validate_event_line(line)
