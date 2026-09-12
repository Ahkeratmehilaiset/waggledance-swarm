from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tools.wd_grok_helper import SCHEMA, consult, exclusive, status, write_state

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def seed(root, age=3600):
    write_state(root, {"schema": SCHEMA, "last_attempt_utc": (NOW-timedelta(seconds=age)).isoformat(), "status": "answered"})


def test_one_call_per_rolling_hour_survives_reload(tmp_path):
    seed(tmp_path)
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        assert status(tmp_path, NOW)["status"] == "reserved"
        assert command[command.index("--tools") + 1] == ""
        assert "--no-subagents" in command and "--always-approve" not in command
        return SimpleNamespace(returncode=0, stdout="Evidence-based advice")
    assert consult(tmp_path, "test/task", "Review supplied evidence", ["fake"], runner=runner, now=NOW)["status"] == "answered"
    assert not status(tmp_path, NOW+timedelta(seconds=3599))["eligible"]
    assert consult(tmp_path, "next", "Second ask", ["fake"], runner=runner, now=NOW)["decision"] == "deferred_hourly_limit"
    assert len(calls) == 1
    assert status(tmp_path, NOW+timedelta(hours=1))["eligible"]


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_failure_consumes_hour(tmp_path, failure):
    seed(tmp_path)
    def runner(*args, **kwargs):
        if failure == "exception":
            raise TimeoutError()
        return SimpleNamespace(returncode=1, stdout="")
    result = consult(tmp_path, "test", "Ask", ["fake"], runner=runner, now=NOW)
    assert result["status"] == "failed"
    assert not status(tmp_path, NOW)["eligible"]


def test_corrupt_or_missing_state_blocks(tmp_path):
    with pytest.raises(ValueError):
        status(tmp_path, NOW)
    (tmp_path / "hourly-state.json").write_text("{}")
    with pytest.raises(ValueError):
        status(tmp_path, NOW)


def test_clock_rollback_does_not_open_budget(tmp_path):
    seed(tmp_path, age=-500)
    assert not status(tmp_path, NOW)["eligible"]


def test_status_is_read_only(tmp_path):
    seed(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    status(tmp_path, NOW)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_competing_process_lock_blocks_second_request(tmp_path):
    seed(tmp_path)
    with exclusive(tmp_path):
        with pytest.raises(OSError):
            with exclusive(tmp_path):
                pytest.fail("Second lock acquired")


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
PS = shutil.which("powershell.exe") or shutil.which("pwsh")


def test_reboot_uses_pinned_passive_grok_entrypoint():
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text())
    assert definition["python_entrypoints"]["grok_helper"] == "tools/wd_grok_helper.py"
    assert "tools/wd_grok_helper.py" in definition["python_files"]
    assert "Initialize-WdGrokRecovery.ps1" in (REBOOT / "start-wd-all.ps1").read_text()
    assert "Invoke-WdGrok.ps1 -Status" in (REBOOT / "start-wd-agent.ps1").read_text()
    wrapper = (REBOOT / "Invoke-WdGrok.ps1").read_text()
    assert "WD_REBOOT_EXPECTED_MANIFEST_HASH" in wrapper
    assert "-VerifyPackage" in wrapper


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
def test_passive_recovery_preserves_budget_and_legacy_history(tmp_path):
    # All machine paths and OS probes are isolated; no real task or model call.
    machine = tmp_path / "machine"
    reports = machine / "grok-scout-reports"
    reports.mkdir(parents=True)
    legacy = machine / "Update-GrokWorktree.ps1"
    legacy.write_text("old worktree updater", encoding="utf-8")
    report = reports / "grok-old-result.md"
    report.write_text("Previous result", encoding="utf-8")
    script = tmp_path / "recovery.ps1"
    source = (REBOOT / "Initialize-WdGrokRecovery.ps1").read_text()
    script.write_text(source.replace("C:\\Python", str(machine)), encoding="utf-8")
    command = f"""
    $ErrorActionPreference = 'Stop'
    function Get-ScheduledTask {{ @() }}
    function Get-CimInstance {{ @() }}
    & '{script}' -Apply | Out-Null
    $before = [IO.File]::ReadAllText('{reports / 'hourly-state.json'}')
    & '{script}' | Out-Null
    if ([IO.File]::ReadAllText('{reports / 'hourly-state.json'}') -cne $before) {{ throw 'Budget reset' }}
    """
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((reports / "hourly-state.json").read_text())
    assert state["status"] == "initialized_conservative_cooldown"
    assert Path(state["previous_report"]) == report
    assert not status(reports)["eligible"]
    assert report.read_text() == "Previous result"
    assert "throw 'Use" in legacy.read_text()
    assert any(p.read_text() == "old worktree updater" for p in
               (machine / "wd-reboot-backups").rglob("Update-GrokWorktree.ps1"))


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
def test_active_grok_blocks_migration_without_mutation(tmp_path):
    script = tmp_path / "recovery.ps1"
    source = (REBOOT / "Initialize-WdGrokRecovery.ps1").read_text()
    machine = tmp_path / "absent-machine"
    script.write_text(source.replace("C:\\Python", str(machine)), encoding="utf-8")
    command = f"""
    $ErrorActionPreference = 'Stop'
    function Get-ScheduledTask {{ @() }}
    function Get-CimInstance {{ [pscustomobject]@{{ Name='grok.exe' }} }}
    & '{script}' -Apply
    """
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "invocation is active" in result.stderr
    assert not machine.exists()
