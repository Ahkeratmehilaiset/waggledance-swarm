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
                                  'extra_count', 'identity_unknown', 'stale_directory', 'stop_failure'])
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
    return @{{pid=[string]$OwnedPid;creation_utc='2026-10-22T00:00:00Z'}}
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
    }}
    & $script:originalWriter $Path $Text
}}
function New-WdA5InnerProcess {{param($HostPath,$Arguments,$Directory,$InputPath,$OutputPath,$ErrorPath)
    if($HostPath-cne'synthetic-host'-or$Arguments.Count-ne4-or$Arguments[3]-notmatch"-Member root -Subcase 'A5/controller_death'"-or$Arguments[3]-notmatch'WD_MIND_SHADOW_SYNTHETIC_MEMBER'){{throw 'not fixed synthetic member command'}}
    $wanted=({q(launch)}+' root'+"`n"+{q(launch)}+' child'+"`n"+{q(launch)}+' grandchild'+"`n")
    if([IO.File]::ReadAllText((Join-Path $Directory 'members.registration'))-cne$wanted){{throw 'registration bytes differ'}}
    [IO.File]::WriteAllText($OutputPath,'');[IO.File]::WriteAllText($ErrorPath,'')
    $value=[pscustomobject]@{{Pid=4242;stopped=$false;disposed=$false}}
    $value|Add-Member ScriptMethod Wait {{param($ms) if($ms-ne0){{throw 'unexpected wait'}};return ($script:mode-ceq'early_exit')}}
    $value|Add-Member ScriptMethod Stop {{$this.stopped=$true;if($script:mode-ceq'stop_failure'){{throw 'synthetic stop failure'}}}}
    $value|Add-Member ScriptMethod Dispose {{$this.disposed=$true}}
    $value|Add-Member ScriptProperty ActiveProcesses {{return [uint32]3}}
    $value|Add-Member ScriptProperty TotalProcesses {{if($script:mode-ceq'unknown_count'){{return 'unknown'}};if($script:mode-ceq'extra_count'){{return [uint32]4}};return [uint32]3}}
    $script:fake=$value;return $value
}}
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic-run';launch_id={q(launch)};source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00Z'}}
$message=$null;$result=$null
try{{$result=Invoke-WdA5ControllerCore $context 'synthetic-host' 'C:\synthetic\member.ps1' {q(run)} @{{synthetic='pin'}} 1 1}}catch{{$message=$_.Exception.Message}}
if($null-ne$script:fake-and-not$script:fake.disposed){{throw 'inner not disposed'}}
$directory=Join-Path {q(run)} {q(launch)};$readyPath=Join-Path $directory 'ready.json';$receipt=$null
if([IO.File]::Exists($readyPath)){{$receipt=[IO.File]::ReadAllText($readyPath)|ConvertFrom-Json}}
$released=$true;$controllerWitness=Join-Path $directory 'controller.witness'
if([IO.File]::Exists($controllerWitness)){{try{{$stream=[IO.File]::Open($controllerWitness,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None);$stream.Dispose()}}catch{{$released=$false}}}}
@{{message=$message;result=$result;receipt=$receipt;ready_before_cleanup=$script:readyBeforeCleanup;controller_witness_released=$released}}|ConvertTo-Json -Depth 14
"""
    value = run_json(script, ps)
    assert value['controller_witness_released'] is True, value
    if mode in ('ready', 'stop_failure'):
        assert value['ready_before_cleanup'] is True, value
        assert value['receipt']['whole_A5_PASS'] is False
        assert value['receipt']['root']['pid'] == '4242'
        assert value['receipt']['context']['launch_id'] == launch
        assert value['receipt']['observation']['expected'] == ['root', 'child', 'grandchild']
        assert all(row['released'] is False for row in value['receipt']['observation']['observed'].values())
        if mode == 'ready':
            assert value['result']['status'] == 'CONTROLLER_EXPIRED_NOT_A5_PASS'
        else:
            assert 'synthetic stop failure' in value['message']
    else:
        assert value['receipt'] is None, value
        expected = {'missing_member': 'member ready timeout', 'early_exit': 'root exited before ready',
                    'unknown_count': 'fixed-tree accounting', 'extra_count': 'fixed-tree accounting',
                    'identity_unknown': 'synthetic identity unknown', 'stale_directory': 'stale A5 launch directory'}[mode]
        assert expected in value['message'], value


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['wrong_schema', 'wrong_launch', 'extra', 'numeric_parent'])
def test_invalid_context_refuses_before_pin_reads(ps, mode):
    script = rf"""
function Read-WdA5PinnedFile {{throw 'unexpected pin read'}}
$context=@{{schema='wd.mind-shadow-a5-controller-context.v1';harness_run_id='synthetic';launch_id='11111111-1111-1111-1111-111111111111';source_commit=('a'*40);manifest_sha256=('B'*64);parent_pid='123';parent_creation_utc='2026-10-22T00:00:00Z'}}
switch({q(mode)}){{wrong_schema{{$context.schema='wrong'}};wrong_launch{{$context.launch_id='wrong'}};extra{{$context.extra='wrong'}};numeric_parent{{$context.parent_pid=123}}}}
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
