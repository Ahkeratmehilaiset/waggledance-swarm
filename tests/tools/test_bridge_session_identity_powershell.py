# SPDX-License-Identifier: BUSL-1.1
"""PowerShell bridge session-agent identity boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from waggledance.core.work_queue import (
    WorkQueueError,
    _claim_mutation_lock,
    archive_stale_claims as python_archive_stale_claims,
    claim_task as python_claim_task,
    heartbeat as python_heartbeat,
    release_task as python_release_task,
)


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = ROOT / ".agent-bridge" / "bin"
BOUND_AGENT = "codex-tools-1"
OTHER_AGENT = "codex-lead-1"


def _slash_claim_basename(task_id: str) -> str:
    assert "/" in task_id
    safe = task_id.replace("/", "_").strip("_")
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{digest}"


@pytest.fixture(autouse=True)
def _valid_work_queue_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "a" * 64)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", str(os.getpid()))
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
        "2026-07-28T00:00:00Z",
    )
    for name in (
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        monkeypatch.delenv(name, raising=False)


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for bridge session identity tests")
    return executable


def _run_script(
    runtime_root: Path,
    script_name: str,
    *args: str,
    bound_agent: str | None = BOUND_AGENT,
    extra_env: dict[str, str | None] | None = None,
    script_root: Path = BRIDGE_BIN,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    if bound_agent is None:
        env.pop("AGENT_BRIDGE_AGENT", None)
    else:
        env["AGENT_BRIDGE_AGENT"] = bound_agent
    env["AGENT_BRIDGE_SESSION_ID"] = "pytest-event-session"
    env["AGENT_BRIDGE_OWNER_SESSION_ID"] = "pytest-session"
    env["AGENT_BRIDGE_OWNER_TOKEN"] = "a" * 64
    env["AGENT_BRIDGE_OWNER_PID"] = str(os.getpid())
    env["AGENT_BRIDGE_OWNER_PROCESS_START_UTC"] = "2026-07-28T00:00:00Z"
    for name in (
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        env.pop(name, None)
    if extra_env:
        for name, value in extra_env.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_root / script_name),
            *args,
        ],
        cwd=runtime_root.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _owner_bound_claim_payload(
    *,
    task_id: str,
    agent: str = "codex",
    mode: str = "read-only",
    write_scope: list[str] | None = None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    claimed_at = now.isoformat().replace("+00:00", "Z")
    expires_at = (now + timedelta(seconds=900)).isoformat().replace(
        "+00:00",
        "Z",
    )
    return {
        "agent": agent,
        "task_id": task_id,
        "summary": "owner-bound PowerShell claim fixture",
        "mode": mode,
        "write_scope": list(write_scope or []),
        "run_id": "pytest",
        "claimed_at_utc": claimed_at,
        "last_heartbeat_utc": claimed_at,
        "lease_seconds": 900,
        "claim_lease_expires_utc": expires_at,
        "session_id": "pytest-event-session",
        "owner_session_id": "pytest-session",
        "owner_token_sha256": hashlib.sha256(
            ("a" * 64).encode("utf-8")
        ).hexdigest(),
        "owner_pid": os.getpid(),
        "owner_process_start_utc": "2026-07-28T00:00:00Z",
    }


def _write_claim_payload(
    runtime_root: Path,
    *,
    filename: str,
    payload: dict[str, object],
) -> Path:
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    claim_path = claims_dir / filename
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    return claim_path


def _work_queue_file_snapshot(runtime_root: Path) -> dict[str, bytes]:
    work_queue = runtime_root / "work_queue"
    if not work_queue.is_dir():
        return {}
    return {
        path.relative_to(work_queue).as_posix(): path.read_bytes()
        for path in sorted(work_queue.rglob("*"))
        if path.is_file()
    }


def _commit_bridge_session_bundle(
    source_repo: Path,
    *,
    compatible: bool,
) -> None:
    bridge_bin = source_repo / ".agent-bridge" / "bin"
    bridge_bin.mkdir(parents=True)
    shutil.copy2(BRIDGE_BIN / "New-AgentBridgeWorktree.ps1", bridge_bin)
    if compatible:
        shutil.copy2(BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1", bridge_bin)
        shutil.copy2(BRIDGE_BIN / "Start-AgentBridgeSession.ps1", bridge_bin)
    else:
        (bridge_bin / "Start-AgentBridgeSession.ps1").write_text(
            "#requires -Version 5.1\nparam([string] $Agent)\n",
            encoding="utf-8",
        )

    assert _git("init", "-b", "main", cwd=source_repo).returncode == 0
    assert _git("config", "user.email", "identity-test@example.invalid", cwd=source_repo).returncode == 0
    assert _git("config", "user.name", "Identity Test", cwd=source_repo).returncode == 0
    assert _git("add", ".agent-bridge", cwd=source_repo).returncode == 0
    committed = _git("commit", "-m", "identity bundle fixture", cwd=source_repo)
    assert committed.returncode == 0, committed.stderr


@pytest.mark.parametrize(
    ("script_name", "args"),
    [
        (
            "Claim-AgentTask.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-TaskId",
                "identity-mismatch-claim",
                "-Summary",
                "must not be written",
            ),
        ),
        ("Get-BridgeNextAction.ps1", ("-Agent", OTHER_AGENT, "-Json")),
        (
            "Write-AgentEvent.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-Type",
                "message",
                "-Message",
                "must not be written",
            ),
        ),
        (
            "Release-AgentTask.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-TaskId",
                "identity-mismatch-release",
            ),
        ),
        ("Send-Liveness.ps1", ("-Agent", OTHER_AGENT, "-State", "active")),
        (
            "Start-AgentBridgeConsumerLoop.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-RuntimeRoot",
                "must-not-exist-consumer-runtime",
                "-WorktreeBase",
                "must-not-exist-consumer-worktrees",
                "-DryRun",
                "-MaxIterations",
                "1",
            ),
        ),
        (
            "Start-AgentBridgeWorktreeSession.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-SourceRepoRoot",
                "must-not-exist-source-repo",
                "-WorktreeRoot",
                "must-not-exist-session-worktrees",
                "-RuntimeRoot",
                "must-not-exist-session-runtime",
            ),
        ),
        (
            "New-AgentBridgeWorktree.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-TaskId",
                "identity-mismatch-worktree",
                "-SourceRepoRoot",
                "must-not-exist-source-repo",
                "-WorktreeRoot",
                "must-not-exist-direct-worktrees",
                "-RuntimeRoot",
                "must-not-exist-direct-runtime",
            ),
        ),
        (
            "Invoke-BridgeGit.ps1",
            ("-Agent", OTHER_AGENT, "-GitArgs", "status"),
        ),
        (
            "Invoke-RoleReview.ps1",
            ("-Agent", OTHER_AGENT, "-Target", "identity-mismatch", "-DryRun"),
        ),
        (
            "Monitor-AgentBridge.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-RuntimeRoot",
                "must-not-exist-monitor-runtime",
                "-MaxIterations",
                "1",
                "-Json",
            ),
        ),
        (
            "New-BridgePrReviewWorktree.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-PullRequest",
                "1",
                "-SourceRepoRoot",
                "must-not-exist-review-source",
                "-WorktreeRoot",
                "must-not-exist-review-worktrees",
                "-RuntimeRoot",
                "must-not-exist-review-runtime",
                "-SkipFetch",
            ),
        ),
        (
            "Read-AgentBridge.ps1",
            ("-Agent", OTHER_AGENT, "-NoContinuity", "-NoAckReceived"),
        ),
        (
            "Start-BridgeHeartbeat.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-RuntimeRoot",
                "must-not-exist-heartbeat-runtime",
                "-IntervalMs",
                "1",
                "-MaxIterations",
                "1",
            ),
        ),
        ("Stop-AgentBridgeSession.ps1", ("-Agent", OTHER_AGENT)),
        (
            "Test-BridgeWake.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-RuntimeRoot",
                "must-not-exist-wake-runtime",
                "-NoConsume",
            ),
        ),
        (
            "Test-BridgeBranchSwitchSafe.ps1",
            ("-Agent", OTHER_AGENT, "-Json"),
        ),
        (
            "Watch-Bridge.ps1",
            (
                "-Agent",
                OTHER_AGENT,
                "-RuntimeRoot",
                "must-not-exist-watcher-runtime",
                "-PollIntervalMs",
                "1",
                "-MaxIterations",
                "1",
            ),
        ),
    ],
)
def test_bound_agent_cannot_request_another_agent_without_runtime_mutation(
    tmp_path: Path,
    script_name: str,
    args: tuple[str, ...],
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(runtime_root, script_name, *args)

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert not runtime_root.exists()
    assert list(tmp_path.iterdir()) == []


def test_non_operator_branch_force_is_rejected_before_runtime_access(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Test-BridgeBranchSwitchSafe.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Force",
        "-Json",
    )

    assert completed.returncode != 0
    assert "bound operator" in completed.stderr
    assert not runtime_root.exists()


def test_operator_json_branch_force_records_override_before_success(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "foreign-write.json").write_text(
        json.dumps(
            {
                "agent": "codex-tools-1",
                "task_id": "foreign-write",
                "summary": "foreign write claim",
                "mode": "write",
                "write_scope": ["tools/example.py"],
            }
        ),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Test-BridgeBranchSwitchSafe.ps1",
        "-Agent",
        "operator",
        "-Force",
        "-Json",
        bound_agent="operator",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["safe"] is False
    assert report["blocking_claims"] == 1
    events = [
        json.loads(line)
        for line in (
            runtime_root / "shared" / "events.jsonl"
        ).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    assert any(
        event["agent"] == "operator"
        and event["type"] == "decision"
        and event["status"] == "override"
        for event in events
    )


def test_operator_git_force_fails_closed_when_audit_writer_is_missing(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "foreign-write.json").write_text(
        json.dumps(
            {
                "agent": "codex-tools-1",
                "task_id": "foreign-write",
                "summary": "foreign write claim",
                "mode": "write",
                "write_scope": ["tools/example.py"],
            }
        ),
        encoding="utf-8",
    )
    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    shutil.copy2(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1",
        isolated_bin,
    )
    isolated_script = isolated_bin / "Invoke-BridgeGit.ps1"
    shutil.copy2(BRIDGE_BIN / "Invoke-BridgeGit.ps1", isolated_script)
    env = os.environ.copy()
    env["AGENT_BRIDGE_AGENT"] = "operator"
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    env["BRIDGE_GIT_SCRIPT"] = str(isolated_script)
    command = (
        "& $env:BRIDGE_GIT_SCRIPT -Agent operator -Force "
        "-GitArgs @('switch', 'must-not-run')"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "cannot authorize git override without Write-AgentEvent.ps1" in (
        completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_agent_identity_match_is_case_sensitive_and_fails_before_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        OTHER_AGENT,
        "-Type",
        "message",
        "-Message",
        "case mismatch must not be written",
        bound_agent=OTHER_AGENT.upper(),
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize("malformed_bound", ["*", "Codex-Tools-1", " codex-tools-1"])
@pytest.mark.parametrize(
    ("script_name", "args"),
    [
        (
            "Write-AgentEvent.ps1",
            ("-Agent", "codex-tools-1", "-Type", "message", "-Message", "no"),
        ),
        (
            "Read-AgentBridge.ps1",
            ("-NoContinuity", "-NoAckReceived"),
        ),
        ("Stop-AgentBridgeSession.ps1", ()),
    ],
)
def test_malformed_bound_identity_fails_before_state_or_job_access(
    tmp_path: Path,
    malformed_bound: str,
    script_name: str,
    args: tuple[str, ...],
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        script_name,
        *args,
        bound_agent=malformed_bound,
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert "malformed" in completed.stderr
    assert not runtime_root.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("requested_agent", "bound_agent", "expected_label"),
    [
        (
            "codex-tools-1\r\nforged-request: yes\t\\tail",
            "",
            "requested agent",
        ),
        (
            BOUND_AGENT,
            "codex-tools-1\r\nforged-bound: yes\t\\tail",
            "AGENT_BRIDGE_AGENT",
        ),
    ],
)
def test_malformed_identity_error_escapes_forged_lines_and_controls(
    requested_agent: str,
    bound_agent: str,
    expected_label: str,
) -> None:
    env = os.environ.copy()
    env["AGENT_BRIDGE_AGENT"] = bound_agent
    env["BRIDGE_IDENTITY_HELPER"] = str(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    )
    env["BRIDGE_REQUESTED_AGENT"] = requested_agent
    command = (
        ". $env:BRIDGE_IDENTITY_HELPER; "
        "Assert-AgentBridgeSessionIdentity "
        "-RequestedAgent ([string]$env:BRIDGE_REQUESTED_AGENT)"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert expected_label in completed.stderr
    assert r"\r\nforged-" in completed.stderr
    assert r"\t\\tail" in completed.stderr
    assert "\r\nforged-" not in completed.stderr
    assert "\nforged-" not in completed.stderr


@pytest.mark.parametrize(
    "malformed_agent",
    [
        " \t ",
        "codex-tools-1\r\nforged-entry: yes\t\\tail",
    ],
)
def test_entrypoint_malformed_requested_agent_uses_escaped_identity_error(
    tmp_path: Path,
    malformed_agent: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        malformed_agent,
        "-Type",
        "message",
        "-Message",
        "must not be written",
        bound_agent=None,
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert "requested agent" in completed.stderr
    assert "\nforged-entry: yes" not in completed.stderr
    if "forged-entry" in malformed_agent:
        # pwsh -File may insert platform-specific diagnostic formatting
        # between words while preserving the escaped, injection-safe value.
        assert r"\r\nforged-entry:" in completed.stderr
        assert r"yes\t\\tail" in completed.stderr
    else:
        assert r"\t" in completed.stderr
    assert not runtime_root.exists()


def test_write_event_rejects_invalid_ambient_run_id_before_runtime_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "must not be written",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_RUN_ID": "bad/run"},
    )

    assert completed.returncode != 0
    assert "run_id must match" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("metadata_args", "extra_env", "expected_error"),
    [
        (("-RunId", "bad/run"), {}, "run_id must match"),
        (("-Role", "INVALID"), {}, "role must match"),
        (
            ("-Capabilities", "INVALID CAPABILITY"),
            {},
            "capability must match",
        ),
        ((), {"AGENT_BRIDGE_SESSION_ID": "bad/run"}, "session_id must match"),
    ],
)
def test_claim_rejects_invalid_event_metadata_before_runtime_write(
    tmp_path: Path,
    metadata_args: tuple[str, ...],
    extra_env: dict[str, str],
    expected_error: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "invalid-claim-metadata",
        "-Summary",
        "must not create a claim",
        *metadata_args,
        bound_agent="codex",
        extra_env=extra_env,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not runtime_root.exists()


def test_claim_rejects_private_marker_before_runtime_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "private-marker-claim",
        "-Summary",
        "_DO_NOT_LEAK must not create a claim",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "private marker" in completed.stderr
    assert not runtime_root.exists()


def test_release_rejects_invalid_run_id_without_archiving_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "release-invalid-run-id.json"
    original_claim = json.dumps(
        {
            "agent": "codex",
            "task_id": "release-invalid-run-id",
            "summary": "must remain active",
            "mode": "read-only",
            "write_scope": [],
        },
        sort_keys=True,
    )
    claim_path.write_text(original_claim, encoding="utf-8")

    completed = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "release-invalid-run-id",
        "-RunId",
        "bad/run",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "run_id must match" in completed.stderr
    assert claim_path.read_text(encoding="utf-8") == original_claim
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_release_rejects_private_marker_without_archiving_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "release-private-marker.json"
    original_claim = json.dumps(
        {
            "agent": "codex",
            "task_id": "release-private-marker",
            "summary": "must remain active",
            "mode": "read-only",
            "write_scope": [],
        },
        sort_keys=True,
    )
    claim_path.write_text(original_claim, encoding="utf-8")

    completed = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "release-private-marker",
        "-Message",
        "PRIVATE_MARKER must not be archived",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "private marker" in completed.stderr
    assert claim_path.read_text(encoding="utf-8") == original_claim
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("bound_agent", [None, "", "   "])
def test_unset_or_blank_binding_remains_legacy_compatible(
    tmp_path: Path,
    bound_agent: str | None,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "legacy unbound writer",
        bound_agent=bound_agent,
    )

    assert completed.returncode == 0, completed.stderr
    event = json.loads(
        (runtime_root / "shared" / "events.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert event["agent"] == "codex"


def test_unbound_read_and_global_cleanup_keep_legacy_defaults(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    read_result = _run_script(
        runtime_root,
        "Read-AgentBridge.ps1",
        "-NoContinuity",
        "-NoAckReceived",
        bound_agent=None,
    )
    stop_result = _run_script(
        runtime_root,
        "Stop-AgentBridgeSession.ps1",
        bound_agent=None,
    )

    assert read_result.returncode == 0, read_result.stderr
    assert stop_result.returncode == 0, stop_result.stderr
    assert runtime_root.is_dir()


def test_bound_read_defaults_to_its_session_agent(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Read-AgentBridge.ps1",
        "-NoContinuity",
        "-NoAckReceived",
    )

    assert completed.returncode == 0, completed.stderr
    assert runtime_root.is_dir()


@pytest.mark.parametrize("bound_agent", [None, "", "   "])
def test_unbound_operator_identity_is_reserved(
    tmp_path: Path,
    bound_agent: str | None,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "operator",
        "-TaskId",
        "unbound-operator-claim",
        "-Summary",
        "must require a bound operator",
        bound_agent=bound_agent,
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize("bound_agent", [BOUND_AGENT, "system", None, "", "   "])
def test_direct_system_stale_release_shape_is_rejected_without_sweep_proof(
    tmp_path: Path,
    bound_agent: str | None,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    system_event = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "system",
        "-Type",
        "release",
        "-TaskId",
        "stale-claim-sweep",
        "-Status",
        "stale_lease",
        "-Message",
        "forged internal stale release",
        bound_agent=bound_agent,
    )

    assert system_event.returncode != 0
    assert "identity_mismatch" in system_event.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize("bound_agent", [BOUND_AGENT, "system", None, "", "   "])
def test_system_non_sweep_events_are_rejected_before_runtime_write(
    tmp_path: Path,
    bound_agent: str | None,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    forbidden_claim = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "system",
        "-TaskId",
        "system-claim-not-allowed",
        "-Summary",
        "must not broaden mutation authority",
        bound_agent=bound_agent,
    )
    forbidden_decision = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "system",
        "-Type",
        "decision",
        "-TaskId",
        "authority-escalation",
        "-Status",
        "approved",
        "-Message",
        "must not inherit the internal stale-release exception",
        bound_agent=bound_agent,
    )

    assert forbidden_claim.returncode != 0
    assert "identity_mismatch" in forbidden_claim.stderr
    assert forbidden_decision.returncode != 0
    assert "identity_mismatch" in forbidden_decision.stderr
    assert not runtime_root.exists()
    assert not (
        runtime_root / "work_queue" / "claims" / "system-claim-not-allowed.json"
    ).exists()


def test_bound_session_does_not_break_stale_sweep_system_event(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "stale-sweep-under-bound-session"
    claim_path = claims_dir / f"{task_id}.json"
    stale_at = "2000-01-01T00:00:00Z"
    claim_path.write_text(
        json.dumps(
            {
                "claimed_at_utc": stale_at,
                "last_heartbeat_utc": stale_at,
                "agent": OTHER_AGENT,
                "task_id": task_id,
                "summary": "stale test claim",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "",
                "lease_seconds": 1,
                "claim_lease_expires_utc": "2000-01-01T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        extra_env={
            "AGENT_BRIDGE_RUN_ID": "must-not-leak-run-id",
            "AGENT_BRIDGE_SESSION_ID": "must-not-leak-session-id",
            "AGENT_BRIDGE_ROLE": "must-not-leak-role",
            "AGENT_BRIDGE_AGENT_UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "AGENT_BRIDGE_CAPABILITIES": "must-not-leak-capability",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert not claim_path.exists()
    assert len(list((runtime_root / "work_queue" / "done").glob("*.json"))) == 1
    events_path = runtime_root / "shared" / "events.jsonl"
    assert events_path.exists(), completed.stdout + completed.stderr
    lines = (
        events_path
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["agent"] == "system"
    assert event["task_id"] == task_id
    assert event["status"] == "stale_lease"
    assert event["run_id"] == ""
    assert "role" not in event
    assert "agent_uuid" not in event
    assert "session_id" not in event
    assert "capabilities" not in event
    serialized_event = json.dumps(event, sort_keys=True)
    assert "must-not-leak" not in serialized_event
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" not in serialized_event
    archived_path = Path(event["payload"]["archived_path"])
    assert archived_path.parent == runtime_root / "work_queue" / "done"
    assert archived_path.is_file()
    assert event["payload"]["claim_claimed_at_utc"].startswith(
        "2000-01-01T00:00:00"
    )
    assert event["payload"]["claim_run_id"] == ""
    assert event["payload"]["archive_released_at_utc"]
    assert (
        event["payload"]["archive_state_semantics"]
        == "verified_before_event_append"
    )


def test_stale_sweep_event_fails_closed_when_active_claims_cannot_be_verified(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "malformed-shadow.json").write_text(
        "{not-json",
        encoding="utf-8",
    )
    task_id = "stale-proof-with-malformed-shadow"
    stale_at = "2000-01-01T00:00:00Z"
    (claims_dir / f"{task_id}.json").write_text(
        json.dumps(
            {
                "claimed_at_utc": stale_at,
                "last_heartbeat_utc": stale_at,
                "agent": OTHER_AGENT,
                "task_id": task_id,
                "summary": "stale test claim",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "old-generation",
                "lease_seconds": 1,
                "claim_lease_expires_utc": "2000-01-01T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
    )

    assert completed.returncode == 0, completed.stderr
    assert "cannot verify active claims" in completed.stdout + completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    assert not events_path.exists() or not events_path.read_text(
        encoding="utf-8"
    ).strip()
    assert len(list((runtime_root / "work_queue" / "done").glob("*.json"))) == 1


@pytest.mark.skipif(
    os.name != "nt",
    reason="bridge bootstrap requires a persistent Windows C: repo",
)
def test_start_session_binds_requested_agent_in_calling_process(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    runtime_root = tmp_path / "bridge-runtime"
    env = os.environ.copy()
    env.pop("AGENT_BRIDGE_AGENT", None)
    env["AGENT_BRIDGE_ROLE"] = "stale-role"
    env["AGENT_BRIDGE_AGENT_UUID"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    env["AGENT_BRIDGE_CAPABILITIES"] = "stale-capability"
    env["BRIDGE_START_SCRIPT"] = str(BRIDGE_BIN / "Start-AgentBridgeSession.ps1")
    env["BRIDGE_TEST_REPO"] = str(repo_root)
    env["BRIDGE_TEST_RUNTIME"] = str(runtime_root)
    command = (
        ". $env:BRIDGE_START_SCRIPT "
        f"-Agent {OTHER_AGENT} "
        "-RepoRoot $env:BRIDGE_TEST_REPO "
        "-PrimaryRepoRoot $env:BRIDGE_TEST_REPO "
        "-RuntimeRoot $env:BRIDGE_TEST_RUNTIME "
        "-SkipBridgeRead -SkipLiveness -SkipGitStatus "
        "-SkipWakeWatcher -SkipHeartbeatJob | Out-Null; "
        "[pscustomobject]@{ "
        "agent = [string]$env:AGENT_BRIDGE_AGENT; "
        "session_id = [string]$env:AGENT_BRIDGE_SESSION_ID; "
        "owner_session_id = [string]$env:AGENT_BRIDGE_OWNER_SESSION_ID; "
        "owner_token = [string]$env:AGENT_BRIDGE_OWNER_TOKEN; "
        "owner_pid = [string]$env:AGENT_BRIDGE_OWNER_PID; "
        "owner_process_start_utc = [string]$env:AGENT_BRIDGE_OWNER_PROCESS_START_UTC; "
        "role = [string]$env:AGENT_BRIDGE_ROLE; "
        "agent_uuid = [string]$env:AGENT_BRIDGE_AGENT_UUID; "
        "capabilities = [string]$env:AGENT_BRIDGE_CAPABILITIES "
        "} | ConvertTo-Json -Compress"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    identity = json.loads(completed.stdout)
    assert identity["agent"] == OTHER_AGENT
    assert identity["session_id"].startswith(f"{OTHER_AGENT}-")
    assert identity["owner_session_id"] == identity["session_id"]
    assert len(identity["owner_token"]) == 64
    assert set(identity["owner_token"]) <= set("0123456789abcdef")
    assert int(identity["owner_pid"]) > 0
    assert identity["owner_process_start_utc"].endswith("Z")
    assert identity["role"] == ""
    assert identity["agent_uuid"] == ""
    assert identity["capabilities"] == ""
    assert runtime_root.is_dir()


@pytest.mark.skipif(
    os.name != "nt",
    reason="bridge bootstrap requires a persistent Windows C: repo",
)
def test_start_session_normalizes_uppercase_agent_uuid(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    runtime_root = tmp_path / "bridge-runtime"
    uppercase_uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    env = os.environ.copy()
    env.pop("AGENT_BRIDGE_AGENT", None)
    env["BRIDGE_START_SCRIPT"] = str(BRIDGE_BIN / "Start-AgentBridgeSession.ps1")
    env["BRIDGE_TEST_REPO"] = str(repo_root)
    env["BRIDGE_TEST_RUNTIME"] = str(runtime_root)
    env["BRIDGE_TEST_UUID"] = uppercase_uuid
    command = (
        ". $env:BRIDGE_START_SCRIPT "
        f"-Agent {OTHER_AGENT} "
        "-RepoRoot $env:BRIDGE_TEST_REPO "
        "-PrimaryRepoRoot $env:BRIDGE_TEST_REPO "
        "-RuntimeRoot $env:BRIDGE_TEST_RUNTIME "
        "-AgentUuid $env:BRIDGE_TEST_UUID "
        "-SkipBridgeRead -SkipLiveness -SkipGitStatus "
        "-SkipWakeWatcher -SkipHeartbeatJob | Out-Null; "
        "[string]$env:AGENT_BRIDGE_AGENT_UUID"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == uppercase_uuid.lower()


def test_start_session_rejects_rebinding_before_runtime_write(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    runtime_root = tmp_path / "bridge-runtime"
    env = os.environ.copy()
    env["AGENT_BRIDGE_AGENT"] = BOUND_AGENT
    env["BRIDGE_START_SCRIPT"] = str(BRIDGE_BIN / "Start-AgentBridgeSession.ps1")
    env["BRIDGE_TEST_REPO"] = str(repo_root)
    env["BRIDGE_TEST_RUNTIME"] = str(runtime_root)
    command = (
        "& $env:BRIDGE_START_SCRIPT "
        f"-Agent {OTHER_AGENT} "
        "-RepoRoot $env:BRIDGE_TEST_REPO "
        "-PrimaryRepoRoot $env:BRIDGE_TEST_REPO "
        "-RuntimeRoot $env:BRIDGE_TEST_RUNTIME "
        "-SkipBridgeRead -SkipLiveness -SkipGitStatus "
        "-SkipWakeWatcher -SkipHeartbeatJob"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "identity_mismatch" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("metadata_args", "expected_error"),
    [
        (("-RunId", "bad/run"), "run_id must match"),
        (("-Role", "BAD ROLE"), "role must match"),
        (("-AgentUuid", "not-a-uuid"), "agent_uuid must be a UUID"),
        (
            ("-Capabilities", "BAD CAPABILITY"),
            "capability must match",
        ),
    ],
)
@pytest.mark.skipif(
    os.name != "nt",
    reason="session metadata validation requires a persistent Windows C: repo",
)
def test_start_session_rejects_invalid_metadata_before_runtime_write(
    tmp_path: Path,
    metadata_args: tuple[str, ...],
    expected_error: str,
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeSession.ps1",
        "-Agent",
        "codex",
        "-RepoRoot",
        str(repo_root),
        "-PrimaryRepoRoot",
        str(repo_root),
        "-RuntimeRoot",
        str(runtime_root),
        *metadata_args,
        "-SkipBridgeRead",
        "-SkipLiveness",
        "-SkipGitStatus",
        "-SkipWakeWatcher",
        "-SkipHeartbeatJob",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not runtime_root.exists()


def test_unbound_consumer_does_not_adopt_stale_identity_metadata(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env = os.environ.copy()
    env.pop("AGENT_BRIDGE_AGENT", None)
    env["AGENT_BRIDGE_RUN_ID"] = "stale-run"
    env["AGENT_BRIDGE_SESSION_ID"] = "stale-session"
    env["AGENT_BRIDGE_ROLE"] = "stale-role"
    env["AGENT_BRIDGE_AGENT_UUID"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    env["AGENT_BRIDGE_CAPABILITIES"] = "stale-capability"
    env["BRIDGE_CONSUMER_SCRIPT"] = str(
        BRIDGE_BIN / "Start-AgentBridgeConsumerLoop.ps1"
    )
    env["BRIDGE_TEST_RUNTIME"] = str(runtime_root)
    env["BRIDGE_TEST_WORKTREE"] = str(worktree)
    command = (
        "& $env:BRIDGE_CONSUMER_SCRIPT "
        "-Agent codex "
        "-RuntimeRoot $env:BRIDGE_TEST_RUNTIME "
        "-Worktree $env:BRIDGE_TEST_WORKTREE "
        "-DryRun -SkipHeartbeatDuringCodex "
        "-DurationMinutes 0 -MaxIterations 1 -PollSeconds 0 | Out-Null; "
        "[pscustomobject]@{ "
        "agent = [string]$env:AGENT_BRIDGE_AGENT; "
        "run_id = [string]$env:AGENT_BRIDGE_RUN_ID; "
        "session_id = [string]$env:AGENT_BRIDGE_SESSION_ID; "
        "owner_session_id = [string]$env:AGENT_BRIDGE_OWNER_SESSION_ID; "
        "owner_token_length = ([string]$env:AGENT_BRIDGE_OWNER_TOKEN).Length; "
        "role = [string]$env:AGENT_BRIDGE_ROLE; "
        "agent_uuid = [string]$env:AGENT_BRIDGE_AGENT_UUID; "
        "capabilities = [string]$env:AGENT_BRIDGE_CAPABILITIES "
        "} | ConvertTo-Json -Compress"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    identity = json.loads(completed.stdout)
    assert identity["agent"] == "codex"
    assert identity["run_id"] == ""
    assert identity["session_id"] == ""
    assert identity["owner_session_id"].startswith("consumer-codex-")
    assert identity["owner_token_length"] == 64
    assert identity["role"] == ""
    assert identity["agent_uuid"] == ""
    assert identity["capabilities"] == ""


@pytest.mark.parametrize(
    ("metadata", "expected_error"),
    [
        ({"AGENT_BRIDGE_RUN_ID": "bad/run"}, "run_id must match"),
        ({"AGENT_BRIDGE_SESSION_ID": "bad/run"}, "session_id must match"),
        ({"AGENT_BRIDGE_ROLE": "INVALID"}, "role must match"),
        ({"AGENT_BRIDGE_AGENT_UUID": "not-a-uuid"}, "agent_uuid must be a UUID"),
        (
            {"AGENT_BRIDGE_CAPABILITIES": "INVALID CAPABILITY"},
            "capability must match",
        ),
    ],
)
def test_same_bound_consumer_validates_inherited_metadata_before_runtime_write(
    tmp_path: Path,
    metadata: dict[str, str],
    expected_error: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeConsumerLoop.ps1",
        "-Agent",
        "codex",
        "-RuntimeRoot",
        str(runtime_root),
        "-Worktree",
        str(worktree),
        "-DryRun",
        "-SkipHeartbeatDuringCodex",
        "-DurationMinutes",
        "0",
        "-MaxIterations",
        "1",
        "-PollSeconds",
        "0",
        bound_agent="codex",
        extra_env=metadata,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not runtime_root.exists()


def test_consumer_does_not_run_codex_when_start_audit_writer_is_missing(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    for name in (
        "AgentBridgeSessionIdentity.ps1",
        "Start-AgentBridgeConsumerLoop.ps1",
        "Test-BridgeWake.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / name, isolated_bin)
    sentinel = tmp_path / "codex-ran.txt"
    sentinel_command = tmp_path / "sentinel-codex.cmd"
    sentinel_command.write_text(
        '@echo off\r\n>"%BRIDGE_TEST_SENTINEL%" echo ran\r\nexit /b 0\r\n',
        encoding="ascii",
    )
    env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        env.pop(name, None)
    env["AGENT_BRIDGE_AGENT"] = "codex"
    env["AGENT_BRIDGE_OWNER_SESSION_ID"] = "missing-audit-fixture"
    env["AGENT_BRIDGE_OWNER_TOKEN"] = "c" * 64
    env["AGENT_BRIDGE_OWNER_PID"] = str(os.getpid())
    env["AGENT_BRIDGE_OWNER_PROCESS_START_UTC"] = "2026-07-28T00:00:00Z"
    env["BRIDGE_TEST_SENTINEL"] = str(sentinel)
    env["BRIDGE_CONSUMER_SCRIPT"] = str(
        isolated_bin / "Start-AgentBridgeConsumerLoop.ps1"
    )
    command = (
        "& $env:BRIDGE_CONSUMER_SCRIPT "
        "-Agent codex "
        f"-RuntimeRoot '{runtime_root}' "
        f"-Worktree '{worktree}' "
        f"-CodexCommand '{sentinel_command}' "
        "-SkipHeartbeatDuringCodex "
        "-DurationMinutes 0 -MaxIterations 1 -PollSeconds 0"
    )

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "consumer status audit failed" in completed.stderr
    assert not sentinel.exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    ("metadata_args", "expected_error"),
    [
        (("-RunId", "bad/run"), "run_id must match"),
        (("-Role", "INVALID"), "role must match"),
        (("-AgentUuid", "not-a-uuid"), "agent_uuid must be a UUID"),
        (
            ("-Capabilities", "INVALID CAPABILITY"),
            "capability must match",
        ),
    ],
)
def test_worktree_session_rejects_invalid_metadata_before_git_or_runtime_mutation(
    tmp_path: Path,
    metadata_args: tuple[str, ...],
    expected_error: str,
) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _commit_bridge_session_bundle(source_repo, compatible=True)
    worktree_root = tmp_path / "worktrees"
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeWorktreeSession.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "invalid-worktree-metadata",
        "-SourceRepoRoot",
        str(source_repo),
        "-WorktreeRoot",
        str(worktree_root),
        "-RuntimeRoot",
        str(runtime_root),
        "-Base",
        "HEAD",
        *metadata_args,
        "-SkipBridgeRead",
        "-SkipLiveness",
        "-SkipGitStatus",
        "-SkipWakeWatcher",
        "-SkipHeartbeatJob",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not worktree_root.exists()
    assert not runtime_root.exists()
    branch = _git(
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/waggledance/codex/invalid-worktree-metadata",
        cwd=source_repo,
    )
    assert branch.returncode != 0


def test_worktree_session_rejects_old_base_before_git_or_runtime_mutation(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _commit_bridge_session_bundle(source_repo, compatible=False)
    worktree_root = tmp_path / "worktrees"
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeWorktreeSession.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "old-base-bundle",
        "-SourceRepoRoot",
        str(source_repo),
        "-WorktreeRoot",
        str(worktree_root),
        "-RuntimeRoot",
        str(runtime_root),
        "-Base",
        "HEAD",
        "-SkipBridgeRead",
        "-SkipLiveness",
        "-SkipGitStatus",
        "-SkipWakeWatcher",
        "-SkipHeartbeatJob",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "identity-bound session bundle is missing" in completed.stderr
    assert not worktree_root.exists()
    assert not runtime_root.exists()
    branch = _git(
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/waggledance/codex/old-base-bundle",
        cwd=source_repo,
    )
    assert branch.returncode != 0


@pytest.mark.skipif(
    os.name != "nt",
    reason="bridge worktree bootstrap requires a persistent Windows C: repo",
)
def test_worktree_session_rejects_dirty_reused_incompatible_target(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _commit_bridge_session_bundle(source_repo, compatible=True)
    worktree_root = tmp_path / "worktrees"
    target = worktree_root / "codex-reused-old-branch"
    worktree_root.mkdir()
    created = _git(
        "worktree",
        "add",
        str(target),
        "-b",
        "reused-old-branch",
        "HEAD",
        cwd=source_repo,
    )
    assert created.returncode == 0, created.stderr
    old_session = "#requires -Version 5.1\nparam([string] $Agent)\n"
    target_session = (
        target / ".agent-bridge" / "bin" / "Start-AgentBridgeSession.ps1"
    )
    target_session.write_text(old_session, encoding="utf-8")
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeWorktreeSession.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "reused-old-bundle",
        "-SourceRepoRoot",
        str(source_repo),
        "-WorktreeRoot",
        str(worktree_root),
        "-RuntimeRoot",
        str(runtime_root),
        "-Base",
        "HEAD",
        "-Branch",
        "reused-old-branch",
        "-SkipBridgeRead",
        "-SkipLiveness",
        "-SkipGitStatus",
        "-SkipWakeWatcher",
        "-SkipHeartbeatJob",
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "incompatible identity-bound session bundle" in completed.stderr
    assert not runtime_root.exists()
    assert target_session.read_text(encoding="utf-8") == old_session
    assert _git("status", "--short", cwd=target).stdout.strip()


def test_claim_uses_diagnostic_writer_pid_not_session_owner_pid(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "writer-pid-semantics",
        "-Summary",
        "claim writer pid is diagnostic only",
        bound_agent="codex",
    )

    assert completed.returncode == 0, completed.stderr
    claim = json.loads(
        (
            runtime_root
            / "work_queue"
            / "claims"
            / "writer-pid-semantics.json"
        ).read_text(encoding="utf-8")
    )
    assert "pid" not in claim
    assert isinstance(claim["writer_pid"], int)
    assert claim["writer_pid"] > 0
    assert claim["writer_pid_semantics"] == "diagnostic_only"
    assert claim["owner_session_id"] == "pytest-session"
    assert claim["owner_token_sha256"] == hashlib.sha256(
        ("a" * 64).encode()
    ).hexdigest()
    assert claim["owner_pid"] == os.getpid()
    assert claim["owner_process_start_utc"].startswith(
        "2026-07-28T00:00:00"
    )
    assert "a" * 64 not in json.dumps(claim, sort_keys=True)


@pytest.mark.parametrize(
    "missing_name",
    [
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "AGENT_BRIDGE_OWNER_TOKEN",
        "AGENT_BRIDGE_OWNER_PID",
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
    ],
)
def test_claim_requires_complete_generation_owner_context_before_runtime_write(
    tmp_path: Path,
    missing_name: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "missing-owner-context",
        "-Summary",
        "must fail before creating runtime state",
        bound_agent="codex",
        extra_env={missing_name: None},
    )

    assert completed.returncode != 0
    assert "claim_owner_mismatch" in completed.stderr
    assert not runtime_root.exists()


def test_release_validates_owner_context_before_claim_existence(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing-runtime"
    missing_release = _run_script(
        missing_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "missing-owner-release",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_OWNER_TOKEN": None},
    )

    assert missing_release.returncode == 3
    assert "claim_owner_mismatch" in missing_release.stderr
    assert "no active claim" not in missing_release.stderr
    assert not missing_root.exists()

    runtime_root = tmp_path / "existing-runtime"
    task_id = "existing-owner-release"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "owner context ordering fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    original = claim_path.read_bytes()

    existing_release = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_OWNER_TOKEN": None},
    )

    assert existing_release.returncode == 3
    assert "claim_owner_mismatch" in existing_release.stderr
    assert claim_path.read_bytes() == original
    assert not (runtime_root / "work_queue" / "done").exists()


def test_same_agent_other_generation_cannot_force_replace_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "generation-bound-force"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "owned by generation A",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    original_claim = claim_path.read_text(encoding="utf-8")

    replaced = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "generation B must not replace",
        "-Force",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_OWNER_TOKEN": "b" * 64},
    )

    assert replaced.returncode != 0
    assert "claim_owner_wrong_generation" in replaced.stderr
    assert claim_path.read_text(encoding="utf-8") == original_claim


def test_same_agent_other_generation_heartbeat_does_not_refresh_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "generation-bound-heartbeat"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "owned by generation A",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    before = json.loads(claim_path.read_text(encoding="utf-8"))

    foreign_heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "generation-b-heartbeat",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_OWNER_TOKEN": "b" * 64},
    )

    assert foreign_heartbeat.returncode == 0, foreign_heartbeat.stderr
    after_foreign = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_foreign["last_heartbeat_utc"] == before["last_heartbeat_utc"]
    assert (
        after_foreign["claim_lease_expires_utc"]
        == before["claim_lease_expires_utc"]
    )

    owner_heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "generation-a-heartbeat",
        bound_agent="codex",
    )
    assert owner_heartbeat.returncode == 0, owner_heartbeat.stderr
    after_owner = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_owner["last_heartbeat_utc"] != before["last_heartbeat_utc"]


def test_same_agent_other_generation_cannot_release_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "generation-bound-release"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "owned by generation A",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"

    foreign_release = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_OWNER_TOKEN": "b" * 64},
    )

    assert foreign_release.returncode == 3
    assert "claim_owner_wrong_generation" in foreign_release.stderr
    assert claim_path.is_file()
    assert not list((runtime_root / "work_queue" / "done").glob("*.json"))

    owner_release = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )
    assert owner_release.returncode == 0, owner_release.stderr
    assert not claim_path.exists()
    archived = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(archived) == 1
    released_claim = json.loads(archived[0].read_text(encoding="utf-8"))
    assert released_claim["release_status"] == "done"


def test_legacy_tokenless_claim_cannot_be_refreshed_or_released(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "legacy-tokenless-claim"
    claim_path = claims_dir / f"{task_id}.json"
    legacy_claim = {
        "claimed_at_utc": "2026-07-28T00:00:00Z",
        "last_heartbeat_utc": "2026-07-28T00:00:00Z",
        "agent": "codex",
        "task_id": task_id,
        "summary": "legacy claim must expire instead of changing owner",
        "mode": "read-only",
        "write_scope": [],
        "lease_seconds": 300,
        "claim_lease_expires_utc": "2099-01-01T00:00:00Z",
    }
    claim_path.write_text(json.dumps(legacy_claim), encoding="utf-8")

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        bound_agent="codex",
    )
    release = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )

    assert heartbeat.returncode == 0, heartbeat.stderr
    assert release.returncode == 3
    assert "claim_owner_legacy_tokenless" in release.stderr
    assert json.loads(claim_path.read_text(encoding="utf-8")) == legacy_claim


def test_powershell_claim_can_be_heartbeated_and_released_by_python_owner(
    tmp_path: Path,
) -> None:
    initialized = _git("init", "-b", "diagnostic-test", cwd=tmp_path)
    assert initialized.returncode == 0, initialized.stderr
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "powershell-to-python-owner-parity"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "PowerShell claim consumed by Python",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    original = json.loads(claim_path.read_text(encoding="utf-8"))
    expected_owner = {
        name: original[name]
        for name in (
            "owner_session_id",
            "owner_token_sha256",
            "owner_pid",
            "owner_process_start_utc",
        )
    }
    expected_diagnostics = {
        name: original[name]
        for name in (
            "writer_pid",
            "writer_pid_semantics",
            "cwd",
            "git_branch",
        )
        if original.get(name)
    }
    assert set(expected_diagnostics) == {
        "writer_pid",
        "writer_pid_semantics",
        "cwd",
        "git_branch",
    }

    refreshed = python_heartbeat(
        agent="codex",
        task_id=task_id,
        bridge_root=runtime_root,
        now_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    after_heartbeat = json.loads(claim_path.read_text(encoding="utf-8"))

    assert refreshed.owner_session_id == expected_owner["owner_session_id"]
    assert refreshed.owner_token_sha256 == expected_owner["owner_token_sha256"]
    assert refreshed.owner_pid == expected_owner["owner_pid"]
    assert (
        refreshed.owner_process_start_utc
        == expected_owner["owner_process_start_utc"]
    )
    assert {
        name: after_heartbeat[name] for name in expected_owner
    } == expected_owner
    assert {
        name: after_heartbeat[name] for name in expected_diagnostics
    } == expected_diagnostics

    released = python_release_task(
        agent="codex",
        task_id=task_id,
        bridge_root=runtime_root,
        now_utc=datetime(2030, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )

    assert released.owner_session_id == expected_owner["owner_session_id"]
    assert released.owner_token_sha256 == expected_owner["owner_token_sha256"]
    assert not claim_path.exists()
    done_files = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(done_files) == 1
    done = json.loads(done_files[0].read_text(encoding="utf-8"))
    assert {name: done[name] for name in expected_owner} == expected_owner
    assert {
        name: done[name] for name in expected_diagnostics
    } == expected_diagnostics


def test_python_claim_can_be_heartbeated_and_released_by_powershell_owner(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "python-to-powershell-owner-parity"
    claim = python_claim_task(
        agent="codex",
        task_id=task_id,
        summary="Python claim consumed by PowerShell",
        bridge_root=runtime_root,
        now_utc=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    original = json.loads(claim_path.read_text(encoding="utf-8"))
    expected_owner = {
        "owner_session_id": "pytest-session",
        "owner_token_sha256": hashlib.sha256(
            ("a" * 64).encode("utf-8")
        ).hexdigest(),
        "owner_pid": os.getpid(),
        "owner_process_start_utc": claim.owner_process_start_utc,
    }
    assert original["session_id"] == "pytest-session"
    assert {name: original[name] for name in expected_owner} == expected_owner

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "python-owner-heartbeat",
        bound_agent="codex",
    )
    assert heartbeat.returncode == 0, heartbeat.stderr
    after_heartbeat = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_heartbeat["last_heartbeat_utc"] != original["last_heartbeat_utc"]
    assert after_heartbeat["session_id"] == original["session_id"]
    assert {
        name: after_heartbeat[name] for name in expected_owner
    } == expected_owner

    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )
    assert released.returncode == 0, released.stderr
    assert not claim_path.exists()
    done_files = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(done_files) == 1
    done = json.loads(done_files[0].read_text(encoding="utf-8"))
    assert done["session_id"] == original["session_id"]
    assert {name: done[name] for name in expected_owner} == expected_owner


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("owner_session_id", "invalid owner session"),
        ("owner_session_id", "pytest-session\n"),
        ("owner_session_id", 123),
        ("owner_token_sha256", "A" * 64),
        (
            "owner_token_sha256",
            hashlib.sha256(("a" * 64).encode("utf-8")).hexdigest() + "\n",
        ),
        ("owner_pid", 0),
        ("owner_pid", "not-a-pid"),
        ("owner_pid", " 1 "),
        ("owner_pid", True),
        ("owner_pid", 1.0),
        ("owner_pid", 2**31),
        ("owner_process_start_utc", "not-a-time"),
        ("owner_process_start_utc", "2026-07-28 00:00:00Z"),
        ("owner_process_start_utc", "2026-07-28T00:00:00Z\n"),
        ("owner_process_start_utc", "2026-07-28T00:00:00z"),
        ("owner_process_start_utc", "2026-07-28T00:00:00+0300"),
    ],
    ids=[
        "malformed-session",
        "session-trailing-lf",
        "session-nonstring",
        "uppercase-digest",
        "digest-trailing-lf",
        "nonpositive-pid",
        "malformed-pid",
        "whitespace-pid",
        "boolean-pid",
        "floating-pid",
        "overflow-pid",
        "malformed-process-start",
        "loose-process-start",
        "process-start-trailing-lf",
        "lowercase-z-process-start",
        "compact-offset-process-start",
    ],
)
def test_present_but_invalid_stored_owner_is_legacy_tokenless(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"invalid-stored-owner-{field}-{type(invalid_value).__name__}"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "stored owner validation fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim[field] = invalid_value
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    mutated = claim_path.read_text(encoding="utf-8")

    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )
    forced = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "must not replace malformed stored owner",
        "-Force",
        bound_agent="codex",
    )

    assert released.returncode == 3
    assert "claim_owner_legacy_tokenless" in released.stderr
    assert forced.returncode != 0
    assert "claim_owner_legacy_tokenless" in forced.stderr
    assert claim_path.read_text(encoding="utf-8") == mutated


@pytest.mark.parametrize(
    (
        "requested_agent",
        "bound_agent",
        "task_id",
        "extra_env",
        "expected_error",
    ),
    [
        (
            "codex\n",
            "codex",
            "requested-agent-trailing-lf",
            None,
            "identity_mismatch",
        ),
        (
            "codex",
            "codex\n",
            "bound-agent-trailing-lf",
            None,
            "identity_mismatch",
        ),
        (
            "codex",
            "codex",
            "task-trailing-lf\n",
            None,
            "task_id invalid",
        ),
        (
            "codex",
            "codex",
            "owner-session-trailing-lf",
            {"AGENT_BRIDGE_OWNER_SESSION_ID": "pytest-session\n"},
            "claim_owner_mismatch",
        ),
        (
            "codex",
            "codex",
            "owner-token-trailing-lf",
            {"AGENT_BRIDGE_OWNER_TOKEN": ("a" * 64) + "\n"},
            "claim_owner_mismatch",
        ),
        (
            "codex",
            "codex",
            "loose-owner-process-start",
            {
                "AGENT_BRIDGE_OWNER_PROCESS_START_UTC": (
                    "2026-07-28 00:00:00Z"
                )
            },
            "claim_owner_mismatch",
        ),
    ],
    ids=[
        "requested-agent",
        "bound-agent",
        "task-id",
        "owner-session",
        "owner-token",
        "owner-process-start",
    ],
)
def test_claim_rejects_noncanonical_identity_values_before_runtime_write(
    tmp_path: Path,
    requested_agent: str,
    bound_agent: str,
    task_id: str,
    extra_env: dict[str, str] | None,
    expected_error: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        requested_agent,
        "-TaskId",
        task_id,
        "-Summary",
        "must reject before runtime write",
        bound_agent=bound_agent,
        extra_env=extra_env,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert not runtime_root.exists()


def test_claim_accepts_canonical_fractional_offset_owner_process_start(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "canonical-owner-process-start"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "canonical offset timestamp is valid",
        bound_agent="codex",
        extra_env={
            "AGENT_BRIDGE_OWNER_PROCESS_START_UTC": (
                "2026-07-28T03:04:05.1234567+03:00"
            )
        },
    )

    assert completed.returncode == 0, completed.stderr
    claim = json.loads(
        (
            runtime_root / "work_queue" / "claims" / f"{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert claim["owner_process_start_utc"].startswith(
        "2026-07-28T00:04:05.1234567"
    )


@pytest.mark.parametrize(
    "stale_env",
    [None, "123"],
    ids=["stale-env-unset", "stale-env-set"],
)
def test_powershell_claim_default_lease_matches_python_default(
    tmp_path: Path,
    stale_env: str | None,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "default-lease-parity"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "omitted lease uses shared default",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_STALE_LEASE_SECONDS": stale_env},
    )

    assert completed.returncode == 0, completed.stderr
    claim = json.loads(
        (
            runtime_root / "work_queue" / "claims" / f"{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    claimed_at = datetime.fromisoformat(claim["claimed_at_utc"])
    expires_at = datetime.fromisoformat(claim["claim_lease_expires_utc"])
    assert claim["lease_seconds"] == 900
    assert (expires_at - claimed_at).total_seconds() == 900


@pytest.mark.parametrize("lease_seconds", ["0", "-1"])
def test_powershell_claim_rejects_explicit_nonpositive_lease_before_write(
    tmp_path: Path,
    lease_seconds: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        f"invalid-lease-{lease_seconds.replace('-', 'negative')}",
        "-Summary",
        "must reject explicit nonpositive lease",
        "-LeaseSeconds",
        lease_seconds,
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "lease_seconds must be a positive Int32" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "write_scope",
    ["", "   ", ", ,"],
    ids=["empty", "whitespace", "commas-only"],
)
def test_powershell_write_claim_rejects_empty_normalized_scope_before_write(
    tmp_path: Path,
    write_scope: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "empty-normalized-write-scope",
        "-Summary",
        "must reject unscoped write claim",
        "-Mode",
        "write",
        "-WriteScope",
        write_scope,
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "write claims require at least one -WriteScope path" in (
        completed.stderr
    )
    assert not runtime_root.exists()


def test_heartbeat_skips_duplicate_task_group_but_refreshes_unique_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    duplicate_task = "duplicate-heartbeat-group"
    unique_task = "unique-heartbeat-claim"

    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        duplicate_task,
        "-Summary",
        "heartbeat duplicate grouping fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr

    claims_dir = runtime_root / "work_queue" / "claims"
    duplicate_path = claims_dir / f"{duplicate_task}.json"
    duplicate_shadow = claims_dir / "duplicate-heartbeat-shadow.json"
    shutil.copy2(duplicate_path, duplicate_shadow)
    unique_path = claims_dir / f"{unique_task}.json"
    unique_claim = json.loads(duplicate_path.read_text(encoding="utf-8"))
    unique_claim["task_id"] = unique_task
    unique_claim["summary"] = "unique heartbeat grouping fixture"
    unique_claim["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
    unique_claim["claim_lease_expires_utc"] = "2026-07-28T00:15:00Z"
    unique_path.write_text(json.dumps(unique_claim), encoding="utf-8")
    duplicate_before = {
        path.name: path.read_bytes()
        for path in (duplicate_path, duplicate_shadow)
    }

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "duplicate-group-heartbeat-event",
        bound_agent="codex",
    )

    assert heartbeat.returncode == 0, heartbeat.stderr
    assert "duplicate active claim records for exact task_id" in (
        heartbeat.stdout + heartbeat.stderr
    )
    assert {
        path.name: path.read_bytes()
        for path in (duplicate_path, duplicate_shadow)
    } == duplicate_before
    refreshed_unique = json.loads(unique_path.read_text(encoding="utf-8"))
    assert refreshed_unique["last_heartbeat_utc"] != "2026-07-28T00:00:00Z"
    assert (
        refreshed_unique["claim_lease_expires_utc"]
        != "2026-07-28T00:15:00Z"
    )


def test_heartbeat_stored_agent_comparison_is_case_sensitive(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "case-sensitive-heartbeat-agent"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "case-sensitive stored agent fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr

    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["agent"] = "Codex"
    claim["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    original = claim_path.read_bytes()

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "case-sensitive-agent-heartbeat-event",
        bound_agent="codex",
    )

    assert heartbeat.returncode == 0, heartbeat.stderr
    assert claim_path.read_bytes() == original


def test_release_missing_runtime_root_refuses_without_writing(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "missing-release-task",
        bound_agent="codex",
    )

    assert released.returncode == 2
    assert "no active claim found for task" in released.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "task_id",
    [
        "a",
        "invalid task",
        "a//b",
        "a/./b",
        "a/../b",
        "a" * 122,
    ],
    ids=[
        "too-short",
        "space",
        "empty-segment",
        "dot-segment",
        "parent-segment",
        "too-long",
    ],
)
def test_powershell_claim_and_release_reject_python_invalid_task_ids_before_write(
    tmp_path: Path,
    task_id: str,
) -> None:
    claim_root = tmp_path / "claim-runtime"
    release_root = tmp_path / "release-runtime"

    claimed = _run_script(
        claim_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "must reject invalid task id",
        bound_agent="codex",
    )
    released = _run_script(
        release_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "task_id invalid" in claimed.stderr
    assert released.returncode != 0
    assert "task_id invalid" in released.stderr
    assert not claim_root.exists()
    assert not release_root.exists()


def test_slash_task_powershell_claim_python_force_heartbeat_release(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "powershell/slash-to-python"
    claim_basename = _slash_claim_basename(task_id)
    claim_path = (
        runtime_root / "work_queue" / "claims" / f"{claim_basename}.json"
    )
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "PowerShell slash claim consumed by Python",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    assert claim_path.is_file()
    assert not (
        runtime_root
        / "work_queue"
        / "claims"
        / "powershell_slash-to-python.json"
    ).exists()
    original = json.loads(claim_path.read_text(encoding="utf-8"))
    original_diagnostics = {
        name: original[name]
        for name in ("writer_pid", "writer_pid_semantics", "cwd", "git_branch")
        if original.get(name)
    }

    now = datetime.now(timezone.utc)
    refreshed_before_force = python_heartbeat(
        agent="codex",
        task_id=task_id,
        bridge_root=runtime_root,
        now_utc=now,
    )
    assert refreshed_before_force.task_id == task_id
    after_first_heartbeat = json.loads(claim_path.read_text(encoding="utf-8"))
    assert {
        name: after_first_heartbeat[name] for name in original_diagnostics
    } == original_diagnostics

    forced = python_claim_task(
        agent="codex",
        task_id=task_id,
        summary="Python force-updated the discovered slash claim",
        bridge_root=runtime_root,
        now_utc=now + timedelta(seconds=1),
        force=True,
    )
    assert forced.task_id == task_id
    after_force = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after_force["summary"] == "Python force-updated the discovered slash claim"
    assert len(list(claim_path.parent.glob("*.json"))) == 1

    refreshed = python_heartbeat(
        agent="codex",
        task_id=task_id,
        bridge_root=runtime_root,
        now_utc=now + timedelta(seconds=2),
    )
    assert refreshed.task_id == task_id

    released = python_release_task(
        agent="codex",
        task_id=task_id,
        bridge_root=runtime_root,
        now_utc=now + timedelta(seconds=3),
    )
    assert released.task_id == task_id
    assert not claim_path.exists()
    done_files = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(done_files) == 1
    assert done_files[0].name.startswith(f"{claim_basename}-")


def test_slash_task_python_claim_powershell_force_heartbeat_release(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "python/slash-to-powershell"
    claim_basename = _slash_claim_basename(task_id)
    claim_path = (
        runtime_root / "work_queue" / "claims" / f"{claim_basename}.json"
    )
    python_claim_task(
        agent="codex",
        task_id=task_id,
        summary="Python slash claim consumed by PowerShell",
        bridge_root=runtime_root,
        now_utc=datetime.now(timezone.utc),
    )
    assert claim_path.is_file()
    before = json.loads(claim_path.read_text(encoding="utf-8"))

    forced = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "PowerShell force-updated the discovered slash claim",
        "-Force",
        bound_agent="codex",
    )
    assert forced.returncode == 0, forced.stderr
    assert claim_path.is_file()
    assert len(list(claim_path.parent.glob("*.json"))) == 1
    after_force = json.loads(claim_path.read_text(encoding="utf-8"))
    assert (
        after_force["summary"]
        == "PowerShell force-updated the discovered slash claim"
    )
    assert after_force["owner_session_id"] == before["owner_session_id"]
    assert after_force["owner_token_sha256"] == before["owner_token_sha256"]
    assert not (
        claim_path.parent / "python_slash-to-powershell.json"
    ).exists()

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )
    assert heartbeat.returncode == 0, heartbeat.stderr
    after_heartbeat = json.loads(claim_path.read_text(encoding="utf-8"))
    assert (
        after_heartbeat["last_heartbeat_utc"]
        != after_force["last_heartbeat_utc"]
    )

    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )
    assert released.returncode == 0, released.stderr
    assert not claim_path.exists()
    done_files = list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert len(done_files) == 1
    assert done_files[0].name.startswith(f"{claim_basename}.")


def test_powershell_claim_and_release_refuse_duplicate_exact_task_records(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "duplicate/slash-task"
    claim_basename = _slash_claim_basename(task_id)
    claim_path = (
        runtime_root / "work_queue" / "claims" / f"{claim_basename}.json"
    )
    python_claim_task(
        agent="codex",
        task_id=task_id,
        summary="duplicate exact task fixture",
        bridge_root=runtime_root,
        now_utc=datetime.now(timezone.utc),
    )
    shadow_path = claim_path.parent / "duplicate-shadow.json"
    shutil.copy2(claim_path, shadow_path)
    original = {
        path.name: path.read_text(encoding="utf-8")
        for path in (claim_path, shadow_path)
    }

    forced = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "must refuse duplicate task records",
        "-Force",
        bound_agent="codex",
    )
    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )

    assert forced.returncode != 0
    assert "duplicate active claim records for exact task_id" in forced.stderr
    assert released.returncode != 0
    assert "duplicate active claim records for exact task_id" in released.stderr
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in (claim_path, shadow_path)
    } == original


def test_python_and_powershell_share_legacy_expiry_decisions(
    tmp_path: Path,
) -> None:
    python_root = tmp_path / "python-runtime"
    powershell_root = tmp_path / "powershell-runtime"
    fixture_now = datetime.now(timezone.utc)

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    fixtures = {
        "legacy-parity-stale": {
            "claimed_at_utc": iso(fixture_now - timedelta(seconds=600)),
            "last_heartbeat_utc": iso(fixture_now + timedelta(days=1)),
            "owner_pid": "not-a-pid",
        },
        "legacy-parity-fresh": {
            "claimed_at_utc": iso(fixture_now - timedelta(seconds=60)),
            "last_heartbeat_utc": iso(fixture_now + timedelta(days=1)),
            "owner_pid": os.getpid(),
            "owner_process_start_utc": "not-a-time",
        },
    }
    for runtime_root in (python_root, powershell_root):
        claims_dir = runtime_root / "work_queue" / "claims"
        claims_dir.mkdir(parents=True)
        for task_id, overrides in fixtures.items():
            payload = {
                "agent": "codex",
                "task_id": task_id,
                "summary": "shared legacy expiry fixture",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "legacy-parity",
                "claimed_at_utc": "",
                "last_heartbeat_utc": "",
                "lease_seconds": 300,
                "claim_lease_expires_utc": iso(
                    fixture_now + timedelta(days=1)
                ),
                "owner_session_id": "pytest-session",
                "owner_token_sha256": hashlib.sha256(
                    ("a" * 64).encode("utf-8")
                ).hexdigest(),
                "owner_pid": os.getpid(),
                "owner_process_start_utc": "2026-07-28T00:00:00Z",
                "writer_pid": 4242,
                "writer_pid_semantics": "diagnostic_only",
                "cwd": "C:\\diagnostic-fixture",
                "git_branch": "diagnostic-branch",
            }
            payload.update(overrides)
            (claims_dir / f"{task_id}.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

    python_archived = python_archive_stale_claims(
        bridge_root=python_root,
        now_utc=fixture_now,
        max_age_seconds=300,
        apply=True,
    )
    powershell_sweep = _run_script(
        powershell_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "300",
        "-Quiet",
    )

    assert [item.claim.task_id for item in python_archived] == [
        "legacy-parity-stale"
    ]
    assert powershell_sweep.returncode == 0, powershell_sweep.stderr
    for runtime_root in (python_root, powershell_root):
        claims_dir = runtime_root / "work_queue" / "claims"
        assert not (claims_dir / "legacy-parity-stale.json").exists()
        assert (claims_dir / "legacy-parity-fresh.json").is_file()
        done_files = list(
            (runtime_root / "work_queue" / "done").glob(
                "legacy-parity-stale*.json"
            )
        )
        assert len(done_files) == 1
        archived = json.loads(done_files[0].read_text(encoding="utf-8-sig"))
        assert "legacy tokenless claim claimed_at_utc" in archived[
            "release_reason"
        ]
        assert archived["last_heartbeat_utc"] == fixtures[
            "legacy-parity-stale"
        ]["last_heartbeat_utc"]
        assert archived["claim_lease_expires_utc"] == iso(
            fixture_now + timedelta(days=1)
        )
        assert archived["writer_pid"] == 4242
        assert archived["writer_pid_semantics"] == "diagnostic_only"
        assert archived["cwd"] == "C:\\diagnostic-fixture"
        assert archived["git_branch"] == "diagnostic-branch"


def test_powershell_stale_sweep_treats_out_of_range_owner_offset_as_legacy(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    task_id = "owner-offset-out-of-range"
    payload = {
        "agent": "codex",
        "task_id": task_id,
        "summary": "out-of-range offset must be legacy",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "offset-review",
        "claimed_at_utc": (
            now - timedelta(seconds=600)
        ).isoformat().replace("+00:00", "Z"),
        "last_heartbeat_utc": (
            now + timedelta(days=1)
        ).isoformat().replace("+00:00", "Z"),
        "lease_seconds": 300,
        "claim_lease_expires_utc": (
            now + timedelta(days=1)
        ).isoformat().replace("+00:00", "Z"),
        "owner_session_id": "pytest-session",
        "owner_token_sha256": hashlib.sha256(
            ("a" * 64).encode("utf-8")
        ).hexdigest(),
        "owner_pid": os.getpid(),
        "owner_process_start_utc": "2026-07-28T00:00:00+15:00",
    }
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "300",
        "-Quiet",
    )

    assert swept.returncode == 0, swept.stderr
    assert not claim_path.exists()


def test_powershell_stale_sweep_clamps_max_timestamp_lease_overflow(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "max-timestamp-lease"
    payload = {
        "agent": "codex",
        "task_id": task_id,
        "summary": "maximum timestamp must not abort sweep",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "max-time-review",
        "claimed_at_utc": "9999-12-31T23:59:59Z",
        "last_heartbeat_utc": "9999-12-31T23:59:59Z",
        "lease_seconds": 300,
        "claim_lease_expires_utc": "",
        "owner_session_id": "pytest-session",
        "owner_token_sha256": hashlib.sha256(
            ("a" * 64).encode("utf-8")
        ).hexdigest(),
        "owner_pid": os.getpid(),
        "owner_process_start_utc": "2026-07-28T00:00:00Z",
    }
    claim_path = claims_dir / f"{task_id}.json"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "300",
        "-Quiet",
    )

    assert swept.returncode == 0, swept.stderr
    assert claim_path.is_file()
    assert not (runtime_root / "work_queue" / "done").exists()


@pytest.mark.parametrize("threshold", ["0", "-1"])
def test_powershell_stale_sweep_rejects_nonpositive_threshold_before_write(
    tmp_path: Path,
    threshold: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        threshold,
        "-Quiet",
    )

    assert swept.returncode != 0
    assert "StaleSeconds must be a positive Int32" in swept.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "write_scope",
    ["///", "\\", " / , \\\\ "],
    ids=["slashes", "backslash", "comma-packed-empty"],
)
def test_powershell_write_claim_rejects_overlap_empty_scope_before_write(
    tmp_path: Path,
    write_scope: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "overlap-empty-write-scope",
        "-Summary",
        "scope must normalize to a path",
        "-Mode",
        "write",
        "-WriteScope",
        write_scope,
        bound_agent="codex",
    )

    assert completed.returncode != 0
    assert "write claims require at least one -WriteScope path" in (
        completed.stderr
    )
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "stored_scope",
    [
        ["tools/foo.py, tests/bar.py"],
        [],
        [", ,"],
        None,
        [123],
    ],
    ids=["comma-packed", "empty", "commas-only", "null", "nonstring"],
)
def test_powershell_claim_refuses_legacy_unsafe_stored_write_scope(
    tmp_path: Path,
    stored_scope: object,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "legacy-write-scope-holder"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "legacy scope fixture",
        "-Mode",
        "write",
        "-WriteScope",
        "placeholder",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["write_scope"] = stored_scope
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    refused = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "new-overlapping-write",
        "-Summary",
        "must fail closed",
        "-Mode",
        "write",
        "-WriteScope",
        "tests/bar.py",
        bound_agent="codex",
    )

    assert refused.returncode == 3
    assert "write-scope conflict" in refused.stderr
    assert claim_path.read_bytes() == before
    assert not (
        runtime_root
        / "work_queue"
        / "claims"
        / "new-overlapping-write.json"
    ).exists()


def test_powershell_uppercase_write_mode_is_canonical_and_blocks_python(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "uppercase-write-holder"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "uppercase mode fixture",
        "-Mode",
        "WRITE",
        "-WriteScope",
        "tools",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    assert json.loads(claim_path.read_text(encoding="utf-8"))["mode"] == "write"

    with pytest.raises(WorkQueueError, match="write-scope conflict"):
        python_claim_task(
            agent="codex-2",
            task_id="python-overlap-uppercase-mode",
            summary="must observe canonical PS write",
            mode="write",
            write_scope=["tools/example.py"],
            bridge_root=runtime_root,
        )


@pytest.mark.parametrize(
    ("lease_case", "lease_value"),
    [
        ("missing", None),
        ("null", None),
        ("boolean", True),
        ("float", 1.0),
        ("overflow", 2**31),
    ],
)
def test_powershell_heartbeat_canonicalizes_invalid_or_missing_lease(
    tmp_path: Path,
    lease_case: str,
    lease_value: object,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"heartbeat-default-lease-{lease_case}"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "heartbeat lease canonicalization fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    if lease_case == "missing":
        payload.pop("lease_seconds", None)
    else:
        payload["lease_seconds"] = lease_value
    payload.pop("claim_lease_expires_utc", None)
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    heartbeat_result = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        f"{task_id}-event",
        bound_agent="codex",
    )

    assert heartbeat_result.returncode == 0, heartbeat_result.stderr
    refreshed = json.loads(claim_path.read_text(encoding="utf-8"))
    assert type(refreshed["lease_seconds"]) is int
    assert refreshed["lease_seconds"] == 900
    heartbeat_at = datetime.fromisoformat(
        refreshed["last_heartbeat_utc"].replace("Z", "+00:00")
    )
    expires_at = datetime.fromisoformat(
        refreshed["claim_lease_expires_utc"].replace("Z", "+00:00")
    )
    assert (expires_at - heartbeat_at).total_seconds() == 900


@pytest.mark.parametrize(
    "owner_process_start_utc",
    [
        "2026-07-28T00:00:00z",
        "2026-07-28T00:00:00+0300",
    ],
    ids=["lowercase-z", "compact-offset"],
)
def test_powershell_heartbeat_rejects_noncanonical_stored_owner_timestamp(
    tmp_path: Path,
    owner_process_start_utc: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "heartbeat-noncanonical-owner-time"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "owner timestamp validation fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["owner_process_start_utc"] = owner_process_start_utc
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    heartbeat_result = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        f"{task_id}-event",
        bound_agent="codex",
    )

    assert heartbeat_result.returncode == 0, heartbeat_result.stderr
    assert claim_path.read_bytes() == before


def test_powershell_stale_case_insensitive_archive_collision_is_zero_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    template_task = "stale-case-template"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        template_task,
        "-Summary",
        "case collision template",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claims_dir = runtime_root / "work_queue" / "claims"
    template_path = claims_dir / f"{template_task}.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template_path.unlink()
    paths = (
        claims_dir / "legacy-upper.json",
        claims_dir / "legacy-lower.json",
    )
    for path, task_id in zip(paths, ("CaseTask", "casetask"), strict=True):
        payload = dict(template)
        payload["task_id"] = task_id
        payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
        payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
        payload["lease_seconds"] = 1
        payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in paths}

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
    )

    assert swept.returncode != 0
    assert "stale archive destination collision" in swept.stderr
    assert {path.name: path.read_bytes() for path in paths} == before
    assert not (runtime_root / "work_queue" / "done").exists()


def test_powershell_stale_existing_directory_collision_is_zero_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    paths = (
        claims_dir / "legacy-first.json",
        claims_dir / "legacy-second.json",
    )
    owner_digest = hashlib.sha256(("a" * 64).encode("utf-8")).hexdigest()
    for path, task_id in zip(
        paths,
        ("directory-collision-first", "directory-collision-second"),
        strict=True,
    ):
        payload = {
            "agent": "codex",
            "task_id": task_id,
            "summary": "directory collision fixture",
            "mode": "read-only",
            "write_scope": [],
            "run_id": "pytest",
            "claimed_at_utc": "2026-07-28T00:00:00Z",
            "last_heartbeat_utc": "2026-07-28T00:00:00Z",
            "lease_seconds": 1,
            "claim_lease_expires_utc": "2026-07-28T00:00:01Z",
            "owner_session_id": "pytest-session",
            "owner_token_sha256": owner_digest,
            "owner_pid": os.getpid(),
            "owner_process_start_utc": "2026-07-28T00:00:00Z",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in paths}
    done_dir = runtime_root / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    start = datetime.now(timezone.utc)
    for offset in range(120):
        stamp = (start + timedelta(seconds=offset)).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        (
            done_dir
            / f"directory-collision-second.{stamp}.stale_lease.json"
        ).mkdir()

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
    )

    assert swept.returncode != 0
    assert "stale archive destination already exists" in swept.stderr
    assert {path.name: path.read_bytes() for path in paths} == before
    assert not list(done_dir.glob("directory-collision-first.*.json"))


def test_powershell_stale_sweep_waits_for_python_mutation_lock(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    task_id = "powershell-freshened-before-locked-sweep"
    claim_path = claims_dir / f"{task_id}.json"
    payload = {
        "agent": "codex",
        "task_id": task_id,
        "summary": "cross-runtime lock race fixture",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "pytest",
        "claimed_at_utc": "2026-07-28T00:00:00Z",
        "last_heartbeat_utc": "2026-07-28T00:00:00Z",
        "lease_seconds": 900,
        "claim_lease_expires_utc": "2026-07-28T00:15:00Z",
        "owner_session_id": "pytest-session",
        "owner_token_sha256": hashlib.sha256(
            ("a" * 64).encode("utf-8")
        ).hexdigest(),
        "owner_pid": os.getpid(),
        "owner_process_start_utc": "2026-07-28T00:00:00Z",
    }
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    command = [
        _powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"),
        "-StaleSeconds",
        "1",
        "-Quiet",
    ]

    with _claim_mutation_lock(runtime_root):
        process = subprocess.Popen(
            command,
            cwd=runtime_root.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.2)
        assert process.poll() is None
        refreshed_at = datetime.now(timezone.utc)
        payload["last_heartbeat_utc"] = refreshed_at.isoformat().replace(
            "+00:00",
            "Z",
        )
        payload["claim_lease_expires_utc"] = (
            refreshed_at + timedelta(seconds=900)
        ).isoformat().replace("+00:00", "Z")
        claim_path.write_text(json.dumps(payload), encoding="utf-8")

    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    assert claim_path.is_file()
    assert not (runtime_root / "work_queue" / "done").exists()


def test_powershell_stale_sweep_rolls_back_archive_when_claim_delete_fails(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "stale-delete-denied"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "delete denial rollback fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
    payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
    payload["lease_seconds"] = 1
    payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before_claim = claim_path.read_bytes()
    wrapper = tmp_path / "deny-delete-stale-sweep.ps1"
    wrapper.write_text(
        """
param(
    [Parameter(Mandatory)] [string] $ClaimPath,
    [Parameter(Mandatory)] [string] $SweepPath
)
$stream = [System.IO.File]::Open(
    $ClaimPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
try {
    & $SweepPath -StaleSeconds 1 -Quiet | Out-Null
} finally {
    $stream.Dispose()
}
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)

    swept = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-ClaimPath",
            str(claim_path),
            "-SweepPath",
            str(BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert swept.returncode != 0
    assert "stale claim sweep incomplete" in swept.stderr
    assert claim_path.read_bytes() == before_claim
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.stale_lease.json"))
    assert not list(done_dir.glob("*.tmp.*"))


def test_powershell_stale_sweep_retains_nonstring_task_identity(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "malformed-nonstring-task.json"
    payload = {
        "agent": "codex",
        "task_id": 1.0,
        "summary": "nonstring task identity fixture",
        "mode": "read-only",
        "write_scope": [],
        "run_id": "pytest",
        "claimed_at_utc": "2026-07-28T00:00:00Z",
        "last_heartbeat_utc": "2026-07-28T00:00:00Z",
        "lease_seconds": 1,
        "claim_lease_expires_utc": "2026-07-28T00:00:01Z",
    }
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    before = claim_path.read_bytes()

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
    )

    assert swept.returncode == 0, swept.stderr
    assert claim_path.read_bytes() == before
    assert not (runtime_root / "work_queue" / "done").exists()


def test_powershell_release_destination_collision_is_zero_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "release-destination-collision"
    created = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "release collision fixture",
        bound_agent="codex",
    )
    assert created.returncode == 0, created.stderr
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    before_claim = claim_path.read_bytes()
    done_dir = runtime_root / "work_queue" / "done"
    done_dir.mkdir(parents=True)
    sentinel = b"preexisting-release-archive"
    start = datetime.now(timezone.utc)
    sentinel_paths: list[Path] = []
    for offset in range(120):
        stamp = (start + timedelta(seconds=offset)).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        path = done_dir / f"{task_id}.{stamp}.done.json"
        path.write_bytes(sentinel)
        sentinel_paths.append(path)

    released = _run_script(
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        bound_agent="codex",
    )

    assert released.returncode == 3
    assert "release archive destination already exists" in released.stderr
    assert claim_path.read_bytes() == before_claim
    assert all(path.read_bytes() == sentinel for path in sentinel_paths)
    assert not list(claim_path.parent.glob(f"{task_id}.json.tmp.*"))
    assert not list(claim_path.parent.glob(f"{task_id}.json.bak.*"))


def test_powershell_release_backup_restore_uses_valid_displaced_path(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "active-claim.json"
    backup_path = tmp_path / "active-claim.json.bak"
    original_path.write_bytes(b"released-state")
    backup_path.write_bytes(b"original-active-state")
    wrapper = tmp_path / "restore-release-backup.ps1"
    wrapper.write_text(
        """
param(
    [Parameter(Mandatory)] [string] $IdentityPath,
    [Parameter(Mandatory)] [string] $OriginalPath,
    [Parameter(Mandatory)] [string] $BackupPath
)
. $IdentityPath
Restore-AgentBridgeFileBackup `
    -OriginalPath $OriginalPath `
    -BackupPath $BackupPath
""".strip(),
        encoding="utf-8",
    )

    restored = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-IdentityPath",
            str(BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"),
            "-OriginalPath",
            str(original_path),
            "-BackupPath",
            str(backup_path),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert restored.returncode == 0, restored.stderr
    assert original_path.read_bytes() == b"original-active-state"
    assert not backup_path.exists()
    assert not list(tmp_path.glob("*.rollback-displaced.*"))


def test_powershell_new_claim_publication_uses_closed_temp_contract() -> None:
    source = (BRIDGE_BIN / "Claim-AgentTask.ps1").read_text(encoding="utf-8")
    force_branch = source.index("if ($forceUpdateExisting)")
    create_branch = source.index("} else {", force_branch)
    create_finally = source.index("} finally {", create_branch)
    create_block = source[create_branch:create_finally]

    write_temp = (
        "[System.IO.File]::WriteAllText($tmpClaim, $json, $encoding)"
    )
    publish_temp = "[System.IO.File]::Move($tmpClaim, $claimPath)"
    assert write_temp in create_block
    assert publish_temp in create_block
    assert create_block.index(write_temp) < create_block.index(publish_temp)
    assert "FileMode]::CreateNew" not in create_block
    assert "Set-Content" not in create_block


@pytest.mark.skipif(
    os.name != "nt",
    reason="the locked-source replacement contract uses Windows sharing",
)
def test_atomic_update_preserves_destination_when_temp_source_is_locked(
    tmp_path: Path,
) -> None:
    destination_path = tmp_path / "active-claim.json"
    temp_path = tmp_path / "active-claim.json.tmp"
    destination_path.write_bytes(b"ORIGINAL")
    temp_path.write_bytes(b"NEW")
    wrapper = tmp_path / "update-claim-from-temp.ps1"
    wrapper.write_text(
        """
param(
    [Parameter(Mandatory)] [string] $IdentityPath,
    [Parameter(Mandatory)] [string] $TempPath,
    [Parameter(Mandatory)] [string] $DestinationPath
)
. $IdentityPath
Update-AgentBridgeFileFromTemp `
    -TempPath $TempPath `
    -DestinationPath $DestinationPath
""".strip(),
        encoding="utf-8",
    )

    with temp_path.open("rb"):
        updated = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "-IdentityPath",
                str(BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"),
                "-TempPath",
                str(temp_path),
                "-DestinationPath",
                str(destination_path),
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert updated.returncode != 0
        assert destination_path.read_bytes() == b"ORIGINAL"
        assert temp_path.read_bytes() == b"NEW"
        assert not list(tmp_path.glob("active-claim.json.bak.*"))
        assert not list(tmp_path.glob("*.rollback-displaced.*"))

    temp_path.unlink()


def test_powershell_heartbeat_refreshes_lease_when_event_writer_fails(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "heartbeat-event-failure"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(task_id=task_id),
    )
    before = json.loads(claim_path.read_text(encoding="utf-8"))

    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    shutil.copy2(
        BRIDGE_BIN / "Send-Liveness.ps1",
        isolated_bin / "Send-Liveness.ps1",
    )
    shutil.copy2(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1",
        isolated_bin / "AgentBridgeSessionIdentity.ps1",
    )
    (isolated_bin / "Write-AgentEvent.ps1").write_text(
        """
[CmdletBinding()]
param(
    [string] $Agent,
    [string] $Type,
    [string] $Status,
    [string] $Severity,
    [string] $To,
    [string] $Message,
    [string] $TaskId,
    [string[]] $Paths,
    [string] $Role,
    [string] $AgentUuid,
    [string[]] $Capabilities
)
throw 'injected event emit failure'
""".strip(),
        encoding="utf-8",
    )

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        task_id,
        bound_agent="codex",
        script_root=isolated_bin,
    )

    assert heartbeat.returncode != 0
    assert "injected event emit failure" in (
        heartbeat.stdout + heartbeat.stderr
    )
    after = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after["last_heartbeat_utc"] != before["last_heartbeat_utc"]
    assert (
        after["claim_lease_expires_utc"]
        != before["claim_lease_expires_utc"]
    )
    assert not list(claim_path.parent.glob(f"{claim_path.name}.tmp.*"))
    assert not list(claim_path.parent.glob(f"{claim_path.name}.bak.*"))


@pytest.mark.skipif(
    os.name != "nt",
    reason="the destination-lock failure uses Windows sharing semantics",
)
def test_powershell_heartbeat_lease_publish_failure_is_nonzero_and_zero_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "heartbeat-lease-publish-failure"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(task_id=task_id),
    )
    before = claim_path.read_bytes()

    with claim_path.open("rb"):
        heartbeat = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )

        assert heartbeat.returncode != 0
        assert "could not bump lease" in (
            heartbeat.stdout + heartbeat.stderr
        ).lower()
        assert claim_path.read_bytes() == before
        assert not list(claim_path.parent.glob(f"{claim_path.name}.tmp.*"))
        assert not list(claim_path.parent.glob(f"{claim_path.name}.bak.*"))
        assert not list(
            claim_path.parent.glob(f"{claim_path.name}.rollback-displaced.*")
        )


@pytest.mark.parametrize(
    "operation",
    ["force", "release", "heartbeat"],
)
def test_powershell_nonstring_stored_agent_never_authorizes_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"nonstring-agent-{operation}"
    payload = _owner_bound_claim_payload(task_id=task_id)
    payload["agent"] = ["codex"]
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )
    before = claim_path.read_bytes()

    if operation == "force":
        completed = _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            "-Summary",
            "must not coerce stored agent",
            "-Force",
            bound_agent="codex",
        )
    elif operation == "release":
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )
    else:
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )

    if operation in {"force", "release"}:
        assert completed.returncode != 0
    assert claim_path.read_bytes() == before
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.json"))


@pytest.mark.parametrize(
    "stored_mode_case",
    ["missing", "nonstring"],
)
def test_powershell_write_claim_fails_closed_on_malformed_stored_mode(
    tmp_path: Path,
    stored_mode_case: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    existing_task = f"malformed-mode-holder-{stored_mode_case}"
    payload = _owner_bound_claim_payload(
        task_id=existing_task,
        mode="write",
        write_scope=["tests/shared.py"],
    )
    if stored_mode_case == "missing":
        payload.pop("mode")
    else:
        payload["mode"] = ["write"]
    existing_path = _write_claim_payload(
        runtime_root,
        filename=f"{existing_task}.json",
        payload=payload,
    )
    before = existing_path.read_bytes()
    new_task = f"malformed-mode-request-{stored_mode_case}"

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        new_task,
        "-Summary",
        "must fail closed against malformed stored mode",
        "-Mode",
        "write",
        "-WriteScope",
        "tests/shared.py",
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "stored mode is missing, non-string, or invalid" in (
        claimed.stdout + claimed.stderr
    )
    assert existing_path.read_bytes() == before
    assert not (
        runtime_root / "work_queue" / "claims" / f"{new_task}.json"
    ).exists()


def test_powershell_stale_sweep_retains_invalid_unicode_task_identity(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    payload = _owner_bound_claim_payload(task_id="unicode-placeholder")
    payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
    payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
    payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
    raw_json = json.dumps(payload).replace(
        '"unicode-placeholder"',
        r'"invalid\ud83d\ude00task"',
    )
    claim_path = runtime_root / "work_queue" / "claims" / "invalid-unicode.json"
    claim_path.parent.mkdir(parents=True)
    claim_path.write_bytes(raw_json.encode("utf-8"))
    before = claim_path.read_bytes()

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
    )

    assert swept.returncode == 0, swept.stderr
    assert claim_path.read_bytes() == before
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.stale_lease.json"))


@pytest.mark.parametrize(
    "operation",
    ["force", "release", "heartbeat", "stale"],
)
def test_preferred_path_collision_refuses_mutation_without_work_queue_writes(
    tmp_path: Path,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "preferred/path-collision"
    payload = _owner_bound_claim_payload(task_id=task_id)
    legacy_path = _write_claim_payload(
        runtime_root,
        filename="legacy-target-record.json",
        payload=payload,
    )
    claims_dir = legacy_path.parent
    preferred_path = claims_dir / f"{_slash_claim_basename(task_id)}.json"
    blocker = dict(payload)
    blocker["agent"] = "other-agent"
    blocker["task_id"] = "different-logical-task"
    blocker["summary"] = "preferred path collision blocker"
    preferred_path.write_text(json.dumps(blocker), encoding="utf-8")
    mutation_lock = runtime_root / "work_queue" / ".claims.mutation.lock"
    mutation_lock.touch()
    before = _work_queue_file_snapshot(runtime_root)
    done_existed_before = (
        runtime_root / "work_queue" / "done"
    ).exists()

    if operation == "force":
        completed = _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            "-Summary",
            "must refuse preferred path collision",
            "-Force",
            bound_agent="codex",
        )
    elif operation == "release":
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )
    elif operation == "heartbeat":
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )
    else:
        completed = _run_script(
            runtime_root,
            "Invoke-StaleClaimSweep.ps1",
            "-StaleSeconds",
            "1",
            "-Quiet",
            bound_agent="codex",
        )

    assert completed.returncode != 0
    assert "claim filename collision at preferred path" in (
        completed.stdout + completed.stderr
    )
    assert _work_queue_file_snapshot(runtime_root) == before
    assert (
        runtime_root / "work_queue" / "done"
    ).exists() is done_existed_before
