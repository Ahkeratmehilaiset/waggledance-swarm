"""Lease-bump error visibility in the legacy PowerShell heartbeat (RCO2 5416a7ab F2; Lead 5d166b86).

Update-BridgeClaimLease skips a round (returns 0, warned) ONLY for the two transient root-mutex refusals, busy and
abandoned. A permanent failure (a missing mutex helper, a root with no canonical form) propagates with its original
error. Start-BridgeHeartbeat records that error, non-terminating, in its job's error stream and keeps writing
session beats, so the claim's owner stays provably live while its lease expiry visibly stops advancing.
Runs on the participation fixture (private bin copy, Local legacy mutex names, temp root, stub event writer).
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_bridge_v2_legacy_mutex_participation import (AGENT, SESSION, SHELLS, TOKEN, _abandon, _claim, _held,  # noqa: F401
                                                       bridge, mutex_name)

pytestmark = pytest.mark.skipif(not SHELLS, reason="Windows PowerShell hosts only")


def _env(runtime: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env.update(AGENT_BRIDGE_RUNTIME_ROOT=str(runtime), AGENT_BRIDGE_OWNER_SESSION_ID=SESSION,
               AGENT_BRIDGE_OWNER_TOKEN=TOKEN)
    return env


def _ps(shell, bridge, command, runtime=None):
    return subprocess.run([shutil.which(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", command], cwd=bridge[1], env=_env(runtime or bridge[2]), capture_output=True,
                          text=True, timeout=180)


def _bump(shell, bridge, root):
    command = (f". '{bridge[0] / 'ClaimLeaseHeartbeat.ps1'}'; "
               f"try {{ 'BUMPED:' + (Update-BridgeClaimLease -Root '{root}' -AgentName '{AGENT}') }} "
               f"catch {{ 'THREW:' + $_.Exception.Message }}")
    return _ps(shell, bridge, command, runtime=root)


def _no_helper(bridge):
    (bridge[0] / "BridgeV2QueueMutex.ps1").unlink()


# --- Update-BridgeClaimLease: transient skips, permanent propagates

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_missing_mutex_helper_propagates_instead_of_skipping_the_round(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    _no_helper(bridge)
    result = _bump(shell, bridge, bridge[2])
    thrown = result.stdout[result.stdout.index("THREW:"):] if "THREW:" in result.stdout else ""
    assert "BridgeV2QueueMutex.ps1" in thrown and "BUMPED:" not in result.stdout, result.stdout + result.stderr
    assert "skipped this round" not in result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_root_with_no_canonical_form_propagates_instead_of_skipping_the_round(bridge, shell, tmp_path):
    root = tmp_path / "rµ"                       # non-ASCII: the canonical-root rule refuses it
    (root / "work_queue" / "claims").mkdir(parents=True)
    result = _bump(shell, bridge, root)
    out = result.stdout.strip().splitlines()[-1]
    assert out == "THREW:scope must be ASCII: both runtimes must normalize it identically", result.stdout
    assert "skipped this round" not in result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_a_busy_root_still_skips_the_round_with_a_warning(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    with _held(bridge[2]):
        result = _bump(shell, bridge, bridge[2])
    assert result.stdout.strip().splitlines()[-1] == "BUMPED:0", result.stdout + result.stderr
    assert "claim lease bump skipped this round: runtime-root queue mutex busy: " in result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_an_abandoned_root_still_skips_the_round_then_bumps(bridge, shell):
    from tools import bridge_v2_queue_ports_windows as ports

    assert _claim(shell, bridge).returncode == 0
    kernel32 = ports._kernel32()
    witness = ports._create(mutex_name(bridge[2]), kernel32)   # keeps the object alive so abandonment is seen
    try:
        _abandon(shell, bridge)
        skipped = _bump(shell, bridge, bridge[2])
        assert skipped.stdout.strip().splitlines()[-1] == "BUMPED:0", skipped.stdout + skipped.stderr
        assert "skipped this round: runtime-root queue mutex was abandoned" in skipped.stdout + skipped.stderr
        assert _bump(shell, bridge, bridge[2]).stdout.strip().splitlines()[-1] == "BUMPED:1"
    finally:
        kernel32.CloseHandle(witness)


# --- Start-BridgeHeartbeat: the permanent failure is in the job's error stream; beats continue

JOB = r"""
$job = Start-Job -ScriptBlock {
    param($script, $root)
    & $script -Agent '__AGENT__' -IntervalMs 200 -MaxIterations 3 -RuntimeRoot $root
} -ArgumentList '__SCRIPT__', '__ROOT__'
[void](Wait-Job -Job $job -Timeout 120)
$errors = @($job.ChildJobs[0].Error)
'STATE:' + $job.State
'ERRORS:' + $errors.Count
foreach ($record in $errors) { 'ERROR:' + $record.Exception.Message }
Remove-Job -Job $job -Force
"""


def _job(shell, bridge):
    command = (JOB.replace("__AGENT__", AGENT).replace("__SCRIPT__", str(bridge[0] / "Start-BridgeHeartbeat.ps1"))
               .replace("__ROOT__", str(bridge[2])))
    return _ps(shell, bridge, command)


def _beats(bridge):
    beats = bridge[2] / "work_queue" / "heartbeats"
    return sorted(beats.rglob("*.json")) if beats.is_dir() else []


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_the_heartbeat_job_records_a_permanent_bump_failure_and_keeps_beating(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    _no_helper(bridge)
    result = _job(shell, bridge)
    lines = result.stdout.strip().splitlines()
    assert "STATE:Completed" in lines, result.stdout + result.stderr      # never Failed: the loop ran out
    errors = [line for line in lines if line.startswith("ERROR:")]
    assert len(errors) == 3 and all("BridgeV2QueueMutex.ps1" in line for line in errors), result.stdout
    assert _beats(bridge), "the session beat must keep being written"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_the_heartbeat_job_is_clean_when_the_bump_succeeds(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    [claim] = (bridge[2] / "work_queue" / "claims").glob("*.json")
    before = json.loads(claim.read_text(encoding="utf-8-sig"))
    result = _job(shell, bridge)
    lines = result.stdout.strip().splitlines()
    assert "STATE:Completed" in lines and "ERRORS:0" in lines, result.stdout + result.stderr
    assert _beats(bridge)
    after = json.loads(claim.read_text(encoding="utf-8-sig"))
    assert after["claim_lease_expires_utc"] > before["claim_lease_expires_utc"]
    assert after["owner_session_id"] == before["owner_session_id"]
    assert after["owner_token_sha256"] == before["owner_token_sha256"]


def test_cancellation_is_rethrown_before_the_visible_catch():
    text = (Path(__file__).resolve().parents[2] / ".agent-bridge" / "bin" / "Start-BridgeHeartbeat.ps1").read_text(
        encoding="utf-8")
    stopped = text.index("} catch [System.Management.Automation.PipelineStoppedException] {")
    canceled = text.index("} catch [System.OperationCanceledException] {")
    visible = text.index("Write-Error -ErrorRecord $_ -ErrorAction Continue")
    assert stopped < canceled < visible


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
def test_failed_heartbeat_preserves_claim_bytes_and_expiry(bridge, shell):
    assert _claim(shell, bridge).returncode == 0
    [claim] = (bridge[2] / "work_queue" / "claims").glob("*.json")
    before = claim.read_bytes()
    expiry = json.loads(before.decode("utf-8-sig"))["claim_lease_expires_utc"]
    _no_helper(bridge)
    result = _job(shell, bridge)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STATE:Completed" in result.stdout and "ERRORS:3" in result.stdout
    assert _beats(bridge)
    assert claim.read_bytes() == before
    assert json.loads(claim.read_text(encoding="utf-8-sig"))["claim_lease_expires_utc"] == expiry


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("kind", ["permanent", "cancel", "wrapped_cancel", "wrapped_pipeline"])
def test_send_liveness_retains_permanent_warning_but_rethrows_cancellation(bridge, shell, kind):
    # A controlled fault in the PRIVATE helper copy, not a real Ctrl-C or fleet call.
    helper = bridge[0] / "ClaimLeaseHeartbeat.ps1"
    helper.write_text(helper.read_text(encoding="utf-8-sig") + r'''
function Update-BridgeClaimLease {
    param($Root, $AgentName)
    throw $global:fixtureError
}
''', encoding="utf-8-sig")
    expressions = {
        "permanent": "[InvalidOperationException]::new('fixture permanent')",
        "cancel": "[OperationCanceledException]::new('fixture canceled')",
        "wrapped_cancel": "[System.Management.Automation.MethodInvocationException]::new('fixture wrapper', [OperationCanceledException]::new('fixture canceled'))",
        "wrapped_pipeline": "[System.Management.Automation.MethodInvocationException]::new('fixture wrapper', [System.Management.Automation.PipelineStoppedException]::new())",
    }
    command = (f"$exception = {expressions[kind]}; "
               "$global:fixtureError = [System.Management.Automation.ErrorRecord]::new($exception, 'fixture-id', "
               "[System.Management.Automation.ErrorCategory]::OperationStopped, 'fixture-target'); "
               f"try {{ & '{bridge[0] / 'Send-Liveness.ps1'}' -Agent '{AGENT}' -Heartbeat; 'CONTINUED' }} "
               "catch { 'THREW:' + $_.FullyQualifiedErrorId; "
               "'SAME_EXCEPTION:' + [object]::ReferenceEquals($_.Exception, $exception); "
               "'TARGET:' + $_.TargetObject }")
    result = _ps(shell, bridge, command)
    assert result.returncode == 0, result.stdout + result.stderr
    if kind == "permanent":
        assert "claim lease keepalive failed: fixture permanent" in result.stdout
        assert "fixture: no event written" in result.stdout and "CONTINUED" in result.stdout
        assert "THREW:" not in result.stdout
    else:
        assert "THREW:fixture-id" in result.stdout, result.stdout + result.stderr
        assert "SAME_EXCEPTION:True" in result.stdout and "TARGET:fixture-target" in result.stdout
        assert "fixture: no event written" not in result.stdout and "CONTINUED" not in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: s.split(".")[0])
@pytest.mark.parametrize("position", [1, 2], ids=["first-bump", "send-bump"])
@pytest.mark.parametrize("kind", ["permanent", "cancel", "wrapped_cancel", "pipeline", "wrapped_pipeline"])
def test_layered_heartbeat_cancellation_stops_before_emit_or_next_iteration(bridge, shell, position, kind):
    assert _claim(shell, bridge).returncode == 0
    [claim] = (bridge[2] / "work_queue" / "claims").glob("*.json")
    before = claim.read_bytes()
    calls = bridge[2] / "calls.txt"
    emits = bridge[2] / "emits.txt"
    helper = bridge[0] / "ClaimLeaseHeartbeat.ps1"
    helper.write_text(helper.read_text(encoding="utf-8-sig") + r'''
function Update-BridgeClaimLease {
    param($Root, $AgentName, $Identity)
    $global:fixtureCalls++
    [IO.File]::AppendAllText($global:fixtureCallPath, "bump`n")
    if ($global:fixtureCalls -eq $global:fixturePosition) { throw $global:fixtureError }
    return 0
}
''', encoding="utf-8-sig")
    (bridge[0] / "Write-AgentEvent.ps1").write_text(
        '$null = $args\n[IO.File]::AppendAllText($global:fixtureEmitPath, "emit`n")\n', encoding="utf-8-sig")
    expressions = {
        "permanent": "[InvalidOperationException]::new('fixture permanent')",
        "cancel": "[OperationCanceledException]::new('fixture canceled')",
        "wrapped_cancel": "[System.Management.Automation.MethodInvocationException]::new('fixture wrapper', [OperationCanceledException]::new('fixture canceled'))",
        "pipeline": "[System.Management.Automation.PipelineStoppedException]::new()",
        "wrapped_pipeline": "[System.Management.Automation.MethodInvocationException]::new('fixture wrapper', [System.Management.Automation.PipelineStoppedException]::new())",
    }
    command = (f"$exception = {expressions[kind]}; "
               "$global:fixtureError = [System.Management.Automation.ErrorRecord]::new($exception, 'layered-id', "
               "[System.Management.Automation.ErrorCategory]::OperationStopped, 'layered-target'); "
               f"$global:fixtureCalls=0; $global:fixturePosition={position}; "
               f"$global:fixtureCallPath='{calls}'; $global:fixtureEmitPath='{emits}'; "
               f"try {{ & '{bridge[0] / 'Start-BridgeHeartbeat.ps1'}' -Agent '{AGENT}' "
               f"-IntervalMs 20 -MaxIterations 3 -RuntimeRoot '{bridge[2]}'; 'CONTINUED' }} "
               "catch { 'THREW:' + $_.FullyQualifiedErrorId; "
               "'SAME_EXCEPTION:' + [object]::ReferenceEquals($_.Exception, $exception); "
               "'TARGET:' + $_.TargetObject }")
    result = _ps(shell, bridge, command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _beats(bridge), "the first session heartbeat precedes the injected failure"
    assert claim.read_bytes() == before
    if kind == "permanent":
        assert len(calls.read_text().splitlines()) == 6
        assert len(emits.read_text().splitlines()) == 3
        # Windows PowerShell wraps the error message after a long script path.
        # Keep the visibility assertion, without depending on renderer width.
        visible = " ".join((result.stdout + result.stderr).split())
        assert "CONTINUED" in result.stdout and "fixture permanent" in visible, result.stdout + result.stderr
        assert "THREW:" not in result.stdout
    else:
        assert "THREW:layered-id" in result.stdout, result.stdout + result.stderr
        assert "SAME_EXCEPTION:True" in result.stdout and "TARGET:layered-target" in result.stdout
        assert "CONTINUED" not in result.stdout
        assert len(calls.read_text().splitlines()) == position
        assert not emits.exists(), "no event after the cancellation"
