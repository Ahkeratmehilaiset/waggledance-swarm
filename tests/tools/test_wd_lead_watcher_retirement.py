"""Owned Lead producer attestation; no real process is stopped in these tests."""
import json
import hashlib
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


def _baseline_publication_probe(tmp_path, ps, case, *, non_atomic_mutant=False):
    """Run the real marker block and attestor; intercept only a fixture byte write.

    The write seam polls at the half-written point deterministically, instead
    of hoping to hit the tiny production timestamp race with a busy loop.
    """
    source = (REBOOT.parents[2] / '.agent-bridge/bin/Watch-Bridge.ps1').read_text(encoding='utf-8')
    start = source.index('if ($ReadyPath) {')
    block = source[start:source.index('$iteration = 0', start)]
    if non_atomic_mutant:
        block = "if ($ReadyPath) { Set-Content -LiteralPath $ReadyPath -Value ([datetime]::UtcNow.ToString('o')) -Encoding UTF8 -NoNewline -Force -ErrorAction Stop }"
    block = block.replace('$readyStream.Write($readyBytes, 0, $readyBytes.Length)',
                          'Write-FixtureBaselineBytes $readyStream $readyBytes')
    block = block.replace('[IO.File]::Move($readyTemp, $readyFile)',
                          'Publish-FixtureBaseline $readyTemp $readyFile')
    block = block.replace('[IO.File]::Replace($readyTemp, $readyFile, [NullString]::Value)',
                          'Publish-FixtureBaseline $readyTemp $readyFile -Replace')
    watcher = tmp_path / 'Watch-Bridge.ps1'
    watcher.write_text('# inert identity fixture', encoding='utf-8')
    script = load(REBOOT / 'start-wd-agent.ps1', 'Get-WdNativeLeadProducerAttestation')
    script += f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ReadyPath={q(tmp_path / 'ready')};$case='{case}';$script:observations=@();$script:tempPath=''
$old='2000-01-01T00:00:01Z'
if($case -cne 'new'){{[IO.File]::WriteAllText($ReadyPath,$old)}}
$ctx=@{{job_id=42;token='0123456789abcdef0123456789abcdef';session_id='current-session';
 generation='new-generation';runtime_root='C:\\fixture';watcher_script={q(watcher)};
 watcher_sha256=(Get-FileHash {q(watcher)}).Hash;baseline_path=$ReadyPath;
 launcher_pid=100;launcher_start_utc='1999-12-31T23:59:00Z'}}
$proof=[pscustomobject]@{{schema='wd.session-wake-producer.v1';token=$ctx.token;
 session_id=$ctx.session_id;generation=$ctx.generation;agent='codex-lead-1';
 runtime_root=$ctx.runtime_root;watcher_script=$ctx.watcher_script;watcher_sha256=$ctx.watcher_sha256;
 baseline_path=$ctx.baseline_path;process_id=200;parent_pid=100;
 creation_utc='2000-01-01T00:00:00Z';parent_start_utc=$ctx.launcher_start_utc;
 command_line='inert owned producer';executable_path='C:\\fixture\\powershell.exe'}}
function Get-Job {{param($Id)[pscustomobject]@{{Id=42;Name='agent-bridge-watcher-codex-lead-1';State='Running'}}}}
function Receive-Job {{param($Id,[switch]$Keep)if(-not $Keep){{throw 'proof consumed'}};$proof}}
function Get-CimInstance {{param($ClassName,$Filter)
 if($Filter -ceq 'ProcessId=200'){{[pscustomobject]@{{ProcessId=200;ParentProcessId=100;
 CreationDate=[datetime]$proof.creation_utc;CommandLine=$proof.command_line;ExecutablePath=$proof.executable_path}}}}
 elseif($Filter -ceq 'ProcessId=100'){{[pscustomobject]@{{ProcessId=100;CreationDate=[datetime]$ctx.launcher_start_utc}}}}
 else{{throw 'live process query forbidden'}}
}}
function Observe-FixtureTarget {{
 $exists=[IO.File]::Exists($ReadyPath)
 $text=if($exists){{[IO.File]::ReadAllText($ReadyPath)}}else{{$null}}
 $attestation=Get-WdNativeLeadProducerAttestation -Context $ctx
 $script:observations+=@(@{{exists=$exists;text=$text;attestation=$attestation}})
}}
function Write-FixtureBaselineBytes {{param($Stream,[byte[]]$Bytes)
 $script:tempPath=$Stream.Name
 $half=[int][Math]::Floor($Bytes.Length/2)
 $Stream.Write($Bytes,0,$half);$Stream.Flush()
 Observe-FixtureTarget
 if($case -ceq 'write_failure'){{throw 'fixture write refused'}}
 $Stream.Write($Bytes,$half,$Bytes.Length-$half)
}}
function Publish-FixtureBaseline {{param($Temp,$Target,[switch]$Replace)
 Observe-FixtureTarget
 if($case -ceq 'publish_failure'){{throw 'fixture publish refused'}}
 if($Replace){{[IO.File]::Replace($Temp,$Target,[NullString]::Value)}}else{{[IO.File]::Move($Temp,$Target)}}
}}
# Non-atomic mutant: emulate the real Set-Content truncation and split write,
# polling only after the exclusive writer handle is closed to expose parse failure.
function Set-Content {{param($LiteralPath,$Value,$Encoding,[switch]$NoNewline,[switch]$Force)
 $bytes=[Text.Encoding]::UTF8.GetBytes($Value);$half=[int][Math]::Floor($bytes.Length/2)
 [IO.File]::WriteAllBytes($LiteralPath,[byte[]]$bytes[0..($half-1)])
 Observe-FixtureTarget
 if($case -ceq 'write_failure'){{throw 'fixture write refused'}}
 [IO.File]::WriteAllBytes($LiteralPath,$bytes)
}}
$caught=$null
try{{
{block}
}}catch{{$caught=$_.Exception.Message}}
$final=if([IO.File]::Exists($ReadyPath)){{[IO.File]::ReadAllText($ReadyPath)}}else{{$null}}
@{{observations=$script:observations;final=$final;caught=$caught;
 final_attestation=(Get-WdNativeLeadProducerAttestation -Context $ctx);
 temp_exists=($script:tempPath -and [IO.File]::Exists($script:tempPath));
 leftovers=@(Get-ChildItem -LiteralPath {q(tmp_path)} -Filter '*.tmp').Count}}|ConvertTo-Json -Depth 8 -Compress
"""
    completed = _run_powershell(script, executable=ps)
    assert completed.returncode == 0
    return json.loads(completed.stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['new', 'replace', 'write_failure', 'publish_failure'])
def test_baseline_marker_publishes_only_complete_bytes_and_preserves_target_on_error(tmp_path, ps, case):
    """CONTROL_ATOMIC_BASELINE_NO_MIXED_BYTES / CONTROL_BASELINE_ERROR_PRESERVES_TARGET.

    MUTANT_NON_ATOMIC_BASELINE_STARTUP_RACE.
    """
    result = _baseline_publication_probe(tmp_path, ps, case)
    assert result['observations'], result
    for observation in result['observations']:
        if case == 'new':
            assert observation['exists'] is False and observation['text'] is None, result
            assert observation['attestation']['reason'] == 'producer_identity_not_ready', result
            assert observation['attestation']['retirement_allowed'] is False, result
        else:
            assert observation['text'] == '2000-01-01T00:00:01Z', result
            assert observation['attestation']['verified'] is True, result
    if case.endswith('_failure'):
        assert result['caught'] == f"fixture {case.removesuffix('_failure')} refused", result
        assert result['final'] == '2000-01-01T00:00:01Z', result
    else:
        assert result['caught'] is None and result['final_attestation']['verified'] is True, result
        assert result['final'] != '2000-01-01T00:00:01Z', result
    assert not result['temp_exists'] and result['leftovers'] == 0, result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_non_atomic_baseline_mutant_exposes_terminal_partial_marker_race(tmp_path, ps):
    """CONTROL_PARTIAL_MARKER_IS_NOT_PRODUCER_READY / MUTANT_NON_ATOMIC_BASELINE_STARTUP_RACE."""
    result = _baseline_publication_probe(tmp_path, ps, 'new', non_atomic_mutant=True)
    partial = result['observations'][0]
    assert partial['exists'] is True and partial['text'] != result['final'], result
    assert partial['attestation']['verified'] is False, result
    assert partial['attestation']['retirement_allowed'] is False, result
    assert partial['attestation']['reason'] != 'producer_identity_not_ready', result
    assert result['final_attestation']['verified'] is True, result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('existing', [False, True], ids=['new', 'replace'])
def test_baseline_marker_native_publication_has_no_staging_leftovers(tmp_path, ps, existing):
    """CONTROL_NATIVE_ATOMIC_BASELINE_PUBLICATION: unmodified real marker block."""
    source = (REBOOT.parents[2] / '.agent-bridge/bin/Watch-Bridge.ps1').read_text(encoding='utf-8')
    start = source.index('if ($ReadyPath) {')
    block = source[start:source.index('$iteration = 0', start)]
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ReadyPath={q(tmp_path / 'ready')}
if(${str(existing).lower()}){{[IO.File]::WriteAllText($ReadyPath,'old-marker')}}
$before=[DateTimeOffset]::UtcNow
{block}
$after=[DateTimeOffset]::UtcNow
$text=[IO.File]::ReadAllText($ReadyPath)
$parsed=[DateTimeOffset]::ParseExact($text,'o',[Globalization.CultureInfo]::InvariantCulture)
@{{in_range=($parsed -ge $before -and $parsed -le $after);
 leftovers=@(Get-ChildItem -LiteralPath {q(tmp_path)} -Filter '*.tmp').Count;
 byte_count=([IO.File]::ReadAllBytes($ReadyPath)).Length;text=$text}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['in_range'] is True and result['leftovers'] == 0, result
    assert result['byte_count'] == len(result['text'].encode('utf-8')), result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_baseline_cleanup_preserves_unowned_staging_on_create_collision(tmp_path, ps):
    """CONTROL_BASELINE_CLEANUP_REQUIRES_CREATED_FILE / MUTANT_DELETE_UNOWNED_BASELINE_STAGING."""
    source = (REBOOT.parents[2] / '.agent-bridge/bin/Watch-Bridge.ps1').read_text(encoding='utf-8')
    start = source.index('if ($ReadyPath) {')
    block = source[start:source.index('$iteration = 0', start)]
    assert "[guid]::NewGuid().ToString('N')" in block
    block = block.replace("[guid]::NewGuid().ToString('N')", "'fixturecollision'")
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ReadyPath={q(tmp_path / 'ready')};$preexisting={q(tmp_path / '.ready.fixturecollision.tmp')}
[IO.File]::WriteAllText($ReadyPath,'prior target bytes')
[IO.File]::WriteAllText($preexisting,'prior unrelated staging bytes')
$caught=$null
try{{
{block}
}}catch{{$caught=$_.Exception.Message}}
@{{caught=$caught;target=[IO.File]::ReadAllText($ReadyPath);staging_exists=[IO.File]::Exists($preexisting);
 staging=$(if([IO.File]::Exists($preexisting)){{[IO.File]::ReadAllText($preexisting)}}else{{$null}})}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['caught'] and 'Open' in result['caught'], result
    assert result['target'] == 'prior target bytes', result
    assert result['staging_exists'] is True, result
    assert result['staging'] == 'prior unrelated staging bytes', result


def test_only_win32_external_stop_fixture_has_platform_guard():
    """MUTANT_UNGUARDED_WIN32_RETIREMENT_FIXTURE / MUTANT_SKIP_ALL_LEAD_RETIREMENT_GUARDS."""
    native = test_exact_external_stop_is_after_final_producer_attestation
    guards = [mark for mark in getattr(native, 'pytestmark', ()) if mark.name == 'skipif']
    assert len(guards) == 1
    assert guards[0].args == (os.name != 'nt',)
    assert 'SystemRoot' in guards[0].kwargs['reason']
    assert 'CommandLineToArgvW' in guards[0].kwargs['reason']
    assert not globals().get('pytestmark'), 'Do not skip the entire portable consumer suite'
    for name, function in tuple(globals().items()):
        if name.startswith('test_') and function is not native:
            assert not any(mark.name in ('skip', 'skipif')
                           for mark in getattr(function, 'pytestmark', ())), name


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['valid', 'job_missing', 'job_failed', 'job_name',
    'token', 'generation', 'session', 'pid', 'creation', 'command', 'parent',
    'parent_creation', 'duplicate', 'cim_unavailable', 'proof_missing', 'baseline_missing',
    'parent_native_remainder_7', 'parent_native_remainder_9', 'parent_next_microsecond',
    'explicit_refusal', 'proof_and_refusal', 'baseline_empty', 'baseline_partial',
    'baseline_invalid', 'baseline_future', 'baseline_before_producer', 'baseline_locked'])
def test_retirement_requires_live_exact_session_producer(tmp_path, ps, case):
    """MUTANT_RETIRE_ON_RUNNING_FLAG / MUTANT_ACCEPT_RECYCLED_PRODUCER_PID.

    MUTANT_PARENT_EXACT_NATIVE_TICKS / MUTANT_PARENT_LIFETIME_TOLERANCE.
    """
    ready = tmp_path / 'ready'
    ready.write_text('2026-10-12T07:00:01Z', encoding='utf-8')
    watcher = tmp_path / 'Watch-Bridge.ps1'
    watcher.write_text('# verified fixture watcher', encoding='utf-8')
    script = load(REBOOT / 'start-wd-agent.ps1', 'Get-WdNativeLeadProducerAttestation')
    script += f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ctx=@{{job_id=42;token='0123456789abcdef0123456789abcdef';session_id='current-session';
 generation='new-generation';runtime_root='C:\\fixture';watcher_script={q(watcher)};
 watcher_sha256=(Get-FileHash {q(watcher)}).Hash;baseline_path={q(ready)};
 launcher_pid=100;launcher_start_utc='2026-10-12T06:59:00Z'}}
if('{case}' -ceq 'parent_native_remainder_7'){{$ctx.launcher_start_utc='2026-10-12T06:59:00.0000007Z'}}
if('{case}' -ceq 'parent_native_remainder_9'){{$ctx.launcher_start_utc='2026-10-12T06:59:00.0000009Z'}}
if('{case}' -ceq 'parent_next_microsecond'){{$ctx.launcher_start_utc='2026-10-12T06:59:00.0000017Z'}}
$script:proof=[pscustomobject]@{{schema='wd.session-wake-producer.v1';token=$ctx.token;
 session_id=$ctx.session_id;generation=$ctx.generation;agent='codex-lead-1';
 runtime_root=$ctx.runtime_root;watcher_script=$ctx.watcher_script;watcher_sha256=$ctx.watcher_sha256;
 baseline_path=$ctx.baseline_path;process_id=200;parent_pid=100;
 creation_utc='2026-10-12T07:00:00Z';parent_start_utc=$ctx.launcher_start_utc;
 command_line='powershell.exe -s -NoLogo -NoProfile';executable_path='C:\\Windows\\powershell.exe'}}
switch('{case}'){{
 'token'{{$script:proof.token='different'}} 'generation'{{$script:proof.generation='old-generation'}}
 'session'{{$script:proof.session_id='old-session'}} 'pid'{{$script:proof.process_id=201}}
 'creation'{{$script:proof.creation_utc='2026-10-12T06:00:00Z'}}
 'command'{{$script:proof.command_line='different command'}}
 'parent'{{$script:proof.parent_pid=101}} 'parent_creation'{{$script:proof.parent_start_utc='2026-10-12T06:58:00Z'}}
 'baseline_missing'{{Remove-Item -LiteralPath {q(ready)}}}
 'baseline_empty'{{[IO.File]::WriteAllText({q(ready)},'')}}
 'baseline_partial'{{[IO.File]::WriteAllText({q(ready)},'2026-10-12T07:')}}
 'baseline_invalid'{{[IO.File]::WriteAllText({q(ready)},'not-a-timestamp')}}
 'baseline_future'{{[IO.File]::WriteAllText({q(ready)},'9999-01-01T00:00:00Z')}}
 'baseline_before_producer'{{[IO.File]::WriteAllText({q(ready)},'2026-10-12T06:59:59Z')}}
}}
function Get-Job {{param($Id)
 if('{case}' -ceq 'job_missing'){{return $null}}
 [pscustomobject]@{{Id=42;Name=$(if('{case}' -ceq 'job_name'){{'foreign-job'}}else{{'agent-bridge-watcher-codex-lead-1'}});
 State=$(if('{case}' -ceq 'job_failed'){{'Failed'}}else{{'Running'}})}}
}}
function Receive-Job {{param($Id,[switch]$Keep)
 if(-not $Keep){{throw 'producer proof must not consume job output'}}
 if('{case}' -ceq 'proof_missing'){{return}}
 if('{case}' -cin @('explicit_refusal','proof_and_refusal')){{
  [pscustomobject]@{{schema='wd.session-wake-producer-refusal.v1';reason='private producer refusal'}}
  if('{case}' -ceq 'explicit_refusal'){{return}}
 }}
 $script:proof;if('{case}' -ceq 'duplicate'){{$script:proof}}
}}
function Get-CimInstance {{param($ClassName,$Filter)
 if('{case}' -ceq 'cim_unavailable'){{throw 'private CIM refusal'}}
 if($Filter -ceq 'ProcessId=200'){{
  [pscustomobject]@{{ProcessId=200;ParentProcessId=100;CreationDate=[datetime]'2026-10-12T07:00:00Z';
   CommandLine='powershell.exe -s -NoLogo -NoProfile';ExecutablePath='C:\\Windows\\powershell.exe'}}
 }}elseif($Filter -ceq 'ProcessId=100'){{
  [pscustomobject]@{{ProcessId=100;CreationDate=[datetime]'2026-10-12T06:59:00Z'}}
 }}
}}
$lock=$null
try{{
 if('{case}' -ceq 'baseline_locked'){{$lock=[IO.File]::Open({q(ready)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)}}
 $r=Get-WdNativeLeadProducerAttestation -Context $ctx
}}finally{{if($null -ne $lock){{$lock.Dispose()}}}}
$r|ConvertTo-Json -Depth 6 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    valid = case in ('valid', 'parent_native_remainder_7', 'parent_native_remainder_9')
    assert result['verified'] is valid, result
    assert result['reason']
    if valid:
        assert result['process_id'] == 200
        assert result['command_line'] == 'powershell.exe -s -NoLogo -NoProfile'
    else:
        assert result['retirement_allowed'] is False
        if case in ('explicit_refusal', 'proof_and_refusal'):
            # CONTROL_EXPLICIT_PRODUCER_REFUSAL_IS_TERMINAL, even beside proof.
            assert result['reason'] == 'producer_identity_refused: private producer refusal'
        if case in ('baseline_empty', 'baseline_partial', 'baseline_invalid',
                    'baseline_future', 'baseline_before_producer', 'baseline_locked'):
            # CONTROL_INVALID_OR_LOCKED_BASELINE_IS_TERMINAL: atomic writer
            # removes its own partial window, without expanding reader grace.
            assert result['reason'] != 'producer_identity_not_ready', result


def test_launcher_does_not_infer_retirement_from_an_old_running_record():
    """MUTANT_RETIRE_BEFORE_CURRENT_PRODUCER_ATTESTATION."""
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    terminal = source[source.index('function Invoke-WdNativeLeadTerminal {'):source.index('function Get-WdClaudeResumeState {')]
    assert 'Invoke-WdNativeLeadWatcherRetirement' in terminal
    assert 'RetirementContext=$RetirementContext' in terminal
    assert 'VerifiedSupervisorCode=$VerifiedSupervisorCode' in terminal
    session = (REBOOT.parents[2] / '.agent-bridge/bin/Start-AgentBridgeSession.ps1').read_text(encoding='utf-8')
    assert 'wd.session-wake-producer.v1' in session
    assert 'Get-CimInstance Win32_Process' in session
    assert '-ReadyPath $baselineArg' in session


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['busy_then_absent', 'busy_forever', 'ambiguous', 'uncertain'])
def test_relay_requires_resolved_retirement_before_ready(tmp_path, ps, case):
    """MUTANT_LATCH_FIRST_REFUSAL / MUTANT_READY_WITH_DUAL_PRODUCERS.

    Execute the actual launcher WaitForExit hook with inert jobs/processes.
    No real watcher is retired or stopped.
    """
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    start = source.index('    $relayNative | Add-Member ScriptMethod WaitForExit {')
    end = source.index('    Invoke-WdNativeToolsWakeRelay -Agent codex-lead-1', start)
    hook = source[start:end]
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$script:calls=0;$script:stops=0;$script:delays=@();$script:writes=@()
$fakeProcess=[pscustomobject]@{{}}
$fakeProcess|Add-Member ScriptMethod WaitForExit {{param($ms)return $false}}
$script:job=[pscustomobject]@{{Id=42;Name='agent-bridge-watcher-codex-lead-1';State='Running'}}
function Get-Job {{param($Id)return $script:job}}
function Stop-Job {{param($Job)if($Job -ne $script:job){{throw 'foreign job'}};$script:stops++;$Job.State='Stopped'}}
function Start-Sleep {{param($Milliseconds)$script:delays+=@($Milliseconds)}}
function Write-WdTurnJson {{param($Path,$Record)$script:writes+=@($Record.status)}}
function Invoke-WdNativeLeadWatcherRetirement {{param($ProducerContext,$RetirementContext,$VerifiedSupervisorCode,$Worktree)
 $script:calls++
 if('{case}' -ceq 'busy_then_absent' -and $script:calls -gt 1){{return @{{status='absent';reason='verified producer, no external watcher'}}}}
 if('{case}' -ceq 'ambiguous'){{return @{{status='kept';reason='ambiguous external Lead watchers; preserve all'}}}}
 if('{case}' -ceq 'uncertain'){{return @{{status='uncertain';reason='external stop uncertain; retained retirement intent'}}}}
 return @{{status='kept';reason='retirement mutex busy; preserve external watcher'}}
}}
$record=@{{status='native_process_started';bridge_wake_producer_status='unchecked';bridge_wake_transport='withheld'}}
$relayNative=[pscustomobject]@{{Process=$fakeProcess;ProducerEnabled=$true;WakeJobId='42';TerminalRecord=$record;
 ReadyPath={q(tmp_path / 'ready.json')};RetirementContext=@{{producer=@{{}};external=@{{}}}};
 VerifiedSupervisorCode='inert';Worktree={q(tmp_path)};RetirementAttempted=$false}}
{hook}
$caught=$null
try{{$null=$relayNative.WaitForExit(1)}}catch{{$caught=$_.Exception.Message}}
[ordered]@{{calls=$script:calls;stops=$script:stops;delays=$script:delays;record=$record;
 caught=$caught;retirement_attempted=$relayNative.RetirementAttempted}}|ConvertTo-Json -Depth 8 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    if case == 'busy_then_absent':
        assert result['calls'] == 2, result
        assert result['stops'] == 0 and result['caught'] is None, result
        assert result['record']['status'] == 'terminal_ready', result
        assert result['retirement_attempted'] is True, result
    else:
        assert result['calls'] == (5 if case == 'busy_forever' else 1), result
        assert result['stops'] == 1 and result['caught'], result
        assert result['record']['bridge_wake_transport'] == 'withheld', result
        assert result['record']['status'] != 'terminal_ready', result
        assert result['retirement_attempted'] is False, result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['not_ready_then_absent', 'not_ready_past_deadline',
    'not_ready_deadline_not_reset', 'not_ready_exact_deadline', 'not_ready_last_tick',
    'explicit_refusal', 'not_ready_uncertain', 'not_ready_suffix', 'invalid_baseline'])
def test_relay_waits_only_for_pending_producer_with_native_start_deadline(tmp_path, ps, case):
    """MUTANT_STOP_ON_FIRST_NOT_READY / MUTANT_RESET_DEADLINE_EACH_POLL.

    CONTROL_EXPLICIT_PRODUCER_REFUSAL_IS_TERMINAL. Execute the actual inert
    WaitForExit hook, substituting only a deterministic clock observation.
    """
    source = (REBOOT / 'start-wd-agent.ps1').read_text(encoding='utf-8')
    start = source.index('    $relayNative | Add-Member ScriptMethod WaitForExit {')
    end = source.index('    Invoke-WdNativeToolsWakeRelay -Agent codex-lead-1', start)
    hook = source[start:end].replace('[DateTimeOffset]::UtcNow', '(Get-FixtureUtcNow)')
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$script:calls=0;$script:stops=0;$script:writes=@()
$script:now=[DateTimeOffset]'2026-10-12T07:00:01Z'
if('{case}' -ceq 'not_ready_past_deadline'){{$script:now=$script:now.AddSeconds(60)}}
if('{case}' -ceq 'not_ready_deadline_not_reset'){{$script:now=$script:now.AddSeconds(58)}}
if('{case}' -ceq 'not_ready_exact_deadline'){{$script:now=[DateTimeOffset]'2026-10-12T07:01:00Z'}}
if('{case}' -ceq 'not_ready_last_tick'){{$script:now=([DateTimeOffset]'2026-10-12T07:01:00Z').AddTicks(-1)}}
function Get-FixtureUtcNow {{return $script:now}}
$fakeProcess=[pscustomobject]@{{StartTime=[datetime]'2026-10-12T07:00:00Z'}}
$fakeProcess|Add-Member ScriptMethod WaitForExit {{param($ms)return $false}}
$script:job=[pscustomobject]@{{Id=42;Name='agent-bridge-watcher-codex-lead-1';State='Running'}}
function Get-Job {{param($Id)if($Id -ne 42){{throw 'foreign job query'}};return $script:job}}
function Stop-Job {{param($Job)if($Job -ne $script:job){{throw 'foreign job'}};$script:stops++;$Job.State='Stopped'}}
function Stop-Process {{throw 'native or external process stop forbidden'}}
function Start-Sleep {{throw 'pending producer must wait for a later relay poll'}}
function Write-WdTurnJson {{param($Path,$Record)$script:writes+=@($Record.status)}}
function Invoke-WdNativeLeadWatcherRetirement {{param($ProducerContext,$RetirementContext,$VerifiedSupervisorCode,$Worktree)
 $script:calls++
 if('{case}' -ceq 'not_ready_then_absent' -and $script:calls -gt 1){{
  return @{{status='absent';reason='verified producer, no external watcher'}}
 }}
 if('{case}' -ceq 'explicit_refusal'){{return @{{status='kept';reason='producer_identity_refused: private producer refusal'}}}}
 if('{case}' -ceq 'not_ready_uncertain'){{return @{{status='uncertain';reason='producer_identity_not_ready'}}}}
 if('{case}' -ceq 'not_ready_suffix'){{return @{{status='kept';reason='producer_identity_not_ready_extra'}}}}
 if('{case}' -ceq 'invalid_baseline'){{return @{{status='kept';reason='producer baseline or script changed'}}}}
 return @{{status='kept';reason='producer_identity_not_ready'}}
}}
$record=@{{status='native_process_started';bridge_wake_producer_status='unchecked';bridge_wake_transport='withheld'}}
$relayNative=[pscustomobject]@{{Process=$fakeProcess;StartTime=$fakeProcess.StartTime;ProducerEnabled=$true;WakeJobId='42';TerminalRecord=$record;
 ReadyPath={q(tmp_path / 'ready.json')};RetirementContext=@{{producer=@{{}};external=@{{}}}};
 VerifiedSupervisorCode='inert';Worktree={q(tmp_path)};RetirementAttempted=$false}}
{hook}
$firstCaught=$null;$firstExited=$null
try{{$firstExited=$relayNative.WaitForExit(1)}}catch{{$firstCaught=$_.Exception.Message}}
$first=@{{calls=$script:calls;stops=$script:stops;caught=$firstCaught;exited=$firstExited;
 status=$record.status;transport=$record.bridge_wake_transport;producer_status=$record.bridge_wake_producer_status;
 retirement_attempted=$relayNative.RetirementAttempted;writes=@($script:writes)}}
$second=$null
if($null -eq $firstCaught -and '{case}' -cin @('not_ready_then_absent','not_ready_deadline_not_reset','not_ready_last_tick')){{
 if('{case}' -ceq 'not_ready_last_tick'){{$script:now=$script:now.AddTicks(1)}}else{{$script:now=$script:now.AddSeconds(2)}}
 $secondCaught=$null;$secondExited=$null
 try{{$secondExited=$relayNative.WaitForExit(1)}}catch{{$secondCaught=$_.Exception.Message}}
 $second=@{{calls=$script:calls;stops=$script:stops;caught=$secondCaught;exited=$secondExited;
  status=$record.status;transport=$record.bridge_wake_transport;retirement_attempted=$relayNative.RetirementAttempted}}
}}
@{{first=$first;second=$second}}|ConvertTo-Json -Depth 8 -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    first = result['first']
    assert first['calls'] == 1, result
    if case in ('not_ready_then_absent', 'not_ready_deadline_not_reset', 'not_ready_last_tick'):
        assert first['caught'] is None and first['exited'] is False, result
        assert first['stops'] == 0 and first['retirement_attempted'] is False, result
        assert first['status'] == 'native_process_started' and first['transport'] == 'withheld', result
        assert first['producer_status'] == 'unchecked' and 'terminal_ready' not in first['writes'], result
        second = result['second']
        assert second['calls'] == 2, result
        if case == 'not_ready_then_absent':
            assert second['caught'] is None and second['exited'] is False and second['stops'] == 0, result
            assert second['status'] == 'terminal_ready' and second['transport'] == 'codex_queue', result
            assert second['retirement_attempted'] is True, result
        else:
            assert second['caught'] and second['stops'] == 1, result
            assert second['status'] != 'terminal_ready' and second['transport'] == 'withheld', result
            assert second['retirement_attempted'] is False, result
    else:
        assert first['caught'] and first['stops'] == 1, result
        assert first['status'] != 'terminal_ready' and first['transport'] == 'withheld', result
        assert first['producer_status'] == 'unavailable' and first['retirement_attempted'] is False, result


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['valid', 'no_producer', 'producer_dies', 'no_external',
    'duplicates', 'wrong_runtime', 'wrong_script', 'command_payload', 'target_recycled',
    'target_command_changes', 'intent_exists', 'uncertain_stop',
    *[f'native_remainder_{i}' for i in range(1, 10)], 'native_next_microsecond'])
@pytest.mark.skipif(os.name != 'nt', reason='Win32 fixture requires SystemRoot and native CommandLineToArgvW')
def test_exact_external_stop_is_after_final_producer_attestation(tmp_path, ps, case):
    """MUTANT_STOP_BEFORE_FINAL_PROOF / MUTANT_KILL_BY_PID / MUTANT_STOP_UNKNOWN.

    MUTANT_EXTERNAL_EXACT_NATIVE_TICKS / MUTANT_EXTERNAL_LIFETIME_TOLERANCE.
    Native remainder 7 reproduces the hash-verified Lead private-process proof:
    639273977184155957 native ticks versus 639273977184155950 CIM ticks.
    The next-microsecond twin must preserve the backup and never write intent.
    """
    journal = tmp_path / '.codex-audit/wd-turn-loop'
    journal.mkdir(parents=True)
    script = load(REBOOT / 'start-wd-agent.ps1', 'Invoke-WdNativeLeadWatcherRetirement')
    script += f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$script:proofCalls=0;$script:kills=0;$script:writes=@();$script:disposed=$false
function Get-WdNativeLeadProducerAttestation {{param($Context)
 $script:proofCalls++
 return @{{verified=('{case}' -cne 'no_producer' -and -not ('{case}' -ceq 'producer_dies' -and $script:proofCalls -ge 2));
  process_id=200;creation_utc='2026-10-12T07:00:00Z';command_line='own producer';reason='private proof'}}
}}
function Write-WdTurnJson {{param($Path,$Value)
 $script:writes+=@($Value.status)
}}
$exe=Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$producer=@{{token='0123456789abcdef0123456789abcdef';launcher_pid=100;session_id='current';generation='new';
 runtime_root={q(tmp_path / 'runtime')};watcher_script={q(tmp_path / 'Watch-Bridge.ps1')}}}
$external=@{{executable=$exe;bundle_parent={q(tmp_path)};source_repo={q(tmp_path)};git_executable='git.exe'}}
$line='"'+$exe+'" -NoProfile -ExecutionPolicy Bypass -File "'+$producer.watcher_script+'" -Agent codex-lead-1 -RuntimeRoot "'+$producer.runtime_root+'"'
switch('{case}'){{
 'wrong_runtime'{{$line=$line.Replace($producer.runtime_root,'C:\\other-runtime')}}
 'wrong_script'{{$line=$line.Replace($producer.watcher_script,'C:\\untrusted\\Watch-Bridge.ps1')}}
 'command_payload'{{$line='powershell.exe -Command "Write-Output ''Watch-Bridge.ps1 -Agent codex-lead-1''"'}}
 'intent_exists'{{[IO.File]::WriteAllText({q(journal / 'native-lead-retirement-0123456789abcdef0123456789abcdef.json')},'retained old intent')}}
}}
$script:target=[pscustomobject]@{{ProcessId=300;ParentProcessId=1;Name='powershell.exe';ExecutablePath=$exe;
CreationDate=[datetime]'2026-10-12T06:00:00Z';CommandLine=$line}}
if('{case}'.StartsWith('native_')){{$script:target.CreationDate=[datetime]'2026-10-12T10:28:38.4155950Z'}}
function Get-CimInstance {{param($ClassName,$Filter)
 if(-not $Filter){{
  if('{case}' -ceq 'no_external'){{return [pscustomobject]@{{Name='powershell.exe';CommandLine='powershell.exe -s -NoProfile'}}}}
  $script:target;if('{case}' -ceq 'duplicates'){{$script:target}}
 }}else{{
  if($Filter -cne 'ProcessId=300'){{throw 'unexpected target query'}}
  $t=$script:target.PSObject.Copy()
  if('{case}' -ceq 'target_recycled'){{$t.CreationDate=[datetime]'2026-10-12T06:00:01Z'}}
  if('{case}' -ceq 'target_command_changes'){{$t.CommandLine+=' changed'}}
  $t
 }}
}}
$script:handle=[pscustomobject]@{{Handle=1;HasExited=$false;StartTime=[datetime]'2026-10-12T06:00:00Z'}}
if('{case}'.StartsWith('native_remainder_')){{
 $script:handle.StartTime=[datetime]'2026-10-12T10:28:38.4155950Z'
 $script:handle.StartTime=$script:handle.StartTime.AddTicks([int]('{case}'.Substring('native_remainder_'.Length)))
}}
if('{case}' -ceq 'native_next_microsecond'){{$script:handle.StartTime=[datetime]'2026-10-12T10:28:38.4155967Z'}}
$script:handle|Add-Member ScriptMethod Kill {{
 if($script:proofCalls -lt 2 -or $script:writes.Count -ne 1 -or $script:writes[0] -cne 'stopping'){{throw 'unsafe stop ordering'}}
 $script:kills++
}}
$script:handle|Add-Member ScriptMethod WaitForExit {{param($Timeout) return ('{case}' -cne 'uncertain_stop')}}
$script:handle|Add-Member ScriptMethod Dispose {{$script:disposed=$true}}
function Get-Process {{param($Id) if($Id -ne 300){{throw 'wrong stop target'}};return $script:handle}}
function Stop-Process {{throw 'bare PID kill forbidden'}}
$supervisor=[IO.File]::ReadAllText({q(REBOOT / 'wd_supervisor.ps1')})
$r=Invoke-WdNativeLeadWatcherRetirement -ProducerContext $producer -RetirementContext $external `
 -VerifiedSupervisorCode $supervisor -Worktree {q(tmp_path)}
@{{result=$r;kills=$script:kills;proof_calls=$script:proofCalls;writes=$script:writes;disposed=$script:disposed}}|ConvertTo-Json -Depth 6 -Compress
"""
    r = json.loads(_run_powershell(script, executable=ps).stdout)
    should_stop = case in ('valid', 'uncertain_stop') or case.startswith('native_remainder_')
    assert r['kills'] == int(should_stop), r
    if case == 'valid' or case.startswith('native_remainder_'):
        assert r['result']['status'] == 'retired' and r['result']['stopped_pid'] == 300, r
        assert r['proof_calls'] == 2 and r['writes'] == ['stopping', 'retired'], r
    elif case in ('no_external', 'command_payload'):
        assert r['result']['status'] == 'absent', r
    elif case == 'uncertain_stop':
        assert r['result']['status'] == 'uncertain', r
    else:
        assert r['result']['status'] == 'kept', r
    if case == 'uncertain_stop':
        assert r['writes'] == ['stopping'], r
    elif not should_stop:
        assert r['writes'] == [], r


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_new_supervisor_helpers_are_imported_from_verified_snapshot_not_executed(tmp_path, ps):
    """MUTANT_UNPINNED_SUPERVISOR_IMPORT / MUTANT_EXECUTE_SUPERVISOR_BOOTSTRAP."""
    data = b"throw 'supervisor entry point must never execute here'"
    (tmp_path / 'wd_supervisor.ps1').write_bytes(data)
    expected = hashlib.sha256(data).hexdigest().upper()
    source = REBOOT / 'start-wd-agent.ps1'
    script = ''.join(load(source, name) for name in [
        'Assert-LanePathWithoutReparse', 'Read-Utf8LaneSnapshot', 'Read-WdLaneTurnRunnerSnapshot'])
    script += f"""
$ErrorActionPreference='Stop'
$anchor=[pscustomobject]@{{files=[pscustomobject]@{{'wd_supervisor.ps1'='{expected}'}}}}
$s=Read-WdLaneTurnRunnerSnapshot -ScriptRoot {q(tmp_path)} -FileName wd_supervisor.ps1 -DeploymentAnchor $anchor
$blocked=0
$anchor.files.'wd_supervisor.ps1'='0'*64
try{{Read-WdLaneTurnRunnerSnapshot -ScriptRoot {q(tmp_path)} -FileName wd_supervisor.ps1 -DeploymentAnchor $anchor}}catch{{$blocked++}}
try{{Read-WdLaneTurnRunnerSnapshot -ScriptRoot {q(tmp_path)} -FileName ../outside.ps1 -SourceTreeMode}}catch{{$blocked++}}
try{{Read-WdLaneTurnRunnerSnapshot -ScriptRoot {q(tmp_path)} -FileName wd_supervisor.ps1}}catch{{$blocked++}}
@{{hash=$s.Hash;blocked=$blocked}}|ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {'hash': expected, 'blocked': 3}
