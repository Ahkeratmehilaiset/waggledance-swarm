# SPDX-License-Identifier: BUSL-1.1
"""PowerShell twin fixtures for the v2 queue ports (authored per operator directive; NOT executed yet).

The twin must derive the SAME runtime-root identity, mutex name and sibling lock path as
Python, and run the same lifecycle. Every lifecycle case uses an injected fake mutex:
no live named mutex is created or waited on. Roots are tmp_path directories.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_v2_queue_transactions import QueueTransactionError, claim_lock_path, mutex_name

ROOT = Path(__file__).resolve().parents[2]
TWIN = ROOT / ".agent-bridge" / "bin" / "BridgeV2QueuePorts.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
WINDOWS = os.name == "nt"

FAKE = r"""
$log = [System.Collections.Generic.List[string]]::new()
function New-FakeMutex([string]$Mode) {
    $fake = [pscustomobject]@{ Mode = $Mode }
    $fake | Add-Member -MemberType ScriptMethod -Name WaitOne -Value {
        param($ms)
        $log.Add("wait:$ms")
        if ($this.Mode -eq 'timeout') { return $false }
        if ($this.Mode -eq 'abandoned') { throw [System.Threading.AbandonedMutexException]::new() }
        return $true
    }
    $fake | Add-Member -MemberType ScriptMethod -Name ReleaseMutex -Value { $log.Add('release') }
    $fake | Add-Member -MemberType ScriptMethod -Name Dispose -Value { $log.Add('dispose') }
    return $fake
}
"""


def _q(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ps(shell: str, body: str) -> subprocess.CompletedProcess:
    script = f". {_q(TWIN)}\n$ErrorActionPreference = 'Stop'\n{FAKE}\n{body}"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WD_", "AGENT_BRIDGE_"))}
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, timeout=60, env=env)


def _claim(tmp_path: Path) -> Path:
    claim = tmp_path / "runtime" / "work_queue" / "claims" / "task.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    return claim


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("suffix", ["runtime", "Runtime Root", "a/./b", "trailing\\"])
def test_the_twin_derives_the_same_mutex_name_as_python(tmp_path, shell, suffix):
    root = tmp_path / "x"
    root.mkdir()
    raw = str(root) + "\\" + suffix
    result = _ps(shell, f"Get-BridgeV2QueueMutexName -RuntimeRoot {_q(raw)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == mutex_name(raw)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_the_twin_uses_the_exact_legacy_sibling_lock_spelling(tmp_path, shell):
    claim = _claim(tmp_path)
    result = _ps(shell, f"Get-BridgeV2ClaimLockPath -ClaimPath {_q(claim)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(claim) + ".lock" == str(claim_lock_path(claim))  # "$ClaimPath.lock"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("raw", ["relative\\root", "\\\\server\\share\\root", "C:root", "C:\\a\\..\\b",
                                 "C:\\root.\\x", "C:\\ROOT~1\\x", "C:\\caf\u00e9\\x"])
def test_the_twin_and_python_refuse_the_same_roots(tmp_path, shell, raw):
    result = _ps(shell, f"Get-BridgeV2QueueMutexName -RuntimeRoot {_q(raw)}")
    assert result.returncode != 0
    if WINDOWS:
        with pytest.raises((QueueTransactionError, OSError)):
            mutex_name(raw)


def _invoke(tmp_path: Path, mode: str, extra: str = "", timeout_ms: int = 300) -> str:
    claim = _claim(tmp_path)
    marker = tmp_path / "ran.txt"
    return (f"{extra}\n$factory = {{ param($n) $log.Add('create'); New-FakeMutex {_q(mode)} }}\n"
            f"try {{ Invoke-BridgeV2QueueLocked -RuntimeRoot {_q(tmp_path / 'runtime')} -ClaimPath {_q(claim)} "
            f"-TimeoutMs {timeout_ms} -MutexFactory $factory -ScriptBlock {{ Set-Content -LiteralPath {_q(marker)} ran }}; "
            f"$outcome = 'ok' }} catch {{ $outcome = 'ERR:' + $_.Exception.Message }}\n"
            "[pscustomobject]@{ outcome = $outcome; log = @($log) } | ConvertTo-Json -Compress")


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("mode,expected_log,fragment,ran", [
    ("ok", ["create", "wait:300", "release", "dispose"], None, True),
    ("timeout", ["create", "wait:300", "dispose"], "runtime-root mutex busy", False),
    ("abandoned", ["create", "wait:300", "release", "dispose"], "reconcile the WAL", False),
])
def test_the_twin_lifecycle_matches_python(tmp_path, shell, mode, expected_log, fragment, ran):
    result = _ps(shell, _invoke(tmp_path, mode))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["log"] == expected_log
    assert (report["outcome"] == "ok") is (fragment is None)
    if fragment:
        assert fragment in report["outcome"]
    assert (tmp_path / "ran.txt").exists() is ran
    if ran:
        assert (tmp_path / "runtime" / "work_queue" / "claims" / "task.json.lock").exists()


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_busy_legacy_sibling_lock_releases_the_mutex_and_runs_nothing(tmp_path, shell):
    lock = _claim(tmp_path).with_name("task.json.lock")
    hold = (f"$held = New-Object System.IO.FileStream({_q(lock)}, [IO.FileMode]::OpenOrCreate, "
            "[IO.FileAccess]::ReadWrite, [IO.FileShare]::None)")
    result = _ps(shell, _invoke(tmp_path, "ok", extra=hold, timeout_ms=200))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert "claim lock busy" in report["outcome"] and not (tmp_path / "ran.txt").exists()
    assert report["log"] == ["create", "wait:200", "release", "dispose"]   # mutex first, released after


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_missing_claims_directory_is_refused_before_any_mutex(tmp_path, shell):
    claim = tmp_path / "runtime" / "work_queue" / "claims" / "task.json"   # directory NOT created
    result = _ps(shell, "$factory = { param($n) $log.Add('create'); New-FakeMutex 'ok' }\n"
                        f"try {{ Invoke-BridgeV2QueueLocked -RuntimeRoot {_q(tmp_path / 'runtime')} "
                        f"-ClaimPath {_q(claim)} -MutexFactory $factory -ScriptBlock {{ 'ran' }} }} "
                        "catch { 'ERR:' + $_.Exception.Message }\n'LOG:' + ($log -join ',')")
    assert "claims directory is missing" in result.stdout
    assert "create" not in result.stdout.split("LOG:")[-1]


@pytest.mark.skipif(WINDOWS, reason="off Windows only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_off_windows_the_twin_has_no_fallback_lock(tmp_path, shell):
    result = _ps(shell, f"Invoke-BridgeV2QueueLocked -RuntimeRoot 'C:/runtime' -ClaimPath {_q(tmp_path / 'c.json')} "
                        "-ScriptBlock { 'ran' }")
    assert result.returncode != 0 and "ran" not in result.stdout


def test_the_twin_defines_functions_only_and_reads_no_environment():
    source = TWIN.read_text(encoding="utf-8")
    assert "$env:" not in source and "[Environment]::GetEnvironmentVariable" not in source
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    top = [line for line in code.splitlines() if line and not line.startswith((" ", "\t", "function ", "}", "<#", "#>"))]
    assert top == [], top   # nothing but function definitions at the top level (the comment block aside)
