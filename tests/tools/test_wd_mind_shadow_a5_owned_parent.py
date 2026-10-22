"""Strict fake A5 OUTER parent; never run a controller/member/native job."""
import json
import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT, _run_powershell
from test_wd_startup_recovery import q

PARENT = ROOT / 'tests/windows/wd_mind_shadow_a5_owned_parent.ps1'
CONTROLLER = ROOT / 'tests/windows/wd_mind_shadow_a5_controller.ps1'


@lru_cache(maxsize=1)
def frozen_controller_text():
    text = CONTROLLER.read_text(encoding='utf-8').replace('\r\n', '\n')
    expected = '7A36976DAF6D73BA98F65B139680C56A8F77486B322E7E5BD83AE41521B2A8E9'
    if hashlib.sha256(text.encode()).hexdigest().upper() != expected:
        text = subprocess.run(['git', 'show', 'bfeb1b8f53209bcbb23804d2860d314ef8f5042e:tests/windows/wd_mind_shadow_a5_controller.ps1'],
                              cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True).stdout.replace('\r\n', '\n')
    assert hashlib.sha256(text.encode()).hexdigest().upper() == expected
    return text


def run_json(script, ps):
    assert PARENT.is_file(), 'Owned A5 parent Source absent'
    prefix = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native Add-Type'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process identity query'}}
function Get-CimInstance {{throw 'unexpected CIM'}}
. {q(PARENT)}
"""
    result = _run_powershell(prefix+script, executable=ps, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_parent_parser_default_partial_and_actual_ready_json_roundtrip(tmp_path, ps):
    packet = tmp_path / 'ready.json'
    script = rf"""
$t=$null;$e=$null;[void][Management.Automation.Language.Parser]::ParseFile({q(PARENT)},[ref]$t,[ref]$e)
if($e.Count){{throw 'parent parse error'}}
try{{Invoke-WdMindShadowA5OwnedParent;throw 'default accepted'}}catch{{if($_.Exception.Message-notmatch'explicit approved native opt-in'){{throw}}}}
try{{Invoke-WdMindShadowA5OwnedParent -ApprovedNativeExecution;throw 'partial accepted'}}catch{{if($_.Exception.Message-notmatch'complete'){{throw}}}}
$source=[ordered]@{{ready_utc='2026-10-22T00:00:00.0000000Z';deadline_utc='2026-10-22T00:00:10.0000000Z';hold_seconds=10}}
[IO.File]::WriteAllText({q(packet)},($source|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
$snapshot=Read-WdA5ParentJsonSnapshot {q(packet)}
if($snapshot.value.ready_utc-isnot[string]-or$snapshot.value.ready_utc-cne$source.ready_utc){{throw 'PS7 date coercion'}}
@{{refused=$true;utc_string_preserved=$true;raw_sha=($snapshot.sha256.Length-eq64)}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'refused': True, 'utc_string_preserved': True, 'raw_sha': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['positive', 'foreign_context', 'foreign_controller', 'late', 'marker_before',
                                  'marker_after', 'partial_ready', 'read_error', 'stop_error', 'wait_timeout',
                                  'exit_wrong', 'member_held', 'foreign_member_line', 'counts_unknown',
                                  'cleanup_error', 'errors_output', 'outer_missing_members'])
def test_actual_parent_actor_order_and_all_failures_stay_unknown(tmp_path, ps, mode):
    run = tmp_path / 'run'
    evidence = tmp_path / 'evidence'
    run.mkdir()
    evidence.mkdir()
    frozen = tmp_path / 'frozen-controller.ps1'
    frozen.write_text(frozen_controller_text(), encoding='utf-8')
    script = rf"""
$script:mode={q(mode)};$script:events=New-Object System.Collections.ArrayList;$script:killed=$false
$module=Import-WdA5ParentPure ([IO.File]::ReadAllText({q(frozen)}).Replace("`r`n","`n"))
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic';launch_id='11111111-1111-1111-1111-111111111111';source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid=[string]$PID;parent_creation_utc='2026-10-22T00:00:00.0000000Z'}}
$pins=@{{host_sha256=('1'*64);loop_sha256=('2'*64);member_sha256=('3'*64);controller_sha256=('4'*64)}}
$config=@{{Context=$context;HostPath='C:\synthetic\pwsh.exe';LoopPath='C:\synthetic\loop.ps1';MemberPath='C:\synthetic\member.ps1';ControllerPath='C:\synthetic\controller.ps1';RunRoot={q(run)};EvidenceRoot={q(evidence)};Pins=$pins;ControllerReadySeconds=1;HoldSeconds=10;SafetyMarginSeconds=2;ReadyWaitSeconds=1;ExitWaitSeconds=1;DrainSeconds=1}}
$directory=Join-Path {q(run)} $context.launch_id
$readyContext=@{{}};foreach($key in $context.Keys){{$readyContext[$key]=$context[$key]}}
if($script:mode-ceq'foreign_context'){{$readyContext.harness_run_id='foreign'}}
$expectedCommand=Get-WdA5ParentMemberCommand $config.MemberPath $directory $context.launch_id 71
$script:ready=@{{schema='wd.mind-shadow-a5-controller-ready.v1';status='INNER_JOB_READY_NOT_A5_PASS';subcase='A5/controller_death';whole_A5_PASS=$false;context=$readyContext;controller=@{{pid=if($script:mode-ceq'foreign_controller'){{'999'}}else{{'4242'}};creation_utc='2026-10-22T00:00:00.0000000Z'}};root=@{{pid='5000';creation_utc='2026-10-22T00:00:00.0000000Z'}};directory=$directory;pins=$pins;cleanup_marker_path=(Join-Path $directory 'cleanup-started.json');command_sha256=(Get-WdA5ParentSha256 ([Text.Encoding]::UTF8.GetBytes($expectedCommand)));ready_utc='2026-10-22T00:00:00.0000000Z';deadline_utc='2026-10-22T00:00:10.0000000Z';hold_seconds=10;inner_accounting=@{{active=3;total=3}};observation=@{{expected=@('root','child','grandchild');observed=@{{root=@{{start_witness='present';released=$false;launch_id=$context.launch_id}};child=@{{start_witness='present';released=$false;launch_id=$context.launch_id}};grandchild=@{{start_witness='present';released=$false;launch_id=$context.launch_id}}}}}}}}
$job=[pscustomobject]@{{Pid=4242;cleaning=$false}}
foreach($row in $script:ready.observation.observed.Values){{$row.expected_launch_id=$row.launch_id;[void]$row.Remove('launch_id');$row.launch_id_verified=$false}}
$script:fullReadyPath={q(evidence / 'full-ready-roundtrip.json')}
[IO.File]::WriteAllText($script:fullReadyPath,($script:ready|ConvertTo-Json -Depth 14 -Compress),[Text.UTF8Encoding]::new($false))
$job|Add-Member ScriptMethod Wait {{param($ms) if($ms-ne0){{throw 'unexpected wait'}};return ($this.cleaning-or($script:killed-and$script:mode-cne'wait_timeout'))}}
$job|Add-Member ScriptMethod StopRootOnly {{
    [void]$script:events.Add('kill')
    if($this.cleaning){{throw 'kill after cleanup'}}
    if($script:mode-ceq'stop_error'){{throw 'synthetic root-only error'}}
    $script:killed=$true
}}
$job|Add-Member ScriptMethod Stop {{[void]$script:events.Add('cleanup');$this.cleaning=$true;if($script:mode-ceq'cleanup_error'){{throw 'synthetic cleanup error'}}}}
$job|Add-Member ScriptMethod Dispose {{[void]$script:events.Add('dispose')}}
$job|Add-Member ScriptProperty ExitCode {{if($script:mode-ceq'exit_wrong'){{return 0}};return 124}}
$job|Add-Member ScriptProperty ActiveProcesses {{if($this.cleaning){{return [uint32]0}};if($script:mode-ceq'counts_unknown'){{return 'UNKNOWN'}};if($script:mode-ceq'outer_missing_members'){{return [uint32]1}};return [uint32]4}}
$job|Add-Member ScriptProperty TotalProcesses {{return [uint32]5}}
$script:job=$job
$seams=@{{
    NewOwned={{param($config,$command) [void]$script:events.Add('factory');if($command-notmatch'Invoke-WdMindShadowA5Controller'-or$command-match'Invoke-Expression'){{throw 'not fixed controller command'}};return $script:job}}
    Identity={{param($pidValue) [void]$script:events.Add('identity');return @{{pid=[string]$pidValue;creation_utc='2026-10-22T00:00:00.0000000Z'}}}}
    NowUtc={{[void]$script:events.Add('now');if($script:mode-ceq'late'){{return '2026-10-22T00:00:08.0000000Z'}};return '2026-10-22T00:00:01.0000000Z'}}
    Ready={{param($path) [void]$script:events.Add('ready');if($script:mode-ceq'read_error'){{throw 'synthetic read error'}};if($script:mode-ceq'partial_ready'){{throw 'partial JSON'}};if($script:mode-ceq'positive'){{return Read-WdA5ParentJsonSnapshot $script:fullReadyPath}};return @{{state='ready';value=$script:ready;sha256=('A'*64)}}}}
    Marker={{param($path) [void]$script:events.Add('marker');if(($script:mode-ceq'marker_before'-and-not$script:killed)-or($script:mode-ceq'marker_after'-and$script:killed)){{return 'present'}};return 'absent'}}
    Errors={{[void]$script:events.Add('errors');if($script:mode-ceq'errors_output'){{return @{{stdout='';stderr='unexpected'}}}};return @{{stdout='';stderr=''}}}}
    Released={{param($path,$expectedLine) [void]$script:events.Add('witness');if(-not$script:killed-or$script:job.cleaning){{throw 'witness wrong phase'}};if($script:mode-ceq'member_held'){{return @{{released=$false;text=$null}}}};return @{{released=$true;text=if($script:mode-ceq'foreign_member_line'){{'foreign'}}else{{$expectedLine}}}}}}
}}
$result=Invoke-WdA5OwnedParentCore -Config $config -PureModule $module -Seams $seams
@{{result=$result;events=@($script:events)}}|ConvertTo-Json -Depth 14
"""
    value = run_json(script, ps)
    result, events = value['result'], value['events']
    assert result['whole_A5_PASS'] is False
    assert result['native_permission_inferred'] is False
    assert events[-1] == 'dispose'
    assert events.count('factory') == 1
    assert events.count('kill') <= 1
    if mode == 'positive':
        assert result['status'] == 'OBSERVATIONS_COMPLETE_NOT_A5_PASS', value
        assert events.index('now') < events.index('kill') < events.index('witness') < events.index('cleanup')
        assert result['outer_accounting']['total'] == 5
        assert all(v['launch_id_verified'] is True for v in result['observation']['observed'].values())
    else:
        assert result['status'] == 'UNKNOWN', value
        assert result['errors'], value
    if mode in ('foreign_context', 'foreign_controller', 'late', 'marker_before', 'partial_ready', 'read_error', 'counts_unknown', 'outer_missing_members'):
        assert 'kill' not in events, value
