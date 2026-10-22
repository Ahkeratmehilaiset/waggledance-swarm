"""A5 Source tests: strict managed fakes only, never a real controller/member."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT, _run_powershell
from test_wd_startup_recovery import q

CONTROLLER = ROOT / 'tests/windows/wd_mind_shadow_a5_controller.ps1'


def run_json(script, ps):
    assert CONTROLLER.is_file(), 'A5 separate controller Source absent'
    prefix = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native Add-Type'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
function Get-CimInstance {{throw 'unexpected CIM'}}
. {q(CONTROLLER)}
"""
    completed = _run_powershell(prefix+script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_controller_parser_default_and_partial_refuse_without_native(ps):
    script = rf"""
$t=$null;$e=$null;[void][Management.Automation.Language.Parser]::ParseFile({q(CONTROLLER)},[ref]$t,[ref]$e)
if($e.Count){{throw 'controller parse error'}}
try{{Invoke-WdMindShadowA5Controller;throw 'default accepted'}}catch{{if($_.Exception.Message-notmatch'explicit approved native opt-in'){{throw}}}}
try{{Invoke-WdMindShadowA5Controller -ApprovedNativeExecution;throw 'partial accepted'}}catch{{if($_.Exception.Message-notmatch'complete'){{throw}}}}
@{{parsed=$true;refused=$true}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'parsed': True, 'refused': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['ready', 'missing_member', 'early_exit', 'unknown_count',
                                  'extra_count', 'identity_unknown', 'stale_directory', 'stop_failure', 'marker_failure'])
def test_controller_body_uses_owned_inner_job_and_ready_precedes_cleanup(tmp_path, ps, mode):
    run = tmp_path / 'run'
    run.mkdir()
    launch = '11111111-1111-1111-1111-111111111111'
    if mode == 'stale_directory':
        (run / launch).mkdir()
    script = rf"""
$script:mode={q(mode)};$script:fake=$null;$script:readyBeforeCleanup=$false
function Get-WdA5Identity {{param([int]$OwnedPid)
    if($script:mode-ceq'identity_unknown'-and$OwnedPid-eq4242){{throw 'synthetic identity unknown'}}
    return @{{pid=[string]$OwnedPid;creation_utc='2026-10-22T00:00:00.0000000Z'}}
}}
function Test-WdA5MemberHeld {{param($Path)
    if($script:mode-ceq'missing_member'-and[IO.Path]::GetFileName($Path)-ceq'grandchild.witness'){{return $false}}
    return $true
}}
$script:originalWriter=(Get-Command Write-WdA5Once).ScriptBlock
function Write-WdA5Once {{param($Path,$Text)
    if([IO.Path]::GetFileName($Path)-ceq'ready.json'){{
        if($null-eq$script:fake-or$script:fake.stopped-or$script:fake.disposed){{throw 'ready after inner cleanup'}}
        $script:readyBeforeCleanup=$true
        $script:readyClock=[Diagnostics.Stopwatch]::StartNew()
    }}
    if([IO.Path]::GetFileName($Path)-ceq'cleanup-started.json'-and$script:mode-ceq'marker_failure'){{throw 'synthetic marker write failure'}}
    & $script:originalWriter $Path $Text
}}
function New-WdA5InnerProcess {{param($HostPath,$Arguments,$Directory,$InputPath,$OutputPath,$ErrorPath)
    if($HostPath-cne'synthetic-host'-or$Arguments.Count-ne4-or$Arguments[3]-notmatch"-Member root -Subcase 'A5/controller_death'"-or$Arguments[3]-notmatch'WD_MIND_SHADOW_SYNTHETIC_MEMBER'){{throw 'not fixed synthetic member command'}}
    $wanted=({q(launch)}+' root'+"`n"+{q(launch)}+' child'+"`n"+{q(launch)}+' grandchild'+"`n")
    if([IO.File]::ReadAllText((Join-Path $Directory 'members.registration'))-cne$wanted){{throw 'registration bytes differ'}}
    [IO.File]::WriteAllText($OutputPath,'');[IO.File]::WriteAllText($ErrorPath,'')
    $value=[pscustomobject]@{{Pid=4242;stopped=$false;disposed=$false}}
    $value|Add-Member ScriptMethod Wait {{param($ms)
        if($ms-ne0){{throw 'unexpected wait'}}
        if($script:mode-ceq'marker_failure'-and$script:readyBeforeCleanup){{throw 'synthetic early ordinary error'}}
        return ($script:mode-ceq'early_exit')
    }}
    $value|Add-Member ScriptMethod Stop {{
        if($script:mode-cne'marker_failure'-and-not[IO.File]::Exists((Join-Path {q(run / launch)} 'cleanup-started.json'))){{throw 'cleanup marker missing before inner stop'}}
        $this.stopped=$true;if($script:mode-ceq'stop_failure'){{throw 'synthetic stop failure'}}
    }}
    $value|Add-Member ScriptMethod Dispose {{
        if($script:mode-ceq'marker_failure'){{$script:disposeElapsed=$script:readyClock.Elapsed.TotalSeconds}}
        $this.disposed=$true
    }}
    $value|Add-Member ScriptProperty ActiveProcesses {{return [uint32]3}}
    $value|Add-Member ScriptProperty TotalProcesses {{if($script:mode-ceq'unknown_count'){{return 'unknown'}};if($script:mode-ceq'extra_count'){{return [uint32]4}};return [uint32]3}}
    $script:fake=$value;return $value
}}
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic-run';launch_id={q(launch)};source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00.0000000Z'}}
$script:disposeElapsed=$null
$message=$null;$result=$null
try{{$result=Invoke-WdA5ControllerCore $context 'synthetic-host' 'C:\synthetic\member.ps1' {q(run)} @{{synthetic='pin'}} 1 1}}catch{{$message=$_.Exception.Message}}
if($null-ne$script:fake-and-not$script:fake.disposed){{throw 'inner not disposed'}}
$directory=Join-Path {q(run)} {q(launch)};$readyPath=Join-Path $directory 'ready.json';$receipt=$null
if([IO.File]::Exists($readyPath)){{
    $json=[IO.File]::ReadAllText($readyPath)
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){{$receipt=$json|ConvertFrom-Json -DateKind String}}
    else{{$receipt=$json|ConvertFrom-Json}}
}}
$window=$null
if($null-ne$receipt){{
    $expectedController=@{{pid=[string]$PID;creation_utc='2026-10-22T00:00:00.0000000Z'}}
    $window=Test-WdA5ReadyKillWindow -Ready $receipt -ExpectedContext $context -ExpectedController $expectedController -NowUtc $receipt.ready_utc -SafetyMarginSeconds 0.25
}}
$released=$true;$controllerWitness=Join-Path $directory 'controller.witness'
if([IO.File]::Exists($controllerWitness)){{try{{$stream=[IO.File]::Open($controllerWitness,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None);$stream.Dispose()}}catch{{$released=$false}}}}
@{{message=$message;result=$result;receipt=$receipt;roundtrip_window=$window;ready_before_cleanup=$script:readyBeforeCleanup;controller_witness_released=$released;cleanup_marker=[IO.File]::Exists((Join-Path $directory 'cleanup-started.json'));dispose_elapsed=$script:disposeElapsed}}|ConvertTo-Json -Depth 14
"""
    value = run_json(script, ps)
    assert value['controller_witness_released'] is True, value
    assert value['cleanup_marker'] is (mode not in ('stale_directory', 'marker_failure')), value
    if mode in ('ready', 'stop_failure', 'marker_failure'):
        assert value['ready_before_cleanup'] is True, value
        if mode == 'marker_failure':
            assert value['dispose_elapsed'] >= 0.95, value
            assert 'synthetic early ordinary error' in value['message'], value
            assert 'synthetic marker write failure' in value['message'], value
        assert value['receipt']['whole_A5_PASS'] is False
        assert value['receipt']['ready_utc'].endswith('Z')
        assert value['receipt']['hold_seconds'] == 1
        assert value['receipt']['deadline_utc'].endswith('Z')
        assert value['receipt']['root']['pid'] == '4242'
        assert value['receipt']['context']['launch_id'] == launch
        assert value['receipt']['observation']['expected'] == ['root', 'child', 'grandchild']
        assert all(row['released'] is False for row in value['receipt']['observation']['observed'].values())
        assert value['roundtrip_window']['within_window'] is True, value
        assert value['roundtrip_window']['native_authority'] is False, value
        for row in value['receipt']['observation']['observed'].values():
            assert row['expected_launch_id'] == launch, value
            assert row['launch_id_verified'] is False, value
            assert 'launch_id' not in row, value
        if mode == 'ready':
            assert value['result']['status'] == 'CONTROLLER_EXPIRED_NOT_A5_PASS'
        elif mode == 'stop_failure':
            assert 'synthetic stop failure' in value['message']
        else:
            assert value['dispose_elapsed'] >= 0.95, value
            assert 'synthetic early ordinary error' in value['message'], value
            assert 'synthetic marker write failure' in value['message'], value
    else:
        assert value['receipt'] is None, value
        expected = {'missing_member': 'member ready timeout', 'early_exit': 'root exited before ready',
                    'unknown_count': 'fixed-tree accounting', 'extra_count': 'fixed-tree accounting',
                    'identity_unknown': 'synthetic identity unknown', 'stale_directory': 'stale A5 launch directory'}[mode]
        assert expected in value['message'], value


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['wrong_schema', 'wrong_launch', 'extra', 'numeric_parent', 'culture_parent'])
def test_invalid_context_refuses_before_pin_reads(ps, mode):
    script = rf"""
function Read-WdA5PinnedFile {{throw 'unexpected pin read'}}
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic';launch_id='11111111-1111-1111-1111-111111111111';source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00.0000000Z'}}
switch({q(mode)}){{wrong_schema{{$context.schema='wrong'}};wrong_launch{{$context.launch_id='wrong'}};extra{{$context.extra='wrong'}};numeric_parent{{$context.parent_pid=123}};culture_parent{{$context.parent_creation_utc='22.10.2026 00:00:00'}}}}
$message=$null
try{{Invoke-WdMindShadowA5Controller -ApprovedNativeExecution -Context $context -HostPath 'synthetic' -LoopPath 'synthetic' -MemberPath 'synthetic' -RunRoot 'synthetic'|Out-Null}}catch{{$message=$_.Exception.Message}}
@{{message=$message}}|ConvertTo-Json
"""
    result = run_json(script, ps)
    assert result['message'].startswith('refused:'), result
    assert 'pin read' not in result['message'], result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_actual_synthetic_pin_reader_and_create_new_no_overwrite(tmp_path, ps):
    payload = tmp_path / 'pinned.txt'
    payload.write_bytes(b'synthetic pinned source\n')
    target = tmp_path / 'once.json'
    script = rf"""
$bytes=[IO.File]::ReadAllBytes({q(payload)});$sha=Get-WdA5Sha256 $bytes
$pin=Read-WdA5PinnedFile {q(payload)} $sha -Text
if($pin.text-cne"synthetic pinned source`n"){{throw 'pinned text changed'}}
try{{Read-WdA5PinnedFile {q(payload)} ('0'*64)|Out-Null;throw 'wrong hash accepted'}}catch{{if($_.Exception.Message-notmatch'pin mismatch'){{throw}}}}
Write-WdA5Once {q(target)} 'first'
try{{Write-WdA5Once {q(target)} 'second';throw 'overwrite accepted'}}catch{{if($_.Exception.Message-eq'overwrite accepted'){{throw}}}}
if([IO.File]::ReadAllText({q(target)})-cne'first'){{throw 'original receipt overwritten'}}
try{{Get-WdA5Literal "bad`n"|Out-Null;throw 'newline literal accepted'}}catch{{if($_.Exception.Message-notmatch'printable ASCII'){{throw}}}}
@{{pin_verified=$true;overwrite_refused=$true}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'pin_verified': True, 'overwrite_refused': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['open', 'at_ready', 'one_tick_before', 'at_cutoff', 'late', 'future',
                                  'missing', 'foreign', 'foreign_controller', 'inconsistent', 'culture',
                                  'offset', 'zero_margin', 'negative_margin', 'nan', 'infinity',
                                  'margin_equals_hold', 'string_hold', 'bool_hold', 'wrong_status',
                                  'string_margin', 'bool_margin', 'fractional_hold', 'invalid_date',
                                  'overflow', 'whole_pass', 'foreign_controller_time', 'array_hold', 'array_time', 'array_pass', 'unrepresentable_margin'])
def test_pure_ready_kill_window_is_strict_bound_and_never_authority(ps, mode):
    script = rf"""
$expected=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic';launch_id='11111111-1111-1111-1111-111111111111';source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00.0000000Z'}}
$controller=@{{pid='456';creation_utc='2026-10-22T00:00:00.0000000Z'}}
$copy=@{{}};foreach($key in $expected.Keys){{$copy[$key]=$expected[$key]}}
$ready=@{{schema='wd.mind-shadow-a5-controller-ready.v1';status='INNER_JOB_READY_NOT_A5_PASS';subcase='A5/controller_death';whole_A5_PASS=$false;context=$copy;controller=@{{pid='456';creation_utc='2026-10-22T00:00:00.0000000Z'}};ready_utc='2026-10-22T00:00:00.0000000Z';hold_seconds=10;deadline_utc='2026-10-22T00:00:10.0000000Z'}}
$now='2026-10-22T00:00:05.0000000Z';$margin=2
switch({q(mode)}){{
    at_ready{{$now=$ready.ready_utc}};one_tick_before{{$now='2026-10-22T00:00:07.9999999Z'}}
    at_cutoff{{$now='2026-10-22T00:00:08.0000000Z'}};late{{$now='2026-10-22T00:00:11.0000000Z'}}
    future{{$now='2026-10-21T23:59:59.9999999Z'}};missing{{$ready.Remove('deadline_utc')}}
    foreign{{$ready.context.harness_run_id='foreign'}};foreign_controller{{$ready.controller.pid='789'}}
    inconsistent{{$ready.deadline_utc='2026-10-22T00:00:11.0000000Z'}}
    culture{{$ready.ready_utc='22.10.2026 00:00:00'}};offset{{$ready.ready_utc='2026-10-22T00:00:00.0000000+00:00'}}
    zero_margin{{$margin=0}};negative_margin{{$margin=-1}};nan{{$margin=[double]::NaN}};infinity{{$margin=[double]::PositiveInfinity}}
    margin_equals_hold{{$margin=10}};string_hold{{$ready.hold_seconds='10'}};bool_hold{{$ready.hold_seconds=$true}}
    wrong_status{{$ready.status='PASS'}}
    string_margin{{$margin='2'}};bool_margin{{$margin=$true}};fractional_hold{{$ready.hold_seconds=10.5}}
    invalid_date{{$ready.ready_utc='2026-02-30T00:00:00.0000000Z'}}
    overflow{{$ready.ready_utc='9999-12-31T23:59:59.0000000Z';$ready.deadline_utc='9999-12-31T23:59:59.0000000Z'}}
    whole_pass{{$ready.whole_A5_PASS=$true}};foreign_controller_time{{$ready.controller.creation_utc='2026-10-22T00:00:01.0000000Z'}}
    array_hold{{$ready.hold_seconds=@(10)}};array_time{{$ready.ready_utc=@($ready.ready_utc)}};array_pass{{$ready.whole_A5_PASS=@($false)}}
    unrepresentable_margin{{$margin=[double]1e-20}}
}}
$result=Test-WdA5ReadyKillWindow -Ready $ready -ExpectedContext $expected -ExpectedController $controller -NowUtc $now -SafetyMarginSeconds $margin
$result|ConvertTo-Json
"""
    result = run_json(script, ps)
    assert result['within_window'] is (mode in ('open', 'at_ready', 'one_tick_before')), result
    assert result['native_authority'] is False
