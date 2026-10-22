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
def test_ready_snapshot_refuses_single_object_array_before_json_coercion(tmp_path, ps):
    packet = tmp_path / 'array-ready.json'
    packet.write_text('[{"ready_utc":"2026-10-22T00:00:00.0000000Z"}]', encoding='utf-8')
    script = rf"""
$message=$null
try{{Read-WdA5ParentJsonSnapshot {q(packet)}|Out-Null;throw 'array accepted'}}catch{{$message=$_.Exception.Message}}
@{{message=$message}}|ConvertTo-Json
"""
    value = run_json(script, ps)
    assert 'JSON object' in value['message'], value


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['positive', 'foreign_context', 'foreign_controller', 'late', 'marker_before',
                                  'marker_after', 'partial_ready', 'read_error', 'stop_error', 'wait_timeout',
                                  'exit_wrong', 'member_held', 'foreign_member_line', 'counts_unknown',
                                  'cleanup_error', 'errors_output', 'outer_missing_members',
                                  'expected_string', 'expected_order', 'expected_extra', 'directory_array',
                                  'pin_array', 'pin_number', 'command_array', 'observed_extra', 'row_array',
                                  'raw_partial', 'raw_array', 'raw_scalar', 'raw_utf8', 'raw_empty', 'snapshot_wrong_type',
                                  'death_delayed', 'death_held', 'death_wrong_type', 'factory_partial_failure',
                                  'invalid_path_array', 'invalid_pin_array', 'invalid_pin_keys', 'invalid_margin_nan',
                                  'invalid_margin_infinite', 'invalid_margin_string', 'invalid_path_alias'])
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
switch($script:mode){{
 invalid_path_array{{$config.MemberPath=@($config.MemberPath)}}
 invalid_pin_array{{$config.Pins=$pins.Clone();$config.Pins.host_sha256=@($config.Pins.host_sha256)}}
 invalid_pin_keys{{$config.Pins=$pins.Clone();$config.Pins.extra=('5'*64)}}
 invalid_margin_nan{{$config.SafetyMarginSeconds=[double]::NaN}}
 invalid_margin_infinite{{$config.SafetyMarginSeconds=[double]::PositiveInfinity}}
 invalid_margin_string{{$config.SafetyMarginSeconds='2'}}
 invalid_path_alias{{$config.MemberPath='C:\synthetic\.\member.ps1'}}
}}
$directory=Join-Path {q(run)} $context.launch_id
$readyContext=@{{}};foreach($key in $context.Keys){{$readyContext[$key]=$context[$key]}}
if($script:mode-ceq'foreign_context'){{$readyContext.harness_run_id='foreign'}}
$expectedCommand=Get-WdA5ParentMemberCommand $config.MemberPath $directory $context.launch_id 71
$script:ready=@{{schema='wd.mind-shadow-a5-controller-ready.v1';status='INNER_JOB_READY_NOT_A5_PASS';subcase='A5/controller_death';whole_A5_PASS=$false;context=$readyContext;controller=@{{pid=if($script:mode-ceq'foreign_controller'){{'999'}}else{{'4242'}};creation_utc='2026-10-22T00:00:00.0000000Z'}};root=@{{pid='5000';creation_utc='2026-10-22T00:00:00.0000000Z'}};directory=$directory;pins=$pins;cleanup_marker_path=(Join-Path $directory 'cleanup-started.json');command_sha256=(Get-WdA5ParentSha256 ([Text.Encoding]::UTF8.GetBytes($expectedCommand)));ready_utc='2026-10-22T00:00:00.0000000Z';deadline_utc='2026-10-22T00:00:10.0000000Z';hold_seconds=10;inner_accounting=@{{active=3;total=3}};observation=@{{expected=@('root','child','grandchild');observed=@{{root=@{{start_witness='present';released=$false;launch_id=$context.launch_id}};child=@{{start_witness='present';released=$false;launch_id=$context.launch_id}};grandchild=@{{start_witness='present';released=$false;launch_id=$context.launch_id}}}}}}}}
$job=[pscustomobject]@{{Pid=4242;cleaning=$false}}
foreach($row in $script:ready.observation.observed.Values){{$row.expected_launch_id=$row.launch_id;[void]$row.Remove('launch_id');$row.launch_id_verified=$false}}
switch($script:mode){{
    expected_string{{$script:ready.observation.expected='root,child,grandchild'}}
    expected_order{{$script:ready.observation.expected=@('child','root','grandchild')}}
    expected_extra{{$script:ready.observation.expected=@('root','child','grandchild','extra')}}
    directory_array{{$script:ready.directory=@($script:ready.directory)}}
    pin_array{{$script:ready.pins=$script:ready.pins.Clone();$script:ready.pins.host_sha256=@($script:ready.pins.host_sha256)}}
    pin_number{{$script:ready.pins=$script:ready.pins.Clone();$script:ready.pins.host_sha256=1}}
    command_array{{$script:ready.command_sha256=@($script:ready.command_sha256)}}
    observed_extra{{$script:ready.observation.observed.extra=@{{start_witness='present'}}}}
    row_array{{$script:ready.observation.observed.root.expected_launch_id=@($context.launch_id)}}
}}
$script:fullReadyPath={q(evidence / 'full-ready-roundtrip.json')}
[IO.File]::WriteAllText($script:fullReadyPath,($script:ready|ConvertTo-Json -Depth 14 -Compress),[Text.UTF8Encoding]::new($false))
switch($script:mode){{
    raw_partial{{[IO.File]::WriteAllText($script:fullReadyPath,'{{')}}
    raw_array{{[IO.File]::WriteAllText($script:fullReadyPath,'[{{}}]')}}
    raw_scalar{{[IO.File]::WriteAllText($script:fullReadyPath,'"scalar"')}}
    raw_utf8{{[IO.File]::WriteAllBytes($script:fullReadyPath,[byte[]]@(255))}}
    raw_empty{{[IO.File]::WriteAllBytes($script:fullReadyPath,[byte[]]@())}}
}}
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
$script:deathReads=0
$job|Add-Member ScriptProperty ActiveProcesses {{
 if($this.cleaning){{return [uint32]0}}
 if($script:mode-ceq'counts_unknown'){{return 'UNKNOWN'}}
 if($script:mode-ceq'outer_missing_members'){{return [uint32]1}}
 if($script:killed){{
  [void]$script:events.Add('death_count');$script:deathReads++
  if($script:mode-ceq'death_wrong_type'){{return 0}}
  if($script:mode-ceq'death_held'){{return [uint32]4}}
  if($script:mode-ceq'death_delayed'-and$script:deathReads-lt3){{return [uint32](4-2*($script:deathReads-1))}}
  return [uint32]0
 }}
 return [uint32]4
}}
$job|Add-Member ScriptProperty TotalProcesses {{return [uint32]5}}
$script:job=$job
$seams=@{{
    NewOwned={{param($config,$command) [void]$script:events.Add('factory');if($script:mode-ceq'factory_partial_failure'){{[void]$script:events.Add('factory_dispose');throw 'original factory failure; partial cleanup failed'}};if($command-notmatch'Invoke-WdMindShadowA5Controller'-or$command-match'Invoke-Expression'){{throw 'not fixed controller command'}};return $script:job}}
    Identity={{param($pidValue) [void]$script:events.Add('identity');return @{{pid=[string]$pidValue;creation_utc='2026-10-22T00:00:00.0000000Z'}}}}
    NowUtc={{[void]$script:events.Add('now');if($script:mode-ceq'late'){{return '2026-10-22T00:00:08.0000000Z'}};return '2026-10-22T00:00:01.0000000Z'}}
    Ready={{param($path) [void]$script:events.Add('ready');if($script:mode-ceq'read_error'){{throw 'synthetic read error'}};if($script:mode-ceq'partial_ready'){{throw 'partial JSON'}};if($script:mode-ceq'positive'-or$script:mode-like'raw_*'){{return Read-WdA5ParentJsonSnapshot $script:fullReadyPath}};if($script:mode-ceq'snapshot_wrong_type'){{return [pscustomobject]@{{state='ready';value=$script:ready;sha256=('A'*64)}}}};return @{{state='ready';value=$script:ready;sha256=('A'*64)}}}}
    Marker={{param($path) [void]$script:events.Add('marker');if(($script:mode-ceq'marker_before'-and-not$script:killed)-or($script:mode-ceq'marker_after'-and$script:killed)){{return 'present'}};return 'absent'}}
    Errors={{[void]$script:events.Add('errors');if($script:mode-ceq'errors_output'){{return @{{stdout='';stderr='unexpected'}}}};return @{{stdout='';stderr=''}}}}
    Released={{param($path,$expectedLine) [void]$script:events.Add('witness');if(-not$script:killed-or$script:job.cleaning-or$script:deathReads-lt1){{throw 'witness before member-death observation or after cleanup'}};if($script:mode-ceq'member_held'){{return @{{released=$false;text=$null}}}};return @{{released=$true;text=if($script:mode-ceq'foreign_member_line'){{'foreign'}}else{{$expectedLine}}}}}}
}}
$result=Invoke-WdA5OwnedParentCore -Config $config -PureModule $module -Seams $seams
@{{result=$result;events=@($script:events)}}|ConvertTo-Json -Depth 14
"""
    value = run_json(script, ps)
    result, events = value['result'], value['events']
    assert result['whole_A5_PASS'] is False
    assert result['native_permission_inferred'] is False
    if mode.startswith('invalid_'):
        assert events == [], value
    elif mode == 'factory_partial_failure':
        assert events == ['identity', 'factory', 'factory_dispose'], value
        assert 'original factory failure' in result['errors'][0], value
    else:
        assert events[-1] == 'dispose'
        assert events.count('factory') == 1
    assert events.count('kill') <= 1
    if mode in ('positive', 'death_delayed'):
        assert result['status'] == 'OBSERVATIONS_COMPLETE_NOT_A5_PASS', value
        assert events.index('now') < events.index('kill') < events.index('death_count') < events.index('errors') < events.index('witness') < events.index('cleanup')
        assert result['outer_accounting']['total'] == 5
        assert result['pre_cleanup_zero']['active'] == 0
        assert result['pre_cleanup_zero']['observed_before_outer_cleanup'] is True
        if mode == 'death_delayed':
            assert events.count('death_count') == 3, value
        assert all(v['launch_id_verified'] is True for v in result['observation']['observed'].values())
    else:
        assert result['status'] == 'UNKNOWN', value
        assert result['errors'], value
    if mode in ('foreign_context', 'foreign_controller', 'late', 'marker_before', 'partial_ready', 'read_error', 'counts_unknown', 'outer_missing_members'):
        assert 'kill' not in events, value
    if mode.startswith(('expected_', 'directory_', 'pin_', 'command_', 'observed_', 'row_', 'raw_', 'snapshot_')):
        assert 'kill' not in events, value
    if mode in ('death_held', 'death_wrong_type'):
        assert 'witness' not in events and 'errors' not in events, value


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_real_source_ready_sharing_and_file_helpers_are_bounded_snapshots(tmp_path, ps):
    source = tmp_path / 'source.ps1'
    source.write_text('synthetic pinned text\n', encoding='utf-8')
    ready = tmp_path / 'ready.json'
    ready.write_text('{"value":"synthetic"}', encoding='utf-8')
    witness = tmp_path / 'root.witness'
    witness.write_bytes(b'synthetic root\n')
    script = rf"""
$source={q(source)};$ready={q(ready)};$witness={q(witness)}
$raw=[IO.File]::ReadAllBytes($source);$hash=Get-WdA5ParentSha256 $raw
$pin=Read-WdA5ParentPin $source $hash -Text
if($pin.text-cne"synthetic pinned text`n"){{throw 'pin read'}}
foreach($bad in @(@($source,('A'*64)),@({q(tmp_path)},$hash))){{
 $refused=$false;try{{Read-WdA5ParentPin $bad[0] $bad[1]|Out-Null}}catch{{$refused=$true}};if(-not$refused){{throw 'bad pin accepted'}}
}}
if((Read-WdA5ParentMarker $source)-cne'present'-or(Read-WdA5ParentMarker {q(tmp_path / 'absent.json')})-cne'absent'){{throw 'marker state'}}
$refused=$false;try{{Read-WdA5ParentMarker {q(tmp_path / 'missing-dir' / 'marker.json')}|Out-Null}}catch{{$refused=$true}};if(-not$refused){{throw 'missing-dir marker accepted'}}
$held=[IO.File]::Open($ready,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try{{$snapshot=Read-WdA5ParentJsonSnapshot $ready;if($snapshot.state-cne'pending'){{throw 'sharing must stay pending'}}}}finally{{$held.Dispose()}}
if((Read-WdA5ParentJsonSnapshot $ready).state-cne'ready'){{throw 'unlocked ready'}}
$refused=$false;try{{Read-WdA5ParentJsonSnapshot {q(tmp_path)}|Out-Null}}catch{{$refused=$true}};if(-not$refused){{throw 'unrelated ready IO accepted'}}
$held=[IO.File]::Open($witness,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
try{{$r=Read-WdA5ParentReleasedWitness $witness;if($r.released){{throw 'held witness accepted'}}}}finally{{$held.Dispose()}}
$r=Read-WdA5ParentReleasedWitness $witness
if(-not$r.released-or$r.text-cne"synthetic root`n"){{throw 'actual synthetic released bytes'}}
$refused=$false;try{{Read-WdA5ParentReleasedWitness {q(tmp_path)}|Out-Null}}catch{{$refused=$true}};if(-not$refused){{throw 'unrelated witness IO accepted'}}
if((Get-WdA5ParentSha256 ([IO.File]::ReadAllBytes($source)))-cne$hash-or[IO.File]::ReadAllText($witness)-cne"synthetic root`n"){{throw 'read helper overwrote fixture'}}
@{{all_controls=$true;sharing_pending=$true;synthetic_only=$true}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'all_controls': True, 'sharing_pending': True, 'synthetic_only': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_real_frozen_member_command_and_fixed_controller_literal_closure(tmp_path, ps):
    frozen = tmp_path / 'frozen-controller.ps1'
    frozen.write_text(frozen_controller_text(), encoding='utf-8')
    script = rf"""
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({q(frozen)},[ref]$t,[ref]$e)
if($e.Count){{throw 'frozen parse'}}
$parts=@();foreach($name in @('Get-WdA5Literal','Get-WdA5MemberCommand')){{
 $found=@($ast.EndBlock.Statements|Where-Object{{$_-is[Management.Automation.Language.FunctionDefinitionAst]-and$_.Name-ceq$name}})
 if($found.Count-ne1){{throw 'pure member function closure'}};$parts+=$found[0].Extent.Text
}}
$oracle=New-Module -ScriptBlock ([scriptblock]::Create($parts-join"`n"))
foreach($pair in @(@(1,1),@(1,120),@(30,120))){{
 $lifetime=$pair[0]+$pair[1]+60
 $actual=Get-WdA5ParentMemberCommand "C:\synthetic\member's.ps1" "C:\synthetic\run's\launch" 'synthetic-launch' $lifetime
 $expected=& $oracle {{param($l)Get-WdA5MemberCommand "C:\synthetic\member's.ps1" "C:\synthetic\run's\launch" 'synthetic-launch' $l}} $lifetime
 if($actual-cne$expected){{throw 'independent frozen command mismatch'}}
}}
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id="synthetic's";launch_id='11111111-1111-1111-1111-111111111111';source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00.0000000Z'}}
$config=@{{Context=$context;HostPath="C:\synthetic\host's.exe";LoopPath='C:\synthetic\loop.ps1';MemberPath="C:\synthetic\member's.ps1";ControllerPath="C:\synthetic\controller's.ps1";RunRoot="C:\synthetic\run's";Pins=@{{host_sha256=('1'*64);loop_sha256=('2'*64);member_sha256=('3'*64);controller_sha256=('4'*64)}};ControllerReadySeconds=30;HoldSeconds=120}}
$command=Get-WdA5ParentControllerCommand $config
$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseInput($command,[ref]$tokens,[ref]$errors)
if($errors.Count-or-not$command.Contains("'synthetic''s'")-or-not$command.Contains("'C:\synthetic\controller''s.ps1'")-or-not$command.EndsWith(' -ReadyTimeoutSeconds 30 -HoldSeconds 120')){{throw 'fixed controller literal/arguments'}}
foreach($key in @('ExpectedHostSha256','ExpectedLoopSha256','ExpectedMemberSha256','ExpectedControllerSha256')){{if(-not$command.Contains(' -'+$key+' ')){{throw 'missing command pin'}}}}
@{{frozen_comparison=$true;fixed_command_parses=$true;executed=$false}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'frozen_comparison': True, 'fixed_command_parses': True, 'executed': False}
