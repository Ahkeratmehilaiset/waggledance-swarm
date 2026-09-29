"""Stale-lease routing on both platforms, plus the real writer's platform fence."""

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"

FIXTURE_WRITER = r"""
[CmdletBinding()]
param(
    [string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
    [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson
)
$ErrorActionPreference = 'Stop'
if ($Agent -cne 'system' -or $Type -cne 'release' -or $Status -cne 'stale_lease') {
    throw 'unexpected stale-routing fixture event'
}
$record = [ordered]@{
    agent=$Agent; type=$Type; status=$Status; severity=$Severity;
    task_id=$TaskId; to=$To; message=$Message;
    payload=($PayloadJson | ConvertFrom-Json -ErrorAction Stop)
}
$path = Join-Path (Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'shared') 'events.jsonl'
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::AppendAllText($path, (($record | ConvertTo-Json -Depth 16 -Compress) + "`n"), $encoding)
"""


def _fixture_sweep(tmp_path: Path, *, real_writer: bool) -> Path:
    """Run unmodified sweep/reader code with an isolated event transport."""
    fixture = tmp_path / "fixture"
    code = fixture / ".agent-bridge/bin"
    shutil.copytree(ROOT / ".agent-bridge/bin", code)
    configs = fixture / "configs"
    configs.mkdir()
    shutil.copy2(ROOT / "configs/bridge_identity_registry.json", configs)
    if real_writer:
        # The fixture must not share production's named kernel mutexes.
        prefix = "Local\\WdStaleFixture-" + uuid.uuid4().hex + "-"
        for script in code.glob("*.ps1"):
            source = script.read_text(encoding="utf-8-sig")
            if "Global\\WaggleDanceBridge" in source:
                script.write_text(
                    source.replace("Global\\WaggleDanceBridge", prefix),
                    encoding="utf-8-sig",
                )
    else:
        (code / "Write-AgentEvent.ps1").write_text(FIXTURE_WRITER, encoding="utf-8")
    return code / SWEEP.name


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    ("request_sender", "request_to", "responder_session", "expected_to",
     "identity_complete", "duplicate_request"),
    [
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1,codex-lead-1", True, None),
        ("codex-lead-1", "fable-5", "tools-session-regression",
         "codex-tools-1", True, None),
        ("codex-lead-1", "codex-tools-1", "different-session",
         "codex-tools-1", True, None),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1", False, None),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1", True, "different"),
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1,codex-lead-1", True, "identical"),
        (None, None, None, "codex-tools-1", False, None),
    ],
)
def test_stale_release_addresses_only_verified_owner_and_dispatcher(
    tmp_path: Path, shell: str, request_sender: str | None,
    request_to: str | None, responder_session: str | None, expected_to: str,
    identity_complete: bool, duplicate_request: str | None,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")

    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    shared = runtime / "shared"
    claims.mkdir(parents=True)
    shared.mkdir(parents=True)
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    task = "codex-lead-1/stale-routing-regression"
    claim = {
        "task_id": task,
        "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102",
        "owner_session_id": "tools-session-regression",
        "run_id": "tools-session-regression",
        "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none",
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }
    (claims / "stale-routing-regression.json").write_text(
        json.dumps(claim), encoding="utf-8"
    )
    if request_sender is not None:
        event = {
            "ts_utc": (past - timedelta(minutes=1)).isoformat(),
            "agent": request_sender,
            "type": "wake_request",
            "task_id": task,
            "status": "assigned",
            "to": request_to,
            "expected_responders": {
                "codex-tools-1": {
                    "agent_uuid": claim["agent_uuid"],
                    "session_id": responder_session,
                }
            },
        }
        if identity_complete:
            event.update(
                agent_uuid="d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
                session_id="lead-session-regression",
                request_id="stale-routing-regression",
            )
        rows = [event]
        if duplicate_request:
            rows.append(dict(
                event,
                request_id=("different-request" if duplicate_request == "different"
                            else event["request_id"]),
            ))
        (shared / "events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    for key in ("AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID",
                "AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_ROLE"):
        env.pop(key, None)
    sweep = _fixture_sweep(tmp_path, real_writer=False)
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep),
         "-StaleSeconds", "1", "-Quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert list((runtime / "work_queue/done").glob("*.stale_lease.json"))
    events = [json.loads(line) for line in (shared / "events.jsonl").read_text(
        encoding="utf-8-sig"
    ).splitlines()]
    release = next(e for e in events if e.get("type") == "release")
    assert release["status"] == "stale_lease"
    assert release["to"] == expected_to
    assert release["payload"]["claim_agent"] == claim["agent"]
    assert release["payload"]["claim_agent_uuid"] == claim["agent_uuid"]
    assert release["payload"]["claim_owner_session_id"] == claim["owner_session_id"]


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_real_writer_respects_platform_fence_after_stale_archive(
    tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")

    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    shared = runtime / "shared"
    claims.mkdir(parents=True)
    shared.mkdir(parents=True)
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    task = "codex-lead-1/stale-routing-platform"
    (claims / "stale-routing-platform.json").write_text(json.dumps({
        "task_id": task,
        "agent": "codex-tools-1",
        "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102",
        "owner_session_id": "tools-session-regression",
        "run_id": "tools-session-regression",
        "claimed_at_utc": past.isoformat(),
        "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60,
        "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none",
        "write_scope": ["tests/tools/test_bridge_stale_routing.py"],
    }), encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    for key in ("AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID",
                "AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_ROLE"):
        env.pop(key, None)
    sweep = _fixture_sweep(tmp_path, real_writer=True)
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(sweep),
         "-StaleSeconds", "1", "-Quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert list((runtime / "work_queue/done").glob("*.stale_lease.json"))
    path = shared / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines()] if path.exists() else []
    if os.name == "nt":
        release = next(row for row in rows if row.get("type") == "release")
        assert release["status"] == "stale_lease"
        assert release["to"] == "codex-tools-1"
    else:
        assert not rows
        # Write-Warning is stdout on PowerShell 5.1 and can differ on pwsh.
        assert "Windows file identity" in completed.stdout + completed.stderr
