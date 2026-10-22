"""Strict synthetic A7 owned-runner mechanics; no real process/native calls."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT, _run_powershell
from test_wd_startup_recovery import q


PROBE = ROOT / 'tests/windows/wd_mind_shadow_a7_owned_probe.ps1'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['missing_switch', 'readable_denied_switch', 'missing_junction', 'missing_plain_root'])
def test_public_preparation_refuses_before_evidence_or_type(tmp_path, ps, mode):
    case = tmp_path / 'case'
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    if mode != 'missing_plain_root':
        case.mkdir()
    if mode == 'readable_denied_switch':
        (case / 'mind-shadow.off').write_bytes(b'')
    prepare = {'missing_switch': 'off_switch', 'readable_denied_switch': 'off_switch_denied',
               'missing_junction': 'junction', 'missing_plain_root': 'none'}[mode]
    name = {'off_switch': 'off_switch_before_start', 'off_switch_denied': 'off_switch_denied_read',
            'junction': 'reparse_ancestry', 'none': 'integrity_mismatch'}[prepare]
    script = rf"""
function Read-WdA7PinnedFile {{param($Path,$ExpectedSha256,[switch]$AsText) return @{{path=$Path;sha256=('C'*64);text='synthetic pinned boundary fixture'}}}}
function Import-WdA7PinnedPlanner {{param($Text) return @'
function Test-WdShadowExactKeys {{param($Value,$Keys) return ($Value.Count-eq$Keys.Count-and@($Keys|Where-Object{{-not$Value.ContainsKey($_)}}).Count-eq0)}}
function Get-WdShadowA7Plan {{param($Name,$Inputs,$CaseRoot) return $script:syntheticPlan}}
function Get-WdShadowA7ChildCommand {{param($Plan,$AdapterPath) return 'literal synthetic command'}}
'@}}
function Get-WdA7ObservedIdentity {{param($ProbePid) return @{{pid=[string]$PID;creation_utc='2026-10-22T00:00:00Z'}}}}
function Initialize-WdA7OwnedType {{throw 'unexpected type initialization before preparation verification'}}
function New-WdA7ManagedProcess {{throw 'unexpected process'}}
$inputs=@{{ExpectedSourceCommit=('a'*40);ExpectedManifestSha256=('B'*64);ExpectedAdapterSha256=('C'*64);ApprovedRunRoot={q(case)};LiveDir={q(tmp_path / 'live')};ApprovedUserSid='S-1-5-21-1-2-3-4';ApprovedIntegrityLevel='medium';ExpectedPeakAdditionalBytes=1}}
$runRoot=if({q(prepare)}-ceq'junction'){{Join-Path {q(case)} 'link\run'}}else{{{q(case)}}}
$script:syntheticPlan=@{{name={q(name)};inputs=$inputs;run_root=$runRoot;refusal='^synthetic$';prepare={q(prepare)};preload='none'}}
$attempts=@{{}};$i=1
foreach($n in @('integrity_mismatch','user_sid_mismatch','preloaded_type','off_switch_before_start','off_switch_denied_read','disk_floor_before_start','reparse_ancestry')){{$attempts[$n]=('11111111-1111-1111-1111-'+$i.ToString('000000000000'));$i++}}
$binding=@{{schema='wd.mind-shadow-a7-prestart-binding.v1';harness_run_id='synthetic';case_id='A7';controller_pid=[string]$PID;controller_creation_utc='2026-10-22T00:00:00Z';source_commit=('a'*40);manifest_sha256=('B'*64);adapter_sha256=('C'*64);attempts=$attempts}}
$message=$null
try{{Invoke-WdMindShadowA7OwnedProbe -ApprovedNativeExecution -Plan $script:syntheticPlan -ApprovedInputs $inputs -CaseRoot {q(case)} -LiteralAdapterCommand 'literal synthetic command' -CaseBinding $binding -HostPath 'synthetic' -AdapterPath 'synthetic' -LoopPath 'synthetic' -PlannerPath 'synthetic' -EvidenceRoot {q(evidence)} -ExpectedHostSha256 ('C'*64) -ExpectedAdapterSha256 ('C'*64) -ExpectedLoopSha256 ('C'*64) -ExpectedPlannerSha256 ('C'*64) -ExpectedRunnerSha256 ('C'*64)|Out-Null}}catch{{$message=$_.Exception.Message}}
@{{message=$message;entries=@([IO.Directory]::GetFileSystemEntries({q(evidence)}))}}|ConvertTo-Json
"""
    result = run_json(script, ps)
    assert result['entries'] == [], result
    assert result['message'].startswith('refused: A7 preparation'), result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['none_ok', 'none_nonempty', 'none_reparse', 'switch_ok',
                                  'switch_nonempty', 'switch_directory', 'denied_ok', 'denied_readable',
                                  'denied_unknown', 'junction_ok', 'junction_foreign', 'junction_plain',
                                  'junction_nonempty', 'preloaded_path_ok', 'preloaded_wrong_path',
                                  'unknown_prepare', 'runroot_mismatch'])
def test_preparation_state_matrix_without_acl_or_junction_operations(tmp_path, ps, mode):
    """Real synthetic entries; denied/reparse metadata only are explicit managed fakes."""
    case = tmp_path / 'case'
    case.mkdir()
    prepare = 'none'
    if mode.startswith(('switch_', 'denied_')):
        prepare = 'off_switch_denied' if mode.startswith('denied_') else 'off_switch'
        if mode == 'switch_directory':
            (case / 'mind-shadow.off').mkdir()
        else:
            (case / 'mind-shadow.off').write_bytes(b'not-empty' if mode == 'switch_nonempty' else b'')
    if mode.startswith('junction_'):
        prepare = 'junction'
        (case / 'link').mkdir()  # Ordinary directory; reparse metadata is fake, never a real junction.
        (case / 'target' / 'run').mkdir(parents=True)
        if mode == 'junction_nonempty':
            (case / 'target' / 'run' / 'extra').write_bytes(b'')
    if mode == 'none_nonempty':
        (case / 'extra').write_bytes(b'')
    if mode == 'unknown_prepare':
        prepare = 'unknown'
    script = rf"""
$script:mode={q(mode)};$script:root={q(case)}
function Get-WdA7PreparationAttributes {{param([string]$Path)
    if($Path-ceq(Join-Path $script:root 'mind-shadow.off')){{
        if($script:mode-ceq'denied_ok'){{throw [UnauthorizedAccessException]::new('synthetic denied attributes')}}
        if($script:mode-ceq'denied_unknown'){{throw [IO.IOException]::new('synthetic unknown I/O')}}
    }}
    if($script:mode-ceq'none_reparse'-and$Path-ceq$script:root){{return [IO.FileAttributes]::Directory-bor[IO.FileAttributes]::ReparsePoint}}
    if($script:mode-like'junction_*'-and$script:mode-cne'junction_plain'-and$Path-ceq(Join-Path $script:root 'link')){{return [IO.FileAttributes]::Directory-bor[IO.FileAttributes]::ReparsePoint}}
    return [IO.File]::GetAttributes($Path)
}}
function Get-WdA7PreparationLinkTarget {{param($Path)
    if($Path-cne(Join-Path $script:root 'link')){{throw 'unexpected link read'}}
    if($script:mode-ceq'junction_foreign'){{return 'C:\foreign-target'}}
    return (Join-Path $script:root 'target')
}}
$runRoot=if({q(prepare)}-ceq'junction'){{Join-Path $script:root 'link\run'}}else{{$script:root}}
if($script:mode-ceq'runroot_mismatch'){{$runRoot=Join-Path $script:root 'foreign'}}
$preload=if($script:mode-like'preloaded_*'){{'lane_loop_type'}}else{{'none'}}
$loop='C:\Python\wd-reboot-bundles\'+('a'*40)+'\Invoke-WdLaneTurnLoop.ps1'
if($script:mode-ceq'preloaded_wrong_path'){{$loop='C:\different\Invoke-WdLaneTurnLoop.ps1'}}
$message=$null
try{{Assert-WdA7PreparedState @{{prepare={q(prepare)};run_root=$runRoot;preload=$preload}} $script:root $loop ('a'*40)}}catch{{$message=$_.Exception.Message}}
@{{accepted=($null-eq$message);message=$message}}|ConvertTo-Json
"""
    result = run_json(script, ps)
    positive = mode in ('none_ok', 'switch_ok', 'denied_ok', 'junction_ok', 'preloaded_path_ok')
    assert result['accepted'] is positive, result
    if not positive:
        assert result['message'].startswith('refused: A7 preparation'), result


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
