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
    $fake | Add-Member -MemberType ScriptMethod -Name ReleaseMutex -Value {
        $log.Add('release')
        if ($this.Mode -eq 'releasefail') { throw [System.ApplicationException]::new('not owned') }
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $log.Add('dispose')
        if ($this.Mode -eq 'disposefail') { throw [System.ObjectDisposedException]::new('fake') }
    }
    return $fake
}
$match = { param($n, $m) 'match' }
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


def _invoke(tmp_path: Path, mode: str, extra: str = "", timeout_ms: int = 300, body: str | None = None,
            inspector: str = "$match", claim: Path | None = None) -> str:
    claim = _claim(tmp_path) if claim is None else claim
    body = f"Set-Content -LiteralPath {_q(tmp_path / 'ran.txt')} ran" if body is None else body
    acl = "" if inspector is None else f"-AclInspector {inspector} "
    return (f"{extra}\n$factory = {{ param($n) $log.Add('create'); New-FakeMutex {_q(mode)} }}\n"
            f"try {{ Invoke-BridgeV2QueueLocked -RuntimeRoot {_q(tmp_path / 'runtime')} -ClaimPath {_q(claim)} "
            f"-TimeoutMs {timeout_ms} -MutexFactory $factory {acl}-ScriptBlock {{ {body} }}; "
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
def test_a_failed_release_is_reported_but_never_masks_the_body(tmp_path, shell):
    clean = _ps(shell, _invoke(tmp_path / "clean", "releasefail"))
    assert clean.returncode == 0, clean.stderr
    report = json.loads(clean.stdout.strip().splitlines()[-1])
    assert "stays owned" in report["outcome"] and (tmp_path / "clean" / "ran.txt").exists()
    assert report["log"] == ["create", "wait:300", "release", "dispose"]
    failed = _ps(shell, _invoke(tmp_path / "failed", "releasefail", body="throw 'body boom'"))
    assert failed.returncode == 0, failed.stderr
    report = json.loads(failed.stdout.strip().splitlines()[-1])
    assert "body boom" in report["outcome"] and "stays owned" not in report["outcome"]
    assert report["log"] == ["create", "wait:300", "release", "dispose"]


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("factory,expected_log", [("throw 'denied'", "create"), ("$null", "create"),
                                                  ("New-FakeMutex 'ok'; New-FakeMutex 'ok'", "create,dispose,dispose")])
def test_a_refused_or_ambiguous_factory_waits_on_nothing(tmp_path, shell, factory, expected_log):
    claim = _claim(tmp_path)
    result = _ps(shell, f"$factory = {{ param($n) $log.Add('create'); {factory} }}\n"
                        f"try {{ Invoke-BridgeV2QueueLocked -RuntimeRoot {_q(tmp_path / 'runtime')} "
                        f"-ClaimPath {_q(claim)} -MutexFactory $factory -AclInspector $match -ScriptBlock {{ 'ran' }} }} "
                        "catch { 'ERR:' + $_.Exception.Message }\n'LOG:' + ($log -join ',')")
    lines = result.stdout.splitlines()
    assert "ran" not in lines and any("create/open refused" in line for line in lines)
    assert lines[-1] == "LOG:" + expected_log   # no wait, no release: only surplus objects are disposed


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_missing_claims_directory_is_refused_before_any_mutex(tmp_path, shell):
    claim = tmp_path / "runtime" / "work_queue" / "claims" / "task.json"   # directory NOT created
    result = _ps(shell, "$factory = { param($n) $log.Add('create'); New-FakeMutex 'ok' }\n"
                        f"try {{ Invoke-BridgeV2QueueLocked -RuntimeRoot {_q(tmp_path / 'runtime')} "
                        f"-ClaimPath {_q(claim)} -MutexFactory $factory -AclInspector $match -ScriptBlock {{ 'ran' }} }} "
                        "catch { 'ERR:' + $_.Exception.Message }\n'LOG:' + ($log -join ',')")
    assert "claims directory is missing" in result.stdout
    assert "create" not in result.stdout.split("LOG:")[-1]


@pytest.mark.skipif(WINDOWS, reason="off Windows only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_off_windows_the_twin_has_no_fallback_lock(tmp_path, shell):
    result = _ps(shell, f"Invoke-BridgeV2QueueLocked -RuntimeRoot 'C:/runtime' -ClaimPath {_q(tmp_path / 'c.json')} "
                        "-ScriptBlock { 'ran' }")
    assert result.returncode != 0 and "ran" not in result.stdout


# -- Tools 8c6066ff: F8-DACL-INHERITED, F8-CLEANUP, F8-WALL-CLOCK, F8-ROOT-CLAIM ----------

def _report(result) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_without_a_reviewed_inspector_the_twin_refuses_before_any_create(tmp_path, shell):
    report = _report(_ps(shell, _invoke(tmp_path, "ok", inspector=None)))
    assert "no reviewed inspector seam" in report["outcome"] and report["log"] == []
    assert not (tmp_path / "ran.txt").exists()


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("inspector", ["{ param($n, $m) 'mismatch' }", "{ param($n, $m) 'Match' }",
                                       "{ param($n, $m) $true }", "{ param($n, $m) }",
                                       "{ param($n, $m) throw 'READ_CONTROL refused' }"])
def test_acl_evidence_other_than_an_exact_match_refuses_before_the_wait(tmp_path, shell, inspector):
    report = _report(_ps(shell, _invoke(tmp_path, "ok", inspector=inspector)))
    assert "not a match" in report["outcome"] and report["log"] == ["create", "dispose"]   # opened, never waited
    assert not (tmp_path / "ran.txt").exists()


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_failed_dispose_is_never_success_and_never_masks_the_body(tmp_path, shell):
    clean = _report(_ps(shell, _invoke(tmp_path / "clean", "disposefail")))
    assert "cleanup failed after a clean body" in clean["outcome"] and (tmp_path / "clean" / "ran.txt").exists()
    assert clean["log"] == ["create", "wait:300", "release", "dispose"]      # every cleanup still attempted
    failed = _report(_ps(shell, _invoke(tmp_path / "failed", "disposefail", body="throw 'body boom'")))
    assert "body boom" in failed["outcome"] and "cleanup failed" not in failed["outcome"]
    assert failed["log"] == ["create", "wait:300", "release", "dispose"]


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_non_contention_claim_lock_failure_is_refused_at_once_with_its_real_type(tmp_path, shell):
    import stat
    lock = _claim(tmp_path).with_name("task.json.lock")
    lock.write_text("")
    os.chmod(lock, stat.S_IREAD)                         # a plain read-only file: passes the leaf walk, open denied
    report = _report(_ps(shell, _invoke(tmp_path, "ok", timeout_ms=5000)))
    assert "claim lock open refused: UnauthorizedAccessException" in report["outcome"]
    assert "busy" not in report["outcome"] and not (tmp_path / "ran.txt").exists()
    assert report["log"] == ["create", "wait:5000", "release", "dispose"]


# -- Tools 51ada: W-LOCK-PATH, W-PRIMARY-WARNING --------------------------------------

@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_directory_at_the_lock_leaf_is_refused_before_the_open(tmp_path, shell):
    (_claim(tmp_path).with_name("task.json.lock")).mkdir()
    report = _report(_ps(shell, _invoke(tmp_path, "ok")))
    assert "the claim lock path is a directory" in report["outcome"] and not (tmp_path / "ran.txt").exists()
    assert report["log"] == ["create", "wait:300", "release", "dispose"]      # refused inside the hold, then released


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_linked_lock_leaf_is_refused_before_the_open(tmp_path, shell):
    target = tmp_path / "elsewhere.lock"
    target.write_text("")
    lock = _claim(tmp_path).with_name("task.json.lock")
    try:
        os.symlink(target, lock)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    report = _report(_ps(shell, _invoke(tmp_path, "ok")))
    assert "link/reparse point" in report["outcome"] and not (tmp_path / "ran.txt").exists()
    assert report["log"] == ["create", "wait:300", "release", "dispose"]
    assert target.read_text() == ""                                           # nothing opened through the link


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_a_stop_warning_preference_never_replaces_the_primary(tmp_path, shell):
    stop = "$WarningPreference = 'Stop'"
    failed = _report(_ps(shell, _invoke(tmp_path / "failed", "disposefail", extra=stop, body="throw 'body boom'")))
    assert "body boom" in failed["outcome"] and "WarningPreference" not in failed["outcome"]
    assert failed["log"] == ["create", "wait:300", "release", "dispose"]      # every cleanup still attempted
    clean = _report(_ps(shell, _invoke(tmp_path / "clean", "disposefail", extra=stop)))
    assert "cleanup failed after a clean body" in clean["outcome"]            # the twin: still never a success


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("claim,fragment", [
    ("outside/work_queue/claims/task.json", "must be <root>/work_queue/claims/<name>.json"),
    ("runtime/work_queue/claims/sub/task.json", "must be <root>/work_queue/claims/<name>.json"),
    ("runtime/work_queue/other/task.json", "must be <root>/work_queue/claims/<name>.json"),
    ("runtime/work_queue/claims/task.json:evil", "alternate stream is forbidden"),
])
def test_a_claim_outside_the_root_claims_directory_is_refused_before_any_create(tmp_path, shell, claim, fragment):
    report = _report(_ps(shell, _invoke(tmp_path, "ok", claim=tmp_path / claim)))
    assert fragment in report["outcome"] and report["log"] == []


@pytest.mark.skipif(not WINDOWS, reason="the twin accepts drive-letter roots only")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_the_claim_lock_budget_is_validated_separately(tmp_path, shell):
    report = _report(_ps(shell, _invoke(tmp_path, "ok", extra="", body="'ran'").replace(
        "-TimeoutMs 300", "-TimeoutMs 300 -ClaimLockTimeoutMs 70000")))
    assert "ClaimLockTimeoutMs must be within 1..60000" in report["outcome"] and report["log"] == []


def test_the_claim_lock_wait_is_a_monotonic_budget_that_retries_only_contention():
    source = TWIN.read_text(encoding="utf-8")
    assert "Get-Date" not in source and "[Diagnostics.Stopwatch]::StartNew()" in source
    assert "Test-BridgeV2LockContention" in source and "-in @(32, 33)" in source


def test_the_twin_defines_functions_only_and_reads_no_environment():
    source = TWIN.read_text(encoding="utf-8")
    assert "$env:" not in source and "[Environment]::GetEnvironmentVariable" not in source
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    top = [line for line in code.splitlines() if line and not line.startswith((" ", "\t", "function ", "}", "<#", "#>"))]
    assert top == [], top   # nothing but function definitions at the top level (the comment block aside)
