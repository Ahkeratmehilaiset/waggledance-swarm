# SPDX-License-Identifier: BUSL-1.1
"""PowerShell bridge session-agent identity boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = ROOT / ".agent-bridge" / "bin"
BOUND_AGENT = "codex-tools-1"
OTHER_AGENT = "codex-lead-1"


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
            str(BRIDGE_BIN / script_name),
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
    assert "claim_owner_mismatch" in replaced.stderr
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

    assert foreign_release.returncode != 0
    assert "claim_owner_mismatch" in foreign_release.stderr
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
    assert release.returncode != 0
    assert "claim_owner_mismatch" in release.stderr
    assert json.loads(claim_path.read_text(encoding="utf-8")) == legacy_claim
