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


REQUIRES_TASK_ID_CASES = [
    ("claim", ""),
    ("release", ""),
    ("done", ""),
    ("handoff", ""),
    ("blocked", ""),
    ("message", "acknowledged"),
    ("message", "received"),
    ("message", "seen"),
]


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
    return _run_bridge_script(root, runtime_root, "Write-AgentEvent.ps1", *args)


def _run_bridge_script(
    root: Path,
    runtime_root: Path,
    script_name: str,
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
            str(root / ".agent-bridge" / "bin" / script_name),
            *args,
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(("event_type", "status"), REQUIRES_TASK_ID_CASES)
def test_task_id_required_events_fail_before_runtime_write(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    args = [
        "-Agent",
        "codex",
        "-Type",
        event_type,
        "-Message",
        "missing task id",
    ]
    if status:
        args.extend(["-Status", status])

    completed = _run_writer(root, runtime_root, *args)

    assert completed.returncode != 0
    assert "requires non-empty -TaskId" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "note"),
        ("status", ""),
    ],
)
def test_events_without_task_id_requirement_can_write(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    args = [
        "-Agent",
        "codex",
        "-Type",
        event_type,
        "-Message",
        "task id optional",
    ]
    if status:
        args.extend(["-Status", status])

    completed = _run_writer(root, runtime_root, *args)

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["type"] == event_type
    assert event["task_id"] == ""
    validate_event_line(line)


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


def test_regex_agent_id_writes_valid_event_and_outbox(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-2",
        "-Type",
        "message",
        "-To",
        "claude-1",
        "-Message",
        "valid multi-agent bridge message",
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["agent"] == "codex-2"
    assert event["to"] == "claude-1"
    assert (runtime_root / "outbox" / "codex-2").exists()
    validate_event_line(line)


def test_invalid_agent_id_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "Codex-2",
        "-Type",
        "message",
        "-Message",
        "invalid uppercase agent id",
    )

    assert completed.returncode != 0
    assert not runtime_root.exists()


def test_claim_and_release_accept_regex_agent_id(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    claim = _run_bridge_script(
        root,
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex-2",
        "-TaskId",
        "multi-agent-claim-smoke",
        "-Summary",
        "regex agent claim smoke",
    )
    assert claim.returncode == 0, claim.stderr

    release = _run_bridge_script(
        root,
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex-2",
        "-TaskId",
        "multi-agent-claim-smoke",
        "-Status",
        "done",
        "-Message",
        "regex agent release smoke",
    )
    assert release.returncode == 0, release.stderr

    events_path = runtime_root / "shared" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["agent"] for line in lines} == {"codex-2"}
    for line in lines:
        validate_event_line(line)
