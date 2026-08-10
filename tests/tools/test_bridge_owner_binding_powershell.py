from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = REPO_ROOT / ".agent-bridge" / "bin"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
AGENT = "codex-lead-1"
OWNER_NAMES = (
    "AGENT_BRIDGE_OWNER_SESSION_ID",
    "AGENT_BRIDGE_OWNER_TOKEN",
    "AGENT_BRIDGE_OWNER_PID",
    "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
)


pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")


def _owner_env(runtime_root: Path, *, token: str = "a" * 64) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENT_BRIDGE_RUNTIME_ROOT": str(runtime_root),
            "AGENT_BRIDGE_AGENT": AGENT,
            "AGENT_BRIDGE_RUN_ID": "pytest-run",
            "AGENT_BRIDGE_SESSION_ID": "pytest-session",
            "AGENT_BRIDGE_OWNER_SESSION_ID": "pytest-session",
            "AGENT_BRIDGE_OWNER_TOKEN": token,
            "AGENT_BRIDGE_OWNER_PID": str(os.getpid()),
            "AGENT_BRIDGE_OWNER_PROCESS_START_UTC": "2026-08-10T00:00:00Z",
        }
    )
    return env


def _run_ps(
    runtime_root: Path,
    script_name: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_BIN / script_name),
            *args,
        ],
        cwd=REPO_ROOT,
        env=env or _owner_env(runtime_root),
        text=True,
        capture_output=True,
        check=False,
    )


def _run_python_cli(
    runtime_root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/work_queue.py", "--json", *args],
        cwd=REPO_ROOT,
        env=env or _owner_env(runtime_root),
        text=True,
        capture_output=True,
        check=False,
    )


def _claim_path(runtime_root: Path, task_id: str) -> Path:
    return runtime_root / "work_queue" / "claims" / f"{task_id}.json"


def test_powershell_claim_is_accepted_by_python_heartbeat(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    task_id = "ps-to-python-owner-parity"
    env = _owner_env(runtime_root)

    created = _run_ps(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        "-Summary",
        "cross-runtime owner parity",
        env=env,
    )

    assert created.returncode == 0, created.stdout + created.stderr
    claim_path = _claim_path(runtime_root, task_id)
    raw_claim = claim_path.read_text(encoding="utf-8")
    claim = json.loads(raw_claim)
    assert claim["owner_session_id"] == "pytest-session"
    assert claim["owner_token_sha256"] == hashlib.sha256(
        ("a" * 64).encode("utf-8")
    ).hexdigest()
    assert claim["owner_pid"] == os.getpid()
    assert claim["owner_process_start_utc"].startswith("2026-08-10T00:00:00")
    assert claim["session_id"] == "pytest-session"
    assert "a" * 64 not in raw_claim

    heartbeat = _run_python_cli(
        runtime_root,
        "heartbeat",
        "--agent",
        AGENT,
        "--task-id",
        task_id,
        env=env,
    )
    assert heartbeat.returncode == 0, heartbeat.stdout + heartbeat.stderr
    refreshed = json.loads(claim_path.read_text(encoding="utf-8"))
    assert refreshed["owner_session_id"] == claim["owner_session_id"]
    assert refreshed["owner_token_sha256"] == claim["owner_token_sha256"]


def test_powershell_release_requires_the_python_claim_owner(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    task_id = "python-to-ps-owner-parity"
    owner_env = _owner_env(runtime_root)
    created = _run_python_cli(
        runtime_root,
        "claim",
        "--agent",
        AGENT,
        "--task-id",
        task_id,
        "--summary",
        "release authorization parity",
        env=owner_env,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    claim_path = _claim_path(runtime_root, task_id)

    missing_owner_env = owner_env.copy()
    for name in OWNER_NAMES:
        missing_owner_env.pop(name, None)
    missing_owner = _run_ps(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        env=missing_owner_env,
    )
    assert missing_owner.returncode != 0
    assert "claim_owner_context_invalid" in (
        missing_owner.stdout + missing_owner.stderr
    )
    assert claim_path.is_file()

    wrong_owner = _run_ps(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        env=_owner_env(runtime_root, token="b" * 64),
    )
    assert wrong_owner.returncode != 0
    assert "claim_owner_wrong_generation" in (
        wrong_owner.stdout + wrong_owner.stderr
    )
    assert claim_path.is_file()

    released = _run_ps(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        env=owner_env,
    )
    assert released.returncode == 0, released.stdout + released.stderr
    assert not claim_path.exists()
    assert len(list((runtime_root / "work_queue" / "done").glob("*.json"))) == 1


def test_force_and_heartbeat_do_not_cross_owner_generations(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    task_id = "ps-force-heartbeat-owner-parity"
    owner_env = _owner_env(runtime_root)
    created = _run_ps(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        "-Summary",
        "owner generation A",
        env=owner_env,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    claim_path = _claim_path(runtime_root, task_id)
    original = json.loads(claim_path.read_text(encoding="utf-8"))

    wrong_env = _owner_env(runtime_root, token="b" * 64)
    forced = _run_ps(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        "-Summary",
        "owner generation B",
        "-Force",
        env=wrong_env,
    )
    assert forced.returncode != 0
    assert "claim_owner_wrong_generation" in forced.stderr
    assert json.loads(claim_path.read_text(encoding="utf-8")) == original

    foreign_heartbeat = _run_ps(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        AGENT,
        "-Heartbeat",
        "-TaskId",
        "foreign-generation-heartbeat",
        env=wrong_env,
    )
    assert foreign_heartbeat.returncode == 0, (
        foreign_heartbeat.stdout + foreign_heartbeat.stderr
    )
    after_foreign = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_foreign["last_heartbeat_utc"] == original["last_heartbeat_utc"]

    owner_heartbeat = _run_ps(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        AGENT,
        "-Heartbeat",
        "-TaskId",
        "owner-generation-heartbeat",
        env=owner_env,
    )
    assert owner_heartbeat.returncode == 0, (
        owner_heartbeat.stdout + owner_heartbeat.stderr
    )
    after_owner = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_owner["last_heartbeat_utc"] != original["last_heartbeat_utc"]


def test_legacy_tokenless_claim_cannot_be_mutated(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "legacy-tokenless-ps-parity"
    claim_path = _claim_path(runtime_root, task_id)
    legacy_claim = {
        "claimed_at_utc": "2026-08-10T00:00:00Z",
        "last_heartbeat_utc": "2026-08-10T00:00:00Z",
        "agent": AGENT,
        "task_id": task_id,
        "summary": "must drain through stale expiry",
        "mode": "read-only",
        "write_scope": [],
        "lease_seconds": 300,
        "claim_lease_expires_utc": "2099-01-01T00:00:00Z",
    }
    claim_path.write_text(json.dumps(legacy_claim), encoding="utf-8")

    heartbeat = _run_ps(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        AGENT,
        "-Heartbeat",
    )
    release = _run_ps(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
    )
    force = _run_ps(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        AGENT,
        "-TaskId",
        task_id,
        "-Summary",
        "must not adopt legacy claim",
        "-Force",
    )

    assert heartbeat.returncode == 0, heartbeat.stdout + heartbeat.stderr
    assert release.returncode != 0
    assert "claim_owner_legacy_tokenless" in release.stdout + release.stderr
    assert force.returncode != 0
    assert "claim_owner_legacy_tokenless" in force.stderr
    assert json.loads(claim_path.read_text(encoding="utf-8")) == legacy_claim


def test_session_launcher_issues_owner_context(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    base_env = os.environ.copy()
    for name in (*OWNER_NAMES, "AGENT_BRIDGE_AGENT"):
        base_env.pop(name, None)
    command = (
        f"$session = . '{BRIDGE_BIN / 'Start-AgentBridgeSession.ps1'}' "
        f"-Agent '{AGENT}' -RuntimeRoot '{runtime_root}' "
        f"-RepoRoot '{REPO_ROOT}' -PrimaryRepoRoot '{REPO_ROOT}' "
        "-RunId 'pytest-launcher-session' -SkipBridgeRead -SkipLiveness "
        "-SkipGitStatus -SkipWakeWatcher -SkipHeartbeatJob; "
        "[pscustomobject]@{ agent = [string]$env:AGENT_BRIDGE_AGENT; "
        "session = [string]$env:AGENT_BRIDGE_OWNER_SESSION_ID; "
        "token_length = ([string]$env:AGENT_BRIDGE_OWNER_TOKEN).Length; "
        "owner_pid = [string]$env:AGENT_BRIDGE_OWNER_PID; "
        "owner_start = [string]$env:AGENT_BRIDGE_OWNER_PROCESS_START_UTC } | "
        "ConvertTo-Json -Compress"
    )
    launched = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-Command", command],
        cwd=REPO_ROOT,
        env=base_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    identity = json.loads(launched.stdout.strip().splitlines()[-1])
    assert identity["agent"] == AGENT
    assert identity["session"] == "pytest-launcher-session"
    assert identity["token_length"] == 64
    assert int(identity["owner_pid"]) > 0
    assert identity["owner_start"]


def test_consumer_launcher_issues_owner_context(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    base_env = os.environ.copy()
    for name in (
        *OWNER_NAMES,
        "AGENT_BRIDGE_AGENT",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
    ):
        base_env.pop(name, None)
    consumer_agent = "codex-tools-1"
    command = (
        f"$result = . '{BRIDGE_BIN / 'Start-AgentBridgeConsumerLoop.ps1'}' "
        f"-Agent '{consumer_agent}' -RuntimeRoot '{runtime_root}' "
        f"-Worktree '{REPO_ROOT}' -LogDir '{runtime_root / 'logs'}' "
        "-DryRun -MaxIterations 1 -PollSeconds 0 -SkipHeartbeatDuringCodex; "
        "[pscustomobject]@{ agent = [string]$env:AGENT_BRIDGE_AGENT; "
        "session = [string]$env:AGENT_BRIDGE_OWNER_SESSION_ID; "
        "token_length = ([string]$env:AGENT_BRIDGE_OWNER_TOKEN).Length; "
        "owner_pid = [string]$env:AGENT_BRIDGE_OWNER_PID; "
        "report_agent = [string]$result.agent } | ConvertTo-Json -Compress"
    )
    launched = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-Command", command],
        cwd=REPO_ROOT,
        env=base_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    identity = json.loads(launched.stdout.strip().splitlines()[-1])
    assert identity["agent"] == consumer_agent
    assert identity["session"].startswith(f"consumer-{consumer_agent}-")
    assert identity["token_length"] == 64
    assert int(identity["owner_pid"]) > 0
    assert identity["report_agent"] == consumer_agent


def test_stop_session_clears_owner_credentials_for_bound_agent(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    env = _owner_env(runtime_root)
    command = (
        f". '{BRIDGE_BIN / 'Stop-AgentBridgeSession.ps1'}' "
        f"-Agent '{AGENT}' -Confirm:$false | Out-Null; "
        "[pscustomobject]@{ session = [bool]$env:AGENT_BRIDGE_OWNER_SESSION_ID; "
        "token = [bool]$env:AGENT_BRIDGE_OWNER_TOKEN; "
        "pid = [bool]$env:AGENT_BRIDGE_OWNER_PID; "
        "start = [bool]$env:AGENT_BRIDGE_OWNER_PROCESS_START_UTC } | "
        "ConvertTo-Json -Compress"
    )
    stopped = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-Command", command],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    remaining = json.loads(stopped.stdout.strip().splitlines()[-1])
    assert remaining == {"session": False, "token": False, "pid": False, "start": False}
