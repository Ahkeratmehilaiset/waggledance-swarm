"""Strict-fake tests of the A5 fixed owned-factory provider (Lead's v13): admission before any write, the frozen
parent's literal controller command through Root's six-argument owned constructor (a strict fake runner), one launch,
setup cleanup of an acquired object, bounded output snapshots and fixed witness paths. Nothing native runs: the runner
is a synthetic file, so no type is compiled and no process, job, controller or member exists."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FACTORY = ROOT / "tests" / "windows" / "wd_mind_shadow_a5_owned_factory.ps1"
PARENT = ROOT / "tests" / "windows" / "wd_mind_shadow_a5_owned_parent.ps1"
CONTROLLER = ROOT / "tests" / "windows" / "wd_mind_shadow_a5_controller.ps1"
LAUNCH = "5a5a5a5a-0000-4000-8000-000000000013"  # synthetic, never a real launch id

FAKE_RUNNER = r"""
function Get-WdA7Sha256 { param([byte[]]$Bytes) throw 'unexpected fake runner hash call' }
function Initialize-WdA7OwnedType { param([string]$LoopText) $global:WdF += , ('init ' + $LoopText.Length) }
function New-WdA7ManagedProcess {
    param($HostPath, $Arguments, $WorkingDirectory, $InputPath, $OutputPath, $ErrorPath)
    $global:WdF += , ('new ' + (@($HostPath, $WorkingDirectory, $InputPath, $OutputPath, $ErrorPath) -join '|'))
    $global:WdArgv = @($Arguments)
    if ($global:WdMode -ceq 'ctor_throws') { throw 'synthetic constructor failure' }
    [IO.File]::WriteAllBytes($OutputPath, [byte[]]@())
    [IO.File]::WriteAllBytes($ErrorPath, [byte[]]@())
    $fake = [pscustomobject]@{ Pid = 6161; ActiveProcesses = [uint32]0; TotalProcesses = [uint32]1; ExitCode = 124 }
    $fake | Add-Member -MemberType ScriptMethod -Name Stop -Value {
        $global:WdF += , 'stop'
        if ($global:WdMode -ceq 'cleanup_errors') { throw 'synthetic cleanup stop failure' }
    }
    if ($global:WdMode -cne 'no_root_stop' -and $global:WdMode -cne 'cleanup_errors') {
        $fake | Add-Member -MemberType ScriptMethod -Name StopRootOnly -Value { $global:WdF += , 'stop_root' }
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Wait -Value { param($Milliseconds) return $true }
    $fake | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $global:WdF += , 'dispose'
        if ($global:WdMode -ceq 'cleanup_errors') { throw 'synthetic cleanup dispose failure' }
    }
    return $fake
}
function Get-WdA7ObservedIdentity {
    param([int]$ProbePid)
    return @{ pid = [string]$ProbePid; creation_utc = '2026-10-22T00:00:01.0000000Z' }
}
"""

HOST = r"""
param([string] $Factory, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Add-Type', 'Get-Process',
        'Wait-Process', 'Get-CimInstance', 'Get-WmiObject', 'Set-Acl', 'Start-Sleep')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
. $Factory
$spec = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture))
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($spec.parent, [ref]$tokens, [ref]$errors)
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $statement.Name -in @('Get-WdA5ParentLiteral', 'Get-WdA5ParentControllerCommand')) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
$rows = @()
foreach ($item in $spec.rows) {
    $global:WdF = @(); $global:WdArgv = @(); $global:WdMode = [string]$item.mode
    $evidence = $spec.audit + '\evidence-' + $item.name
    $run = $spec.audit + '\run-' + $item.name
    if ($item.run_outside) { $run = $spec.outside + '\run-' + $item.name }
    if ($item.same_roots) {  # RCO2 R2: the same folder, its leaf in another case
        $run = [IO.Path]::GetDirectoryName($evidence) + '\' + [IO.Path]::GetFileName($evidence).ToUpperInvariant()
    }
    [void][IO.Directory]::CreateDirectory($evidence)
    if ($item.stale_output) { [IO.File]::WriteAllText($evidence + '\a5-controller.stdout.log', 'old') }
    # The frozen controller needs an existing EMPTY plain run root: the fixture supplies it (or the row's defect),
    # and the inventory before the factory call separates these fixture entries from factory side effects.
    if ($item.run_file) { [IO.File]::WriteAllText($run, 'not a folder') }
    elseif (-not $item.run_missing) { [void][IO.Directory]::CreateDirectory($run) }
    if ($item.run_nonempty) { [IO.File]::WriteAllText($run + '\stale.txt', 'stale') }
    $inventory = {
        @(foreach ($root in @($evidence, $run)) {
                if ([IO.Directory]::Exists($root)) {
                    'D ' + $root
                    [IO.Directory]::GetFileSystemEntries($root, '*', [IO.SearchOption]::AllDirectories) |
                        ForEach-Object { 'E ' + $_ }
                } elseif ([IO.File]::Exists($root)) { 'F ' + $root }
            }) | Sort-Object
    }
    $before = @(& $inventory)
    $launch = $spec.launch
    if ($item.bad_context) { $launch = $launch.ToUpperInvariant() }
    $config = @{
        Context = @{ schema = 'wd.mind-shadow-a5-controller-context.v1'; harness_run_id = 'SYNTHETIC-RUN'
            launch_id = $launch; source_commit = ('a' * 40); manifest_sha256 = ('B' * 64); parent_pid = '4242'
            parent_creation_utc = '2026-10-22T00:00:00.0000000Z' }
        HostPath = $spec.host; LoopPath = $spec.loop; MemberPath = $spec.member; ControllerPath = $spec.controller
        RunRoot = $run; EvidenceRoot = $evidence
        Pins = @{ host_sha256 = $spec.pins.host; loop_sha256 = $spec.pins.loop; member_sha256 = $spec.pins.member
            controller_sha256 = $spec.pins.controller }
        ControllerReadySeconds = 10; HoldSeconds = 60; SafetyMarginSeconds = 5; ReadyWaitSeconds = 20
        ExitWaitSeconds = 5; DrainSeconds = 5
    }
    if ($item.bad_controller_pin) { $config.Pins.controller_sha256 = '0' * 64 }
    $parentPin = $spec.pins.parent; if ($item.bad_parent_pin) { $parentPin = 'F' * 64 }
    $runnerPin = $spec.pins.runner; if ($item.bad_runner_pin) { $runnerPin = 'F' * 64 }
    $record = @{}
    $row = [ordered]@{ name = $item.name }
    try {
        $seams = New-WdA5OwnedFactorySeams -ApprovedNativeExecution:([bool]$item.approved) -Config $config `
            -ParentPath $spec.parent -ExpectedParentSha256 $parentPin -RunnerPath $spec.runner `
            -ExpectedRunnerSha256 $runnerPin -Record $record -MaxOutputBytes ([long]$item.max)
        $row.keys = @($seams.Keys | Sort-Object)
        $command = Get-WdA5ParentControllerCommand $config
        $given = $config; if ($item.foreign_config) { $given = $config.Clone() }
        if ($item.substitute) { $command = $command + ' -Foreign' }
        $owned = $null
        $row.launch = $(try { $owned = & $seams['NewOwned'] $given $command; 'RAN' } catch {
                [string]$_.Exception.Message })
        $row.second = 'SKIPPED'  # a refused substitute is checked alone: a second admitted call would launch
        if (-not ($item.substitute -or $item.foreign_config)) {
            $row.second = $(try { [void](& $seams['NewOwned'] $config (Get-WdA5ParentControllerCommand $config))
                    'RAN' } catch { [string]$_.Exception.Message })
        }
        $row.argv = @($global:WdArgv)
        $row.command_ok = (@($global:WdArgv).Count -eq 4 -and
            $global:WdArgv[3] -ceq (Get-WdA5ParentControllerCommand $config))
        $directory = $run + '\' + $launch
        $row.identity = & $seams['Identity'] 6161
        $now = & $seams['NowUtc']
        $row.now_ok = $now -cmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z\z'
        $row.ready = (& $seams['Ready'] ($directory + '\ready.json')).state
        $row.foreign = @(foreach ($pair in @(@('Ready', ($run + '\ready.json')), @('Marker', ($directory + '\x.json')),
                @('Released', ($directory + '\other.witness')))) {
            try { [void](& $seams[$pair[0]] $pair[1] 'line'); 'RAN' } catch { [string]$_.Exception.Message }
        })
        $row.marker = $(try { & $seams['Marker'] ($directory + '\cleanup-started.json') } catch { 'threw' })
        if ($null -ne $owned) {
            $out = $evidence + '\a5-controller.stdout.log'
            if ($item.snapshot -ceq 'text') { [IO.File]::WriteAllBytes($out, [Text.Encoding]::UTF8.GetBytes('ok')) }
            if ($item.snapshot -ceq 'oversized') { [IO.File]::WriteAllBytes($out, [byte[]]::new(17)) }
            if ($item.snapshot -ceq 'invalid') { [IO.File]::WriteAllBytes($out, [byte[]]@(0xC3, 0x28)) }
            if ($item.snapshot -ceq 'missing') { [IO.File]::Delete($out) }
            $held = $null
            if ($item.snapshot -ceq 'held') {
                $held = [IO.File]::Open($out, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            }
            try { $row.errors = $(try { & $seams['Errors'] } catch { [string]$_.Exception.Message }) }
            finally { if ($null -ne $held) { $held.Dispose() } }
            $row.out_after = [IO.File]::Exists($out)
        }
        $row.result = 'SEAMS'
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message }
    $row.record = $record
    $row.calls = @($global:WdF)
    $row.input = [IO.File]::Exists($evidence + '\a5-controller.input')
    $row.run_dir = [IO.Directory]::Exists($run)
    $row.inv_before = @($before)
    $row.inv_after = @(& $inventory)
    $row.env = [string][Environment]::GetEnvironmentVariable('WD_MIND_SHADOW_SYNTHETIC_MEMBER')
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 8
"""

ROWS = {
    "launch_ok": {}, "snapshot_text": {"snapshot": "text"}, "not_approved": {"approved": False},
    "bad_context": {"bad_context": True}, "bad_controller_pin": {"bad_controller_pin": True},
    "bad_parent_pin": {"bad_parent_pin": True}, "bad_runner_pin": {"bad_runner_pin": True},
    "bound_zero": {"max": 0}, "bound_over": {"max": 32769}, "run_outside": {"run_outside": True},
    "run_missing": {"run_missing": True}, "run_not_directory": {"run_file": True},
    "run_nonempty": {"run_nonempty": True}, "same_roots": {"same_roots": True},
    "stale_output": {"stale_output": True}, "substituted_command": {"substitute": True},
    "foreign_config": {"foreign_config": True}, "setup_failure": {"mode": "no_root_stop"},
    "setup_cleanup_errors": {"mode": "cleanup_errors"}, "ctor_throws": {"mode": "ctor_throws"},
    "snapshot_oversized": {"snapshot": "oversized", "max": 16}, "snapshot_invalid": {"snapshot": "invalid"},
    "snapshot_held": {"snapshot": "held"}, "snapshot_missing": {"snapshot": "missing"},
}
REFUSED = {"not_approved": "explicit opt-in", "bad_context": "invalid A5 context",
           "bad_controller_pin": "parent raw pin mismatch",  # the frozen parent's own pin reader message
           "bad_parent_pin": "parent file is not its pinned bytes", "bad_runner_pin": "runner file is not its pinned",
           "bound_zero": "output bound", "bound_over": "output bound", "run_outside": "RunRoot is not a folder",
           "stale_output": "never overwritten", "run_missing": "RunRoot is not an existing empty plain folder",
           "run_not_directory": "RunRoot is not an existing empty plain folder",
           "run_nonempty": "RunRoot is not an existing empty plain folder",
           "same_roots": "EvidenceRoot and RunRoot are the same folder"}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _hosts():
    found = []
    for label, name in (("ps7", "pwsh"), ("ps5", "powershell")):
        path = shutil.which(name)
        if path:
            found.append((label, path))
    return found


@pytest.fixture(scope="module", params=_hosts() or [None], ids=lambda host: host[0] if host else "no-host")
def rows(request, tmp_path_factory):
    if os.name != "nt" or request.param is None:
        pytest.skip("the factory admits only local C: NTFS paths and needs a PowerShell host: untested, not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("a5factory")
    pins = folder / "pins"
    pins.mkdir()
    (pins / "host.exe").write_bytes(b"SYNTHETIC host\n")
    (pins / "loop.ps1").write_bytes(b"SYNTHETIC loop\n")
    (pins / "member.ps1").write_bytes(b"SYNTHETIC member\n")
    (pins / "runner.ps1").write_bytes(FAKE_RUNNER.encode("utf-8"))
    (folder / ".codex-audit").mkdir()
    (folder / "outside").mkdir()
    items = []
    for name, change in ROWS.items():
        item = {"name": name, "approved": True, "max": 32768, "mode": "", "snapshot": ""}
        item.update({flag: False for flag in ("run_outside", "stale_output", "bad_context", "bad_controller_pin",
                                              "bad_parent_pin", "bad_runner_pin", "foreign_config", "substitute",
                                              "run_missing", "run_file", "run_nonempty", "same_roots")})
        item.update(change)
        items.append(item)
    spec = {"parent": str(PARENT), "controller": str(CONTROLLER), "runner": str(pins / "runner.ps1"),
            "host": str(pins / "host.exe"), "loop": str(pins / "loop.ps1"), "member": str(pins / "member.ps1"),
            "audit": str(folder / ".codex-audit"), "outside": str(folder / "outside"), "launch": LAUNCH,
            "pins": {"parent": _sha(PARENT), "controller": _sha(CONTROLLER), "runner": _sha(pins / "runner.ps1"),
                     "host": _sha(pins / "host.exe"), "loop": _sha(pins / "loop.ps1"),
                     "member": _sha(pins / "member.ps1")},
            "rows": items}
    host = folder / "host.ps1"
    host.write_text(HOST, encoding="utf-8")
    fixture = folder / "fixture.json"
    fixture.write_text(json.dumps(spec), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(FACTORY),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    return {row["name"]: row for row in json.loads(done.stdout.strip().splitlines()[-1])}, spec


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_factory_refuses_before_any_write_or_call(rows, name):
    row = rows[0][name]
    assert row["result"] == "ERROR" and REFUSED[name] in row["error"], row
    assert row["error"].startswith("refused: ") or name == "bad_controller_pin", row
    assert row["calls"] == [] and row["input"] is False and row["record"] == {}, row
    # no factory side effect at all: the evidence and run-root inventories are exactly the fixture's
    assert row["inv_after"] == row["inv_before"], row


@pytest.mark.parametrize("name", ["launch_ok", "snapshot_text"])
def test_factory_launches_the_frozen_parent_command_once_through_the_owned_constructor(rows, name):
    row, spec = rows[0][name], rows[1]
    assert row["result"] == "SEAMS" and row["launch"] == "RAN", row
    assert row["keys"] == sorted(["NewOwned", "Identity", "NowUtc", "Ready", "Marker", "Errors", "Released"])
    evidence = spec["audit"] + "\\evidence-" + name
    assert row["calls"] == ["init " + str(len("SYNTHETIC loop\n")), "new " + "|".join([
        spec["host"], evidence, evidence + "\\a5-controller.input", evidence + "\\a5-controller.stdout.log",
        evidence + "\\a5-controller.stderr.log"])], row
    assert row["argv"][:3] == ["-NoProfile", "-NonInteractive", "-Command"] and row["command_ok"] is True, row
    assert row["second"].startswith("refused: one owned launch only"), row
    assert row["record"]["launch"]["pid"] == "6161" and row["record"]["errors"] == [] and row["env"] == ""
    assert row["identity"] == {"pid": "6161", "creation_utc": "2026-10-22T00:00:01.0000000Z"} and row["now_ok"]
    assert row["ready"] == "pending" and row["marker"] == "threw", row  # no run directory: unknown answers throw
    assert all(text.startswith("refused: not ") for text in row["foreign"]), row
    # the factory and the fake constructor add exactly the fixed input and the two fixed logs; the empty run root
    # supplied by the fixture stays empty (the frozen controller, never run here, would create the run directory)
    added = sorted(set(row["inv_after"]) - set(row["inv_before"]))
    assert added == sorted("E " + evidence + "\\a5-controller." + suffix for suffix in ("input", "stdout.log",
                                                                                         "stderr.log")), row
    run = spec["audit"] + "\\run-" + name
    assert "D " + run in row["inv_before"] and not set(row["inv_before"]) - set(row["inv_after"]), row
    want = "ok" if name == "snapshot_text" else ""
    assert row["errors"] == {"stdout": want, "stderr": ""} and row["out_after"] is True, row


@pytest.mark.parametrize("name,text", [("substituted_command", "frozen parent command"),
                                       ("foreign_config", "frozen parent command")])
def test_factory_new_owned_refuses_another_config_or_command_before_any_write(rows, name, text):
    row = rows[0][name]
    assert row["launch"].startswith("refused: ") and text in row["launch"] and row["input"] is False, row
    assert row["calls"][:1] in ([], ["init " + str(len("SYNTHETIC loop\n"))]), row


@pytest.mark.parametrize("name", ["setup_failure", "setup_cleanup_errors", "ctor_throws"])
def test_factory_cleans_up_an_acquired_object_once_and_keeps_every_error(rows, name):
    row = rows[0][name]
    errors = row["record"]["errors"]
    if name == "ctor_throws":  # nothing was received, so nothing is cleaned here
        assert row["calls"][2:] == [] and errors == ["launch: synthetic constructor failure"], row
        assert "synthetic constructor failure" in row["launch"], row
        return
    assert row["calls"][2:] == ["stop", "dispose"], row  # ONE owned Stop and ONE Dispose, never retried
    assert "lacks StopRootOnly" in row["launch"] and errors[0] == "setup: the owned object lacks StopRootOnly", row
    if name == "setup_cleanup_errors":
        assert [text.split(":")[0] for text in errors] == ["setup", "cleanup stop", "cleanup dispose"], row
    assert row["second"].startswith("refused: one owned launch only"), row


@pytest.mark.parametrize("name,text", [("snapshot_oversized", "over its bound"), ("snapshot_invalid", "UTF-8"),
                                       ("snapshot_held", "still held"), ("snapshot_missing", "missing")])
def test_factory_output_snapshots_are_bounded_strict_and_never_wait(rows, name, text):
    row = rows[0][name]
    assert isinstance(row["errors"], str) and row["errors"].startswith("refused: ") and text in row["errors"], row


def test_factory_direct_execution_refuses_and_dot_source_is_inert():
    hosts = _hosts()
    if os.name != "nt" or not hosts:
        pytest.skip("needs a Windows PowerShell host: untested, not evidence")
    for _, executable in hosts:
        done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(FACTORY)],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 3 and "refused: " in done.stderr, done
        inert = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command",
                                ". '" + str(FACTORY) + "'; (Get-Command New-WdA5OwnedFactorySeams).Name"],
                               capture_output=True, text=True, timeout=60)
        assert inert.returncode == 0 and inert.stdout.strip() == "New-WdA5OwnedFactorySeams", inert
