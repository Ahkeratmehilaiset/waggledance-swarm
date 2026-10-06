"""Cold startup updates Grok Build without a version pin or a consultation."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wd_grok_helper as helper
from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import load


@pytest.fixture
def update_installation(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    helper.write_state(root, {"schema": helper.SCHEMA, "status": "answered",
                             "last_attempt_utc": "2026-01-01T00:00:00+00:00"})
    executable = tmp_path / "grok.exe"
    executable.write_bytes(b"test placeholder, never executed")
    return root, executable


@pytest.mark.parametrize("failure", [None, "before", "update", "after", "timeout"])
def test_update_uses_bare_command_and_checks_every_exit(update_installation, failure):
    root, executable = update_installation
    original = (root / "hourly-state.json").read_bytes()
    ledger = root / helper.LEDGER_NAME
    ledger.write_bytes(b'{"existing_consultation":"preserve"}\n')
    original_ledger = ledger.read_bytes()
    calls = []

    def runner(command, **kwargs):
        stage = ("before", "update", "after")[len(calls)]
        calls.append(command)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] > 0
        # Updating and consultation share the same OS lock throughout all probes.
        with pytest.raises(OSError):
            with helper.exclusive(root):
                pytest.fail("Grok update released the consultation lock")
        if failure == "timeout" and stage == "update":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=7 if failure == stage else 0,
                               stdout="grok test-before" if stage == "before" else "grok test-after",
                               stderr="simulated error" if failure == stage else "")

    if failure:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            helper.update_cli(root, executable, runner=runner)
    else:
        report = helper.update_cli(root, executable, runner=runner)
        assert report["update_status"] == "updated"
        assert report["before"] == "grok test-before"
        assert report["after"] == "grok test-after"
        assert report["update_command"] == "grok update"
    assert calls == [[str(executable), "--version"], [str(executable), "update"],
                     [str(executable), "--version"]][:len(calls)]
    assert len(calls) == {None: 3, "before": 1, "update": 2, "after": 3, "timeout": 2}[failure]
    assert (root / "hourly-state.json").read_bytes() == original
    assert ledger.read_bytes() == original_ledger


@pytest.mark.parametrize("state", ["reserved", "interrupted_or_unknown", "future", "missing", "malformed"])
def test_update_preserves_unresolved_guard(update_installation, state):
    root, executable = update_installation
    if state == "missing":
        (root / "hourly-state.json").unlink()
    elif state == "malformed":
        (root / "hourly-state.json").write_text("{}", encoding="utf-8")
    else:
        helper.write_state(root, {"schema": helper.SCHEMA,
                                 "status": "answered" if state == "future" else state,
                                 "last_attempt_utc": "2099-01-01T00:00:00+00:00" if state == "future"
                                 else "2026-01-01T00:00:00+00:00"})
    def forbidden(*args, **kwargs):
        pytest.fail("An unresolved consultation must block software updates")
    with pytest.raises(ValueError):
        helper.update_cli(root, executable, runner=forbidden)


def test_busy_lock_blocks_update(update_installation):
    root, executable = update_installation
    with helper.exclusive(root), pytest.raises(OSError):
        helper.update_cli(root, executable, runner=lambda *a, **kw: pytest.fail("busy"))


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["idle", "grok_busy", "codex_busy", "skip", "failure", "bad_receipt"])
def test_startup_grok_update_guard_and_failure(ps, case):
    script = load(REBOOT / "start-wd-all.ps1", "Test-WdCliUpdateDeferred")
    script += load(REBOOT / "start-wd-all.ps1", "Get-WdCliUpdateStatus")
    script += load(REBOOT / "start-wd-all.ps1", "Invoke-WdGrokCliUpdate")
    script += f"$case='{case}'\n" + r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$script:calls=0
function Get-CimInstance { param($ClassName,$ErrorAction)
 if($case -eq 'grok_busy'){[pscustomobject]@{Name='grok.exe'}}
 if($case -eq 'codex_busy'){[pscustomobject]@{Name='codex.exe'}}
}
function Fake-Wrapper {
 param($Tool,[switch]$VerifyPackage,[Parameter(ValueFromRemainingArguments)][string[]]$ToolArguments)
 if($Tool -cne 'tools/wd_grok_helper.py' -or -not $VerifyPackage -or
    ($ToolArguments -join ' ') -cne '--update-cli'){throw 'Wrong update invocation'}
 $script:calls++
 $global:LASTEXITCODE=$(if($case -eq 'failure'){7}else{0})
 @{schema='wd.grok-cli-update.v1';update_status=$(if($case -eq 'bad_receipt'){'pending'}else{'updated'});
   before='grok old';after='grok new';update_command='grok update'}|ConvertTo-Json -Compress
}
$failed=$false; $report=$null
try {$report=Invoke-WdGrokCliUpdate -Wrapper 'Fake-Wrapper' -Skip:($case -eq 'skip')}
catch {$failed=$true}
@{failed=$failed;calls=$script:calls;status=$(if($report){$report.update_status}else{$null})}|ConvertTo-Json -Compress
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result == {
        "failed": case in ("failure", "bad_receipt"),
        "calls": 0 if case in ("grok_busy", "skip") else 1,
        "status": {"grok_busy": "deferred_live_sessions", "skip": "operator_skipped",
                   "failure": None, "bad_receipt": None}.get(case, "updated"),
    }


def test_all_updates_precede_model_resolution_and_agent_launch():
    source = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8-sig")
    update = source.index("$grokUpdateRecord = Invoke-WdGrokCliUpdate")
    assert source.index("DRY RUN: no updates") < update
    assert source.index("-Path $claudeUpdateCurrentPath -Arguments @('update')") < update
    assert update < source.index("$cliVersionRecord =")
    assert update < source.index("$supervisorBootstrapResult =")
    assert update < source.index("Write-Host 'Resolving the current Grok model")
    assert update < source.index("Start-Process -FilePath $wtPath")
    assert "grok_build = $grokUpdateRecord" in source


def test_update_cli_main_returns_evidence_not_status(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["helper", "--update-cli"])
    monkeypatch.setattr(helper, "update_cli", lambda root, executable: {
        "schema": "wd.grok-cli-update.v1", "update_status": "updated",
        "executable": str(executable)})
    assert helper.main() == 0
    assert json.loads(capsys.readouterr().out)["executable"] == str(tmp_path / ".grok/bin/grok.exe")


@pytest.mark.parametrize("args", [["--status"], ["--prompt-file", "evidence.md"],
                                  ["--requested-by", "fable-5"], ["--task-id", "task"]])
def test_update_cli_rejects_consultation_arguments(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["helper", "--update-cli", *args])
    assert helper.main() == 2
