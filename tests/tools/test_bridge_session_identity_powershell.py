# SPDX-License-Identifier: BUSL-1.1
"""PowerShell bridge session-agent identity boundary tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
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
BOUND_AGENT_UUID = "7a8af68d-20bc-4598-9953-23c5dd98b102"
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


def _run_script(
    runtime_root: Path,
    script_name: str,
    *args: str,
    bound_agent: str | None = BOUND_AGENT,
    extra_env: dict[str, str | None] | None = None,
    script_root: Path = BRIDGE_BIN,
    powershell: str | None = None,
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
            powershell or _powershell(),
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


def _normalized_powershell_output(
    completed: subprocess.CompletedProcess[str],
) -> str:
    combined = completed.stdout + completed.stderr
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", combined)
    return " ".join(without_ansi.replace("|", " ").split())


def _wait_for_path(path: Path, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for path: {path}")


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


def _create_windows_junction(link_path: Path, target_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction semantics are required")
    created = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(link_path),
            str(target_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create test junction: {created.stderr}")


def _run_identity_command(
    tmp_path: Path,
    powershell: str,
    command: str,
    *,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WD_IDENTITY_SCRIPT"] = str(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    )
    env.update(extra_env)
    return subprocess.run(
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


def _run_identity_script_text(
    tmp_path: Path,
    powershell: str,
    script_text: str,
    *,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    script_path = tmp_path / "identity-regression.ps1"
    script_path.write_text(script_text, encoding="utf-8")
    env = os.environ.copy()
    env["WD_IDENTITY_SCRIPT"] = str(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    )
    env.update(extra_env)
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_v3_path_binding_loads_when_v2_type_is_already_cached(
    tmp_path: Path,
    powershell: str,
) -> None:
    parent = tmp_path / "pinned-parent"
    parent.mkdir()
    child = parent / "child.json"
    command = r"""
[void](Add-Type -TypeDefinition @'
public static class AgentBridgeExactFileDeleteV2
{
    public static string CachedContract() { return "old-v2"; }
}
'@)
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$pin = $null
try {
    $pin = Enter-AgentBridgeParentDirectoryPin `
        -ChildPath ([Environment]::GetEnvironmentVariable(
            'WD_CHILD_PATH', 'Process')) `
        -Context 'same-process V2 to V3 test'
    [pscustomobject]@{
        old_v2_loaded = $null -ne ('AgentBridgeExactFileDeleteV2' -as [type])
        new_v3_loaded = $null -ne ('AgentBridgeExactFileDeleteV3' -as [type])
        old_contract = [AgentBridgeExactFileDeleteV2]::CachedContract()
        pinned_parent = [string]$pin.parent_path
    } | ConvertTo-Json -Compress
} finally {
    Exit-AgentBridgeParentDirectoryPin -Pin $pin
}
"""

    completed = _run_identity_script_text(
        tmp_path,
        powershell,
        command,
        extra_env={"WD_CHILD_PATH": str(child)},
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["old_v2_loaded"] is True
    assert result["new_v3_loaded"] is True
    assert result["old_contract"] == "old-v2"
    assert Path(result["pinned_parent"]) == parent


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_append_rolls_back_raced_hardlink_before_throwing(
    tmp_path: Path,
    powershell: str,
) -> None:
    target = tmp_path / "events.jsonl"
    alias = tmp_path / "events-alias.jsonl"
    original = b"seed\n"
    target.write_bytes(original)
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$script:originalGuard = ${function:Assert-AgentBridgeRegularUnlinkedFile}
$script:guardCalls = 0
function Assert-AgentBridgeRegularUnlinkedFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [string] $Context = 'test target'
    )
    & $script:originalGuard @PSBoundParameters
    $script:guardCalls++
    if ($script:guardCalls -eq 2) {
        [void](New-Item `
            -ItemType HardLink `
            -Path ([Environment]::GetEnvironmentVariable(
                'WD_ALIAS_PATH', 'Process')) `
            -Target $LiteralPath `
            -ErrorAction Stop)
    }
}
$appendError = $null
try {
    Add-AgentBridgeBytesToRegularUnlinkedFile `
        -LiteralPath ([Environment]::GetEnvironmentVariable(
            'WD_TARGET_PATH', 'Process')) `
        -Bytes ([Text.Encoding]::UTF8.GetBytes("append`n")) `
        -Context 'raced hardlink append'
} catch {
    $appendError = $_.Exception.Message
}
[pscustomobject]@{
    guard_calls = $script:guardCalls
    append_error = $appendError
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_TARGET_PATH": str(target),
            "WD_ALIAS_PATH": str(alias),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["guard_calls"] >= 2
    assert "durably rolled back" in result["append_error"]
    assert target.read_bytes() == original
    assert alias.read_bytes() == original


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_append_rejects_parent_junction_swap_before_first_write(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    shared = bridge_root / "shared"
    moved_shared = bridge_root / "shared-before-race"
    external = tmp_path / "external"
    shared.mkdir(parents=True)
    external.mkdir()
    target = shared / "events.jsonl"
    external_target = external / "events.jsonl"
    original = b"victim\n"
    external_target.write_bytes(original)
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$script:originalGuard = ${function:Assert-AgentBridgePlainDirectory}
$script:guardCalls = 0
function Assert-AgentBridgePlainDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $Context
    )
    & $script:originalGuard @PSBoundParameters
    $script:guardCalls++
    if ($script:guardCalls -eq 1) {
        [IO.Directory]::Move(
            [Environment]::GetEnvironmentVariable('WD_SHARED', 'Process'),
            [Environment]::GetEnvironmentVariable('WD_MOVED_SHARED', 'Process')
        )
        [void](New-Item `
            -ItemType Junction `
            -Path ([Environment]::GetEnvironmentVariable(
                'WD_SHARED', 'Process')) `
            -Target ([Environment]::GetEnvironmentVariable(
                'WD_EXTERNAL', 'Process')) `
            -ErrorAction Stop)
    }
}
$appendError = $null
try {
    Add-AgentBridgeBytesToRegularUnlinkedFile `
        -LiteralPath ([Environment]::GetEnvironmentVariable(
            'WD_TARGET_PATH', 'Process')) `
        -Bytes ([Text.Encoding]::UTF8.GetBytes("append`n")) `
        -Context 'raced parent append'
} catch {
    $appendError = $_.Exception.Message
}
[pscustomobject]@{
    guard_calls = $script:guardCalls
    append_error = $appendError
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_SHARED": str(shared),
            "WD_MOVED_SHARED": str(moved_shared),
            "WD_EXTERNAL": str(external),
            "WD_TARGET_PATH": str(target),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["guard_calls"] == 1
    assert "reparse point" in result["append_error"]
    assert external_target.read_bytes() == original


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_trusted_create_new_rejects_parent_junction_swap(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    spool = bridge_root / "spool"
    moved_spool = bridge_root / "spool-before-race"
    external = tmp_path / "external"
    spool.mkdir(parents=True)
    external.mkdir()
    destination = spool / "failed-event.jsonl"
    external_destination = external / destination.name
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$script:originalGuard = ${function:Assert-AgentBridgeTrustedBytesIdentity}
$script:guardCalls = 0
function Assert-AgentBridgeTrustedBytesIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [string] $Context = 'trusted bytes'
    )
    & $script:originalGuard @PSBoundParameters
    $script:guardCalls++
    if ($script:guardCalls -eq 1) {
        [IO.Directory]::Move(
            [Environment]::GetEnvironmentVariable('WD_SPOOL', 'Process'),
            [Environment]::GetEnvironmentVariable('WD_MOVED_SPOOL', 'Process')
        )
        [void](New-Item `
            -ItemType Junction `
            -Path ([Environment]::GetEnvironmentVariable(
                'WD_SPOOL', 'Process')) `
            -Target ([Environment]::GetEnvironmentVariable(
                'WD_EXTERNAL', 'Process')) `
            -ErrorAction Stop)
    }
}
$bytes = [Text.Encoding]::UTF8.GetBytes("trusted-event`n")
$sha256 = Get-AgentBridgeSha256Hex -Bytes $bytes
$result = Invoke-AgentBridgeTrustedBytesCreateNew `
    -DestinationPath ([Environment]::GetEnvironmentVariable(
        'WD_DESTINATION', 'Process')) `
    -PublishBytes $bytes `
    -ExpectedSha256 $sha256 `
    -ExpectedLength ([long]$bytes.Length) `
    -Context 'raced create-new parent'
[pscustomobject]@{
    succeeded = [bool]$result.succeeded
    error = if ($null -ne $result.error) { $result.error.Message } else { '' }
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_SPOOL": str(spool),
            "WD_MOVED_SPOOL": str(moved_spool),
            "WD_EXTERNAL": str(external),
            "WD_DESTINATION": str(destination),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["succeeded"] is False
    assert "reparse point" in result["error"]
    assert not external_destination.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_exact_delete_wrapper_refuses_destructive_cleanup(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    done = bridge_root / "done"
    moved_done = bridge_root / "done-before-race"
    external = tmp_path / "external"
    done.mkdir(parents=True)
    external.mkdir()
    name = "archive-preparation.tmp"
    intended = done / name
    external_target = external / name
    contents = b"same-authorized-bytes"
    intended.write_bytes(contents)
    external_target.write_bytes(contents)
    done.rename(moved_done)
    _create_windows_junction(done, external)
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$bytes = [IO.File]::ReadAllBytes(
    [Environment]::GetEnvironmentVariable('WD_EXTERNAL_TARGET', 'Process'))
$sha256 = Get-AgentBridgeSha256Hex -Bytes $bytes
$deleteError = $null
try {
    Remove-AgentBridgeExactFile `
        -LiteralPath ([Environment]::GetEnvironmentVariable(
            'WD_INTENDED_PATH', 'Process')) `
        -ExpectedSha256 $sha256 `
        -ExpectedLength ([long]$bytes.Length) `
        -Context 'raced exact-delete parent'
} catch {
    $deleteError = $_.Exception.Message
}
[pscustomobject]@{ error = $deleteError } | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_INTENDED_PATH": str(done / name),
            "WD_EXTERNAL_TARGET": str(external_target),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "destructive cleanup is disabled" in result["error"]
    assert (moved_done / name).read_bytes() == contents
    assert external_target.read_bytes() == contents


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_ensure_directory_rejects_parent_junction_swap_before_creation(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    moved_bridge = tmp_path / "bridge-before-race"
    external = tmp_path / "external"
    bridge_root.mkdir()
    external.mkdir()
    target = bridge_root / "shared"
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$script:targetCalls = 0
function Test-Path {
    [CmdletBinding(DefaultParameterSetName='Path')]
    param(
        [Parameter(Position=0, ParameterSetName='Path')] [string[]] $Path,
        [Parameter(Position=0, ParameterSetName='LiteralPath')]
        [Alias('PSPath')] [string[]] $LiteralPath,
        [object] $PathType,
        [switch] $IsValid
    )
    $parameters = @{}
    if ($PSBoundParameters.ContainsKey('LiteralPath')) {
        $parameters.LiteralPath = $LiteralPath
        $candidate = [string]$LiteralPath[0]
    } else {
        $parameters.Path = $Path
        $candidate = [string]$Path[0]
    }
    if ($PSBoundParameters.ContainsKey('PathType')) {
        $parameters.PathType = $PathType
    }
    if ($IsValid) { $parameters.IsValid = $true }
    $answer = Microsoft.PowerShell.Management\Test-Path @parameters
    if ($candidate -ceq [Environment]::GetEnvironmentVariable(
            'WD_TARGET_PATH', 'Process')) {
        $script:targetCalls++
        if ($script:targetCalls -eq 1) {
            [IO.Directory]::Move(
                [Environment]::GetEnvironmentVariable('WD_BRIDGE', 'Process'),
                [Environment]::GetEnvironmentVariable(
                    'WD_MOVED_BRIDGE', 'Process')
            )
            [void](New-Item `
                -ItemType Junction `
                -Path ([Environment]::GetEnvironmentVariable(
                    'WD_BRIDGE', 'Process')) `
                -Target ([Environment]::GetEnvironmentVariable(
                    'WD_EXTERNAL', 'Process')) `
                -ErrorAction Stop)
        }
    }
    return $answer
}
$ensureError = $null
try {
    Ensure-AgentBridgePlainDirectory `
        -LiteralPath ([Environment]::GetEnvironmentVariable(
            'WD_TARGET_PATH', 'Process')) `
        -Context 'raced directory creation'
} catch {
    $ensureError = $_.Exception.Message
}
[pscustomobject]@{
    target_calls = $script:targetCalls
    error = $ensureError
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_script_text(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_TARGET_PATH": str(target),
            "WD_BRIDGE": str(bridge_root),
            "WD_MOVED_BRIDGE": str(moved_bridge),
            "WD_EXTERNAL": str(external),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["target_calls"] == 1, result
    assert "reparse" in result["error"]
    assert not (external / "shared").exists()
    assert not (moved_bridge / "shared").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_mutation_lock_rejects_parent_junction_swap_before_open(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    work_queue = bridge_root / "work_queue"
    moved_work_queue = bridge_root / "work_queue-before-race"
    external = tmp_path / "external"
    work_queue.mkdir(parents=True)
    external.mkdir()
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$script:originalGuard = ${function:Assert-AgentBridgeExistingQueueDirectories}
$script:guardCalls = 0
function Assert-AgentBridgeExistingQueueDirectories {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $WorkQueueDir)
    & $script:originalGuard @PSBoundParameters
    $script:guardCalls++
    if ($script:guardCalls -eq 1) {
        [IO.Directory]::Move(
            [Environment]::GetEnvironmentVariable('WD_WORK_QUEUE', 'Process'),
            [Environment]::GetEnvironmentVariable(
                'WD_MOVED_WORK_QUEUE', 'Process')
        )
        [void](New-Item `
            -ItemType Junction `
            -Path ([Environment]::GetEnvironmentVariable(
                'WD_WORK_QUEUE', 'Process')) `
            -Target ([Environment]::GetEnvironmentVariable(
                'WD_EXTERNAL', 'Process')) `
            -ErrorAction Stop)
    }
}
$lock = $null
$lockError = $null
try {
    $lock = Enter-AgentBridgeMutationLock `
        -BridgeRoot ([Environment]::GetEnvironmentVariable(
            'WD_BRIDGE', 'Process')) `
        -TimeoutMilliseconds 250
} catch {
    $lockError = $_.Exception.Message
} finally {
    Exit-AgentBridgeMutationLock -Lock $lock
}
[pscustomobject]@{
    guard_calls = $script:guardCalls
    error = $lockError
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={
            "WD_BRIDGE": str(bridge_root),
            "WD_WORK_QUEUE": str(work_queue),
            "WD_MOVED_WORK_QUEUE": str(moved_work_queue),
            "WD_EXTERNAL": str(external),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["guard_calls"] == 1
    assert "reparse point" in result["error"]
    assert not (external / ".claims.mutation.lock").exists()
    assert not (moved_work_queue / ".claims.mutation.lock").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_mutation_lock_composite_enter_exit_round_trip(
    tmp_path: Path,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    command = r"""
. ([Environment]::GetEnvironmentVariable('WD_IDENTITY_SCRIPT', 'Process'))
$bridge = [Environment]::GetEnvironmentVariable('WD_BRIDGE', 'Process')
$first = Enter-AgentBridgeMutationLock -BridgeRoot $bridge
$isComposite = (
    $null -ne $first.stream -and
    $null -ne $first.parent_pin
)
Exit-AgentBridgeMutationLock -Lock $first
$second = Enter-AgentBridgeMutationLock -BridgeRoot $bridge
Exit-AgentBridgeMutationLock -Lock $second
[pscustomobject]@{
    is_composite = [bool]$isComposite
    lock_exists = Test-Path -LiteralPath (
        Join-Path $bridge 'work_queue\.claims.mutation.lock')
} | ConvertTo-Json -Compress
"""

    completed = _run_identity_command(
        tmp_path,
        powershell,
        command,
        extra_env={"WD_BRIDGE": str(bridge_root)},
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {"is_composite": True, "lock_exists": True}


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_shared_bridge_root_resolver_matches_python_precedence(
    tmp_path: Path,
    powershell: str,
) -> None:
    default_root = tmp_path / "repo-local-bridge"
    legacy_root = tmp_path / "legacy-bridge"
    runtime_root = tmp_path / "runtime-bridge"
    command = (
        ". ([Environment]::GetEnvironmentVariable("
        "'WD_IDENTITY_SCRIPT', 'Process')); "
        "Resolve-AgentBridgeRoot -DefaultRoot "
        "([Environment]::GetEnvironmentVariable("
        "'WD_DEFAULT_BRIDGE', 'Process'))"
    )
    cases = (
        (None, None, default_root),
        (None, f"  {legacy_root}  ", legacy_root),
        (f"  {runtime_root}  ", str(legacy_root), runtime_root),
    )

    for runtime_value, legacy_value, expected in cases:
        env = os.environ.copy()
        env["WD_IDENTITY_SCRIPT"] = str(
            BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
        )
        env["WD_DEFAULT_BRIDGE"] = str(default_root)
        for name, value in (
            ("AGENT_BRIDGE_RUNTIME_ROOT", runtime_value),
            ("AGENT_BRIDGE_ROOT", legacy_value),
        ):
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
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

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(expected)


def test_all_powershell_bridge_entry_points_use_shared_root_resolver() -> None:
    expected_call_counts = {
        "Claim-AgentTask.ps1": 1,
        "Get-BridgeNextAction.ps1": 1,
        "Invoke-StaleClaimSweep.ps1": 1,
        "Release-AgentTask.ps1": 1,
        "Send-Liveness.ps1": 1,
        "Write-AgentEvent.ps1": 2,
    }

    for script_name, expected_count in expected_call_counts.items():
        source = (BRIDGE_BIN / script_name).read_text(encoding="utf-8")
        assert source.count("Resolve-AgentBridgeRoot") == expected_count


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_fails_closed_on_unreadable_active_claim(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    claim_path = _write_claim_payload(
        runtime_root,
        filename="foreign-write.json",
        payload=_owner_bound_claim_payload(
            task_id="foreign-write",
            agent="fable-5",
            mode="write",
            write_scope=["waggledance/core/work_queue.py"],
        ),
    )

    control = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )
    assert control.returncode == 0, control.stderr
    control_report = json.loads(control.stdout)
    assert control_report["action"] == "parallel_read_only"
    assert control_report["safe_mode"] == "read-only"
    assert control_report["foreign_write_claim_count"] == 1

    claim_path.write_text("{not-json\n", encoding="utf-8")
    attack = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert attack.returncode == 2, attack.stderr
    attack_report = json.loads(attack.stdout)
    assert attack_report["ok"] is False
    assert attack_report["decision"] == "bridge_next_action_error"
    assert "unreadable_count=1" in attack_report["errors"][0]
    assert "foreign-write.json" in attack_report["errors"][0]
    assert "action" not in attack_report
    assert "safe_mode" not in attack_report


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_fails_closed_on_non_file_claim_entry(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    blocking_entry = runtime_root / "work_queue" / "claims" / "blocking.json"
    blocking_entry.mkdir(parents=True)

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert "blocking.json" in report["errors"][0]
    assert "could not be read" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "layout_case",
    [
        "event-stream-directory",
        "claims-path-file",
        "shared-file",
        "work-queue-file",
    ],
)
def test_next_action_fails_closed_on_invalid_bridge_layout(
    tmp_path: Path,
    powershell: str,
    layout_case: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    runtime_root.mkdir()
    if layout_case == "event-stream-directory":
        (runtime_root / "shared" / "events.jsonl").mkdir(parents=True)
    elif layout_case == "claims-path-file":
        claims_path = runtime_root / "work_queue" / "claims"
        claims_path.parent.mkdir()
        claims_path.write_text("not a directory", encoding="utf-8")
    elif layout_case == "shared-file":
        (runtime_root / "shared").write_text(
            "not a directory",
            encoding="utf-8",
        )
    else:
        (runtime_root / "work_queue").write_text(
            "not a directory",
            encoding="utf-8",
        )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert layout_case.split("-")[0] in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("mode_case", "stored_mode"),
    [
        ("missing", None),
        ("null", None),
        ("unknown", "read_only"),
    ],
)
def test_next_action_fails_closed_on_malformed_claim_mode(
    tmp_path: Path,
    powershell: str,
    mode_case: str,
    stored_mode: object,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    payload = _owner_bound_claim_payload(
        task_id=f"foreign-{mode_case}-mode",
        agent="fable-5",
        mode="read-only",
    )
    if mode_case == "missing":
        payload.pop("mode")
    else:
        payload["mode"] = stored_mode
    _write_claim_payload(
        runtime_root,
        filename=f"foreign-{mode_case}-mode.json",
        payload=payload,
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert "unreadable_count=1" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("shape_case", "field_name", "stored_value"),
    [
        ("agent-nonstring", "agent", [BOUND_AGENT]),
        ("agent-uppercase", "agent", BOUND_AGENT.upper()),
        ("agent-key-case", "AGENT", BOUND_AGENT),
        ("task-nonstring", "task_id", ["owned-task"]),
        ("task-key-case", "TASK_ID", "owned-task"),
        ("scope-object", "write_scope", {"path": "tools"}),
        ("scope-nonstring", "write_scope", [123]),
        ("scope-empty", "write_scope", []),
    ],
)
def test_next_action_fails_closed_on_unsafe_claim_shape(
    tmp_path: Path,
    powershell: str,
    shape_case: str,
    field_name: str,
    stored_value: object,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    payload = _owner_bound_claim_payload(
        task_id="owned-task",
        agent=BOUND_AGENT,
        mode="write",
        write_scope=["tools"],
    )
    if field_name == "AGENT":
        payload.pop("agent")
    elif field_name == "TASK_ID":
        payload.pop("task_id")
    payload[field_name] = stored_value
    _write_claim_payload(
        runtime_root,
        filename=f"{shape_case}.json",
        payload=payload,
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    expected_error = (
        "claim agent must be a canonical string"
        if shape_case.startswith("agent-")
        else (
            "claim task_id must be a canonical string"
            if shape_case.startswith("task-")
            else "unreadable_count=1"
        )
    )
    assert expected_error in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "stored_scope",
    [
        ["tools/foo.py."],
        ["tools/foo.py::$DATA"],
        ["unrelated/path.py", ""],
        ["///"],
    ],
)
def test_next_action_and_claim_fail_closed_on_windows_alias_scope(
    tmp_path: Path,
    powershell: str,
    stored_scope: list[str],
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    _write_claim_payload(
        runtime_root,
        filename="aliased-scope-holder.json",
        payload=_owner_bound_claim_payload(
            task_id="aliased-scope-holder",
            agent="fable-5",
            mode="write",
            write_scope=stored_scope,
        ),
    )

    recommendation = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )
    assert recommendation.returncode == 2, recommendation.stderr
    report = json.loads(recommendation.stdout)
    assert report["decision"] == "bridge_next_action_error"
    assert "unreadable_count=1" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report

    acquisition = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "new-against-aliased-scope",
        "-Summary",
        "malformed holder must block acquisition",
        "-Mode",
        "write",
        "-WriteScope",
        "target/file.py",
        powershell=powershell,
    )
    assert acquisition.returncode != 0
    assert not (
        runtime_root
        / "work_queue"
        / "claims"
        / "new-against-aliased-scope.json"
    ).exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("owner_pid", "1234"),
        ("lease_seconds", "900"),
        ("write_scope", "tools/raw.py"),
        ("last_heartbeat_utc", 123),
        ("claim_lease_expires_utc", 123),
    ],
)
def test_next_action_and_claim_fail_closed_on_raw_authority_type(
    tmp_path: Path,
    powershell: str,
    field_name: str,
    malformed_value: object,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    payload = _owner_bound_claim_payload(
        task_id=f"raw-{field_name}",
        agent=BOUND_AGENT,
        mode="write",
        write_scope=["tools/raw.py"],
    )
    payload[field_name] = malformed_value
    _write_claim_payload(
        runtime_root,
        filename=f"raw-{field_name}.json",
        payload=payload,
    )

    recommendation = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )
    assert recommendation.returncode == 2, recommendation.stderr
    report = json.loads(recommendation.stdout)
    assert report["decision"] == "bridge_next_action_error"
    assert "unreadable_count=1" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report

    acquisition = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        f"new-against-{field_name}",
        "-Summary",
        "raw authority type must block acquisition",
        "-Mode",
        "write",
        "-WriteScope",
        "tests/independent.py",
        powershell=powershell,
    )
    assert acquisition.returncode != 0
    assert not (
        runtime_root
        / "work_queue"
        / "claims"
        / f"new-against-{field_name}.json"
    ).exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_and_claim_reject_hard_linked_active_claim(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    claim_path = _write_claim_payload(
        runtime_root,
        filename="hard-linked-holder.json",
        payload=_owner_bound_claim_payload(
            task_id="hard-linked-holder",
            agent=BOUND_AGENT,
            mode="write",
            write_scope=["tools/hardlink.py"],
        ),
    )
    os.link(claim_path, tmp_path / "external-claim-alias.json")

    recommendation = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )
    assert recommendation.returncode == 2, recommendation.stderr
    report = json.loads(recommendation.stdout)
    assert report["decision"] == "bridge_next_action_error"
    assert "filesystem link" in report["errors"][0]

    acquisition = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "new-against-hard-link",
        "-Summary",
        "hard-linked active claim must block acquisition",
        "-Mode",
        "write",
        "-WriteScope",
        "tests/independent.py",
        powershell=powershell,
    )
    assert acquisition.returncode != 0
    assert not (
        runtime_root
        / "work_queue"
        / "claims"
        / "new-against-hard-link.json"
    ).exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_and_claim_reject_junction_claims_directory(
    tmp_path: Path,
    powershell: str,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction semantics are required")
    runtime_root = tmp_path / "runtime" / ".agent-bridge"
    work_queue = runtime_root / "work_queue"
    work_queue.mkdir(parents=True)
    external_claims = tmp_path / "external-claims"
    external_claims.mkdir()
    (external_claims / "external-holder.json").write_text(
        json.dumps(
            _owner_bound_claim_payload(
                task_id="external-holder",
                agent=BOUND_AGENT,
                mode="write",
                write_scope=["tools/external.py"],
            )
        ),
        encoding="utf-8",
    )
    claims_link = work_queue / "claims"
    created = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(claims_link),
            str(external_claims),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create test junction: {created.stderr}")

    try:
        recommendation = _run_script(
            runtime_root,
            "Get-BridgeNextAction.ps1",
            "-Agent",
            BOUND_AGENT,
            "-Json",
            powershell=powershell,
        )
        assert recommendation.returncode == 2, recommendation.stderr
        report = json.loads(recommendation.stdout)
        assert report["decision"] == "bridge_next_action_error"
        assert "reparse link" in report["errors"][0]

        acquisition = _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            BOUND_AGENT,
            "-TaskId",
            "new-against-junction",
            "-Summary",
            "junction state must block acquisition",
            "-Mode",
            "write",
            "-WriteScope",
            "tests/independent.py",
            powershell=powershell,
        )
        assert acquisition.returncode != 0
        assert not (external_claims / "new-against-junction.json").exists()
    finally:
        os.rmdir(claims_link)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["heartbeat", "release", "stale"])
def test_powershell_claim_mutations_reject_junction_claims_directory(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "runtime" / ".agent-bridge"
    work_queue = runtime_root / "work_queue"
    work_queue.mkdir(parents=True)
    external_claims = tmp_path / "external-claims"
    external_claims.mkdir()
    task_id = f"junction-claims-{operation}"
    payload = _owner_bound_claim_payload(task_id=task_id, agent="codex")
    if operation == "stale":
        payload.update(
            {
                "claimed_at_utc": "2020-01-01T00:00:00Z",
                "last_heartbeat_utc": "2020-01-01T00:00:00Z",
                "lease_seconds": 1,
                "claim_lease_expires_utc": "2020-01-01T00:00:01Z",
            }
        )
    (external_claims / f"{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    (external_claims / "external-sentinel.bin").write_bytes(
        b"external-claims-sentinel"
    )
    external_before = {
        path.name: path.read_bytes()
        for path in sorted(external_claims.iterdir())
    }
    claims_link = work_queue / "claims"
    _create_windows_junction(claims_link, external_claims)

    try:
        if operation == "heartbeat":
            completed = _run_script(
                runtime_root,
                "Send-Liveness.ps1",
                "-Agent",
                "codex",
                "-Heartbeat",
                "-TaskId",
                f"{task_id}-event",
                bound_agent="codex",
                powershell=powershell,
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
                powershell=powershell,
            )
        else:
            completed = _run_script(
                runtime_root,
                "Invoke-StaleClaimSweep.ps1",
                "-StaleSeconds",
                "1",
                "-Quiet",
                bound_agent="codex",
                powershell=powershell,
            )

        assert completed.returncode != 0
        assert "reparse link" in _normalized_powershell_output(completed)
        assert {
            path.name: path.read_bytes()
            for path in sorted(external_claims.iterdir())
        } == external_before
        assert not (work_queue / "done").exists()
        assert not (runtime_root / "shared" / "events.jsonl").exists()
    finally:
        os.rmdir(claims_link)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["heartbeat", "release", "stale"])
def test_powershell_claim_mutations_reject_junction_done_directory(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "runtime" / ".agent-bridge"
    task_id = f"junction-done-{operation}"
    payload = _owner_bound_claim_payload(task_id=task_id, agent="codex")
    if operation == "stale":
        payload.update(
            {
                "claimed_at_utc": "2020-01-01T00:00:00Z",
                "last_heartbeat_utc": "2020-01-01T00:00:00Z",
                "lease_seconds": 1,
                "claim_lease_expires_utc": "2020-01-01T00:00:01Z",
            }
        )
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )
    claim_before = claim_path.read_bytes()
    external_done = tmp_path / "external-done"
    external_done.mkdir()
    (external_done / "external-archive.json").write_bytes(
        b'{"external":"archive-sentinel"}'
    )
    external_before = {
        path.name: path.read_bytes()
        for path in sorted(external_done.iterdir())
    }
    done_link = runtime_root / "work_queue" / "done"
    _create_windows_junction(done_link, external_done)

    try:
        if operation == "heartbeat":
            completed = _run_script(
                runtime_root,
                "Send-Liveness.ps1",
                "-Agent",
                "codex",
                "-Heartbeat",
                "-TaskId",
                f"{task_id}-event",
                bound_agent="codex",
                powershell=powershell,
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
                powershell=powershell,
            )
        else:
            completed = _run_script(
                runtime_root,
                "Invoke-StaleClaimSweep.ps1",
                "-StaleSeconds",
                "1",
                "-Quiet",
                bound_agent="codex",
                powershell=powershell,
            )

        assert completed.returncode != 0
        assert "reparse link" in _normalized_powershell_output(completed)
        assert claim_path.read_bytes() == claim_before
        assert {
            path.name: path.read_bytes()
            for path in sorted(external_done.iterdir())
        } == external_before
        assert not (runtime_root / "shared" / "events.jsonl").exists()
    finally:
        os.rmdir(done_link)


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_fails_closed_on_duplicate_active_task_ids(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    for filename in ("duplicate-a.json", "duplicate-b.json"):
        _write_claim_payload(
            runtime_root,
            filename=filename,
            payload=_owner_bound_claim_payload(
                task_id="duplicate-active-task",
                agent=BOUND_AGENT,
                mode="write",
                write_scope=["tools"],
            ),
        )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert "duplicate active claim task_id" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("owner_case", ["wrong-token", "legacy-tokenless"])
def test_next_action_never_treats_foreign_generation_as_own_write(
    tmp_path: Path,
    powershell: str,
    owner_case: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    payload = _owner_bound_claim_payload(
        task_id=f"same-agent-{owner_case}",
        agent=BOUND_AGENT,
        mode="write",
        write_scope=["tools"],
    )
    if owner_case == "wrong-token":
        payload["owner_token_sha256"] = "b" * 64
    else:
        for field in (
            "owner_session_id",
            "owner_token_sha256",
            "owner_pid",
            "owner_process_start_utc",
        ):
            payload.pop(field)
    _write_claim_payload(
        runtime_root,
        filename=f"same-agent-{owner_case}.json",
        payload=payload,
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "parallel_read_only"
    assert report["safe_mode"] == "read-only"
    assert report["foreign_write_claim_count"] == 1
    assert report["claim_snapshot"]["foreign_write"][0]["task_id"] == (
        f"same-agent-{owner_case}"
    )
    assert report["claim_snapshot"]["own"] == []


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_fails_closed_on_unreadable_selected_event(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    valid_event = {
        "ts_utc": "2026-07-31T12:00:00Z",
        "agent": "fable-5",
        "type": "message",
        "task_id": "directed-review",
        "status": "review_requested",
        "to": BOUND_AGENT,
        "message": "review the frozen snapshot",
    }
    events_path.write_text(
        json.dumps(valid_event) + "\n",
        encoding="utf-8",
    )

    control = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )
    assert control.returncode == 0, control.stderr
    control_report = json.loads(control.stdout)
    assert control_report["action"] == "answer_incoming"
    assert control_report["safe_mode"] == "read-only"
    assert control_report["open_incoming_count"] == 1

    events_path.write_text("{not-json\n", encoding="utf-8")
    attack = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert attack.returncode == 2, attack.stderr
    attack_report = json.loads(attack.stdout)
    assert attack_report["ok"] is False
    assert attack_report["decision"] == "bridge_next_action_error"
    assert "invalid JSON in bridge events" in attack_report["errors"][0]
    assert "line 1" in attack_report["errors"][0]
    assert "action" not in attack_report
    assert "safe_mode" not in attack_report


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("attack_case", ["case-variant-keys", "array-values"])
def test_next_action_fails_closed_on_structurally_invalid_closure_event(
    tmp_path: Path,
    powershell: str,
    attack_case: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    request = {
        "ts_utc": "2026-07-31T12:00:00Z",
        "agent": "fable-5",
        "type": "message",
        "task_id": "directed-review",
        "status": "review_requested",
        "to": BOUND_AGENT,
        "message": "review the frozen snapshot",
    }
    if attack_case == "case-variant-keys":
        closure = {
            "TS_UTC": "2026-07-31T12:00:30Z",
            "AGENT": BOUND_AGENT,
            "TYPE": "message",
            "TASK_ID": "directed-review",
            "STATUS": "answered",
            "TO": "fable-5",
        }
    else:
        closure = {
            "ts_utc": ["2026-07-31T12:00:30Z"],
            "agent": [BOUND_AGENT],
            "type": ["message"],
            "task_id": ["directed-review"],
            "status": ["answered"],
            "to": ["fable-5"],
        }
    events_path.write_text(
        json.dumps(request) + "\n" + json.dumps(closure) + "\n",
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert "line 2" in report["errors"][0]
    assert "must be an exact string" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_matches_closure_task_id_with_exact_case(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events = [
        {
            "ts_utc": "2026-07-31T12:00:00Z",
            "agent": "fable-5",
            "type": "message",
            "task_id": "directed-review",
            "status": "review_requested",
            "to": BOUND_AGENT,
        },
        {
            "ts_utc": "2026-07-31T12:00:30Z",
            "agent": BOUND_AGENT,
            "type": "message",
            "task_id": "DIRECTED-REVIEW",
            "status": "answered",
            "to": "fable-5",
        },
    ]
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "answer_incoming"
    assert report["safe_mode"] == "read-only"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("blocked", "blocked"),
        ("ownership_proposal", "open"),
        ("status", "request"),
    ],
)
def test_next_action_routes_polymorphic_request_read_only(
    tmp_path: Path,
    powershell: str,
    event_type: str,
    status: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    event = {
        "ts_utc": "2026-07-31T12:00:00Z",
        "agent": "fable-5",
        "type": event_type,
        "task_id": "polymorphic-review",
        "status": status,
        "to": BOUND_AGENT,
        "message": "review this custom domain event",
    }
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "answer_incoming"
    assert report["safe_mode"] == "read-only"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("ack_status", ["acknowledged", "received", "seen"])
def test_next_action_ack_never_closes_incoming_request(
    tmp_path: Path,
    powershell: str,
    ack_status: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events = [
        {
            "ts_utc": "2026-07-31T12:00:00Z",
            "agent": "fable-5",
            "type": "message",
            "task_id": "ack-only-review",
            "status": "request",
            "to": BOUND_AGENT,
        },
        {
            "ts_utc": "2026-07-31T12:00:30Z",
            "agent": BOUND_AGENT,
            "type": "message",
            "task_id": "ack-only-review",
            "status": ack_status,
            "to": "fable-5",
        },
    ]
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "answer_incoming"
    assert report["safe_mode"] == "read-only"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_does_not_close_via_legacy_id_alias(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events = [
        {
            "ts_utc": "2026-07-31T12:00:00Z",
            "agent": "fable-5",
            "type": "message",
            "task_id": "canonical-review",
            "status": "request",
            "to": BOUND_AGENT,
        },
        {
            "ts_utc": "2026-07-31T12:00:30Z",
            "agent": BOUND_AGENT,
            "type": "decision",
            "task_id": "",
            "id": "canonical-review",
            "status": "pass",
            "to": "fable-5",
        },
    ]
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["action"] == "answer_incoming"
    assert report["safe_mode"] == "read-only"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-31T12:00:00+14:00",
        "2026-07-31T12:00:00",
    ],
)
def test_next_action_fails_closed_on_non_utc_event_timestamp(
    tmp_path: Path,
    powershell: str,
    timestamp: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    event = {
        "ts_utc": timestamp,
        "agent": "fable-5",
        "type": "message",
        "task_id": "non-utc-review",
        "status": "request",
        "to": BOUND_AGENT,
    }
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    completed = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Now",
        "2026-07-31T12:01:00Z",
        "-Json",
        powershell=powershell,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(completed.stdout)
    assert report["decision"] == "bridge_next_action_error"
    assert "UTC offset" in report["errors"][0]
    assert "action" not in report
    assert "safe_mode" not in report


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_next_action_fails_closed_on_invalid_utf8_suppression_config(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    suppression_path = (
        runtime_root / "shared" / "production_liveness_suppression.json"
    )
    suppression_path.parent.mkdir(parents=True)
    suppression_path.write_text(
        json.dumps(
            {
                "version": 1,
                "suppressed_agents": {
                    BOUND_AGENT: {"reason": "operator pause"}
                },
            }
        ),
        encoding="utf-8",
    )

    control = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )
    assert control.returncode == 0, control.stderr
    control_report = json.loads(control.stdout)
    assert control_report["action"] == "agent_suppressed_unavailable"
    assert control_report["safe_mode"] == "read-only"

    suppression_path.write_bytes(
        b'{"suppressed_agents":{"codex-\xfftools-1":{"reason":"pause"}}}'
    )
    attack = _run_script(
        runtime_root,
        "Get-BridgeNextAction.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Json",
        powershell=powershell,
    )

    assert attack.returncode == 2, attack.stderr
    attack_report = json.loads(attack.stdout)
    assert attack_report["ok"] is False
    assert attack_report["decision"] == "bridge_next_action_error"
    assert "UTF-8" in attack_report["errors"][0]
    assert "action" not in attack_report
    assert "safe_mode" not in attack_report


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_claim_rejects_non_ascii_scope_before_fresh_runtime_write(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "fresh-unicode-request",
        "-Summary",
        "reject Unicode scope before creating the runtime root",
        "-Mode",
        "write",
        "-WriteScope",
        "tools/\u0130mpl.py",
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert "printable ASCII" in _normalized_powershell_output(completed)
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "write_scope",
    [
        "tools//foo.py",
        "tools/./foo.py",
        "tools/sub/../foo.py",
    ],
)
def test_claim_rejects_noncanonical_scope_before_fresh_runtime_write(
    tmp_path: Path,
    powershell: str,
    write_scope: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "fresh-noncanonical-scope",
        "-Summary",
        "reject filesystem-alias scope spellings before writing",
        "-Mode",
        "write",
        "-WriteScope",
        write_scope,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert "empty, '.' or '..' segments" in _normalized_powershell_output(
        completed
    )
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("write_scope", "error"),
    [
        ("tools/foo.py.", "end in"),
        ("tools/dir. /foo.py", "end in"),
        ("tools/foo.py::$DATA", "must not contain ':'"),
        ("/tools/foo.py", "repository-relative"),
        ("\\\\server\\share\\foo.py", "repository-relative"),
    ],
)
def test_claim_rejects_windows_alias_scope_before_fresh_runtime_write(
    tmp_path: Path,
    powershell: str,
    write_scope: str,
    error: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "fresh-windows-alias-scope",
        "-Summary",
        "reject Windows alias scope spellings before writing",
        "-Mode",
        "write",
        "-WriteScope",
        write_scope,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert error in _normalized_powershell_output(completed)
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_claim_rejects_mixed_nonstring_stored_write_scope(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    _write_claim_payload(
        runtime_root,
        filename="mixed-scope-holder.json",
        payload=_owner_bound_claim_payload(
            task_id="mixed-scope-holder",
            agent="fable-5",
            mode="write",
            write_scope=["unrelated/path.py", 123],
        ),
    )
    before = _work_queue_file_snapshot(runtime_root)

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "mixed-scope-request",
        "-Summary",
        "stored non-string scope element must fail closed",
        "-Mode",
        "write",
        "-WriteScope",
        "target/file.py",
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert "claim field 'write_scope' must be an array of strings" in (
        _normalized_powershell_output(completed)
    )
    assert not (
        runtime_root / "work_queue" / "claims" / "mixed-scope-request.json"
    ).exists()
    assert _work_queue_file_snapshot(runtime_root) == before


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_claim_rejects_cross_runtime_unicode_scope_divergence(
    tmp_path: Path,
    powershell: str,
) -> None:
    control_root = tmp_path / "control" / ".agent-bridge"
    _write_claim_payload(
        control_root,
        filename="ascii-holder.json",
        payload=_owner_bound_claim_payload(
            task_id="ascii-holder",
            agent="fable-5",
            mode="write",
            write_scope=["tools/impl.py"],
        ),
    )
    control = _run_script(
        control_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "ascii-request",
        "-Summary",
        "ASCII overlap control",
        "-Mode",
        "write",
        "-WriteScope",
        "tools/impl.py",
        powershell=powershell,
    )
    assert control.returncode != 0
    assert "write-scope conflict" in _normalized_powershell_output(control)

    unicode_root = tmp_path / "unicode" / ".agent-bridge"
    _write_claim_payload(
        unicode_root,
        filename="python-holder.json",
        payload=_owner_bound_claim_payload(
            task_id="python-holder",
            agent="fable-5",
            mode="write",
            # Python lower-casing U+0130 persists U+0069 + U+0307.
            write_scope=["tools/i\u0307mpl.py"],
        ),
    )
    attack = _run_script(
        unicode_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        BOUND_AGENT,
        "-TaskId",
        "unicode-request",
        "-Summary",
        "cross-runtime Unicode scope must fail closed",
        "-Mode",
        "write",
        "-WriteScope",
        "tools/\u0130mpl.py",
        powershell=powershell,
    )

    assert attack.returncode != 0
    assert "printable ASCII" in _normalized_powershell_output(attack)
    assert not (
        unicode_root / "work_queue" / "claims" / "unicode-request.json"
    ).exists()


def _isolated_claim_mutation_bin_with_identity_injections(
    tmp_path: Path,
    *,
    injections: list[tuple[str, str]],
) -> Path:
    isolated_bin = tmp_path / "isolated-claim-mutation-bin"
    isolated_bin.mkdir()
    for script_name in (
        "AgentBridgeSessionIdentity.ps1",
        "Claim-AgentTask.ps1",
        "Invoke-StaleClaimSweep.ps1",
        "Release-AgentTask.ps1",
        "Send-Liveness.ps1",
        "Write-AgentEvent.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / script_name, isolated_bin / script_name)
    identity_path = isolated_bin / "AgentBridgeSessionIdentity.ps1"
    source = identity_path.read_text(encoding="utf-8")
    for marker, replacement in injections:
        assert source.count(marker) == 1
        source = source.replace(marker, replacement, 1)
    identity_path.write_text(
        source,
        encoding="utf-8",
    )
    return isolated_bin


def _run_powershell_claim_mutation(
    runtime_root: Path,
    *,
    operation: str,
    task_id: str,
    script_root: Path,
    powershell: str,
    extra_env: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    if operation == "new":
        return _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            "-Summary",
            "new claim through direct byte publication",
            bound_agent="codex",
            extra_env=extra_env,
            script_root=script_root,
            powershell=powershell,
        )
    if operation == "force":
        return _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            "-Summary",
            "force update through snapshot transaction",
            "-Force",
            bound_agent="codex",
            extra_env=extra_env,
            script_root=script_root,
            powershell=powershell,
        )
    if operation == "heartbeat":
        return _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            task_id,
            bound_agent="codex",
            extra_env=extra_env,
            script_root=script_root,
            powershell=powershell,
        )
    if operation == "release":
        return _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
            extra_env=extra_env,
            script_root=script_root,
            powershell=powershell,
        )
    raise AssertionError(f"unsupported claim mutation operation: {operation}")


def _fresh_foreign_claim_bytes(task_id: str) -> bytes:
    payload = _owner_bound_claim_payload(task_id=task_id)
    payload["summary"] = "fresh foreign generation"
    payload["owner_session_id"] = "fresh-foreign-session"
    payload["owner_token_sha256"] = hashlib.sha256(
        ("b" * 64).encode("utf-8")
    ).hexdigest()
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _direct_cas_fixture(
    runtime_root: Path,
    *,
    operation: str,
    task_id: str,
) -> tuple[Path, bytes | None]:
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    if operation == "new":
        return claim_path, None
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(task_id=task_id),
    )
    return claim_path, claim_path.read_bytes()


def _publish_context_guard(body: str) -> str:
    indented_body = "\n".join(
        f"            {line}" for line in body.splitlines()
    )
    return (
        "        if (\n"
        "            $Context -ceq 'published new claim' -or\n"
        "            $Context -ceq 'published claim transaction'\n"
        "        ) {\n"
        f"{indented_body}\n"
        "        }"
    )


def _direct_cas_done_records(runtime_root: Path) -> list[Path]:
    done_dir = runtime_root / "work_queue" / "done"
    if not done_dir.is_dir():
        return []
    return sorted(path for path in done_dir.glob("*.json") if path.is_file())


def _assert_no_direct_cas_event(runtime_root: Path) -> None:
    assert not (runtime_root / "shared" / "events.jsonl").exists()


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


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_consumer_uses_unicode_safe_environment_spec_without_invoke_file(
    tmp_path: Path,
    powershell: str,
) -> None:
    unicode_root = tmp_path / "consumer-transport-ø-雪"
    runtime_root = unicode_root / "bridge-runtime"
    worktree = unicode_root / "worktree-λ"
    log_dir = unicode_root / "logs-Ж"
    worktree.mkdir(parents=True)
    log_dir.mkdir()
    capture_path = unicode_root / "transport.json"
    fake_codex = unicode_root / "fake-codex.ps1"
    fake_codex.write_text(
        """\
$ErrorActionPreference = 'Stop'
$logDir = [string]$env:BRIDGE_TEST_CONSUMER_LOG_DIR
$capturePath = [string]$env:BRIDGE_TEST_CONSUMER_CAPTURE
$payload = [ordered]@{
    invoke_count = @(
        Get-ChildItem -LiteralPath $logDir -Filter '*.invoke.json' -File
    ).Count
    legacy_spec = [string]$env:BRIDGE_CONSUMER_SPEC
    base64_spec = [string]$env:BRIDGE_CONSUMER_SPEC_B64
    arguments = @($args)
}
[System.IO.File]::WriteAllText(
    $capturePath,
    ($payload | ConvertTo-Json -Depth 8 -Compress)
)
exit 0
""",
        encoding="utf-8-sig",
    )
    model = "mødel-雪"

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeConsumerLoop.ps1",
        "-Agent",
        BOUND_AGENT,
        "-AgentUuid",
        BOUND_AGENT_UUID,
        "-RuntimeRoot",
        str(runtime_root),
        "-Worktree",
        str(worktree),
        "-LogDir",
        str(log_dir),
        "-CodexCommand",
        str(fake_codex),
        "-Model",
        model,
        "-Prompt",
        "prompt-λ",
        "-SkipHeartbeatDuringCodex",
        "-DurationMinutes",
        "0",
        "-MaxIterations",
        "1",
        "-PollSeconds",
        "0",
        extra_env={
            "BRIDGE_TEST_CONSUMER_LOG_DIR": str(log_dir),
            "BRIDGE_TEST_CONSUMER_CAPTURE": str(capture_path),
            # A stale parent value must neither be trusted nor reach Codex.
            "BRIDGE_CONSUMER_SPEC": "attacker-controlled-path",
        },
        powershell=powershell,
    )

    assert completed.returncode == 0, _normalized_powershell_output(completed)
    transport = json.loads(capture_path.read_text(encoding="utf-8-sig"))
    assert transport["invoke_count"] == 0
    assert transport["legacy_spec"] == ""
    assert transport["base64_spec"] == ""
    arguments = transport["arguments"]
    assert arguments[arguments.index("-C") + 1] == str(worktree)
    assert arguments[arguments.index("--model") + 1] == model
    assert len(list(log_dir.glob("*.log"))) == 1
    assert not list(log_dir.glob("*.invoke.json"))
    source = (BRIDGE_BIN / "Start-AgentBridgeConsumerLoop.ps1").read_text(
        encoding="utf-8"
    )
    assert ".invoke.json" not in source
    assert "BRIDGE_CONSUMER_SPEC_B64" in source


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_consumer_environment_transport_preserves_nonzero_exit_code(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    worktree = tmp_path / "worktree"
    log_dir = tmp_path / "logs"
    worktree.mkdir()
    log_dir.mkdir()
    fake_codex = tmp_path / "exit-37.ps1"
    fake_codex.write_text("exit 37\n", encoding="utf-8-sig")

    completed = _run_script(
        runtime_root,
        "Start-AgentBridgeConsumerLoop.ps1",
        "-Agent",
        BOUND_AGENT,
        "-AgentUuid",
        BOUND_AGENT_UUID,
        "-RuntimeRoot",
        str(runtime_root),
        "-Worktree",
        str(worktree),
        "-LogDir",
        str(log_dir),
        "-CodexCommand",
        str(fake_codex),
        "-SkipHeartbeatDuringCodex",
        "-DurationMinutes",
        "0",
        "-MaxIterations",
        "1",
        "-PollSeconds",
        "0",
        powershell=powershell,
    )

    assert completed.returncode == 0, _normalized_powershell_output(completed)
    events_path = runtime_root / "shared" / "events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    finished = [
        event
        for event in events
        if event.get("status") == "consumer_tick_failed"
    ]
    assert len(finished) == 1
    assert finished[0]["payload"]["exit_code"] == 37
    assert finished[0]["payload"]["codex_timed_out"] is False


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_consumer_repeated_tick_coordinates_use_distinct_log_leaves(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    worktree = tmp_path / "worktree"
    log_dir = tmp_path / "logs"
    worktree.mkdir()
    log_dir.mkdir()
    wrapper = tmp_path / "consumer-log-wrapper.ps1"
    wrapper.write_text(
        """\
$ErrorActionPreference = 'Stop'
function global:Get-Date {
    return [datetime]'2026-08-01T00:00:00Z'
}
$consumer = [string]$env:BRIDGE_TEST_CONSUMER_SCRIPT
$common = @{
    Agent = [string]$env:AGENT_BRIDGE_AGENT
    RuntimeRoot = [string]$env:BRIDGE_TEST_RUNTIME_ROOT
    Worktree = [string]$env:BRIDGE_TEST_WORKTREE
    LogDir = [string]$env:BRIDGE_TEST_LOG_DIR
    DurationMinutes = 0
    MaxIterations = 1
    PollSeconds = 0
    DryRun = $true
    SkipHeartbeatDuringCodex = $true
}
$first = @(& $consumer @common)
$second = @(& $consumer @common)
[ordered]@{
    first = [string]$first[0].log_path
    second = [string]$second[0].log_path
} | ConvertTo-Json -Compress
""",
        encoding="utf-8-sig",
    )

    completed = _run_script(
        runtime_root,
        wrapper.name,
        script_root=tmp_path,
        powershell=powershell,
        extra_env={
            "BRIDGE_TEST_CONSUMER_SCRIPT": str(
                BRIDGE_BIN / "Start-AgentBridgeConsumerLoop.ps1"
            ),
            "BRIDGE_TEST_RUNTIME_ROOT": str(runtime_root),
            "BRIDGE_TEST_WORKTREE": str(worktree),
            "BRIDGE_TEST_LOG_DIR": str(log_dir),
        },
    )

    assert completed.returncode == 0, _normalized_powershell_output(completed)
    paths = json.loads(completed.stdout)
    assert paths["first"] != paths["second"]
    leaf_pattern = re.compile(
        rf"^{BOUND_AGENT}-001-20260801T000000000Z-p\d+-[0-9a-f]{{32}}\.log$"
    )
    assert leaf_pattern.fullmatch(Path(paths["first"]).name)
    assert leaf_pattern.fullmatch(Path(paths["second"]).name)


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
    assert claim["session_id"] == "pytest-event-session"
    assert claim["owner_session_id"] == "pytest-session"
    assert claim["owner_token_sha256"] == hashlib.sha256(
        ("a" * 64).encode()
    ).hexdigest()
    assert claim["owner_pid"] == os.getpid()
    assert claim["owner_process_start_utc"].startswith(
        "2026-07-28T00:00:00"
    )
    assert "a" * 64 not in json.dumps(claim, sort_keys=True)


def test_powershell_claim_session_id_falls_back_to_owner_session(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "claim-session-fallback"

    completed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "missing ambient session uses owner generation session",
        bound_agent="codex",
        extra_env={"AGENT_BRIDGE_SESSION_ID": None},
    )

    assert completed.returncode == 0, completed.stderr
    claim = json.loads(
        (
            runtime_root
            / "work_queue"
            / "claims"
            / f"{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert claim["session_id"] == "pytest-session"
    assert claim["owner_session_id"] == "pytest-session"


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
        ("owner_pid", str(os.getpid())),
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
        "numeric-pid-string",
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
def test_present_but_invalid_stored_owner_cannot_be_released_or_forced(
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
    before = _work_queue_file_snapshot(runtime_root)

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
    assert f"claim field '{field}'" in released.stderr
    assert forced.returncode == 3
    assert f"claim field '{field}'" in forced.stderr
    assert claim_path.read_text(encoding="utf-8") == mutated
    assert _work_queue_file_snapshot(runtime_root) == before
    assert not (runtime_root / "work_queue" / "done").exists()


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


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "payload_json",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e9999}',
        '{"value":1,}',
        '{/*comment*/"value":1}',
        '{"value":01}',
        '{"value":+1}',
        "{'value':1}",
        '{"value":.1}',
        '{"value":1.}',
        '{"value":-01}',
        '{"value":00}',
        '{"value":1.e2}',
        '{"value":-.1}',
        r"""{"value":"bad\'escape"}""",
        '{"value":"raw\nnewline"}',
        "{value:1}",
        '{\u00a0"value":1}',
        '{\x0b"value":1}',
    ],
)
def test_write_event_rejects_non_strict_payload_without_runtime_write(
    tmp_path: Path,
    powershell: str,
    payload_json: str,
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
        "invalid payload must not be written",
        "-PayloadJson",
        payload_json,
        bound_agent="codex",
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert "payload must be valid JSON" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


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
    assert "write_scope entries must be non-empty paths" in (
        completed.stderr
    )
    assert not runtime_root.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_heartbeat_fails_after_owned_duplicate_task_group(
    tmp_path: Path,
    powershell: str,
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
        powershell=powershell,
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
    claims_before = {
        path.name: path.read_bytes()
        for path in (duplicate_path, duplicate_shadow, unique_path)
    }
    events_path = runtime_root / "shared" / "events.jsonl"
    events_before = events_path.read_bytes()

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "duplicate-group-heartbeat-event",
        bound_agent="codex",
        powershell=powershell,
    )

    assert heartbeat.returncode != 0
    assert "duplicate active claim records for exact task_id" in (
        heartbeat.stdout + heartbeat.stderr
    )
    assert {
        path.name: path.read_bytes()
        for path in (duplicate_path, duplicate_shadow, unique_path)
    } == claims_before
    assert events_path.read_bytes() == events_before


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_foreign_duplicate_group_blocks_all_powershell_mutators(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    duplicate_task = "foreign-duplicate\r\ninjected"
    unique_task = "unique-owned-mutation-target"
    duplicate_payload = _owner_bound_claim_payload(
        task_id=duplicate_task,
        agent="foreign-agent",
    )
    duplicate_paths = (
        _write_claim_payload(
            runtime_root,
            filename="foreign-duplicate-a.json",
            payload=duplicate_payload,
        ),
        _write_claim_payload(
            runtime_root,
            filename="foreign-duplicate-b.json",
            payload=duplicate_payload,
        ),
    )
    unique_path = _write_claim_payload(
        runtime_root,
        filename=f"{unique_task}.json",
        payload=_owner_bound_claim_payload(task_id=unique_task),
    )
    claims_before = {
        path.name: path.read_bytes() for path in (*duplicate_paths, unique_path)
    }

    results = (
        _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            "new-unrelated-task",
            "-Summary",
            "global duplicates must block claim acquisition",
            bound_agent="codex",
            powershell=powershell,
        ),
        _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            unique_task,
            bound_agent="codex",
            powershell=powershell,
        ),
        _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            "foreign-duplicate-heartbeat-event",
            bound_agent="codex",
            powershell=powershell,
        ),
    )

    for completed in results:
        combined = completed.stdout + completed.stderr
        assert completed.returncode != 0, combined
        assert "duplicate active claim records for exact task_id" in combined
        assert "foreign-duplicate\\r\\ninjected" in combined
        assert "foreign-duplicate\r\ninjected" not in combined
        assert "foreign-duplicate\ninjected" not in combined
    assert {
        path.name: path.read_bytes() for path in (*duplicate_paths, unique_path)
    } == claims_before
    assert not (
        runtime_root / "work_queue" / "claims" / "new-unrelated-task.json"
    ).exists()
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_heartbeat_fails_after_owned_invalid_task_id_claim(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claim_path = _write_claim_payload(
        runtime_root,
        filename="owned-invalid-task-id.json",
        payload=_owner_bound_claim_payload(
            task_id="invalid?owned-task",
            agent="codex",
        ),
    )
    before = claim_path.read_bytes()

    heartbeat = _run_script(
        runtime_root,
        "Send-Liveness.ps1",
        "-Agent",
        "codex",
        "-Heartbeat",
        "-TaskId",
        "owned-invalid-task-heartbeat",
        bound_agent="codex",
        powershell=powershell,
    )

    combined = heartbeat.stdout + heartbeat.stderr
    assert heartbeat.returncode != 0
    assert "invalid active claim task_id skipped" in combined
    assert "owned active claim has invalid task_id" in combined
    assert claim_path.read_bytes() == before
    assert not (runtime_root / "shared" / "events.jsonl").exists()


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
    assert after_force["session_id"] == "pytest-event-session"
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
        "legacy-parity-numeric-pid-string": {
            "claimed_at_utc": iso(fixture_now - timedelta(seconds=600)),
            "last_heartbeat_utc": iso(fixture_now + timedelta(days=1)),
            "owner_pid": str(os.getpid()),
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

    expected_stale = [
        "legacy-parity-numeric-pid-string",
        "legacy-parity-stale",
    ]
    assert [item.claim.task_id for item in python_archived] == expected_stale
    assert powershell_sweep.returncode == 0, powershell_sweep.stderr
    for runtime_root in (python_root, powershell_root):
        claims_dir = runtime_root / "work_queue" / "claims"
        for task_id in expected_stale:
            assert not (claims_dir / f"{task_id}.json").exists()
        assert (claims_dir / "legacy-parity-fresh.json").is_file()
        for task_id in expected_stale:
            done_files = list(
                (runtime_root / "work_queue" / "done").glob(f"{task_id}*.json")
            )
            assert len(done_files) == 1
            archived = json.loads(
                done_files[0].read_text(encoding="utf-8-sig")
            )
            assert "legacy tokenless claim claimed_at_utc" in archived[
                "release_reason"
            ]
            assert archived["last_heartbeat_utc"] == fixtures[task_id][
                "last_heartbeat_utc"
            ]
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
    assert "write_scope paths must be repository-relative" in (
        completed.stderr
    )
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("stored_scope", "expected_errors"),
    [
        (["tools/foo.py, tests/bar.py"], ("write-scope conflict",)),
        ([], ("active write claim requires a usable write_scope",)),
        (
            [", ,"],
            (
                "claim field 'write_scope' is malformed",
                "write_scope entries must be non-empty paths",
            ),
        ),
        (None, ("claim field 'write_scope' must be an array of strings",)),
        ([123], ("claim field 'write_scope' must be an array of strings",)),
    ],
    ids=["comma-packed", "empty", "commas-only", "null", "nonstring"],
)
def test_powershell_claim_refuses_legacy_unsafe_stored_write_scope(
    tmp_path: Path,
    stored_scope: object,
    expected_errors: tuple[str, ...],
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
    before = _work_queue_file_snapshot(runtime_root)

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
    for expected_error in expected_errors:
        assert expected_error in refused.stderr
    assert _work_queue_file_snapshot(runtime_root) == before
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
        ("numeric-string", "900"),
        ("boolean", True),
        ("float", 1.0),
        ("overflow", 2**31),
    ],
)
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_powershell_heartbeat_rejects_invalid_or_missing_lease(
    tmp_path: Path,
    lease_case: str,
    lease_value: object,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"heartbeat-default-lease-{lease_case}"
    payload = _owner_bound_claim_payload(
        task_id=task_id,
        agent="codex",
    )
    if lease_case == "missing":
        payload.pop("lease_seconds", None)
    else:
        payload["lease_seconds"] = lease_value
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )
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
        powershell=powershell,
    )

    assert heartbeat_result.returncode != 0
    assert "lease_seconds" in (
        heartbeat_result.stdout + heartbeat_result.stderr
    )
    assert claim_path.read_bytes() == before
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["heartbeat", "release"])
@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("mode", {"unexpected": "write"}),
        ("write_scope", "tools/raw.py"),
        ("lease_seconds", "900"),
        ("claimed_at_utc", 17),
        ("last_heartbeat_utc", False),
        ("claim_lease_expires_utc", 17),
    ],
    ids=[
        "mode-object",
        "scope-string",
        "numeric-lease-string",
        "claimed-at-number",
        "heartbeat-boolean",
        "lease-expiry-number",
    ],
)
def test_powershell_owned_claim_mutations_reject_raw_authority_types(
    tmp_path: Path,
    powershell: str,
    operation: str,
    field_name: str,
    malformed_value: object,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"raw-mutation-{operation}-{field_name}"
    payload = _owner_bound_claim_payload(task_id=task_id, agent="codex")
    payload[field_name] = malformed_value
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )
    before = claim_path.read_bytes()

    if operation == "heartbeat":
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            f"{task_id}-event",
            bound_agent="codex",
            powershell=powershell,
        )
    else:
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
            powershell=powershell,
        )

    assert completed.returncode != 0
    assert field_name in (completed.stdout + completed.stderr)
    assert claim_path.read_bytes() == before
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


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


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_powershell_stale_sweep_rolls_back_archive_when_source_quarantine_fails(
    tmp_path: Path,
    powershell: str,
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
    events_path = runtime_root / "shared" / "events.jsonl"
    before_events = events_path.read_bytes()
    isolated_bin = tmp_path / "isolated-stale-quarantine-bin"
    isolated_bin.mkdir()
    shutil.copy2(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1",
        isolated_bin / "AgentBridgeSessionIdentity.ps1",
    )
    sweep_source = (
        BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"
    ).read_text(encoding="utf-8")
    quarantine_source = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    injected_quarantine = (
        "            throw 'injected source quarantine failure'\n"
        f"{quarantine_source}"
    )
    assert sweep_source.count(quarantine_source) == 1
    (isolated_bin / "Invoke-StaleClaimSweep.ps1").write_text(
        sweep_source.replace(
            quarantine_source,
            injected_quarantine,
            1,
        ),
        encoding="utf-8",
    )

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
        script_root=isolated_bin,
        powershell=powershell,
    )

    combined = _normalized_powershell_output(swept)
    assert swept.returncode != 0
    assert "injected source quarantine failure" in combined
    assert "stale claim sweep incomplete" in combined
    assert claim_path.read_bytes() == before_claim
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.stale_lease.json"))
    retained_temps = list(done_dir.glob("*.tmp.*"))
    assert len(retained_temps) == 1
    assert json.loads(retained_temps[0].read_text(encoding="utf-8"))[
        "task_id"
    ] == task_id
    retained_archives = list(
        done_dir.glob("*.stale_lease.json.rollback-retained.*")
    )
    assert len(retained_archives) == 1
    assert json.loads(retained_archives[0].read_text(encoding="utf-8"))[
        "task_id"
    ] == task_id
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == before_claim
    assert not list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    assert events_path.read_bytes() == before_events


def test_powershell_stale_sweep_rolls_back_whole_batch_on_second_quarantine_failure(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claim_paths: list[Path] = []
    for task_id in ("stale-batch-first", "stale-batch-second"):
        payload = _owner_bound_claim_payload(task_id=task_id)
        payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
        payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
        payload["lease_seconds"] = 1
        payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
        claim_paths.append(
            _write_claim_payload(
                runtime_root,
                filename=f"{task_id}.json",
                payload=payload,
            )
        )
    before = {path: path.read_bytes() for path in claim_paths}

    isolated_bin = tmp_path / "isolated-stale-bin"
    isolated_bin.mkdir()
    shutil.copy2(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1",
        isolated_bin / "AgentBridgeSessionIdentity.ps1",
    )
    sweep_source = (
        BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"
    ).read_text(encoding="utf-8")
    quarantine_source = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    injected_quarantine = (
        "            $injectedSourceQuarantineCount++\n"
        "            if ($injectedSourceQuarantineCount -eq 2) {\n"
        "                throw 'injected second source quarantine failure'\n"
        "            }\n"
        f"{quarantine_source}"
    )
    assert sweep_source.count(quarantine_source) == 1
    sweep_source = sweep_source.replace(
        "$committedPlans = @()",
        "$committedPlans = @()\n$injectedSourceQuarantineCount = 0",
        1,
    ).replace(quarantine_source, injected_quarantine, 1)
    (isolated_bin / "Invoke-StaleClaimSweep.ps1").write_text(
        sweep_source,
        encoding="utf-8",
    )

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
        script_root=isolated_bin,
    )

    combined = _normalized_powershell_output(swept)
    assert swept.returncode != 0
    assert "injected second source quarantine failure" in combined
    assert "rollback failures: <none>" in combined
    assert {path: path.read_bytes() for path in claim_paths} == before
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.stale_lease.json"))
    expected_task_ids = {path.stem for path in claim_paths}
    retained_temps = list(done_dir.glob("*.tmp.*"))
    assert len(retained_temps) == len(claim_paths)
    assert {
        json.loads(path.read_text(encoding="utf-8"))["task_id"]
        for path in retained_temps
    } == expected_task_ids
    retained_archives = list(
        done_dir.glob("*.stale_lease.json.rollback-retained.*")
    )
    assert len(retained_archives) == len(claim_paths)
    assert {
        json.loads(path.read_text(encoding="utf-8"))["task_id"]
        for path in retained_archives
    } == expected_task_ids
    claims_dir = runtime_root / "work_queue" / "claims"
    backups = list(claims_dir.glob("*.stale-backup.*"))
    assert len(backups) == len(claim_paths)
    for claim_path in claim_paths:
        matching = list(
            claims_dir.glob(f"{claim_path.name}.stale-backup.*")
        )
        assert len(matching) == 1
        assert matching[0].read_bytes() == before[claim_path]
    first_quarantines = list(
        claims_dir.glob(f"{claim_paths[0].name}.stale-quarantine.*")
    )
    assert len(first_quarantines) == 1
    assert first_quarantines[0].read_bytes() == before[claim_paths[0]]
    assert not list(
        claims_dir.glob(f"{claim_paths[1].name}.stale-quarantine.*")
    )


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


@pytest.mark.parametrize(
    ("fixture_name", "fixture_bytes"),
    [
        ("malformed", b"{not-json"),
        ("array", b"[]"),
        ("null", b"null"),
        ("invalid-utf8", b'{"task_id":"noise","value":"\xff"}'),
    ],
)
@pytest.mark.parametrize("mode", ["read-only", "write"])
def test_powershell_claim_acquisition_rejects_untrusted_active_record(
    tmp_path: Path,
    fixture_name: str,
    fixture_bytes: bytes,
    mode: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    noise_path = claims_dir / f"legacy-{fixture_name}.json"
    noise_path.write_bytes(fixture_bytes)
    before = _work_queue_file_snapshot(runtime_root)
    task_id = f"strict-acquisition-{fixture_name}-{mode}"
    args = [
        "-Agent",
        "codex",
        "-TaskId",
        task_id,
        "-Summary",
        "must fail before acquiring beside untrusted active state",
        "-Mode",
        mode,
    ]
    if mode == "write":
        args.extend(["-WriteScope", "tools"])

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        *args,
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "active claim" in (claimed.stdout + claimed.stderr).lower()
    assert _work_queue_file_snapshot(runtime_root) == before
    assert not (claims_dir / f"{task_id}.json").exists()
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared").exists()


def test_powershell_claim_acquisition_rejects_nonfile_json_entry(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    noise_dir = claims_dir / "legacy-directory.json"
    noise_dir.mkdir()

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "strict-acquisition-nonfile",
        "-Summary",
        "must reject a non-file queue entry",
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "active claim record must be a file" in (
        claimed.stdout + claimed.stderr
    )
    assert list(claims_dir.iterdir()) == [noise_dir]
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared").exists()


def test_powershell_claim_publication_uses_direct_trusted_bytes() -> None:
    identity = (
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    ).read_text(encoding="utf-8")
    claim = (BRIDGE_BIN / "Claim-AgentTask.ps1").read_text(encoding="utf-8")
    heartbeat = (
        BRIDGE_BIN / "Send-Liveness.ps1"
    ).read_text(encoding="utf-8")
    release = (
        BRIDGE_BIN / "Release-AgentTask.ps1"
    ).read_text(encoding="utf-8")

    assert "Publish-AgentBridgeNewFileFromBytes" in claim
    assert "-PublishBytes $jsonBytes" in claim
    assert "-ExpectedSourceBytes ([byte[]]$matchedEntry.snapshot_bytes)" in claim
    assert "-ExpectedSourceBytes ([byte[]]$entry.snapshot_bytes)" in heartbeat
    assert "-ExpectedSourceBytes ([byte[]]$matchedEntry.snapshot_bytes)" in release
    assert "FileMode]::CreateNew" in identity
    assert "FileAccess]::ReadWrite" in identity
    assert "FileShare]::None" in identity
    assert "Publish-AgentBridgeNewFileFromTemp" not in identity
    assert "$stagePath" not in identity


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["new", "force", "heartbeat", "release"])
def test_powershell_direct_publication_retains_partial_canonical_on_write_failure(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-partial-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    expected_path = tmp_path / "expected-publish.bin"
    create_marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    write_marker = (
        "        # CAS V2 DIRECT MARKER: write trusted canonical bytes."
    )
    capture = _publish_context_guard(
        "[System.IO.File]::WriteAllBytes(\n"
        "    $env:WD_TEST_EXPECTED_BYTES,\n"
        "    $PublishBytes\n"
        ")"
    )
    partial = _publish_context_guard(
        "$partialLength = [int][Math]::Floor($PublishBytes.Length / 2)\n"
        "if ($partialLength -lt 1) { $partialLength = 1 }\n"
        "$stream.Write($PublishBytes, 0, $partialLength)\n"
        "throw 'injected direct partial write failure'"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[
            (create_marker, f"{capture}\n{create_marker}"),
            (write_marker, f"{partial}\n{write_marker}"),
        ],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
        extra_env={"WD_TEST_EXPECTED_BYTES": str(expected_path)},
    )

    assert completed.returncode != 0
    expected = expected_path.read_bytes()
    partial_length = max(1, len(expected) // 2)
    done_records = _direct_cas_done_records(runtime_root)
    canonical = done_records[0] if operation == "release" else claim_path
    assert canonical.read_bytes() == expected[:partial_length]
    if operation == "release":
        assert original is not None
        assert claim_path.read_bytes() == original
        assert len(done_records) == 1
    elif operation != "new":
        assert original is not None
    if original is not None:
        recoveries = list(
            claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
        )
        assert any(path.read_bytes() == original for path in recoveries)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["new", "force", "heartbeat", "release"])
def test_powershell_direct_publication_rejects_final_hardlink_alias(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-hardlink-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    expected_path = tmp_path / "expected-publish.bin"
    alias_path = tmp_path / "canonical-attack-alias.bin"
    create_marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    final_marker = (
        "            # CAS V2 DIRECT MARKER: final canonical identity gate."
    )
    capture = _publish_context_guard(
        "[System.IO.File]::WriteAllBytes(\n"
        "    $env:WD_TEST_EXPECTED_BYTES,\n"
        "    $PublishBytes\n"
        ")"
    )
    hardlink = _publish_context_guard(
        "[void](New-Item -ItemType HardLink `\n"
        "    -Path $env:WD_TEST_ALIAS_PATH `\n"
        "    -Target $DestinationPath -ErrorAction Stop)"
    ).replace("        if", "            if", 1).replace(
        "\n        }", "\n            }", 1
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[
            (create_marker, f"{capture}\n{create_marker}"),
            (final_marker, f"{hardlink}\n{final_marker}"),
        ],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
        extra_env={
            "WD_TEST_EXPECTED_BYTES": str(expected_path),
            "WD_TEST_ALIAS_PATH": str(alias_path),
        },
    )

    assert completed.returncode != 0
    expected = expected_path.read_bytes()
    done_records = _direct_cas_done_records(runtime_root)
    canonical = done_records[0] if operation == "release" else claim_path
    assert canonical.read_bytes() == expected
    assert alias_path.read_bytes() == expected
    if operation == "release":
        assert original is not None
        assert claim_path.read_bytes() == original
    if original is not None:
        assert any(
            path.read_bytes() == original
            for path in claim_path.parent.glob(
                f"{claim_path.name}.cas-quarantine.*"
            )
        )
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["new", "force", "heartbeat", "release"])
def test_powershell_exclusive_handle_blocks_final_atomic_replacement(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-exclusive-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    expected_path = tmp_path / "expected-publish.bin"
    foreign_path = tmp_path / "foreign-replacement.bin"
    displaced_path = tmp_path / "displaced-canonical.bin"
    foreign_bytes = b"FOREIGN-AT-FINAL-IDENTITY-GATE"
    encoded_foreign = base64.b64encode(foreign_bytes).decode("ascii")
    create_marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    final_marker = (
        "            # CAS V2 DIRECT MARKER: final canonical identity gate."
    )
    capture = _publish_context_guard(
        "[System.IO.File]::WriteAllBytes(\n"
        "    $env:WD_TEST_EXPECTED_BYTES,\n"
        "    $PublishBytes\n"
        ")"
    )
    replace_body = (
        "[System.IO.File]::WriteAllBytes(\n"
        "    $env:WD_TEST_FOREIGN_PATH,\n"
        "    [System.Convert]::FromBase64String(\n"
        f"        '{encoded_foreign}'\n"
        "    )\n"
        ")\n"
        "[System.IO.File]::Replace(\n"
        "    $env:WD_TEST_FOREIGN_PATH,\n"
        "    $DestinationPath,\n"
        "    $env:WD_TEST_DISPLACED_PATH\n"
        ")"
    )
    replacement = _publish_context_guard(replace_body).replace(
        "        if", "            if", 1
    ).replace("\n        }", "\n            }", 1)
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[
            (create_marker, f"{capture}\n{create_marker}"),
            (final_marker, f"{replacement}\n{final_marker}"),
        ],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
        extra_env={
            "WD_TEST_EXPECTED_BYTES": str(expected_path),
            "WD_TEST_FOREIGN_PATH": str(foreign_path),
            "WD_TEST_DISPLACED_PATH": str(displaced_path),
        },
    )

    assert completed.returncode != 0
    expected = expected_path.read_bytes()
    done_records = _direct_cas_done_records(runtime_root)
    canonical = done_records[0] if operation == "release" else claim_path
    assert canonical.read_bytes() == expected
    assert foreign_path.read_bytes() == foreign_bytes
    assert not displaced_path.exists()
    if operation == "release":
        assert original is not None
        assert claim_path.read_bytes() == original
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_fresh_source_generation_wins_before_quarantine_move(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-fresh-source-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert original is not None
    fresh = _fresh_foreign_claim_bytes(task_id)
    encoded_fresh = base64.b64encode(fresh).decode("ascii")
    move = "    [System.IO.File]::Move($SourcePath, $quarantinePath)"
    injected = (
        "    [System.IO.File]::WriteAllBytes(\n"
        "        $SourcePath,\n"
        "        [System.Convert]::FromBase64String(\n"
        f"            '{encoded_fresh}'\n"
        "        )\n"
        "    )\n"
        f"{move}"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(move, injected)],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert claim_path.read_bytes() == fresh
    recoveries = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert any(path.read_bytes() == fresh for path in recoveries)
    assert not _direct_cas_done_records(runtime_root)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_quarantine_swap_after_capture_restores_captured_bytes(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-q-swap-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert original is not None
    foreign = b"FOREIGN-QUARANTINE-AFTER-CAPTURE"
    encoded_foreign = base64.b64encode(foreign).decode("ascii")
    marker = (
        "        # CAS V2 TRANSACTION MARKER: verify captured quarantine path."
    )
    injected = (
        "        $foreignQ = $quarantinePath + '.foreign-q'\n"
        "        $displacedQ = $quarantinePath + '.displaced-q'\n"
        "        [System.IO.File]::WriteAllBytes(\n"
        "            $foreignQ,\n"
        "            [System.Convert]::FromBase64String(\n"
        f"                '{encoded_foreign}'\n"
        "            )\n"
        "        )\n"
        "        [System.IO.File]::Replace(\n"
        "            $foreignQ,\n"
        "            $quarantinePath,\n"
        "            $displacedQ\n"
        "        )\n"
        f"{marker}"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(marker, injected)],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert claim_path.read_bytes() == original
    q_artifacts = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert any(path.read_bytes() == foreign for path in q_artifacts)
    assert any(path.read_bytes() == original for path in q_artifacts)
    assert not _direct_cas_done_records(runtime_root)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_quarantine_hardlink_failure_restores_single_link_source(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-q-hardlink-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert original is not None
    alias_path = tmp_path / "quarantine-hardlink-alias.bin"
    move = "    [System.IO.File]::Move($SourcePath, $quarantinePath)"
    injected = (
        f"{move}\n"
        "    [void](New-Item -ItemType HardLink `\n"
        "        -Path $env:WD_TEST_Q_ALIAS_PATH `\n"
        "        -Target $quarantinePath -ErrorAction Stop)"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(move, injected)],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
        extra_env={"WD_TEST_Q_ALIAS_PATH": str(alias_path)},
    )

    assert completed.returncode != 0
    assert claim_path.read_bytes() == original
    assert claim_path.stat().st_nlink == 1
    assert alias_path.read_bytes() == original
    recoveries = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert any(path.read_bytes() == original for path in recoveries)
    assert not _direct_cas_done_records(runtime_root)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_fresh_replacement_with_hardlink_restores_captured_generation(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-fresh-hardlink-{operation}"
    claim_path, authorized = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert authorized is not None
    fresh = _fresh_foreign_claim_bytes(task_id)
    encoded_fresh = base64.b64encode(fresh).decode("ascii")
    move = "    [System.IO.File]::Move($SourcePath, $quarantinePath)"
    injected = (
        "    $authorizedPath = $SourcePath + '.test-authorized-A'\n"
        "    [System.IO.File]::Move($SourcePath, $authorizedPath)\n"
        "    [System.IO.File]::WriteAllBytes(\n"
        "        $SourcePath,\n"
        "        [System.Convert]::FromBase64String(\n"
        f"            '{encoded_fresh}'\n"
        "        )\n"
        "    )\n"
        "    $freshAlias = $SourcePath + '.test-fresh-hardlink'\n"
        "    [void](New-Item -ItemType HardLink `\n"
        "        -Path $freshAlias -Target $SourcePath -ErrorAction Stop)\n"
        f"{move}"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(move, injected)],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
    )

    assert completed.returncode != 0
    authorized_path = claim_path.with_name(
        claim_path.name + ".test-authorized-A"
    )
    alias_path = claim_path.with_name(
        claim_path.name + ".test-fresh-hardlink"
    )
    quarantines = [
        path
        for path in claim_path.parent.glob(
            f"{claim_path.name}.cas-quarantine.*"
        )
        if ".test-" not in path.name
    ]
    assert authorized_path.read_bytes() == authorized
    assert claim_path.read_bytes() == fresh
    assert claim_path.stat().st_nlink == 1
    assert len(quarantines) == 1
    assert alias_path.samefile(quarantines[0])
    assert quarantines[0].read_bytes() == fresh
    alias_path.write_bytes(b"MUTATED-REJECTED-FRESH-HARDLINK")
    assert quarantines[0].read_bytes() == b"MUTATED-REJECTED-FRESH-HARDLINK"
    assert claim_path.read_bytes() == fresh
    assert not _direct_cas_done_records(runtime_root)
    _assert_no_direct_cas_event(runtime_root)
    assert "exactly one filesystem link" in _normalized_powershell_output(
        completed
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_post_move_capture_failure_never_replays_authorized_bytes(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-capture-unavailable-{operation}"
    claim_path, authorized = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert authorized is not None
    open_marker = (
        "        # CAS V2 EXISTING MARKER: open quarantined path exclusively."
    )
    injected = (
        "        if ($Context -ceq 'authorized quarantined claim source') {\n"
        "            throw 'injected pre-byte quarantine capture failure'\n"
        "        }"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(open_marker, f"{injected}\n{open_marker}")],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
    )

    combined = _normalized_powershell_output(completed)
    quarantines = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert completed.returncode != 0
    assert "injected pre-byte quarantine capture failure" in combined
    assert "not restored from pre-move authorization" in combined
    assert not claim_path.exists()
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == authorized
    assert not _direct_cas_done_records(runtime_root)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_powershell_release_create_new_collision_restores_source(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "direct-release-collision"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation="release",
        task_id=task_id,
    )
    assert original is not None
    foreign = b"FOREIGN-PREEXISTING-RELEASE-DESTINATION"
    encoded_foreign = base64.b64encode(foreign).decode("ascii")
    marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    body = (
        "if ($Context -ceq 'published claim transaction') {\n"
        "    [System.IO.File]::WriteAllBytes(\n"
        "        $DestinationPath,\n"
        "        [System.Convert]::FromBase64String(\n"
        f"            '{encoded_foreign}'\n"
        "        )\n"
        "    )\n"
        "}"
    )
    guarded = "\n".join(f"        {line}" for line in body.splitlines())
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(marker, f"{guarded}\n{marker}")],
    )

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation="release",
        task_id=task_id,
        script_root=isolated_bin,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert claim_path.read_bytes() == original
    done_records = _direct_cas_done_records(runtime_root)
    assert len(done_records) == 1
    assert done_records[0].read_bytes() == foreign
    recoveries = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert any(path.read_bytes() == original for path in recoveries)
    _assert_no_direct_cas_event(runtime_root)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["force", "heartbeat", "release"])
def test_powershell_success_retains_exact_committed_recovery(
    tmp_path: Path,
    powershell: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"direct-success-recovery-{operation}"
    claim_path, original = _direct_cas_fixture(
        runtime_root,
        operation=operation,
        task_id=task_id,
    )
    assert original is not None

    completed = _run_powershell_claim_mutation(
        runtime_root,
        operation=operation,
        task_id=task_id,
        script_root=BRIDGE_BIN,
        powershell=powershell,
    )

    assert completed.returncode == 0, _normalized_powershell_output(completed)
    recoveries = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original
    done_records = _direct_cas_done_records(runtime_root)
    if operation == "release":
        assert not claim_path.exists()
        assert len(done_records) == 1
        assert json.loads(done_records[0].read_text(encoding="utf-8"))[
            "release_status"
        ] == "done"
    else:
        assert claim_path.is_file()
        assert claim_path.read_bytes() != original
        assert not done_records
    events = (
        runtime_root / "shared" / "events.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(events) == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    "raw_claim",
    [
        b'{"agent":"codex","task_id":"dup","task_id":"dup"}',
        (
            b'{"agent":"codex","task_id":"dup",'
            b'"owner_session_id":"one","owner_session_id":"two"}'
        ),
        b'{"agent":"codex","task_id":"dup","task_\\u0069d":"dup"}',
        b'{"agent":"codex","task_id":"dup","Task_Id":"dup"}',
        '{"agent":"codex","task_id":"dup","Straße":true}'.encode(),
    ],
)
def test_powershell_public_claim_paths_reject_ambiguous_json_names(
    tmp_path: Path,
    powershell: str,
    raw_claim: bytes,
) -> None:
    calls = [
        (
            "Claim-AgentTask.ps1",
            ("-Agent", "codex", "-TaskId", "new-beside-ambiguous", "-Summary", "x"),
        ),
        (
            "Send-Liveness.ps1",
            ("-Agent", "codex", "-Heartbeat", "-TaskId", "dup"),
        ),
        (
            "Release-AgentTask.ps1",
            ("-Agent", "codex", "-TaskId", "dup"),
        ),
    ]
    for index, (script_name, args) in enumerate(calls):
        runtime_root = tmp_path / f"runtime-{index}"
        claim_path = runtime_root / "work_queue" / "claims" / "ambiguous.json"
        claim_path.parent.mkdir(parents=True)
        claim_path.write_bytes(raw_claim)

        completed = _run_script(
            runtime_root,
            script_name,
            *args,
            bound_agent="codex",
            powershell=powershell,
        )

        assert completed.returncode != 0
        assert claim_path.read_bytes() == raw_claim
        assert not _direct_cas_done_records(runtime_root)
        _assert_no_direct_cas_event(runtime_root)


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
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_powershell_heartbeat_lease_publish_failure_is_nonzero_and_zero_write(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "heartbeat-lease-publish-failure"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(task_id=task_id),
    )
    before = claim_path.read_bytes()

    marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    injected_marker = (
        "        if ($Context -ceq 'published claim transaction') {\n"
        "            throw 'injected heartbeat lease publication failure'\n"
        "        }\n"
        f"{marker}"
    )
    isolated_bin = _isolated_claim_mutation_bin_with_identity_injections(
        tmp_path,
        injections=[(marker, injected_marker)],
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
        powershell=powershell,
    )

    combined = _normalized_powershell_output(heartbeat).lower()
    assert heartbeat.returncode != 0
    assert "could not bump lease" in combined
    assert "injected heartbeat lease publication failure" in combined
    assert claim_path.read_bytes() == before
    assert not list(claim_path.parent.glob(f"{claim_path.name}.tmp.*"))
    assert not list(claim_path.parent.glob(f"{claim_path.name}.bak.*"))
    assert not list(
        claim_path.parent.glob(f"{claim_path.name}.rollback-displaced.*")
    )
    recoveries = list(
        claim_path.parent.glob(f"{claim_path.name}.cas-quarantine.*")
    )
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == before
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_direct_publication_has_no_consumed_temp_cleanup_surface() -> None:
    identity = (
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    ).read_text(encoding="utf-8")
    claim = (BRIDGE_BIN / "Claim-AgentTask.ps1").read_text(encoding="utf-8")

    assert "$tempConsumed" not in identity
    assert "$tmpClaimConsumed" not in claim
    assert "Publish-AgentBridgeNewFileFromTemp" not in identity
    assert "Update-AgentBridgeFileFromTemp" not in identity


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
    before = _work_queue_file_snapshot(runtime_root)
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

    assert claimed.returncode == 3
    assert "claim field 'mode' must be exact and canonical" in (
        claimed.stdout + claimed.stderr
    )
    assert _work_queue_file_snapshot(runtime_root) == before
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


def _claim_projection_attack_payload(task_id: str) -> dict[str, object]:
    payload = _owner_bound_claim_payload(task_id=task_id)
    payload.update(
        {
            "summary": ["scalar-summary-secret"],
            "run_id": {"secret": "scalar-run-secret"},
            "session_id": ["scalar-session-secret"],
            "role": ["optional-role-secret"],
            "agent_uuid": {"secret": "optional-uuid-secret"},
            "capabilities": [
                " cap.one ",
                17,
                "cap.one",
                "cap.two",
                ["nested-capability-secret"],
                None,
            ],
            "writer_pid": " 4321 ",
            "writer_pid_semantics": ["optional-writer-secret"],
            "cwd": {"secret": "optional-cwd-secret"},
            "git_branch": False,
            "owner_token": "raw-owner-secret",
            "pid": 9876,
            "unknown_field": {"secret": "unknown-field-secret"},
        }
    )
    return payload


@pytest.mark.parametrize("operation", ["heartbeat", "release"])
def test_powershell_claim_mutations_persist_only_canonical_claim_fields(
    tmp_path: Path,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"canonical-projection-{operation}"
    raw_payload = _claim_projection_attack_payload(task_id)
    expected_owner = {
        name: raw_payload[name]
        for name in (
            "owner_session_id",
            "owner_token_sha256",
            "owner_pid",
            "owner_process_start_utc",
        )
    }
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=raw_payload,
    )

    if operation == "heartbeat":
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            f"{task_id}-event",
            bound_agent="codex",
        )
        persisted_path = claim_path
    else:
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )
        archives = list(
            (runtime_root / "work_queue" / "done").glob("*.json")
        )
        assert len(archives) == 1
        persisted_path = archives[0]

    assert completed.returncode == 0, completed.stderr
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["agent"] == "codex"
    assert persisted["task_id"] == task_id
    assert persisted["summary"] == ""
    assert persisted["mode"] == "read-only"
    assert persisted["write_scope"] == []
    assert persisted["run_id"] == ""
    assert persisted["claimed_at_utc"] == raw_payload["claimed_at_utc"]
    assert persisted["session_id"] == ""
    assert {
        name: persisted[name] for name in expected_owner
    } == expected_owner
    assert persisted["capabilities"] == ["cap.one", "cap.two"]
    assert persisted["writer_pid"] == 4321
    for field in (
        "role",
        "agent_uuid",
        "writer_pid_semantics",
        "cwd",
        "git_branch",
        "owner_token",
        "pid",
        "unknown_field",
    ):
        assert field not in persisted

    if operation == "heartbeat":
        assert persisted["last_heartbeat_utc"]
        assert persisted["lease_seconds"] == 900
        assert persisted["claim_lease_expires_utc"]
    else:
        assert (
            persisted["last_heartbeat_utc"]
            == raw_payload["last_heartbeat_utc"]
        )
        assert persisted["lease_seconds"] == 900
        assert (
            persisted["claim_lease_expires_utc"]
            == raw_payload["claim_lease_expires_utc"]
        )
        assert persisted["release_status"] == "done"
        assert persisted["release_message"] == ""
        assert persisted["released_at_utc"]

    serialized = json.dumps(persisted, sort_keys=True)
    for secret in (
        "scalar-summary-secret",
        "scalar-run-secret",
        "scalar-session-secret",
        "nested-capability-secret",
        "raw-owner-secret",
        "unknown-field-secret",
    ):
        assert secret not in serialized


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_bound_operator_force_cannot_replace_foreign_agent_claim(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "operator-must-not-take-foreign-claim"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(
            task_id=task_id,
            agent="codex",
        ),
    )
    before = claim_path.read_bytes()

    forced = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "operator",
        "-TaskId",
        task_id,
        "-Summary",
        "operator force must not change ownership",
        "-Force",
        bound_agent="operator",
        powershell=powershell,
    )

    assert forced.returncode == 3
    assert "cannot force-update claim owned by codex" in forced.stderr
    assert claim_path.read_bytes() == before
    assert not (runtime_root / "shared").exists()
    assert not (runtime_root / "work_queue" / "done").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("fixture_name", "fixture_bytes"),
    [
        (
            "utf8-bom",
            b"\xef\xbb\xbf"
            b'{"task_id":"bom-active","mode":"read-only"}',
        ),
        ("trailing-comma", b'{"task_id":"trailing-active",}'),
        ("comment", b'{/*comment*/"task_id":"comment-active"}'),
        ("nan", b'{"task_id":"nan-active","value":NaN}'),
        ("infinity", b'{"task_id":"infinity-active","value":Infinity}'),
        ("overflow", b'{"task_id":"overflow-active","value":1e9999}'),
        ("leading-zero", b'{"task_id":"leading-zero","value":01}'),
        ("leading-plus", b'{"task_id":"leading-plus","value":+1}'),
        ("single-quote", b"{'task_id':'single-quote'}"),
        ("leading-dot", b'{"task_id":"leading-dot","value":.1}'),
        ("trailing-dot", b'{"task_id":"trailing-dot","value":1.}'),
        ("negative-leading-zero", b'{"task_id":"neg-zero","value":-01}'),
        ("double-zero", b'{"task_id":"double-zero","value":00}'),
        ("dot-before-exponent", b'{"task_id":"dot-exp","value":1.e2}'),
        ("negative-leading-dot", b'{"task_id":"neg-dot","value":-.1}'),
        (
            "invalid-escape",
            b"""{"task_id":"invalid-escape","value":"bad\\'escape"}""",
        ),
        (
            "raw-newline",
            b"""{"task_id":"raw-newline","value":"raw
newline"}""",
        ),
        ("unquoted-key", b'{task_id:"unquoted-key"}'),
        ("nbsp-whitespace", b'{\xc2\xa0"task_id":"nbsp"}'),
        ("vertical-tab-whitespace", b'{\x0b"task_id":"vtab"}'),
        (
            "invalid-utf8",
            b'{"task_id":"invalid-active","value":"\xff"}',
        ),
    ],
)
def test_powershell_strict_acquisition_rejects_json_extensions_on_both_engines(
    tmp_path: Path,
    powershell: str,
    fixture_name: str,
    fixture_bytes: bytes,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    untrusted_path = claims_dir / f"{fixture_name}.json"
    untrusted_path.write_bytes(fixture_bytes)
    before = _work_queue_file_snapshot(runtime_root)

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        f"strict-{fixture_name}",
        "-Summary",
        "untrusted JSON must block acquisition",
        bound_agent="codex",
        powershell=powershell,
    )

    assert claimed.returncode != 0
    assert "active claim" in (claimed.stdout + claimed.stderr).lower()
    assert _work_queue_file_snapshot(runtime_root) == before
    assert not (runtime_root / "shared").exists()
    assert not (runtime_root / "work_queue" / "done").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("operation", ["heartbeat", "release"])
@pytest.mark.parametrize("fault", ["invalid-utf8", "utf8-bom"])
def test_powershell_claim_mutators_do_not_canonicalize_untrusted_utf8(
    tmp_path: Path,
    powershell: str,
    operation: str,
    fault: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"{operation}-{fault}-claim"
    payload = _owner_bound_claim_payload(task_id=task_id)
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    if fault == "utf8-bom":
        raw = b"\xef\xbb\xbf" + raw
    else:
        raw = raw[:-1] + b',"untrusted":"\xff"}'
    claim_path = runtime_root / "work_queue" / "claims" / f"{task_id}.json"
    claim_path.parent.mkdir(parents=True)
    claim_path.write_bytes(raw)

    if operation == "heartbeat":
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            f"{task_id}-event",
            bound_agent="codex",
            powershell=powershell,
        )
        assert completed.returncode != 0
        assert "unreadable active claim blocks lease refresh" in (
            completed.stdout + completed.stderr
        )
    else:
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
            powershell=powershell,
        )
        assert completed.returncode != 0

    assert claim_path.read_bytes() == raw
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_direct_claim_update_has_no_backup_cleanup_warning_path() -> None:
    identity = (
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1"
    ).read_text(encoding="utf-8")

    assert "$backupPath" not in identity
    assert ".bak." not in identity
    assert "Remove-Item -LiteralPath $backupPath" not in identity
    assert "cas-quarantine" in identity


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_last_event_warning_cannot_hide_canonical_event(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    isolated_bin = tmp_path / "isolated-event-warning-bin"
    isolated_bin.mkdir()
    for name in (
        "AgentBridgeSessionIdentity.ps1",
        "Write-AgentEvent.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / name, isolated_bin / name)
    writer_path = isolated_bin / "Write-AgentEvent.ps1"
    source = writer_path.read_text(encoding="utf-8")
    last_write = (
        "    Write-JsonAtomic -Path $lastPath "
        "-Json ($event | ConvertTo-Json -Depth 12)"
    )
    assert source.count(last_write) == 1
    writer_path.write_text(
        source.replace(
            last_write,
            "    throw 'injected last-event cache failure'",
            1,
        ),
        encoding="utf-8",
    )

    completed = _run_script(
        runtime_root,
        "Write-AgentEvent.ps1",
        "-Agent",
        BOUND_AGENT,
        "-Type",
        "message",
        "-TaskId",
        "last-event-warning",
        "-Message",
        "canonical event must remain successful",
        "-WarningAction",
        "Stop",
        script_root=isolated_bin,
        powershell=powershell,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "injected last-event cache failure" in combined
    events_path = runtime_root / "shared" / "events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["task_id"], event["type"]) for event in events] == [
        ("last-event-warning", "message")
    ]
    assert not (
        runtime_root / "shared" / f"last_{BOUND_AGENT}.json"
    ).exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_liveness_event_append_stays_outside_claim_mutation_lock(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "event-outside-claim-lock"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=_owner_bound_claim_payload(task_id=task_id),
    )
    before = json.loads(claim_path.read_text(encoding="utf-8"))

    isolated_bin = tmp_path / "isolated-lock-boundary-bin"
    isolated_bin.mkdir()
    for name in (
        "AgentBridgeSessionIdentity.ps1",
        "Send-Liveness.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / name, isolated_bin / name)
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
[System.IO.File]::WriteAllText(
    [string]$env:WD_TEST_EVENT_READY,
    'ready'
)
while (-not (Test-Path -LiteralPath $env:WD_TEST_EVENT_RELEASE)) {
    Start-Sleep -Milliseconds 20
}
[pscustomobject]@{ task_id = $TaskId; type = $Type }
""".strip(),
        encoding="utf-8",
    )
    contender_script = tmp_path / "claim-lock-contender.ps1"
    contender_script.write_text(
        """
param(
    [string] $IdentityPath,
    [string] $BridgeRoot,
    [string] $AcquiredPath
)
. $IdentityPath
$lock = Enter-AgentBridgeMutationLock -BridgeRoot $BridgeRoot
try {
    [System.IO.File]::WriteAllText($AcquiredPath, 'acquired')
} finally {
    Exit-AgentBridgeMutationLock -Lock $lock
}
""".strip(),
        encoding="utf-8",
    )

    event_ready = tmp_path / "event-ready"
    event_release = tmp_path / "event-release"
    lock_acquired = tmp_path / "lock-acquired"
    env = os.environ.copy()
    env.update(
        {
            "AGENT_BRIDGE_RUNTIME_ROOT": str(runtime_root),
            "AGENT_BRIDGE_AGENT": "codex",
            "AGENT_BRIDGE_SESSION_ID": "pytest-event-session",
            "AGENT_BRIDGE_OWNER_SESSION_ID": "pytest-session",
            "AGENT_BRIDGE_OWNER_TOKEN": "a" * 64,
            "AGENT_BRIDGE_OWNER_PID": str(os.getpid()),
            "AGENT_BRIDGE_OWNER_PROCESS_START_UTC": (
                "2026-07-28T00:00:00Z"
            ),
            "WD_TEST_EVENT_READY": str(event_ready),
            "WD_TEST_EVENT_RELEASE": str(event_release),
        }
    )
    heartbeat = subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(isolated_bin / "Send-Liveness.ps1"),
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            "slow-event-boundary",
        ],
        cwd=runtime_root.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    contender: subprocess.Popen[str] | None = None
    acquired_before_event_release = False
    heartbeat_stdout = ""
    heartbeat_stderr = ""
    contender_stdout = ""
    contender_stderr = ""
    try:
        _wait_for_path(event_ready)
        assert heartbeat.poll() is None
        contender = subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(contender_script),
                "-IdentityPath",
                str(isolated_bin / "AgentBridgeSessionIdentity.ps1"),
                "-BridgeRoot",
                str(runtime_root),
                "-AcquiredPath",
                str(lock_acquired),
            ],
            cwd=runtime_root.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_path(lock_acquired, timeout_seconds=3.0)
            acquired_before_event_release = True
        except AssertionError:
            acquired_before_event_release = False
        assert heartbeat.poll() is None
    finally:
        event_release.write_text("release", encoding="utf-8")
        heartbeat_stdout, heartbeat_stderr = heartbeat.communicate(
            timeout=20
        )
        if contender is not None:
            contender_stdout, contender_stderr = contender.communicate(
                timeout=20
            )

    assert acquired_before_event_release, (
        "claim mutation lock remained held during the slow event append"
    )
    assert heartbeat.returncode == 0, (
        f"{heartbeat_stdout}\n{heartbeat_stderr}"
    )
    assert contender is not None
    assert contender.returncode == 0, (
        f"{contender_stdout}\n{contender_stderr}"
    )
    after = json.loads(claim_path.read_text(encoding="utf-8"))
    assert after["last_heartbeat_utc"] != before["last_heartbeat_utc"]


def test_powershell_stale_archive_and_event_share_canonical_projection(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "canonical-projection-stale"
    raw_payload = _claim_projection_attack_payload(task_id)
    raw_payload.update(
        {
            "mode": {"secret": "scalar-mode-secret"},
            "write_scope": [
                " scope/one, scope/two ",
                17,
                "scope/one",
                ["nested-scope-secret"],
                None,
            ],
            "claimed_at_utc": ["scalar-claimed-secret"],
            "last_heartbeat_utc": {"secret": "scalar-heartbeat-secret"},
            "lease_seconds": ["900"],
            "claim_lease_expires_utc": True,
        }
    )
    raw_payload["owner_pid"] = " 4242 "
    raw_payload["owner_process_start_utc"] = ["owner-start-secret"]
    raw_payload["writer_pid"] = ["4321"]
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=raw_payload,
    )

    completed = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
    )

    assert completed.returncode == 0, completed.stderr
    assert not claim_path.exists()
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_id}*.stale_lease.json"
        )
    )
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["summary"] == ""
    assert archived["mode"] == ""
    assert archived["write_scope"] == ["scope/one", "scope/two"]
    assert archived["run_id"] == ""
    assert archived["claimed_at_utc"] == ""
    assert archived["last_heartbeat_utc"] == ""
    assert archived["lease_seconds"] == 0
    assert archived["claim_lease_expires_utc"] == ""
    assert archived["capabilities"] == ["cap.one", "cap.two"]
    assert archived["release_status"] == "stale_lease"
    for field in (
        "session_id",
        "owner_pid",
        "owner_process_start_utc",
        "role",
        "agent_uuid",
        "writer_pid",
        "writer_pid_semantics",
        "cwd",
        "git_branch",
        "owner_token",
        "pid",
        "unknown_field",
    ):
        assert field not in archived

    event_lines = (
        runtime_root / "shared" / "events.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == 1
    event = json.loads(event_lines[0])
    assert event["status"] == "stale_lease"
    assert event["payload"]["claim_claimed_at_utc"] == ""
    assert event["payload"]["claim_run_id"] == ""
    assert event["payload"]["last_heartbeat_utc"] == ""
    assert Path(event["payload"]["archived_path"]) == archives[0]
    serialized = json.dumps(
        {"archive": archived, "event": event},
        sort_keys=True,
    )
    for secret in (
        "scalar-summary-secret",
        "scalar-run-secret",
        "scalar-claimed-secret",
        "scalar-heartbeat-secret",
        "nested-scope-secret",
        "nested-capability-secret",
        "owner-start-secret",
        "raw-owner-secret",
        "unknown-field-secret",
    ):
        assert secret not in serialized


def test_powershell_stale_rollback_retains_archive_when_restore_fails(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    first_task = "stale-rollback-visible-first"
    second_task = "stale-rollback-visible-second"
    claim_paths: list[Path] = []
    for task_id in (first_task, second_task):
        payload = _owner_bound_claim_payload(task_id=task_id)
        payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
        payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
        payload["lease_seconds"] = 1
        payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
        claim_paths.append(
            _write_claim_payload(
                runtime_root,
                filename=f"{task_id}.json",
                payload=payload,
            )
        )
    before = {path: path.read_bytes() for path in claim_paths}

    isolated_bin = tmp_path / "isolated-stale-restore-bin"
    isolated_bin.mkdir()
    shutil.copy2(
        BRIDGE_BIN / "AgentBridgeSessionIdentity.ps1",
        isolated_bin / "AgentBridgeSessionIdentity.ps1",
    )
    sweep_source = (
        BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"
    ).read_text(encoding="utf-8")
    quarantine_source = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    injected_quarantine = (
        "            $injectedSourceQuarantineCount++\n"
        "            if ($injectedSourceQuarantineCount -eq 2) {\n"
        "                throw 'injected second source quarantine failure'\n"
        "            }\n"
        f"{quarantine_source}"
    )
    restore_source = (
        "        $restoreResult = Invoke-AgentBridgeTrustedBytesCreateNew `\n"
        "            -DestinationPath ([string]$plan.file.FullName) `\n"
        "            -PublishBytes ([byte[]]$plan.rollback_source_bytes) `\n"
        "            -ExpectedSha256 ([string]$plan.rollback_source_sha256) `\n"
        "            -ExpectedLength ([long]$plan.rollback_source_length) `\n"
        "            -Context 'rollback restored stale-claim source'"
    )
    injected_restore = (
        f"        if ([string]$plan.task_id -ceq '{first_task}') {{\n"
        "            $restoreResult = [pscustomobject]@{\n"
        "                succeeded = $false\n"
        "                error = [System.IO.IOException]::new(\n"
        "                    'injected active source restore failure'\n"
        "                )\n"
        "            }\n"
        "        } else {\n"
        f"{restore_source.replace('        $restoreResult', '            $restoreResult', 1)}\n"
        "        }"
    )
    assert sweep_source.count(quarantine_source) == 1
    assert sweep_source.count(restore_source) == 1
    sweep_source = sweep_source.replace(
        "$committedPlans = @()",
        "$committedPlans = @()\n$injectedSourceQuarantineCount = 0",
        1,
    ).replace(quarantine_source, injected_quarantine, 1).replace(
        restore_source,
        injected_restore,
        1,
    )
    (isolated_bin / "Invoke-StaleClaimSweep.ps1").write_text(
        sweep_source,
        encoding="utf-8",
    )

    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
        script_root=isolated_bin,
    )

    combined = _normalized_powershell_output(swept)
    assert swept.returncode != 0
    assert "injected active source restore failure" in combined
    assert "source=missing" in combined
    assert "archive=missing" in combined
    assert "backup=retained" in combined
    assert not claim_paths[0].exists()
    assert claim_paths[1].read_bytes() == before[claim_paths[1]]

    done_dir = runtime_root / "work_queue" / "done"
    first_archives = list(
        done_dir.glob(f"{first_task}*.stale_lease.json")
    )
    second_archives = list(
        done_dir.glob(f"{second_task}*.stale_lease.json")
    )
    assert not first_archives
    assert not second_archives
    rollback_archives = list(
        done_dir.glob("*.stale_lease.json.rollback-retained.*")
    )
    assert len(rollback_archives) == 2
    recovery_backups = list(
        claim_paths[0].parent.glob(f"{claim_paths[0].name}.stale-backup.*")
    )
    assert len(recovery_backups) == 1
    assert recovery_backups[0].read_bytes() == before[claim_paths[0]]
    second_backups = list(
        claim_paths[1].parent.glob(
            f"{claim_paths[1].name}.stale-backup.*"
        )
    )
    assert len(second_backups) == 1
    assert second_backups[0].read_bytes() == before[claim_paths[1]]


def test_powershell_stale_retention_notice_keeps_audit_and_result(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "stale-cleanup-audit-visible"
    payload = _owner_bound_claim_payload(task_id=task_id)
    payload["claimed_at_utc"] = "2026-07-28T00:00:00Z"
    payload["last_heartbeat_utc"] = "2026-07-28T00:00:00Z"
    payload["lease_seconds"] = 1
    payload["claim_lease_expires_utc"] = "2026-07-28T00:00:01Z"
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )

    isolated_bin = tmp_path / "isolated-stale-cleanup-bin"
    isolated_bin.mkdir()
    for script_name in (
        "AgentBridgeSessionIdentity.ps1",
        "Invoke-StaleClaimSweep.ps1",
        "Write-AgentEvent.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / script_name, isolated_bin / script_name)
    swept = _run_script(
        runtime_root,
        "Invoke-StaleClaimSweep.ps1",
        "-StaleSeconds",
        "1",
        "-Quiet",
        bound_agent="codex",
        script_root=isolated_bin,
    )

    combined = _normalized_powershell_output(swept)
    assert swept.returncode == 0, combined
    assert "recovery artifacts intentionally retained by no-delete policy" in (
        combined
    )
    assert task_id in swept.stdout
    assert not claim_path.exists()
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_id}*.stale_lease.json"
        )
    )
    assert len(archives) == 1
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert len(backups) == 1
    event_lines = (
        runtime_root / "shared" / "events.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == 1
    event = json.loads(event_lines[0])
    assert event["task_id"] == task_id
    assert event["status"] == "stale_lease"
    assert Path(event["payload"]["archived_path"]) == archives[0]


def test_powershell_strict_acquisition_rejects_case_variant_task_id(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    payload = _owner_bound_claim_payload(task_id="case-variant-task-source")
    payload["Task_Id"] = payload.pop("task_id")
    claim_path = _write_claim_payload(
        runtime_root,
        filename="case-variant-task-source.json",
        payload=payload,
    )
    before = claim_path.read_bytes()

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "case-variant-task-request",
        "-Summary",
        "case-variant task_id must remain unknown",
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "task_id must be a non-empty string" in (
        claimed.stdout + claimed.stderr
    )
    assert claim_path.read_bytes() == before
    assert not (
        claim_path.parent / "case-variant-task-request.json"
    ).exists()


def test_powershell_strict_acquisition_rejects_singleton_object_array(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    payload = _owner_bound_claim_payload(task_id="singleton-array-source")
    claim_path = claims_dir / "singleton-array-source.json"
    claim_path.write_text(json.dumps([payload]), encoding="utf-8")
    before = claim_path.read_bytes()

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        "singleton-array-request",
        "-Summary",
        "top-level arrays are never active claim objects",
        bound_agent="codex",
    )

    assert claimed.returncode != 0
    assert "must be a JSON object" in (claimed.stdout + claimed.stderr)
    assert claim_path.read_bytes() == before
    assert not (claims_dir / "singleton-array-request.json").exists()


@pytest.mark.parametrize(
    ("case_variant", "expected_error"),
    [
        ("MODE", "claim field 'mode' must be exact and canonical"),
        (
            "Write_Scope",
            "claim field 'write_scope' must be an array of strings",
        ),
    ],
)
def test_powershell_case_variant_scope_fields_remain_fail_closed(
    tmp_path: Path,
    case_variant: str,
    expected_error: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    source_task = f"case-variant-scope-{case_variant.lower()}"
    payload = _owner_bound_claim_payload(
        task_id=source_task,
        agent="other-agent",
    )
    if case_variant == "MODE":
        payload["MODE"] = payload.pop("mode")
        payload["write_scope"] = ["*"]
    else:
        payload["mode"] = "write"
        payload["Write_Scope"] = ["unrelated"]
        payload.pop("write_scope")
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{source_task}.json",
        payload=payload,
    )
    before = _work_queue_file_snapshot(runtime_root)
    requested_task = f"{source_task}-request"

    claimed = _run_script(
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex",
        "-TaskId",
        requested_task,
        "-Summary",
        "case variants cannot weaken wildcard conflict handling",
        "-Mode",
        "write",
        "-WriteScope",
        "tools",
        bound_agent="codex",
    )

    assert claimed.returncode == 3
    assert expected_error in (claimed.stdout + claimed.stderr)
    assert _work_queue_file_snapshot(runtime_root) == before
    assert not (claim_path.parent / f"{requested_task}.json").exists()


@pytest.mark.parametrize("case_variant", ["agent", "owner"])
@pytest.mark.parametrize("operation", ["release", "heartbeat", "force"])
def test_powershell_case_variant_identity_cannot_authorize_claim_mutation(
    tmp_path: Path,
    case_variant: str,
    operation: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = f"case-variant-{case_variant}-{operation}"
    payload = _owner_bound_claim_payload(task_id=task_id, agent="codex")
    if case_variant == "agent":
        payload["AGENT"] = payload.pop("agent")
    else:
        for field in (
            "owner_session_id",
            "owner_token_sha256",
            "owner_pid",
            "owner_process_start_utc",
        ):
            payload[field.upper()] = payload.pop(field)
    claim_path = _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
    )
    before = claim_path.read_bytes()

    if operation == "release":
        completed = _run_script(
            runtime_root,
            "Release-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            bound_agent="codex",
        )
        assert completed.returncode != 0
    elif operation == "force":
        completed = _run_script(
            runtime_root,
            "Claim-AgentTask.ps1",
            "-Agent",
            "codex",
            "-TaskId",
            task_id,
            "-Summary",
            "case-variant identity cannot force-update",
            "-Force",
            bound_agent="codex",
        )
        assert completed.returncode != 0
    else:
        completed = _run_script(
            runtime_root,
            "Send-Liveness.ps1",
            "-Agent",
            "codex",
            "-Heartbeat",
            "-TaskId",
            f"{task_id}-event",
            bound_agent="codex",
        )
        assert completed.returncode == 0, completed.stderr

    assert claim_path.read_bytes() == before
    done_dir = runtime_root / "work_queue" / "done"
    assert not list(done_dir.glob("*.json"))


def test_powershell_canonical_projection_ignores_case_variant_fields(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "case-variant-canonical-projection"
    payload = _owner_bound_claim_payload(task_id=task_id, agent="codex")
    for field in (
        "summary",
        "run_id",
        "session_id",
    ):
        payload.pop(field, None)
    payload.update(
        {
            "SUMMARY": "case-summary-secret",
            "RUN_ID": "case-run-secret",
            "SESSION_ID": "case-session-secret",
            "ROLE": "case-role-secret",
            "CAPABILITIES": [
                "case-capability-secret",
                ["case-nested-secret"],
            ],
            "WRITER_PID": "4321",
            "OWNER_TOKEN": "case-owner-token-secret",
        }
    )
    _write_claim_payload(
        runtime_root,
        filename=f"{task_id}.json",
        payload=payload,
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
    archives = list(
        (runtime_root / "work_queue" / "done").glob("*.json")
    )
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["summary"] == ""
    assert archived["mode"] == "read-only"
    assert archived["write_scope"] == []
    assert archived["run_id"] == ""
    assert archived["claimed_at_utc"] == payload["claimed_at_utc"]
    assert archived["last_heartbeat_utc"] == payload["last_heartbeat_utc"]
    assert archived["lease_seconds"] == 900
    assert (
        archived["claim_lease_expires_utc"]
        == payload["claim_lease_expires_utc"]
    )
    assert archived["session_id"] == ""
    for field in (
        "role",
        "capabilities",
        "writer_pid",
        "owner_token",
    ):
        assert field not in archived
    serialized = json.dumps(archived, sort_keys=True)
    for secret in (
        "case-summary-secret",
        "case-run-secret",
        "case-session-secret",
        "case-role-secret",
        "case-capability-secret",
        "case-nested-secret",
        "case-owner-token-secret",
    ):
        assert secret not in serialized
