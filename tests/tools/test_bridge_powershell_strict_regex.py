from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = ROOT / ".agent-bridge" / "bin"
PROFILE_UUID = "e1fdad8c-d7f8-489f-a6fe-5b1787dbaeb4"
RCO_SHA = "a" * 40


def _powershells() -> list[str]:
    executables: list[str] = []
    seen: set[str] = set()
    for name in ("pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(name)
        if executable is None:
            continue
        key = os.path.normcase(str(Path(executable).resolve())).lower()
        if key in seen:
            continue
        seen.add(key)
        executables.append(executable)
    return executables


POWERSHELLS = _powershells() or [
    pytest.param("", marks=pytest.mark.skip(reason="PowerShell is required"))
]


def _bridge_env(runtime_root: Path, *, agent: str = "codex") -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_ROOT",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        env.pop(name, None)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    env["AGENT_BRIDGE_AGENT"] = agent
    env["AGENT_BRIDGE_OWNER_SESSION_ID"] = "strict-regex-test-session"
    env["AGENT_BRIDGE_OWNER_TOKEN"] = "d" * 64
    env["AGENT_BRIDGE_OWNER_PID"] = str(os.getpid())
    env["AGENT_BRIDGE_OWNER_PROCESS_START_UTC"] = "2026-07-31T00:00:00Z"
    return env


def _run_bridge_script(
    powershell: str,
    runtime_root: Path,
    script_name: str,
    *args: str,
    agent: str = "codex",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_BIN / script_name),
            *args,
        ],
        cwd=ROOT,
        env=_bridge_env(runtime_root, agent=agent),
        check=False,
        capture_output=True,
        text=True,
    )


def test_authority_regex_literals_do_not_use_line_tolerant_dollar_anchor() -> None:
    entry_points = (
        "AgentBridgeSessionIdentity.ps1",
        "Claim-AgentTask.ps1",
        "Get-BridgeNextAction.ps1",
        "Invoke-StaleClaimSweep.ps1",
        "Release-AgentTask.ps1",
        "Send-Liveness.ps1",
        "Write-AgentEvent.ps1",
    )

    offenders: dict[str, list[str]] = {}
    for script_name in entry_points:
        source = (BRIDGE_BIN / script_name).read_text(encoding="utf-8")
        matches = re.findall(r"'(\^[^'\r\n]*\$)'", source)
        if matches:
            offenders[script_name] = matches

    assert offenders == {}


def test_format_operator_never_binds_only_last_concatenated_fragment() -> None:
    entry_points = (
        "AgentBridgeSessionIdentity.ps1",
        "Claim-AgentTask.ps1",
        "Get-BridgeNextAction.ps1",
        "Invoke-StaleClaimSweep.ps1",
        "Release-AgentTask.ps1",
        "Send-Liveness.ps1",
        "Write-AgentEvent.ps1",
    )
    unsafe_format = re.compile(
        r'"\s*\+\s*\r?\n\s*"[^"\r\n]*"\s+-f'
    )

    offenders = {
        script_name: unsafe_format.findall(
            (BRIDGE_BIN / script_name).read_text(encoding="utf-8")
        )
        for script_name in entry_points
    }

    assert {name: matches for name, matches in offenders.items() if matches} == {}


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("script_name", "base_args"),
    (
        (
            "Claim-AgentTask.ps1",
            (
                "-Agent",
                "codex",
                "-TaskId",
                "strict-run-id-claim",
                "-Summary",
                "trailing LF must fail before claim mutation",
            ),
        ),
        (
            "Release-AgentTask.ps1",
            (
                "-Agent",
                "codex",
                "-TaskId",
                "strict-run-id-release",
            ),
        ),
        (
            "Write-AgentEvent.ps1",
            (
                "-Agent",
                "codex",
                "-Type",
                "status",
                "-Status",
                "audit",
                "-Message",
                "trailing LF must fail before event mutation",
            ),
        ),
    ),
)
def test_bridge_writers_reject_run_id_with_trailing_lf_before_mutation(
    tmp_path: Path,
    powershell: str,
    script_name: str,
    base_args: tuple[str, ...],
) -> None:
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_bridge_script(
        powershell,
        runtime_root,
        script_name,
        *base_args,
        "-RunId",
        "valid-run-id\n",
    )

    assert completed.returncode != 0
    assert "run_id must match" in (completed.stdout + completed.stderr)
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_trusted_byte_identity_rejects_sha_with_trailing_lf(
    tmp_path: Path,
    powershell: str,
) -> None:
    identity_script = BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    expected_sha = hashlib.sha256(b"strict-bytes").hexdigest() + "\n"
    env = os.environ.copy()
    env["WD_IDENTITY_SCRIPT"] = str(identity_script)
    env["WD_EXPECTED_SHA"] = expected_sha
    command = (
        ". ([Environment]::GetEnvironmentVariable("
        "'WD_IDENTITY_SCRIPT', 'Process')); "
        "$bytes = [Text.Encoding]::UTF8.GetBytes('strict-bytes'); "
        "Assert-AgentBridgeTrustedBytesIdentity "
        "-Bytes $bytes "
        "-ExpectedSha256 ([Environment]::GetEnvironmentVariable("
        "'WD_EXPECTED_SHA', 'Process')) "
        "-ExpectedLength ([long]$bytes.Length) "
        "-Context 'strict expected SHA test'"
    )

    completed = subprocess.run(
        [
            powershell,
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
    assert "invalid expected snapshot identity" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rco_head_sha_with_trailing_lf_is_rejected_before_mutation(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "strict-rco-head-sha"
    payload = json.dumps(
        {
            "head": RCO_SHA + "\n",
            "canonical_task_id": task_id,
        },
        sort_keys=True,
    )

    completed = _run_bridge_script(
        powershell,
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        task_id,
        "-Status",
        "rco_pass",
        "-Message",
        f"strict RCO head {RCO_SHA}",
        "-PayloadJson",
        payload,
        agent="claude-rco-1",
    )

    assert completed.returncode != 0
    assert "rco_pass head must be lowercase 40-hex sha" in (
        completed.stdout + completed.stderr
    )
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_profile_uuid_with_trailing_lf_is_rejected_as_malformed(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    agents_dir = runtime_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "codex.json").write_text(
        json.dumps(
            {
                "agent_id": "codex",
                "agent_uuid": PROFILE_UUID + "\n",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    completed = _run_bridge_script(
        powershell,
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        "codex",
        "-Type",
        "status",
        "-Status",
        "audit",
        "-Message",
        "malformed profile UUID must fail closed",
    )

    assert completed.returncode != 0
    assert "bridge agent profile agent_uuid must be a UUID" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()
