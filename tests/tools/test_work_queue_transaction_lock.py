"""Windows black-box tests for the cross-runtime work-queue transaction lock."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Callable

import pytest

from waggledance.core.work_queue import (
    WorkQueueError,
    claim_task,
    work_queue_mutex_name,
)


ROOT = Path(__file__).resolve().parents[2]
PS_CLAIM_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Claim-AgentTask.ps1"
PS_RELEASE_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Release-AgentTask.ps1"
PS_HEARTBEAT_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Send-Liveness.ps1"
PS_SWEEP_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Invoke-StaleClaimSweep.ps1"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROCESS_TIMEOUT_SECONDS = 20

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="the production cross-runtime transaction mutex is Windows-only",
)


def _powershell_cases() -> list[pytest.ParameterSet]:
    cases: list[pytest.ParameterSet] = []
    for label, candidate in (
        ("windows-powershell-5.1", "powershell.exe"),
        ("powershell-7", "pwsh.exe"),
    ):
        executable = shutil.which(candidate)
        if executable:
            cases.append(pytest.param(label, executable, id=label))
        else:
            cases.append(
                pytest.param(
                    label,
                    candidate,
                    marks=pytest.mark.skip(reason=f"{candidate} is not installed"),
                    id=label,
                )
            )
    return cases


POWERSHELL_CASES = _powershell_cases()

HOLDER_CODE = r"""
import os
from pathlib import Path
import time

from waggledance.core.work_queue import _work_queue_transaction

bridge = Path(os.environ["WD_TEST_BRIDGE_ROOT"])
ready = Path(os.environ["WD_TEST_READY_PATH"])
release = Path(os.environ["WD_TEST_RELEASE_PATH"])
with _work_queue_transaction(bridge, timeout_ms=5000):
    ready.write_text("acquired", encoding="ascii")
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test did not release external work-queue holder")
        time.sleep(0.01)
"""

PYTHON_CLAIM_CODE = r"""
import json
import os
from dataclasses import asdict
from pathlib import Path
import sys
import time

from waggledance.core.work_queue import WorkQueueError, claim_task

ready_value = os.environ.get("WD_TEST_READY_PATH", "")
if ready_value:
    Path(ready_value).write_text("ready", encoding="ascii")
gate_value = os.environ.get("WD_TEST_GATE_PATH", "")
if gate_value:
    gate = Path(gate_value)
    deadline = time.monotonic() + 10
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("work-queue race gate was not opened")
        time.sleep(0.005)
try:
    claim = claim_task(
        agent=os.environ["WD_TEST_AGENT"],
        task_id=os.environ["WD_TEST_TASK_ID"],
        summary="transaction lock black-box claim",
        mode="write",
        write_scope=(os.environ["WD_TEST_WRITE_SCOPE"],),
        bridge_root=Path(os.environ["WD_TEST_BRIDGE_ROOT"]),
    )
except WorkQueueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(3)
print(json.dumps(asdict(claim), sort_keys=True))
"""

PYTHON_HEARTBEAT_CODE = r"""
import os
from pathlib import Path
import sys
import time

from waggledance.core.work_queue import WorkQueueError, heartbeat

ready = Path(os.environ["WD_TEST_READY_PATH"])
gate = Path(os.environ["WD_TEST_GATE_PATH"])
ready.write_text("ready", encoding="ascii")
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("work-queue race gate was not opened")
    time.sleep(0.005)
try:
    heartbeat(
        agent=os.environ["WD_TEST_AGENT"],
        task_id=os.environ["WD_TEST_TASK_ID"],
        bridge_root=Path(os.environ["WD_TEST_BRIDGE_ROOT"]),
    )
except WorkQueueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(3)
"""

POWERSHELL_CLAIM_COMMAND = r"""
$ErrorActionPreference = 'Stop'
if ($env:WD_TEST_READY_PATH) {
    [System.IO.File]::WriteAllText($env:WD_TEST_READY_PATH, 'ready')
}
if ($env:WD_TEST_GATE_PATH) {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $env:WD_TEST_GATE_PATH)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'work-queue race gate was not opened'
        }
        Start-Sleep -Milliseconds 5
    }
}
& $env:WD_TEST_CLAIM_SCRIPT `
    -Agent $env:WD_TEST_AGENT `
    -TaskId $env:WD_TEST_TASK_ID `
    -Summary 'transaction lock black-box claim' `
    -Mode write `
    -WriteScope $env:WD_TEST_WRITE_SCOPE
"""

POWERSHELL_RELEASE_COMMAND = r"""
$ErrorActionPreference = 'Stop'
[System.IO.File]::WriteAllText($env:WD_TEST_READY_PATH, 'ready')
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path -LiteralPath $env:WD_TEST_GATE_PATH)) {
    if ([DateTime]::UtcNow -ge $deadline) {
        throw 'work-queue race gate was not opened'
    }
    Start-Sleep -Milliseconds 5
}
& $env:WD_TEST_RELEASE_SCRIPT `
    -Agent $env:WD_TEST_AGENT `
    -TaskId $env:WD_TEST_TASK_ID
"""

POWERSHELL_SWEEP_COMMAND = r"""
$ErrorActionPreference = 'Stop'
[System.IO.File]::WriteAllText($env:WD_TEST_READY_PATH, 'ready')
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path -LiteralPath $env:WD_TEST_GATE_PATH)) {
    if ([DateTime]::UtcNow -ge $deadline) {
        throw 'work-queue race gate was not opened'
    }
    Start-Sleep -Milliseconds 5
}
& $env:WD_TEST_SWEEP_SCRIPT -StaleSeconds 1 -Quiet | Out-Null
"""


def _base_env(bridge_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_STALE_LEASE_SECONDS",
        "AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE",
        "AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE",
        "AGENT_BRIDGE_TEST_WORK_QUEUE_TERMINAL_METADATA_FAILURE",
    ):
        env.pop(name, None)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env["WD_TEST_BRIDGE_ROOT"] = str(bridge_root)
    return env


def _popen(command: list[str], env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )


def _spawn_holder(
    bridge_root: Path,
    ready_path: Path,
    release_path: Path,
) -> subprocess.Popen[str]:
    env = _base_env(bridge_root)
    env["WD_TEST_READY_PATH"] = str(ready_path)
    env["WD_TEST_RELEASE_PATH"] = str(release_path)
    return _popen([sys.executable, "-c", HOLDER_CODE], env)


def _claim_env(
    bridge_root: Path,
    *,
    agent: str,
    task_id: str,
    write_scope: str,
    ready_path: Path | None = None,
    gate_path: Path | None = None,
) -> dict[str, str]:
    env = _base_env(bridge_root)
    env.update(
        {
            "WD_TEST_AGENT": agent,
            "WD_TEST_TASK_ID": task_id,
            "WD_TEST_WRITE_SCOPE": write_scope,
            "WD_TEST_CLAIM_SCRIPT": str(PS_CLAIM_SCRIPT),
        }
    )
    if ready_path is not None:
        env["WD_TEST_READY_PATH"] = str(ready_path)
    if gate_path is not None:
        env["WD_TEST_GATE_PATH"] = str(gate_path)
    return env


def _spawn_python_claim(
    bridge_root: Path,
    *,
    agent: str,
    task_id: str,
    write_scope: str,
    ready_path: Path | None = None,
    gate_path: Path | None = None,
) -> subprocess.Popen[str]:
    env = _claim_env(
        bridge_root,
        agent=agent,
        task_id=task_id,
        write_scope=write_scope,
        ready_path=ready_path,
        gate_path=gate_path,
    )
    return _popen([sys.executable, "-c", PYTHON_CLAIM_CODE], env)


def _spawn_powershell_claim(
    powershell: str,
    bridge_root: Path,
    *,
    agent: str,
    task_id: str,
    write_scope: str,
    ready_path: Path | None = None,
    gate_path: Path | None = None,
) -> subprocess.Popen[str]:
    env = _claim_env(
        bridge_root,
        agent=agent,
        task_id=task_id,
        write_scope=write_scope,
        ready_path=ready_path,
        gate_path=gate_path,
    )
    return _popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            POWERSHELL_CLAIM_COMMAND,
        ],
        env,
    )


def _spawn_python_heartbeat(
    bridge_root: Path,
    *,
    agent: str,
    task_id: str,
    ready_path: Path,
    gate_path: Path,
) -> subprocess.Popen[str]:
    env = _claim_env(
        bridge_root,
        agent=agent,
        task_id=task_id,
        write_scope="",
        ready_path=ready_path,
        gate_path=gate_path,
    )
    return _popen([sys.executable, "-c", PYTHON_HEARTBEAT_CODE], env)


def _spawn_gated_powershell_operation(
    powershell: str,
    bridge_root: Path,
    *,
    command: str,
    agent: str,
    task_id: str,
    ready_path: Path,
    gate_path: Path,
) -> subprocess.Popen[str]:
    env = _claim_env(
        bridge_root,
        agent=agent,
        task_id=task_id,
        write_scope="",
        ready_path=ready_path,
        gate_path=gate_path,
    )
    env["WD_TEST_RELEASE_SCRIPT"] = str(PS_RELEASE_SCRIPT)
    env["WD_TEST_SWEEP_SCRIPT"] = str(PS_SWEEP_SCRIPT)
    return _popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        env,
    )


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"process exited before creating {path.name}: "
                f"exit={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {path}")


def _finish(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    stdout, stderr = process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
    return int(process.returncode), stdout, stderr


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is not None and process.poll() is None:
        process.kill()
        process.wait(timeout=5)


def _claim_payloads(bridge_root: Path) -> list[dict[str, object]]:
    claims_dir = bridge_root / "work_queue" / "claims"
    if not claims_dir.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(claims_dir.glob("*.json"))
    ]


def _open_work_queue_mutex_sentinel(bridge_root: Path):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(
        None,
        False,
        work_queue_mutex_name(bridge_root),
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel32, handle


def _assert_external_holder_blocks_claim(
    tmp_path: Path,
    spawn_claim: Callable[[Path, Path], subprocess.Popen[str]],
) -> None:
    bridge_root = tmp_path / "bridge-runtime"
    holder_ready = tmp_path / "holder-ready"
    release_holder = tmp_path / "release-holder"
    claim_ready = tmp_path / "claim-ready"
    holder = _spawn_holder(bridge_root, holder_ready, release_holder)
    claimant: subprocess.Popen[str] | None = None
    try:
        _wait_for_path(holder_ready, holder)
        claimant = spawn_claim(bridge_root, claim_ready)
        _wait_for_path(claim_ready, claimant)

        observation_deadline = time.monotonic() + 0.75
        while time.monotonic() < observation_deadline:
            assert claimant.poll() is None
            assert _claim_payloads(bridge_root) == []
            time.sleep(0.01)

        release_holder.write_text("release", encoding="ascii")
        holder_code, holder_stdout, holder_stderr = _finish(holder)
        claim_code, claim_stdout, claim_stderr = _finish(claimant)
        assert holder_code == 0, (holder_stdout, holder_stderr)
        assert claim_code == 0, (claim_stdout, claim_stderr)

        payloads = _claim_payloads(bridge_root)
        assert len(payloads) == 1
        assert payloads[0]["mode"] == "write"
        assert payloads[0]["write_scope"]
    finally:
        release_holder.write_text("release", encoding="ascii")
        _terminate(claimant)
        if holder.poll() is None:
            _finish(holder)


def test_external_python_holder_blocks_python_claim(tmp_path: Path) -> None:
    def spawn(bridge_root: Path, ready_path: Path) -> subprocess.Popen[str]:
        return _spawn_python_claim(
            bridge_root,
            agent="python-agent",
            task_id="python-blocked-claim",
            write_scope="src/python",
            ready_path=ready_path,
        )

    _assert_external_holder_blocks_claim(tmp_path, spawn)


def _swap_ascii_case(value: str) -> str:
    swapped: list[str] = []
    for character in value:
        if "a" <= character <= "z":
            swapped.append(character.upper())
        elif "A" <= character <= "Z":
            swapped.append(character.lower())
        else:
            swapped.append(character)
    return "".join(swapped)


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize(
    "root_template",
    [
        r"{tmp}\MiXeD\bridge",
        r"{tmp}\Tést\Straße\bridge",
        r"{tmp}\test\ıiİ\bridge",
        "C:\\",
    ],
)
def test_python_and_powershell_derive_the_same_root_scoped_mutex_name(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    root_template: str,
) -> None:
    root_value = root_template.replace("{tmp}", str(tmp_path))
    root_variants = {
        root_value,
        _swap_ascii_case(root_value),
        root_value.rstrip("\\/") + "\\",
    }
    helper = ROOT / ".agent-bridge" / "bin" / "WorkQueueTransaction.ps1"
    command = (
        f". '{helper}'; "
        "Get-WaggleDanceWorkQueueMutexName "
        "-BridgeRoot $env:WD_TEST_BRIDGE_ROOT"
    )

    derived_names: set[str] = set()
    for root_variant in root_variants:
        env = os.environ.copy()
        env["WD_TEST_BRIDGE_ROOT"] = root_variant
        completed = subprocess.run(
            [
                powershell,
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
            timeout=PROCESS_TIMEOUT_SECONDS,
            creationflags=CREATE_NO_WINDOW,
        )

        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        powershell_name = completed.stdout.strip()
        python_name = work_queue_mutex_name(root_variant)
        assert powershell_name == python_name
        derived_names.add(python_name)

    assert len(derived_names) == 1


def test_different_runtime_root_is_not_blocked_by_external_holder(
    tmp_path: Path,
) -> None:
    held_root = tmp_path / "held-runtime"
    independent_root = tmp_path / "independent-runtime"
    holder_ready = tmp_path / "holder-ready"
    release_holder = tmp_path / "release-holder"
    holder = _spawn_holder(held_root, holder_ready, release_holder)
    claimant: subprocess.Popen[str] | None = None
    try:
        _wait_for_path(holder_ready, holder)
        claimant = _spawn_python_claim(
            independent_root,
            agent="python-agent",
            task_id="independent-root-claim",
            write_scope="src/independent",
        )
        claim_result = _finish(claimant)
        assert claim_result[0] == 0, claim_result
        assert holder.poll() is None
        assert _claim_payloads(held_root) == []
        assert len(_claim_payloads(independent_root)) == 1
    finally:
        release_holder.write_text("release", encoding="ascii")
        _terminate(claimant)
        if holder.poll() is None:
            _finish(holder)


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
def test_external_python_holder_blocks_powershell_claim(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
) -> None:
    def spawn(bridge_root: Path, ready_path: Path) -> subprocess.Popen[str]:
        return _spawn_powershell_claim(
            powershell,
            bridge_root,
            agent="ps-agent",
            task_id=f"{engine_name}-blocked-claim",
            write_scope="src/powershell",
            ready_path=ready_path,
        )

    _assert_external_holder_blocks_claim(tmp_path, spawn)


def _assert_one_race_winner(
    bridge_root: Path,
    gate_path: Path,
    participants: list[tuple[str, subprocess.Popen[str]]],
) -> None:
    gate_path.write_text("go", encoding="ascii")
    results = [
        (label, *_finish(process)) for label, process in participants
    ]
    winners = [result for result in results if result[1] == 0]
    losers = [result for result in results if result[1] != 0]
    assert len(winners) == 1, results
    assert len(losers) == len(results) - 1, results

    payloads = _claim_payloads(bridge_root)
    assert len(payloads) == 1
    claim = payloads[0]
    assert isinstance(claim, dict)
    assert claim["mode"] == "write"
    assert isinstance(claim["task_id"], str) and claim["task_id"]
    assert isinstance(claim["agent"], str) and claim["agent"]
    assert isinstance(claim["write_scope"], list) and claim["write_scope"]


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
def test_python_and_powershell_overlapping_scope_race_has_one_winner(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
) -> None:
    bridge_root = tmp_path / "bridge-runtime"
    gate_path = tmp_path / "start-gate"
    python_ready = tmp_path / "python-ready"
    powershell_ready = tmp_path / "powershell-ready"
    python_claim = _spawn_python_claim(
        bridge_root,
        agent="python-agent",
        task_id=f"python-vs-{engine_name}",
        write_scope="src/shared",
        ready_path=python_ready,
        gate_path=gate_path,
    )
    powershell_claim = _spawn_powershell_claim(
        powershell,
        bridge_root,
        agent="ps-agent",
        task_id=f"{engine_name}-vs-python",
        write_scope="src/shared/child",
        ready_path=powershell_ready,
        gate_path=gate_path,
    )
    participants = [
        ("python", python_claim),
        (engine_name, powershell_claim),
    ]
    try:
        _wait_for_path(python_ready, python_claim)
        _wait_for_path(powershell_ready, powershell_claim)
        _assert_one_race_winner(bridge_root, gate_path, participants)
    finally:
        for _, process in participants:
            _terminate(process)


def test_python_ps5_ps7_overlapping_scope_race_has_one_winner(
    tmp_path: Path,
) -> None:
    powershell5 = shutil.which("powershell.exe")
    powershell7 = shutil.which("pwsh.exe")
    if not powershell5 or not powershell7:
        pytest.skip("three-runtime race requires Windows PowerShell 5.1 and PowerShell 7")

    bridge_root = tmp_path / "bridge-runtime"
    gate_path = tmp_path / "start-gate"
    ready_paths = [
        tmp_path / "python-ready",
        tmp_path / "ps5-ready",
        tmp_path / "ps7-ready",
    ]
    participants = [
        (
            "python",
            _spawn_python_claim(
                bridge_root,
                agent="python-agent",
                task_id="three-way-python",
                write_scope="packages/shared",
                ready_path=ready_paths[0],
                gate_path=gate_path,
            ),
        ),
        (
            "powershell-5.1",
            _spawn_powershell_claim(
                powershell5,
                bridge_root,
                agent="ps5-agent",
                task_id="three-way-ps5",
                write_scope="packages/shared/child",
                ready_path=ready_paths[1],
                gate_path=gate_path,
            ),
        ),
        (
            "powershell-7",
            _spawn_powershell_claim(
                powershell7,
                bridge_root,
                agent="ps7-agent",
                task_id="three-way-ps7",
                write_scope="packages",
                ready_path=ready_paths[2],
                gate_path=gate_path,
            ),
        ),
    ]
    try:
        for (_, process), ready_path in zip(participants, ready_paths, strict=True):
            _wait_for_path(ready_path, process)
        _assert_one_race_winner(bridge_root, gate_path, participants)
    finally:
        for _, process in participants:
            _terminate(process)


@pytest.mark.parametrize("corrupt_claim_set", [False, True])
def test_abandoned_owner_recovers_only_after_claim_set_validation(
    tmp_path: Path,
    corrupt_claim_set: bool,
) -> None:
    bridge_root = tmp_path / "bridge-runtime"
    holder_ready = tmp_path / "holder-ready"
    never_release = tmp_path / "never-release"
    kernel32, sentinel = _open_work_queue_mutex_sentinel(bridge_root)
    holder = _spawn_holder(bridge_root, holder_ready, never_release)
    try:
        _wait_for_path(holder_ready, holder)
        holder.kill()
        holder.wait(timeout=5)

        if corrupt_claim_set:
            claims_dir = bridge_root / "work_queue" / "claims"
            claims_dir.mkdir(parents=True, exist_ok=True)
            corrupt = claims_dir / "corrupt.json"
            corrupt.write_text("{not json", encoding="utf-8")
            with pytest.raises(WorkQueueError, match="unreadable claim file"):
                claim_task(
                    agent="python-agent",
                    task_id="post-abandon-corrupt",
                    summary="must validate abandoned state",
                    bridge_root=bridge_root,
                )
            assert corrupt.read_text(encoding="utf-8") == "{not json"
            assert list(claims_dir.glob("*.json")) == [corrupt]
        else:
            claim_task(
                agent="python-agent",
                task_id="post-abandon-clean",
                summary="recover clean abandoned state",
                bridge_root=bridge_root,
            )
            payloads = _claim_payloads(bridge_root)
            assert [payload["task_id"] for payload in payloads] == [
                "post-abandon-clean"
            ]
    finally:
        _terminate(holder)
        if not kernel32.CloseHandle(sentinel):
            raise OSError("failed to close WorkQueueV1 sentinel handle")


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
def test_release_and_heartbeat_never_resurrect_an_active_claim(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}"
    task_id = f"release-heartbeat-{engine_name}"
    claim_task(
        agent="shared-agent",
        task_id=task_id,
        summary="release heartbeat race",
        bridge_root=bridge_root,
    )
    gate_path = tmp_path / "start-gate"
    heartbeat_ready = tmp_path / "heartbeat-ready"
    release_ready = tmp_path / "release-ready"
    heartbeat_process = _spawn_python_heartbeat(
        bridge_root,
        agent="shared-agent",
        task_id=task_id,
        ready_path=heartbeat_ready,
        gate_path=gate_path,
    )
    release_process = _spawn_gated_powershell_operation(
        powershell,
        bridge_root,
        command=POWERSHELL_RELEASE_COMMAND,
        agent="shared-agent",
        task_id=task_id,
        ready_path=release_ready,
        gate_path=gate_path,
    )
    try:
        _wait_for_path(heartbeat_ready, heartbeat_process)
        _wait_for_path(release_ready, release_process)
        gate_path.write_text("go", encoding="ascii")
        heartbeat_result = _finish(heartbeat_process)
        release_result = _finish(release_process)
        assert heartbeat_result[0] in {0, 3}, heartbeat_result
        assert release_result[0] == 0, release_result
    finally:
        _terminate(heartbeat_process)
        _terminate(release_process)

    assert _claim_payloads(bridge_root) == []
    terminal = list((bridge_root / "work_queue" / "done").glob("*.json"))
    assert len(terminal) == 1
    payload = json.loads(terminal[0].read_text(encoding="utf-8"))
    assert payload["task_id"] == task_id
    assert payload["release_status"] == "done"


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
def test_stale_sweep_and_heartbeat_have_one_linearized_generation(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}"
    task_id = f"sweep-heartbeat-{engine_name}"
    claim_task(
        agent="shared-agent",
        task_id=task_id,
        summary="sweep heartbeat race",
        bridge_root=bridge_root,
    )
    claim_path = next(
        (bridge_root / "work_queue" / "claims").glob("*.json")
    )
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "claimed_at_utc": "2000-01-01T00:00:00Z",
            "last_heartbeat_utc": "2000-01-01T00:00:00Z",
            "lease_seconds": 1,
            "claim_lease_expires_utc": "2000-01-01T00:00:01Z",
        }
    )
    claim_path.write_text(json.dumps(payload), encoding="utf-8")

    gate_path = tmp_path / "start-gate"
    heartbeat_ready = tmp_path / "heartbeat-ready"
    sweep_ready = tmp_path / "sweep-ready"
    heartbeat_process = _spawn_python_heartbeat(
        bridge_root,
        agent="shared-agent",
        task_id=task_id,
        ready_path=heartbeat_ready,
        gate_path=gate_path,
    )
    sweep_process = _spawn_gated_powershell_operation(
        powershell,
        bridge_root,
        command=POWERSHELL_SWEEP_COMMAND,
        agent="shared-agent",
        task_id=task_id,
        ready_path=sweep_ready,
        gate_path=gate_path,
    )
    try:
        _wait_for_path(heartbeat_ready, heartbeat_process)
        _wait_for_path(sweep_ready, sweep_process)
        gate_path.write_text("go", encoding="ascii")
        heartbeat_result = _finish(heartbeat_process)
        sweep_result = _finish(sweep_process)
        assert heartbeat_result[0] in {0, 3}, heartbeat_result
        assert sweep_result[0] == 0, sweep_result
    finally:
        _terminate(heartbeat_process)
        _terminate(sweep_process)

    active = list((bridge_root / "work_queue" / "claims").glob("*.json"))
    terminal = list((bridge_root / "work_queue" / "done").glob("*.json"))
    assert len(active) + len(terminal) == 1
    if active:
        active_payload = json.loads(active[0].read_text(encoding="utf-8"))
        assert heartbeat_result[0] == 0
        assert active_payload["last_heartbeat_utc"] != "2000-01-01T00:00:00Z"
    else:
        terminal_payload = json.loads(terminal[0].read_text(encoding="utf-8"))
        assert terminal_payload["release_status"] == "stale_lease"


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize("operation", ["release", "stale-sweep"])
def test_powershell_terminal_metadata_failure_reports_committed_result(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    operation: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-{operation}"
    task_id = f"metadata-failure-{operation}-{engine_name}"
    now_utc = (
        datetime(2000, 1, 1, tzinfo=timezone.utc)
        if operation == "stale-sweep"
        else None
    )
    claim_task(
        agent="ps-agent",
        task_id=task_id,
        summary="terminal metadata failure",
        bridge_root=bridge_root,
        now_utc=now_utc,
        lease_seconds=1 if operation == "stale-sweep" else None,
    )
    env = _base_env(bridge_root)
    env["AGENT_BRIDGE_TEST_WORK_QUEUE_TERMINAL_METADATA_FAILURE"] = "1"
    if operation == "release":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_RELEASE_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            task_id,
        ]
    else:
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_SWEEP_SCRIPT),
            "-StaleSeconds",
            "1",
            "-Quiet",
        ]

    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    combined_output = completed.stdout + completed.stderr
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "metadata update failed" in combined_output
    assert task_id in completed.stdout
    assert _claim_payloads(bridge_root) == []
    terminal = list((bridge_root / "work_queue" / "done").glob("*.json"))
    assert len(terminal) == 1
    payload = json.loads(terminal[0].read_text(encoding="utf-8"))
    assert payload["task_id"] == task_id
    assert "release_status" not in payload


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
def test_powershell_legacy_heartbeat_materializes_environment_lease(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-legacy-heartbeat"
    task_id = f"legacy-heartbeat-{engine_name}"
    claim_task(
        agent="ps-agent",
        task_id=task_id,
        summary="legacy heartbeat lease",
        bridge_root=bridge_root,
    )
    claim_path = next(
        (bridge_root / "work_queue" / "claims").glob("*.json")
    )
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    env = _base_env(bridge_root)
    env["AGENT_BRIDGE_STALE_LEASE_SECONDS"] = "1000"

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_HEARTBEAT_SCRIPT),
            "-Agent",
            "ps-agent",
            "-Heartbeat",
            "-TaskId",
            task_id,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    refreshed = json.loads(claim_path.read_text(encoding="utf-8"))
    assert refreshed["lease_seconds"] == 1000
    heartbeat_at = datetime.fromisoformat(
        refreshed["last_heartbeat_utc"].replace("Z", "+00:00")
    )
    expires_at = datetime.fromisoformat(
        refreshed["claim_lease_expires_utc"].replace("Z", "+00:00")
    )
    assert (expires_at - heartbeat_at).total_seconds() == 1000


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize("operation", ["claim", "release", "stale-sweep"])
def test_powershell_committed_result_survives_append_failure(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    operation: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-{operation}"
    task_id = f"append-failure-{operation}-{engine_name}"
    if operation != "claim":
        claim_task(
            agent="ps-agent",
            task_id=task_id,
            summary="event append failure",
            bridge_root=bridge_root,
            now_utc=(
                datetime(2000, 1, 1, tzinfo=timezone.utc)
                if operation == "stale-sweep"
                else None
            ),
            lease_seconds=1 if operation == "stale-sweep" else None,
        )

    env = _base_env(bridge_root)
    env["AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE"] = "Append"
    if operation == "claim":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_CLAIM_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            task_id,
            "-Summary",
            "event append failure",
        ]
    elif operation == "release":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_RELEASE_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            task_id,
        ]
    else:
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_SWEEP_SCRIPT),
            "-StaleSeconds",
            "1",
            "-Quiet",
        ]

    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert task_id in completed.stdout
    assert "committed, but bridge event emit failed" in (
        completed.stdout + completed.stderr
    ) or operation == "stale-sweep"
    spool = list((bridge_root / "spool").glob("failed-append-*.jsonl"))
    assert len(spool) == 1
    if operation == "claim":
        payloads = _claim_payloads(bridge_root)
        assert len(payloads) == 1
        assert payloads[0]["task_id"] == task_id
    else:
        assert _claim_payloads(bridge_root) == []
        terminal = list((bridge_root / "work_queue" / "done").glob("*.json"))
        assert len(terminal) == 1


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize(
    "invalid_field,invalid_value",
    [
        ("lease_seconds", -1),
        ("capabilities", {"unexpected": "object"}),
        ("capabilities", None),
    ],
)
def test_powershell_mutations_fail_closed_on_invalid_canonical_claim_fields(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    invalid_field: str,
    invalid_value: object,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-{invalid_field}"
    claims_dir = bridge_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True)
    malformed = {
        "agent": "existing-agent",
        "task_id": "existing-task",
        "summary": "invalid canonical field",
        "mode": "read-only",
        "write_scope": [],
        "claimed_at_utc": "2026-07-26T00:00:00Z",
        "last_heartbeat_utc": "2026-07-26T00:00:00Z",
    }
    malformed[invalid_field] = invalid_value
    malformed_path = claims_dir / "existing-task.json"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
    before = malformed_path.read_bytes()
    env = _base_env(bridge_root)

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_CLAIM_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            "new-task",
            "-Summary",
            "must fail closed",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    assert completed.returncode != 0
    assert f"invalid {invalid_field}" in (completed.stdout + completed.stderr)
    assert malformed_path.read_bytes() == before
    assert len(list(claims_dir.glob("*.json"))) == 1


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize("lease_seconds", [0, -1])
def test_powershell_explicit_nonpositive_claim_lease_is_rejected(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    lease_seconds: int,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-{lease_seconds}"
    env = _base_env(bridge_root)
    env["AGENT_BRIDGE_STALE_LEASE_SECONDS"] = "1000"

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_CLAIM_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            "invalid-explicit-lease",
            "-Summary",
            "must reject explicit nonpositive lease",
            "-LeaseSeconds",
            str(lease_seconds),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    assert completed.returncode != 0
    assert "must be positive" in (completed.stdout + completed.stderr)
    assert _claim_payloads(bridge_root) == []


@pytest.mark.parametrize("engine_name,powershell", POWERSHELL_CASES)
@pytest.mark.parametrize(
    "operation",
    ["claim", "force", "release", "heartbeat", "stale-sweep"],
)
def test_every_powershell_mutator_refuses_an_unlocked_fallback(
    tmp_path: Path,
    engine_name: str,
    powershell: str,
    operation: str,
) -> None:
    bridge_root = tmp_path / f"bridge-runtime-{engine_name}-{operation}"
    claim_task(
        agent="ps-agent",
        task_id="locked-existing",
        summary="existing generation",
        mode="write",
        write_scope=("src/existing",),
        bridge_root=bridge_root,
    )
    claims_dir = bridge_root / "work_queue" / "claims"
    before = {
        path.name: path.read_bytes() for path in claims_dir.glob("*.json")
    }
    env = _base_env(bridge_root)
    env["AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE"] = "1"

    if operation == "claim":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_CLAIM_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            "locked-new",
            "-Summary",
            "must not claim unlocked",
        ]
    elif operation == "force":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_CLAIM_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            "locked-existing",
            "-Summary",
            "must not refresh unlocked",
            "-Mode",
            "write",
            "-WriteScope",
            "src/existing",
            "-Force",
        ]
    elif operation == "release":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_RELEASE_SCRIPT),
            "-Agent",
            "ps-agent",
            "-TaskId",
            "locked-existing",
        ]
    elif operation == "heartbeat":
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_HEARTBEAT_SCRIPT),
            "-Agent",
            "ps-agent",
            "-Heartbeat",
            "-TaskId",
            "locked-heartbeat-event",
        ]
    else:
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_SWEEP_SCRIPT),
            "-StaleSeconds",
            "1",
            "-Quiet",
        ]

    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        creationflags=CREATE_NO_WINDOW,
    )

    if operation == "heartbeat":
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert "could not lock/bump work-queue leases" in (
            completed.stdout + completed.stderr
        )
    else:
        assert completed.returncode != 0
        assert "mutex construction failure" in (
            completed.stdout + completed.stderr
        )
    after = {
        path.name: path.read_bytes() for path in claims_dir.glob("*.json")
    }
    assert after == before
