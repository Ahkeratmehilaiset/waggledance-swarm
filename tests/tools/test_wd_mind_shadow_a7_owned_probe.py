"""Strict synthetic A7 owned-runner mechanics; no real process/native calls."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT, _run_powershell
from test_wd_startup_recovery import q


PROBE = ROOT / 'tests/windows/wd_mind_shadow_a7_owned_probe.ps1'


def run_json(script, ps):
    assert PROBE.is_file(), 'Actual owned-job A7 runner Source is absent'
    prefix = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected real Add-Type/native compilation'}}
function Start-Process {{throw 'unexpected real process'}}
function Get-Process {{throw 'unexpected real process query'}}
function Get-CimInstance {{throw 'unexpected real CIM'}}
. {q(PROBE)}
"""
    completed = _run_powershell(prefix + script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_default_partial_and_parser_refuse_without_native(ps):
    script = rf"""
$t=$null;$e=$null
[void][Management.Automation.Language.Parser]::ParseFile({q(PROBE)},[ref]$t,[ref]$e)
if($e.Count){{throw 'parse error'}}
try {{Invoke-WdMindShadowA7OwnedProbe;throw 'default accepted'}}catch{{if($_.Exception.Message-notmatch 'explicit approved native opt-in'){{throw}}}}
try {{Invoke-WdMindShadowA7OwnedProbe -ApprovedNativeExecution;throw 'partial accepted'}}catch{{if($_.Exception.Message-notmatch 'complete'){{throw}}}}
@{{parsed=$true;refused=$true}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'parsed': True, 'refused': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_typed_case_binding_refuses_before_source_or_process_reads(ps):
    script = rf"""
$binding=@{{schema='wd.mind-shadow-a7-prestart-binding.v1';harness_run_id='synthetic';case_id='A7';controller_pid=[long]4242;controller_creation_utc='synthetic-time';source_commit=('a'*40);manifest_sha256=('B'*64);adapter_sha256=('C'*64);attempts=@{{}}}}
try {{
    Invoke-WdMindShadowA7OwnedProbe -ApprovedNativeExecution -Plan @{{name='not_used'}} -ApprovedInputs @{{not_used='synthetic'}} -CaseRoot 'C:\synthetic\.codex-audit\case' -LiteralAdapterCommand 'must never run' -CaseBinding $binding -EvidenceRoot 'C:\synthetic\.codex-audit'
    throw 'typed binding accepted'
}} catch {{if($_.Exception.Message-notmatch 'complete typed case binding'){{throw}}}}
@{{typed_binding_refused=$true}}|ConvertTo-Json
"""
    assert run_json(script, ps) == {'typed_binding_refused': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['exact_two', 'preloaded_one', 'fast_excess', 'root_only', 'wrong_guard',
                                  'new_directory', 'zero_exit', 'unknown_total', 'identity_unknown', 'timeout'])
def test_actual_owned_command_mechanics_with_strict_fake(tmp_path, ps, mode):
    evidence = tmp_path / 'evidence'
    runroot = tmp_path / 'runroot'
    evidence.mkdir()
    runroot.mkdir()
    name = 'preloaded_type' if mode == 'preloaded_one' else 'off_switch_before_start'
    total = {'preloaded_one': 1, 'fast_excess': 3, 'root_only': 1}.get(mode, 2)
    script = rf"""
$script:fakeMode={q(mode)}
function Get-WdA7ObservedIdentity {{param([int]$ProbePid)
    if($script:fakeMode-ceq 'identity_unknown'){{throw 'synthetic missing creation'}}
    return @{{pid=[string]$ProbePid;creation_utc='2026-10-22T00:00:00.0000000Z'}}
}}
function New-WdA7ManagedProcess {{
    param($HostPath,$Arguments,$WorkingDirectory,$InputPath,$OutputPath,$ErrorPath)
    if($Arguments.Count-ne6 -or $Arguments[5]-cne 'literal synthetic command'){{throw 'wrong literal command'}}
    [IO.File]::WriteAllText($OutputPath,'')
    $line=if($script:fakeMode-ceq 'wrong_guard'){{'WD-A7-REFUSAL wrong'}}else{{'WD-A7-REFUSAL exact'}}
    [IO.File]::WriteAllText($ErrorPath,$line+"`n")
    if($script:fakeMode-ceq 'new_directory'){{[void][IO.Directory]::CreateDirectory({q(runroot / 'unexpected-run')})}}
    $value=[pscustomobject]@{{Pid=4242;stopped=$false;disposed=$false;total=[uint32]{total}}}
    $value|Add-Member ScriptMethod Wait {{param($ms) if($ms-ne0){{throw 'unexpected wait mode'}};return ($script:fakeMode-cne 'timeout' -or $this.stopped)}}
    $value|Add-Member ScriptMethod Stop {{$this.stopped=$true}}
    $value|Add-Member ScriptMethod Dispose {{$this.disposed=$true}}
    $value|Add-Member ScriptProperty ActiveProcesses {{if($script:fakeMode-ceq 'timeout'-and-not$this.stopped){{return [uint32]1}};return [uint32]0}}
    $value|Add-Member ScriptProperty TotalProcesses {{if($script:fakeMode-ceq 'unknown_total'){{return 'UNKNOWN'}};return $this.total}}
    $value|Add-Member ScriptProperty ExitCode {{if($this.stopped){{return 124}};if($script:fakeMode-ceq 'zero_exit'){{return 0}};return 2}}
    $script:fakeJob=$value
    return $value
}}
$result=Invoke-WdA7OwnedCommand -HostPath 'C:\synthetic\pwsh.exe' -LiteralAdapterCommand 'literal synthetic command' -WorkingDirectory {q(ROOT)} -EvidenceDirectory {q(evidence)} -RunRoot {q(runroot)} -Name {q(name)} -RefusalPattern '^WD-A7-REFUSAL exact\z' -AttemptId '11111111-1111-1111-1111-111111111111' -WaitSeconds 1 -DrainSeconds 1 -MaxOutputBytes 4096
if(-not $script:fakeJob.disposed){{throw 'job not disposed'}}
$result|ConvertTo-Json -Depth 8
"""
    value = run_json(script, ps)
    assert value['whole_A7_PASS'] is False
    assert value['owned_job']['expected_probe_processes'] == (1 if mode == 'preloaded_one' else 2)
    if mode in ('exact_two', 'preloaded_one'):
        assert value['status'] == 'OWNED_GUARD_REFUSAL_OBSERVED'
        assert value['entry']['refusal']['process_started'] is False
        assert value['owned_job']['total_processes'] == total
        assert value['entry']['attempt']['probe_pid'] == '4242'
    else:
        assert value['status'] in ('FAIL', 'UNKNOWN')
        if mode == 'fast_excess':
            assert value['owned_job']['total_processes'] == 3
            assert value['entry']['refusal']['process_started'] is True
        if mode in ('unknown_total', 'identity_unknown', 'timeout'):
            assert value['entry']['refusal']['process_started'] is None
