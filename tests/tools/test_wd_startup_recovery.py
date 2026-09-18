"""Runtime boundary regressions: exact external sessions and cold Tools holds."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def load(path, name):
    prefix = (f"$PSDefaultParameterValues['Assert-WdLaneLaunchAvailable:ParserSourcePath']={q(REBOOT / 'wd_supervisor.ps1')}\n"
              + "$PSDefaultParameterValues['Assert-WdLaneLaunchAvailable:AllowUnpinnedParser']=$true\n"
              if name == "Assert-WdLaneLaunchAvailable" else "")
    return prefix + f"""
if($PSVersionTable.PSEdition -eq 'Desktop'){{$env:PSModulePath=Join-Path $PSHOME 'Modules'}}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(path)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'parse error'}}
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq {q(name)}}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
"""


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_fleet_discovery_uses_actual_script_and_argv_not_command_payload(ps):
    script = load(REBOOT / "start-wd-all.ps1", "Get-LaneProcesses") + r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@('legacy-lead.ps1')}
$rows=@(
 [pscustomobject]@{ProcessId=1;CommandLine='powershell.exe -NoProfile -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1'},
 [pscustomobject]@{ProcessId=2;CommandLine='pwsh.exe -Command "Write-Output ''powershell.exe -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1 ''"'},
 [pscustomobject]@{ProcessId=3;CommandLine='pwsh.exe -Command "Write-Output ''legacy-lead.ps1''"'},
 [pscustomobject]@{ProcessId=4;CommandLine='"C:\Program Files\PowerShell\7\pwsh.exe" -NoLogo -WindowStyle Hidden -File "C:\Space Dir\start-wd-agent.ps1" -Agent "codex-lead-1"'},
 [pscustomobject]@{ProcessId=5;CommandLine='powershell.exe -File C:\Python\other.ps1 -Message "start-wd-agent.ps1 -Agent codex-lead-1 legacy-lead.ps1"'},
 [pscustomobject]@{ProcessId=6;CommandLine='powershell.exe -File C:\Python\start-wd-agent.ps1 -Agent claude-rco-1 -Message "-Agent codex-lead-1"'},
 [pscustomobject]@{ProcessId=7;CommandLine='powershell.exe -NoProfile -File C:\Python\legacy-lead.ps1'},
 [pscustomobject]@{ProcessId=8;CommandLine='pwsh.exe -f C:\Python\start-wd-agent.ps1 -Agent codex-lead-1'},
 [pscustomobject]@{ProcessId=9;CommandLine='pwsh.exe C:\Python\start-wd-agent.ps1 -Agent codex-lead-1'},
 [pscustomobject]@{ProcessId=10;CommandLine='pwsh.exe -File C:\Python\start-wd-agent.ps1.bak -Agent codex-lead-1'},
 [pscustomobject]@{ProcessId=11;CommandLine='other.exe -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1'}
)
'''
    script += f"""
@(Get-LaneProcesses -Lane $lane -Processes $rows -ParserSourcePath {q(REBOOT / 'wd_supervisor.ps1')} -AllowUnpinnedParser) |
 Select-Object -ExpandProperty ProcessId | ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == [1, 4, 7, 8, 9]


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_command_payload_cannot_fake_a_launcher_or_native_parent(ps):
    script = load(REBOOT / "start-wd-agent.ps1", "Assert-WdLaneLaunchAvailable") + r'''
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$other=[pscustomobject]@{agent='fable-5';legacy_process_markers=@()}
$parent=[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name='powershell.exe';CreationDate=[datetime]'2026-09-18T10:00:00Z';
 CommandLine='powershell.exe -Command "Write-Output ''powershell.exe -File C:\Python\start-wd-agent.ps1 -Agent fable-5 ''"'}
$native=[pscustomobject]@{ProcessId=201;ParentProcessId=200;Name='codex.exe';CreationDate=[datetime]'2026-09-18T10:01:00Z';CommandLine='codex.exe'}
function Get-CimInstance {$global:rows}
function Check{try{Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($lane,$other) -CurrentPid 100;$true}catch{$false}}
$global:rows=@($parent);$textOnly=Check
$global:rows=@($parent,$native);$fakeParent=Check
$parent.CommandLine='powershell -File C:\Python\start-wd-agent.ps1 -Agent fable-5';$realOther=Check
$parent.CommandLine='powershell -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1';$realSame=Check
@{text_only=$textOnly;fake_parent=$fakeParent;real_other=$realOther;real_same=$realSame}|ConvertTo-Json
'''
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "text_only": True, "fake_parent": False, "real_other": True, "real_same": False,
    }


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("kind", ["fleet", "lane"])
def test_imported_process_parser_is_pinned_before_execution(tmp_path, ps, kind):
    source = tmp_path / "wd_supervisor.ps1"
    source.write_bytes((REBOOT / "wd_supervisor.ps1").read_bytes())
    launcher = REBOOT / ("start-wd-all.ps1" if kind == "fleet" else "start-wd-agent.ps1")
    helper = "Read-Utf8FleetSnapshot" if kind == "fleet" else "Read-Utf8LaneSnapshot"
    function = "Get-LaneProcesses" if kind == "fleet" else "Assert-WdLaneLaunchAvailable"
    script = load(launcher, helper) + load(launcher, function)
    invoke = ("Get-LaneProcesses -Lane $lane -Processes $rows -ParserSourcePath $source" if kind == "fleet" else
              "Assert-WdLaneLaunchAvailable -Lane $lane -CurrentPid 100 -ParserSourcePath $source")
    script += f'''
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$source={q(source)};$manifest={q(tmp_path / 'deployment-manifest.json')}
$lane=[pscustomobject]@{{agent='codex-lead-1';legacy_process_markers=@()}}
$rows=@([pscustomobject]@{{ProcessId=200;Name='powershell.exe';CommandLine='powershell.exe -File C:\\other.ps1'}})
function Get-CimInstance {{$rows}}
function Start-Process {{throw 'Parser import executed runtime actions'}}
function Check {{param([switch]$Allow) try{{$null={invoke} -AllowUnpinnedParser:$Allow;$true}}catch{{$false}}}}
$pin=(Get-FileHash $source).Hash
@{{files=@{{'wd_supervisor.ps1'=$pin}}}}|ConvertTo-Json|Set-Content $manifest -Encoding UTF8
$bundleManifestAnchor=(Get-FileHash $manifest).Hash;$script:LaneManifestAnchor=$bundleManifestAnchor
$valid=Check
$bundleManifestAnchor='F'*64;$script:LaneManifestAnchor=$bundleManifestAnchor;$badAnchor=Check
$bundleManifestAnchor=(Get-FileHash $manifest).Hash;$script:LaneManifestAnchor=$bundleManifestAnchor
Add-Content $source '# tampered';$badSource=Check
@{{files=@{{}}}}|ConvertTo-Json|Set-Content $manifest -Encoding UTF8
$bundleManifestAnchor=(Get-FileHash $manifest).Hash;$script:LaneManifestAnchor=$bundleManifestAnchor
$missingPin=Check
Remove-Item -LiteralPath $manifest
$missingManifest=Check;$explicitSource=Check -Allow
@{{missing_manifest=$missingManifest;explicit_source=$explicitSource;valid=$valid;bad_anchor=$badAnchor;bad_source=$badSource;missing_pin=$missingPin}}|ConvertTo-Json
'''
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "valid": True, "bad_anchor": False, "bad_source": False, "missing_pin": False,
        "missing_manifest": False, "explicit_source": True,
    }


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["exited", "recovered", "unreadable", "reused"])
def test_lane_guard_rechecks_transient_missing_command_line(ps, case):
    script = load(REBOOT / "start-wd-agent.ps1", "Assert-WdLaneLaunchAvailable")
    script += f"$global:case={q(case)}\n" + r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$row=[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name='powershell.exe';
 CommandLine=$null;CreationDate=[datetime]'2026-09-18T10:00:00Z'}
function Get-CimInstance {
 param($ClassName,$Filter,$ErrorAction)
 if(-not $Filter){return $row}
 if($Filter -ne 'ProcessId=200'){throw 'wrong recheck filter'}
 if($global:case -eq 'exited'){return}
 if($global:case -ne 'unreadable'){$row.CommandLine='powershell.exe -File C:\unrelated\worker.ps1'}
 if($global:case -eq 'reused'){$row=$row.PSObject.Copy();$row.CreationDate=$row.CreationDate.AddSeconds(1)}
 return $row
}
try{Assert-WdLaneLaunchAvailable -Lane $lane -CurrentPid 100; @{ok=$true}|ConvertTo-Json}
catch{@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json}
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case in {"exited", "recovered"}), result


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["exact", "parent_reused", "parent_command", "parent_path",
                                   "child_prefix", "child_path", "child_name", "older_child",
                                   "unapproved", "managed_ancestor", "indirect", "duplicate"])
def test_external_worker_parent_is_exact_and_never_overrides_lane_ownership(ps, case):
    script = load(REBOOT / "start-wd-agent.ps1", "Assert-WdLaneLaunchAvailable")
    script += f"$case={q(case)}\n" + r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$parent=[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name='python.exe';
 CommandLine='C:\external\python.exe worker.py';ExecutablePath='C:\external\python.exe';CreationDate=[datetime]'2026-09-18T10:00:00Z'}
$native=[pscustomobject]@{ProcessId=201;ParentProcessId=200;Name='codex.exe';
 CommandLine='C:\cli\codex.exe -s read-only -C C:\external exec task-2';ExecutablePath='C:\cli\codex.exe';CreationDate=[datetime]'2026-09-18T10:01:00Z'}
$approval=[pscustomobject]@{kind='native_parent';pid=200;name=$parent.Name;command_line=$parent.CommandLine;
 executable_path=$parent.ExecutablePath;process_start_utc='2026-09-18T10:00:00Z';native_child_name='codex.exe';
 native_child_executable_path='C:\cli\codex.exe';native_child_command_prefix='C:\cli\codex.exe -s read-only -C C:\external exec '}
$entries=@($approval)
$global:rows=@($parent,$native)
switch($case){
 'parent_reused' {$parent.CreationDate=$parent.CreationDate.AddSeconds(1)}
 'parent_command' {$parent.CommandLine+=' changed'}
 'parent_path' {$parent.ExecutablePath='C:\other\python.exe'}
 'child_prefix' {$native.CommandLine='C:\cli\codex.exe -s workspace-write -C C:\external exec task-2'}
 'child_path' {$native.ExecutablePath='C:\other\codex.exe'}
 'child_name' {$native.Name='claude.exe'}
 'older_child' {$native.CreationDate=$parent.CreationDate.AddSeconds(-1)}
 'unapproved' {$entries=@()}
 'managed_ancestor' {
  $parent.ParentProcessId=199
  $global:rows+= [pscustomobject]@{ProcessId=199;ParentProcessId=1;Name='powershell.exe';
   CommandLine='powershell.exe -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1';CreationDate=[datetime]'2026-09-18T09:59:00Z'}
 }
 'indirect' {$native.ParentProcessId=202;$global:rows+=[pscustomobject]@{ProcessId=202;ParentProcessId=200;Name='python.exe';CreationDate=[datetime]'2026-09-18T10:00:30Z'}}
 'duplicate' {$entries=@($approval,$approval)}
}
function Get-CimInstance {$global:rows}
try{Assert-WdLaneLaunchAvailable -Lane $lane -ExternalSessions $entries -CurrentPid 100; @{ok=$true}|ConvertTo-Json}
catch{@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json}
'''
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case == "exact"), result


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["exact", "unknown_kind", "empty_prefix", "no_boundary", "relative_child", "wrong_child", "expired", "duplicate"])
def test_external_parent_snapshot_validation(tmp_path, ps, case):
    path = tmp_path / "external-parent.json"
    entry = {
        "kind": "native_parent", "pid": 200, "name": "python.exe",
        "command_line": "python.exe external_worker.py", "executable_path": str(tmp_path / "python.exe"),
        "process_start_utc": "2026-09-18T10:00:00Z", "native_child_name": "codex.exe",
        "native_child_executable_path": str(tmp_path / "codex.exe"),
        "native_child_command_prefix": "codex.exe -s read-only -C external exec ",
    }
    if case == "unknown_kind": entry["kind"] = "any_process"
    if case == "empty_prefix": entry["native_child_command_prefix"] = " "
    if case == "no_boundary": entry["native_child_command_prefix"] = "codex.exe"
    if case == "relative_child": entry["native_child_executable_path"] = "codex.exe"
    if case == "wrong_child": entry["native_child_name"] = "powershell.exe"
    path.write_text(json.dumps({"schema": "wd.external-agent-sessions.v1", "expires_at_utc": "2000-01-01T00:00:00Z",
                                "processes": [entry, entry] if case == "duplicate" else [entry]}), encoding="utf-8")
    script = "".join(load(REBOOT / "start-wd-agent.ps1", name) for name in (
        "Assert-LanePathWithoutReparse", "Read-Utf8LaneSnapshot", "Read-WdExternalSessions"))
    script += f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$path={q(path)}
if({q(case)} -ne 'expired') {{
 $record=Get-Content $path -Raw|ConvertFrom-Json
 $record.expires_at_utc=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o')
 $record|ConvertTo-Json -Depth 6|Set-Content $path -Encoding UTF8
}}
try{{$entries=@(Read-WdExternalSessions -Path $path -ExpectedHash (Get-FileHash $path).Hash); @{{ok=($entries.Count -eq 1)}}|ConvertTo-Json}}
catch{{@{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json}}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case == "exact"), result


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_external_identity_never_adopts_same_lane_or_reused_pid(ps):
    script = load(REBOOT / "start-wd-agent.ps1", "Assert-WdLaneLaunchAvailable") + r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$lane=[pscustomobject]@{agent='codex-lead-1';legacy_process_markers=@()}
$native=[pscustomobject]@{ProcessId=201;ParentProcessId=200;Name='codex.exe';
 CommandLine='C:\test\codex.exe';ExecutablePath='C:\test\codex.exe';CreationDate=[datetime]'2026-09-15T00:00:02Z'}
$approval=[pscustomobject]@{pid=201;name='codex.exe';command_line=$native.CommandLine;
 executable_path=$native.ExecutablePath;process_start_utc='2026-09-15T00:00:02Z'}
$global:rows=@($native)
function Get-CimInstance { $global:rows }
function Check($entries) {
 try { Assert-WdLaneLaunchAvailable -Lane $lane -ExternalSessions $entries -CurrentPid 100; $true }
 catch { $false }
}
$exact=Check @($approval)
$absent=Check @()
$duplicate=Check @($approval,$approval)
$native.CreationDate=$native.CreationDate.AddSeconds(1)
$reused=Check @($approval)
$native.CreationDate=$native.CreationDate.AddSeconds(-1)
$native.CommandLine='C:\test\codex.exe --other'
$command=Check @($approval)
$native.CommandLine=$approval.command_line
$native.ExecutablePath='C:\other\codex.exe'
$path=Check @($approval)
$native.ExecutablePath=$approval.executable_path
$global:rows=@($native,[pscustomobject]@{ProcessId=200;ParentProcessId=1;Name='powershell.exe';
 CommandLine='powershell -File C:\Python\start-wd-agent.ps1 -Agent codex-lead-1';CreationDate=[datetime]'2026-09-15T00:00:00Z'})
$sameLane=Check @($approval)
@{exact=$exact;absent=$absent;duplicate=$duplicate;reused=$reused;command=$command;path=$path;same_lane=$sameLane}|ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "exact": True, "absent": False, "duplicate": False, "reused": False,
        "command": False, "path": False, "same_lane": False,
    }


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["clean", "blocked", "orphan_pending", "live"])
def test_tools_cold_start_preserves_pending_evidence(tmp_path, ps, case):
    runtime = tmp_path / "bridge"
    worktree = tmp_path / "tools"
    journal = worktree / ".codex-audit" / "wd-turn-loop"
    runtime.mkdir()
    journal.mkdir(parents=True)
    pending = journal / ("turn-" + "a" * 32 + ".pending")
    if case != "clean":
        pending.write_text("original evidence", encoding="utf-8")
    pointer = runtime / ".wd-turn-codex-tools-1.owner.json"
    if case in ("blocked", "live"):
        pointer.write_text(json.dumps({
            "schema": "wd.lane-turn-owner.v1", "agent": "codex-tools-1",
            "pid": 87654321, "process_start_utc": "2026-09-15T00:00:00Z",
            "status": "blocked", "worktree": str(worktree), "journal_root": str(journal),
            "pending_path": str(pending), "session_id": "old", "generation": "a" * 40,
        }), encoding="utf-8")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = load(REBOOT / "start-wd-tools-consumer.ps1", "Assert-WdToolsColdStart")
    script += "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest\n"
    if case == "blocked":
        # Dotfiles are hidden on Linux; exercise the same file attribute on Windows.
        script += f"[IO.File]::SetAttributes({q(pointer)}, [IO.FileAttributes]::Hidden)\n"
    if case == "live":
        script += "function Get-Process { [pscustomobject]@{ProcessName='powershell';StartTime=[datetime]'2026-09-15T00:00:00Z'} }\n"
    script += fr"""
try {{
 Assert-WdToolsColdStart -BridgeRoot {q(runtime)} -LaneRoot {q(worktree)} -TurnLoopCode ([IO.File]::ReadAllText({q(REBOOT / 'Invoke-WdLaneTurnLoop.ps1')}))
 @{{ok=$true}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case in ("clean", "live")), result
    if case == "blocked":
        assert "blocked_previous_unresolved_turn" in result["error"]
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_external_snapshot_hash_expiry_and_duplicates(tmp_path, ps):
    path = tmp_path / "external.json"
    script = "".join(load(REBOOT / "start-wd-agent.ps1", name) for name in (
        "Assert-LanePathWithoutReparse", "Read-Utf8LaneSnapshot", "Read-WdExternalSessions"))
    script += fr"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$path={q(path)}
$entry=@{{pid=201;name='codex.exe';command_line='codex.exe';executable_path={q(tmp_path / 'codex.exe')};process_start_utc='2026-09-15T00:00:00Z'}}
$record=@{{schema='wd.external-agent-sessions.v1';expires_at_utc=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o');processes=@($entry)}}
function Save {{ [IO.File]::WriteAllText($path,($record|ConvertTo-Json -Depth 4)); (Get-FileHash -LiteralPath $path).Hash }}
function Check($hash) {{ try {{ $r=@(Read-WdExternalSessions -Path $path -ExpectedHash $hash); $r.Count -eq 1 }} catch {{ $false }} }}
$hash=Save; $valid=Check $hash; $badHash=Check ('F'*64)
$record.expires_at_utc=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o'); $expired=Check (Save)
$record.expires_at_utc=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o'); $record.processes=@($entry,$entry); $duplicate=Check (Save)
@{{valid=$valid;bad_hash=$badHash;expired=$expired;duplicate=$duplicate}}|ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {
        "valid": True, "bad_hash": False, "expired": False, "duplicate": False,
    }
