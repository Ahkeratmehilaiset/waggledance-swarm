"""Synthetic behavior of the Mind shadow job adapter's drain loop at the fixed deadline (Tools P2 late-drain finding).

The adapter is never executed here. A test host parses it with the PowerShell parser, defines its top-level functions,
replaces every function that reads the disk or the run root with one that throws, replaces the process, job and
expression cmdlets with mocks that throw on any call, and then runs the adapter's OWN drain-loop, stop-path and
outcome statements (extracted from the parsed file, not reimplemented) against a fake monotonic clock and a fake
managed job. completed needs a drain positively observed strictly before the deadline; a drain first observed at or
after it, or an observation that crosses it, takes the stop path. A verdict here is synthetic author proof of that
control flow, never native acceptance: native A1-A7 stays NOT_RUN. Finding: Tools (TOOLS_PR1915_LATE_DRAIN_COMPLETED);
repair assignment: Lead; fable-5 wrote this test and the adapter.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "ops" / "windows" / "reboot" / "Start-WdMindShadowJob.ps1"

HOST = r"""
param([string] $Adapter, [string] $Fixture, [string] $FakeRoot)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Remove-Item', 'Set-Content',
        'Out-File', 'Move-Item', 'Copy-Item')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Adapter, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'the adapter does not parse' }
$top = @($ast.EndBlock.Statements)
foreach ($statement in $top) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
foreach ($name in @('Publish-WdShadowRecord', 'Read-WdShadowFileSnapshot', 'Test-WdShadowOffSwitch',
        'Get-WdShadowDiskShortfall', 'Get-WdShadowReparseAncestor')) {
    Set-Item -Path "function:$name" -Value ([scriptblock]::Create("throw 'unexpected disk read: $name'"))
}
function Get-StatementRange([object[]] $Statements, [string] $First, [string] $Last) {
    $from = -1; $to = -1
    for ($i = 0; $i -lt $Statements.Count; $i++) {
        $text = $Statements[$i].Extent.Text
        if ($from -lt 0 -and $text.StartsWith($First)) { $from = $i }
        if ($from -ge 0 -and $to -lt 0 -and $text.StartsWith($Last)) { $to = $i }
    }
    if ($from -lt 0 -or $to -lt $from) { throw "statement range not found: $First .. $Last" }
    return (@($Statements[$from..$to] | ForEach-Object { $_.Extent.Text }) -join "`n")
}
$constants = @($top | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    @('$DeadlineSeconds', '$StopConfirmationWaitSeconds', '$PollMilliseconds') -ccontains $_.Left.Extent.Text
} | ForEach-Object { $_.Extent.Text }) -join "`n"
$initial = Get-StatementRange $top '$unknown = New-Object System.Collections.ArrayList' '$lock = [ordered]@{'
$outcomeText = Get-StatementRange $top '$rootExited = $null -ne $rootExitAt' '$outcome = Resolve-WdShadowOutcome'
$loops = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.WhileStatementAst] -and $node.Condition.Extent.Text -like '*$deadlineMs*'
}, $true))
if ($loops.Count -ne 1) { throw "expected one drain loop, found $($loops.Count)" }
$drainText = Get-StatementRange @($loops[0].Parent.Statements) '$deadlineMs = ' 'if ($null -ne $rootExitAt) {'

function global:Start-Sleep {
    param([int] $Milliseconds)
    $global:WdFakeMs = [long]$global:WdFakeMs + $Milliseconds
}
function Get-WdShadowGuardStop {
    param([string] $OffSwitchPath, [string[]] $Roots, [string[]] $Paths)
    $global:WdFakeMs = [long]$global:WdFakeMs + [long]$global:WdFake['guard_ms']
    return $null
}
function Test-WdShadowLiveDirLock {
    param([string] $Path)
    $global:WdFakeMs = [long]$global:WdFakeMs + [long]$global:WdFake['lock_ms']
    $ok = $global:WdFake['lock_ok'] -ne $false
    $failure = $null
    if (-not $ok) { $failure = 'System.IO.IOException' }
    return [ordered]@{ attempted = $true; acquired = $ok; released = $ok; error = $failure }
}
# The S15 ownership behavior is independently exercised by strict fake seams
# in test_shadow_job_s15_containment. This fixture isolates the existing job
# deadline/Stop accounting, so the added ancestry observer is a no-op fake.
function Update-OwnedShadowTree {
    param([hashtable]$State, [hashtable]$Seams, [scriptblock]$WithinBudget)
}
function New-WdFakeClock {
    $fake = New-Object psobject
    Add-Member -InputObject $fake -MemberType ScriptProperty -Name ElapsedMilliseconds -Value {
        [long]$global:WdFakeMs
    }
    Add-Member -InputObject $fake -MemberType ScriptProperty -Name Elapsed -Value {
        [TimeSpan]::FromTicks([long]$global:WdFakeMs * 10000)
    }
    return $fake
}
function New-WdFakeManaged {
    $fake = New-Object psobject
    Add-Member -InputObject $fake -MemberType NoteProperty -Name Pid -Value 4242
    Add-Member -InputObject $fake -MemberType ScriptMethod -Name Wait -Value {
        param([int] $Milliseconds)
        $f = $global:WdFake
        if ($f['wait_throws']) { throw (New-Object InvalidOperationException 'synthetic unknown job state') }
        $now = [long]$global:WdFakeMs
        $exit = $f['exit_at_ms']
        $result = $false
        if ($null -ne $exit -and [long]$exit -le $now) {
            $result = $true
        } elseif ($null -ne $exit -and [long]$exit -le $now + $Milliseconds) {
            $global:WdFakeMs = [long]$exit
            $result = $true
        } else {
            $global:WdFakeMs = $now + $Milliseconds
        }
        $global:WdFakeMs = [long]$global:WdFakeMs + [long]$f['wait_extra_ms']
        return $result
    }
    # PowerShell reads a failing property getter as $null in both hosts (observed in this test host for a script
    # property and for a .NET getter), so an unreadable job state reaches the adapter as $null, not as a throw.
    Add-Member -InputObject $fake -MemberType ScriptProperty -Name ActiveProcesses -Value {
        $f = $global:WdFake
        if ($f['active_unreadable']) { return $null }
        $value = 1
        if ($null -ne $f['zero_at_ms'] -and [long]$f['zero_at_ms'] -le [long]$global:WdFakeMs) { $value = 0 }
        $global:WdFakeMs = [long]$global:WdFakeMs + [long]$f['active_ms']
        return $value
    }
    Add-Member -InputObject $fake -MemberType ScriptMethod -Name Stop -Value {
        $f = $global:WdFake
        $f['stop_calls'] = [int]$f['stop_calls'] + 1
        if ($f['stop_throws']) { throw (New-Object InvalidOperationException 'synthetic Stop failure') }
        $now = [long]$global:WdFakeMs
        if ($null -eq $f['zero_at_ms'] -or [long]$f['zero_at_ms'] -gt $now) { $f['zero_at_ms'] = $now }
        if (-not $f['root_survives_stop'] -and ($null -eq $f['exit_at_ms'] -or [long]$f['exit_at_ms'] -gt $now)) {
            $f['exit_at_ms'] = $now
        }
    }
    Add-Member -InputObject $fake -MemberType ScriptProperty -Name ExitCode -Value { 0 }
    Add-Member -InputObject $fake -MemberType ScriptProperty -Name LiveDescendants -Value { @() }
    return $fake
}
function Invoke-WdFakeRun($Entry) {
    $global:WdFake = $Entry
    $global:WdFakeMs = [long]$Entry['start_ms']
    # Bind the declared default without running the adapter body or its mandatory-parameter binder.
    $RunDeadlineSeconds = ($ast.ParamBlock.Parameters | Where-Object {
        $_.Name.VariablePath.UserPath -ceq 'RunDeadlineSeconds'
    }).DefaultValue.SafeGetValue()
    . ([scriptblock]::Create($constants))
    . ([scriptblock]::Create($initial))
    $clock = New-WdFakeClock
    $managed = New-WdFakeManaged
    $ownedState = @{ errors = New-Object System.Collections.ArrayList }
    $ownedSeams = @{}
    $forceStop = $false
    # PR1916 CI fixture portability (Lead fixture assignment v2): the fake paths live below the test's own temporary
    # directory and use the host's path rules, because the extracted statements Join-Path on them and Linux pwsh has
    # no C drive. The guard and lock fakes above ignore these values; nothing is created at them.
    $runRoot = [IO.Path]::Combine($FakeRoot, 'SYNTHETIC-RUN-ROOT')
    $offSwitchPath = [IO.Path]::Combine($runRoot, 'mind-shadow.off')
    $guardRoots = @([IO.Path]::GetPathRoot($FakeRoot))
    $liveFull = [IO.Path]::Combine($FakeRoot, 'SYNTHETIC-LIVE')
    $guardPaths = @($runRoot, $liveFull)
    . ([scriptblock]::Create($drainText))
    . ([scriptblock]::Create($outcomeText))
    return [ordered]@{
        name = $Entry['name']; outcome = [string]$outcome; drained = [bool]$drained; stop_called = [bool]$stopCalled
        stop_calls = [int]$Entry['stop_calls']; unknown = @($unknown | ForEach-Object { [string]$_['field'] })
    }
}
$rows = @()
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $entry = @{}
    foreach ($property in $item.PSObject.Properties) { $entry[$property.Name] = $property.Value }
    $rows += Invoke-WdFakeRun $entry
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 4
"""

DEADLINE_MS = 1800 * 1000


def _run(name, start_ms, exit_at_ms, zero_at_ms, **extra):
    item = {"name": name, "start_ms": start_ms, "exit_at_ms": exit_at_ms, "zero_at_ms": zero_at_ms, "guard_ms": 0,
            "wait_extra_ms": 0, "active_ms": 0, "lock_ms": 0, "lock_ok": True, "wait_throws": False,
            "active_unreadable": False, "stop_throws": False, "root_survives_stop": False, "stop_calls": 0}
    item.update(extra)
    return item


def _expect(outcome, drained, stop_called):
    return {"outcome": outcome, "drained": drained, "stop_called": stop_called}


LATE = _expect("stopped_at_deadline", False, True)
EARLY = _expect("completed", True, False)

FIXTURES = [
    # Tools counterexample: entry 1 ms before the deadline, the guards take 2 ms, then the drain is seen at once.
    (_run("late_drain_after_guards_cross_the_deadline", DEADLINE_MS - 1, 0, 0, guard_ms=2), LATE),
    # The wait returns before the deadline, but the ActiveProcesses reading itself ends after it.
    (_run("late_drain_reading_crosses_the_deadline", DEADLINE_MS - 10, DEADLINE_MS - 5, DEADLINE_MS - 5,
          active_ms=10), LATE),
    (_run("drain_first_observed_exactly_at_the_deadline", DEADLINE_MS - 1, DEADLINE_MS, DEADLINE_MS), LATE),
    # The root exited early; the members drain just before the deadline but the 250 ms poll first sees it at it.
    (_run("late_drain_seen_after_a_sleep_slice", DEADLINE_MS - 1000, DEADLINE_MS - 1000, DEADLINE_MS - 10), LATE),
    (_run("drain_observed_one_ms_before_the_deadline", DEADLINE_MS - 2, DEADLINE_MS - 1, DEADLINE_MS - 1), EARLY),
    # An early drain stays completed when only the later lock bookkeeping ends after the deadline.
    (_run("early_drain_with_late_lock_bookkeeping", DEADLINE_MS - 10, DEADLINE_MS - 5, DEADLINE_MS - 5,
          lock_ms=100), EARLY),
    (_run("early_drain_long_before_the_deadline", 0, 5000, 5000), EARLY),
    (_run("root_never_exits_until_the_deadline_stop", DEADLINE_MS - 5000, None, None), LATE),
    (_run("throwing_stop_fails_closed", DEADLINE_MS - 5000, None, None, stop_throws=True),
     _expect("stop_unconfirmed", False, True)),
    (_run("throwing_job_wait_fails_closed", DEADLINE_MS - 1000, DEADLINE_MS - 500, None, wait_throws=True),
     _expect("stop_unconfirmed", False, True)),
    (_run("unreadable_job_state_fails_closed", DEADLINE_MS - 1000, DEADLINE_MS - 500, DEADLINE_MS - 500,
          active_unreadable=True), _expect("stop_unconfirmed", False, True)),
    (_run("root_alive_after_a_confirmed_stop_fails_closed", DEADLINE_MS - 5000, None, None, root_survives_stop=True),
     _expect("stop_unconfirmed", False, True)),
    (_run("failed_lock_after_an_early_drain_fails_closed", 0, 5000, 5000, lock_ok=False),
     _expect("stop_unconfirmed", True, False)),
]


def _hosts():
    hosts = []
    pwsh = shutil.which("pwsh")
    if pwsh:
        hosts.append(("ps7", pwsh))
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        ps5 = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if ps5.is_file():
            hosts.append(("ps5", str(ps5)))
    return hosts


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic drain-loop run needs pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def runs(request, tmp_path_factory):
    _, executable = request.param
    folder = tmp_path_factory.mktemp("deadline")
    host = folder / "host.ps1"
    host.write_text(HOST, encoding="utf-8")
    fixture = folder / "fixture.json"
    fixture.write_text(json.dumps([item for item, _ in FIXTURES]), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(ADAPTER),
                           str(fixture), str(folder)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in rows}


@pytest.mark.parametrize("item,expected", FIXTURES, ids=[item["name"] for item, _ in FIXTURES])
def test_drain_loop_outcome_from_a_fake_clock_and_job(runs, item, expected):
    row = runs[item["name"]]
    assert {key: row[key] for key in expected} == expected
    assert row["stop_calls"] == (1 if expected["stop_called"] else 0)


def test_every_fixture_has_exactly_one_run(runs):
    assert sorted(runs) == sorted(item["name"] for item, _ in FIXTURES)
