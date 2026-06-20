from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELEASE_AGENT_TASK = ROOT / ".agent-bridge" / "bin" / "Release-AgentTask.ps1"


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for Release-AgentTask smoke tests")
    return executable


def _run_release(
    runtime_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RELEASE_AGENT_TASK),
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _write_claim(
    path: Path,
    *,
    agent: str,
    task_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "agent": agent,
                "task_id": task_id,
                "summary": "release regression claim",
                "mode": "write",
                "write_scope": ["tools/example.py"],
                "claimed_at_utc": "2026-06-20T00:00:00Z",
                "last_heartbeat_utc": "2026-06-20T00:00:00Z",
                "lease_seconds": 300,
                "claim_lease_expires_utc": "2026-06-20T00:05:00Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_release_archives_claim_by_json_task_id_when_filename_has_hash(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    task_id = "codex-tools-1/example-release-task"
    hashed_claim = (
        runtime_root
        / "work_queue"
        / "claims"
        / "codex-tools-1_example-release-task-deadbeefcafe.json"
    )
    _write_claim(hashed_claim, agent="codex-tools-1", task_id=task_id)

    completed = _run_release(
        runtime_root,
        "-Agent",
        "codex-tools-1",
        "-TaskId",
        task_id,
        "-Status",
        "done",
        "-Message",
        "green",
    )

    assert completed.returncode == 0, completed.stderr
    assert not hashed_claim.exists()
    done_files = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(done_files) == 1
    done_payload = json.loads(done_files[0].read_text(encoding="utf-8-sig"))
    assert done_payload["task_id"] == task_id
    assert done_payload["release_status"] == "done"
    assert done_payload["release_message"] == "green"

    events = [
        json.loads(line)
        for line in (runtime_root / "shared" / "events.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
    ]
    assert events[-1]["agent"] == "codex-tools-1"
    assert events[-1]["type"] == "done"
    assert events[-1]["task_id"] == task_id
    assert events[-1]["status"] == "done"
