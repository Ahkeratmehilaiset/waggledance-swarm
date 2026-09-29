"""Stale-lease release routing against the real PowerShell event writer."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    ("request_sender", "request_to", "responder_session", "expected_to"),
    [
        ("codex-lead-1", "codex-tools-1", "tools-session-regression",
         "codex-tools-1,codex-lead-1"),
        ("codex-lead-1", "fable-5", "tools-session-regression",
         "codex-tools-1"),
        ("codex-lead-1", "codex-tools-1", "different-session",
         "codex-tools-1"),
        (None, None, None, "codex-tools-1"),
    ],
)
def test_stale_release_addresses_only_verified_owner_and_dispatcher(
    tmp_path: Path, shell: str, request_sender: str | None,
    request_to: str | None, responder_session: str | None, expected_to: str,
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
            "agent_uuid": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
            "session_id": "lead-session-regression",
            "type": "wake_request",
            "task_id": task,
            "status": "assigned",
            "to": request_to,
            "request_id": "stale-routing-regression",
            "expected_responders": {
                "codex-tools-1": {
                    "agent_uuid": claim["agent_uuid"],
                    "session_id": responder_session,
                }
            },
        }
        (shared / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    for key in ("AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID",
                "AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_ROLE"):
        env.pop(key, None)
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(SWEEP),
         "-StaleSeconds", "1", "-Quiet"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = [json.loads(line) for line in (shared / "events.jsonl").read_text(
        encoding="utf-8-sig"
    ).splitlines()]
    release = next(e for e in events if e.get("type") == "release")
    assert release["status"] == "stale_lease"
    assert release["to"] == expected_to
    assert release["payload"]["claim_agent"] == claim["agent"]
    assert release["payload"]["claim_agent_uuid"] == claim["agent_uuid"]
    assert release["payload"]["claim_owner_session_id"] == claim["owner_session_id"]
