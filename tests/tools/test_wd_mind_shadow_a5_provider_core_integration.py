"""Actual A5 factory + actual parent Core in one strict-fake run.

Only the pinned Root runner's initializer/process/observed-identity boundary is
synthetic. Actual pin reads, literal commands, clock, ready/window, output and
witness readers participate. No native job/controller/member/workload is run.
Source under test always comes from this checkout, never a runtime/frozen fallback.
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FACTORY = ROOT / "tests/windows/wd_mind_shadow_a5_owned_factory.ps1"
PARENT = ROOT / "tests/windows/wd_mind_shadow_a5_owned_parent.ps1"
CONTROLLER = ROOT / "tests/windows/wd_mind_shadow_a5_controller.ps1"

RUNNER = r"""
function Get-WdA7Sha256 { param([byte[]]$Bytes) throw 'unexpected runner hash' }
function Initialize-WdA7OwnedType {
    param([string]$Text)
    if ($Text -cne "SYNTHETIC loop`n") { throw 'unexpected initializer input' }
    [void]$global:A5Calls.Add('initialize-fake')
}
function Get-WdA7ObservedIdentity {
    param([int]$Id)
    [void]$global:A5Calls.Add('identity-' + $Id)
    if ($Id -eq $PID) {
        return @{pid=[string]$Id;creation_utc=$global:A5Config.Context.parent_creation_utc}
    }
    if ($Id -ne 6161) { throw 'unexpected identity boundary' }
    return @{pid='6161';creation_utc=$global:A5ControllerTime}
}
function New-WdA7ManagedProcess {
    param($HostPath,$Arguments,$WorkingDirectory,$InputPath,$OutputPath,$ErrorPath)
    [void]$global:A5Calls.Add('constructor-fake')
    $global:A5Constructor=@{host=$HostPath;argv=@($Arguments);directory=$WorkingDirectory
        input=$InputPath;stdout=$OutputPath;stderr=$ErrorPath}
    foreach ($path in @($OutputPath,$ErrorPath)) {
        $s=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $s.Dispose()
    }
    $c=$global:A5Config;$launch=$c.Context.launch_id;$directory=Join-Path $c.RunRoot $launch
    [void][IO.Directory]::CreateDirectory($directory)
    $readyTime=[DateTime]::UtcNow
    $format="yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'"
    $rows=@{};foreach($name in @('root','child','grandchild')) {
        $rows[$name]=@{start_witness='present';released=$false;expected_launch_id=$launch;launch_id_verified=$false}
    }
    $command=& $global:A5CommandOracle {
        param($p,$d,$l,$life)Get-WdA5MemberCommand $p $d $l $life
    } $c.MemberPath $directory $launch ($c.ControllerReadySeconds+$c.HoldSeconds+60)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$commandHash=[BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($command))).Replace('-','')}
    finally{$hash.Dispose()}
    $ready=@{schema='wd.mind-shadow-a5-controller-ready.v1';status='INNER_JOB_READY_NOT_A5_PASS'
        subcase='A5/controller_death';whole_A5_PASS=$false;context=$c.Context
        controller=@{pid='6161';creation_utc=$global:A5ControllerTime}
        root=@{pid='7171';creation_utc=$global:A5ControllerTime};directory=$directory;pins=$c.Pins
        cleanup_marker_path=(Join-Path $directory 'cleanup-started.json')
        command_sha256=$commandHash
        ready_utc=$readyTime.ToString($format,[Globalization.CultureInfo]::InvariantCulture)
        deadline_utc=$readyTime.AddSeconds($c.HoldSeconds).ToString($format,[Globalization.CultureInfo]::InvariantCulture)
        hold_seconds=$c.HoldSeconds;inner_accounting=@{active=3;total=3}
        observation=@{expected=@('root','child','grandchild');observed=$rows}}
    if ($global:A5Mode -ceq 'bad_frame') { $ready.observation.expected='root,child,grandchild' }
    $global:A5Ready=$ready
    $readyPath=Join-Path $directory 'ready.json'
    $s=[IO.File]::Open($readyPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$b=[Text.Encoding]::UTF8.GetBytes(($ready|ConvertTo-Json -Depth 12 -Compress));$s.Write($b,0,$b.Length);$s.Flush($true)}
    finally{$s.Dispose()}
    [void]$global:A5Calls.Add('ready-published-synthetic')
    foreach($name in @('root','child','grandchild','controller')) {
        $line=$launch+' '+$name
        if($name-ceq'controller'){$line+=' 6161 '+$global:A5ControllerTime}
        if($global:A5Mode-ceq'foreign_witness'-and$name-ceq'root'){$line='FOREIGN root'}
        $s=[IO.File]::Open((Join-Path $directory ($name+'.witness')),[IO.FileMode]::CreateNew,
            [IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        $b=[Text.Encoding]::UTF8.GetBytes($line+"`n");$s.Write($b,0,$b.Length);$s.Flush($true)
        $global:A5Held[$name]=$s
    }
    if($global:A5Mode-ceq'held_output') {
        $global:A5Held['output']=[IO.File]::Open($OutputPath,[IO.FileMode]::Open,
            [IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    }
    $owned=[pscustomobject]@{Pid=[int]6161;TotalProcesses=[uint32]4}
    $owned|Add-Member ScriptProperty ActiveProcesses {
        if($global:A5Cleaning){return [uint32]0}
        if($global:A5Killed){
            [void]$global:A5Calls.Add('pre-cleanup-accounting')
            if($global:A5Mode-ceq'unknown_accounting'){return 'UNKNOWN'}
            return [uint32]0
        }
        return [uint32]4
    }
    $owned|Add-Member ScriptProperty ExitCode {if($global:A5Killed){return [int]124};return [int]0}
    $owned|Add-Member ScriptMethod Wait {
        param($Milliseconds)
        if($Milliseconds-ne0){throw 'unexpected wait contract'}
        return ($global:A5Killed-or$global:A5Cleaning)
    }
    $owned|Add-Member ScriptMethod StopRootOnly {
        [void]$global:A5Calls.Add('root-only-stop')
        if($global:A5Killed-or$global:A5Cleaning){throw 'root stop replay/wrong phase'}
        $global:A5KillUtc=[DateTime]::UtcNow
        $global:A5Killed=$true
        foreach($name in @('root','child','grandchild','controller')) {
            if($global:A5Mode-ceq'held_witness'-and$name-ceq'root'){continue}
            $global:A5Held[$name].Dispose()
        }
        if($global:A5Mode-ceq'early_cleanup_errors') {
            $directory=Join-Path $global:A5Config.RunRoot $global:A5Config.Context.launch_id
            [IO.File]::WriteAllText((Join-Path $directory 'cleanup-started.json'),'{}')
        }
    }
    $owned|Add-Member ScriptMethod Stop {
        [void]$global:A5Calls.Add('outer-stop');$global:A5Cleaning=$true
        foreach($s in @($global:A5Held.Values)){$s.Dispose()}
        # Preserve bytes, but invalidate the observed paths at OUTER cleanup:
        # a reader moved after this boundary must fail, never manufacture success.
        $directory=Join-Path $global:A5Config.RunRoot $global:A5Config.Context.launch_id
        foreach($name in @('root','child','grandchild','controller')) {
            $path=Join-Path $directory ($name+'.witness')
            [IO.File]::Move($path,($path+'.after-outer-cleanup'))
        }
        if($global:A5Mode-ceq'early_cleanup_errors'){throw 'synthetic outer stop failure'}
    }
    $owned|Add-Member ScriptMethod Dispose {
        [void]$global:A5Calls.Add('outer-dispose')
        if(-not$global:A5Cleaning){throw 'dispose before outer stop'}
        foreach($s in @($global:A5Held.Values)){$s.Dispose()}
        if($global:A5Mode-ceq'early_cleanup_errors'){throw 'synthetic outer dispose failure'}
    }
    return $owned
}
"""

HOST = r"""
param([string]$Fixture)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($name in @('Add-Type','Start-Process','Stop-Process','Start-Job','Invoke-Expression','Get-Process',
    'Get-CimInstance','Get-WmiObject','Set-Acl','Get-Acl','New-Item')){
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native boundary: $name'"))
}
$spec=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture))
. $spec.parent
. $spec.factory
$global:A5Calls=New-Object Collections.Generic.List[string]
$global:A5Held=@{};$global:A5Killed=$false;$global:A5Cleaning=$false;$global:A5Mode=$spec.mode
$global:A5KillUtc=$null;$global:A5Constructor=$null;$global:A5Ready=$null
$global:A5ControllerTime='2026-10-22T00:00:01.0000000Z'
$global:A5Config=@{
    Context=@{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='SYNTHETIC-INTEGRATION'
        launch_id='5a5a5a5a-0000-4000-8000-000000000015';source_commit=$spec.source_commit
        manifest_sha256=('B'*64);parent_pid=[string]$PID;parent_creation_utc='2026-10-22T00:00:00.0000000Z'}
    HostPath=$spec.host;LoopPath=$spec.loop;MemberPath=$spec.member;ControllerPath=$spec.controller
    RunRoot=$spec.run;EvidenceRoot=$spec.evidence
    Pins=@{host_sha256=$spec.pins.host;loop_sha256=$spec.pins.loop;member_sha256=$spec.pins.member
        controller_sha256=$spec.pins.controller}
    ControllerReadySeconds=10;HoldSeconds=60;SafetyMarginSeconds=5;ReadyWaitSeconds=5
    ExitWaitSeconds=2;DrainSeconds=2
}
$record=@{};$result=$null
try{
    # Test-only independent command oracle, same hash-verified frozen controller, no native/controller body.
    $text=(Read-WdA5ParentPin $spec.controller $spec.pins.controller -Text).text
    $pure=Import-WdA5ParentPure $text
    $global:A5CommandOracle=Import-WdA5FactoryClosure $text @('Get-WdA5Literal','Get-WdA5MemberCommand') 'pure command'
    $seams=New-WdA5OwnedFactorySeams -ApprovedNativeExecution -Config $global:A5Config -ParentPath $spec.parent `
        -ExpectedParentSha256 $spec.pins.parent -RunnerPath $spec.runner -ExpectedRunnerSha256 $spec.pins.runner `
        -Record $record
    if(@($global:A5Calls).Count){throw 'factory performed a runner operation during admission'}
    $result=Invoke-WdA5OwnedParentCore -Config $global:A5Config -PureModule $pure -Seams $seams
    $directory=Join-Path $spec.run $global:A5Config.Context.launch_id
    $readyHash=Get-WdA5ParentSha256 ([IO.File]::ReadAllBytes((Join-Path $directory 'ready.json')))
    $killWindow=$false
    if($null-ne$global:A5KillUtc){
        $start=[DateTime]::ParseExact($global:A5Ready.ready_utc,"yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            [Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AdjustToUniversal)
        $killWindow=$global:A5KillUtc-ge$start-and$global:A5KillUtc-lt$start.AddSeconds(55)
    }
    @{result=$result;record=$record;calls=@($global:A5Calls);constructor=$global:A5Constructor
        command=(Get-WdA5ParentControllerCommand $global:A5Config);ready_hash=$readyHash;kill_within_window=$killWindow
        parent_pid=[string]$PID;run=$spec.run;evidence=$spec.evidence}|ConvertTo-Json -Depth 16 -Compress
}finally{foreach($s in @($global:A5Held.Values)){$s.Dispose()}}
"""


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


HOSTS = [(name, shutil.which(name)) for name in ("pwsh", "powershell") if shutil.which(name)]
MODES = ["positive", "bad_frame", "unknown_accounting", "held_output", "held_witness", "foreign_witness",
         "early_cleanup_errors"]


@pytest.mark.parametrize("label,executable", HOSTS or [("no-host", None)], ids=[h[0] for h in HOSTS] or ["no-host"])
@pytest.mark.parametrize("mode", MODES)
def test_actual_factory_and_parent_core_share_one_strict_fake_run(tmp_path, label, executable, mode):
    if os.name != "nt" or executable is None:
        pytest.skip("Windows C: NTFS and PS5.1/PS7 DateKind-capable hosts only; untested, not native evidence")
    pins = tmp_path / "pins"
    pins.mkdir()
    for name in ("host.exe", "loop.ps1", "member.ps1"):
        (pins / name).write_bytes(("SYNTHETIC " + name.split(".")[0] + "\n").encode("ascii"))
    runner = pins / "runner.ps1"
    runner.write_bytes(RUNNER.encode("utf-8"))
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    run, evidence = audit / "run", audit / "evidence"
    run.mkdir()
    evidence.mkdir()
    assert run != evidence and not list(run.iterdir()) and not list(evidence.iterdir())
    spec = {"factory": str(FACTORY), "parent": str(PARENT), "controller": str(CONTROLLER), "runner": str(runner),
            "host": str(pins / "host.exe"), "loop": str(pins / "loop.ps1"), "member": str(pins / "member.ps1"),
            "run": str(run), "evidence": str(evidence), "mode": mode, "source_commit": "a" * 40}
    spec["pins"] = {key: _sha(Path(spec[key])) for key in ("parent", "controller", "runner", "host", "loop", "member")}
    fixture, host = tmp_path / "fixture.json", tmp_path / "host.ps1"
    fixture.write_text(json.dumps(spec), encoding="utf-8")
    host.write_bytes(HOST.encode("utf-8"))
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(fixture)],
                          capture_output=True, text=True, timeout=60)
    (tmp_path / "stdout.log").write_text(done.stdout, encoding="utf-8")
    (tmp_path / "stderr.log").write_text(done.stderr, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-5000:]
    row = json.loads(done.stdout.strip().splitlines()[-1])
    result, calls, record = row["result"], row["calls"], row["record"]
    assert result["whole_A5_PASS"] is False and result["native_permission_inferred"] is False
    assert calls.count("constructor-fake") == record["launch"]["calls"] == 1 and record["launch"]["acquired"] is True
    assert calls[0] == "identity-" + row["parent_pid"]
    assert calls.count("outer-stop") == calls.count("outer-dispose") == 1 and calls[-2:] == ["outer-stop", "outer-dispose"]
    assert calls.index("constructor-fake") < calls.index("ready-published-synthetic") < calls.index("identity-6161")
    constructor = row["constructor"]
    assert constructor["argv"] == ["-NoProfile", "-NonInteractive", "-Command", row["command"]]
    assert constructor["host"] == spec["host"] and constructor["directory"] == str(evidence)
    assert [constructor[k] for k in ("input", "stdout", "stderr")] == [str(evidence / name) for name in
             ("a5-controller.input", "a5-controller.stdout.log", "a5-controller.stderr.log")]
    assert record["errors"] == [] and result["ready_snapshot_sha256"] == row["ready_hash"]
    directory = run / "5a5a5a5a-0000-4000-8000-000000000015"
    assert all(not (directory / (name + ".witness")).exists() and
               (directory / (name + ".witness.after-outer-cleanup")).exists()
               for name in ("root", "child", "grandchild", "controller"))
    if mode == "positive":
        assert result["status"] == "OBSERVATIONS_COMPLETE_NOT_A5_PASS" and result["errors"] == []
        assert calls.count("root-only-stop") == 1 and row["kill_within_window"] is True
        assert calls.index("root-only-stop") < calls.index("pre-cleanup-accounting") < calls.index("outer-stop")
        assert result["pre_cleanup_zero"]["active"] == 0 and result["pre_cleanup_zero"]["observed_before_outer_cleanup"]
        assert result["observation"]["observed_before_outer_cleanup"] is True
        assert all(r["released"] and r["launch_id_verified"] for r in result["observation"]["observed"].values())
    else:
        assert result["status"] == "UNKNOWN" and result["observation"] is None and result["errors"]
        assert calls.count("root-only-stop") == (0 if mode == "bad_frame" else 1)
        primary_prefix = {
            "bad_frame": "primary: expected member array required",
            "unknown_accounting": "primary: pre-cleanup member-death accounting unknown",
            "held_output": "primary: refused: the output snapshot file is still held",
            "held_witness": "primary: released witness missing/foreign/held: root",
            "foreign_witness": "primary: released witness missing/foreign/held: root",
        }
        if mode in primary_prefix:
            assert result["errors"][0].startswith(primary_prefix[mode]), row
        if mode == "early_cleanup_errors":
            assert [e.split(":")[0] for e in result["errors"]] == ["primary", "outer stop", "outer dispose"]
