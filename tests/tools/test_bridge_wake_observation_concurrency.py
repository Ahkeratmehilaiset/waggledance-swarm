"""Concurrent wake-sentinel writers: cooperating writers serialize on the sentinel lock, and a collision with a
writer outside the lock is retried from a fresh read.

Every Write-BridgeWakeObservation call holds <sentinel>.lock (FileShare.None) for its whole read, merge and
publication. The cross-process test runs a second real writer in a child process of the same lane shell, started
while the first writer's transaction is open; the nested tests run a second real writer in the same process. The
collision tests inject a writer that does NOT take the lock (for example an older writer during mixed-version
operation) at the named publication boundary (Publish-BridgeWakeSnapshot). Earlier author and RCO reports (RCO2
repair-train preflight, Codex P1 on #1864) describe transient IOException when two writers publish the same
sentinel; that is not reproduced or measured here. Inert temp files only, on every lane test shell: no live
watcher, and no timing or stress result is used as proof. A non-colliding lost update by a writer outside the lock
stays a known limit (the last test): nothing here claims complete correlation or any watcher outcome. The
cross-process test cleans up the one child it started (that Process object only) on every path; that cleanup is
tested against a strict fake, so no real child is left running or killed to prove it.
"""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import q

BIN = REBOOT.parents[2] / '.agent-bridge/bin'
SCHEMA = 'wd.bridge-wake-observation.v1'

# What the injected publication step does on each call, before (or instead of) the real publication.
FAULTS = {
    # Another writer replaces the sentinel, then this writer's Replace fails (Win32 1175).
    'competing_replace': """
  if($script:calls -eq 1){
    Write-Competitor -Path $Path -Text (@{schema='wd.bridge-wake-observation.v1';requests=@(@{request_id='other-1';agent='operator';session_id='race';reply_ts_utc=''});correlation_complete=$true;authority_effect='none'}|ConvertTo-Json -Depth 5 -Compress)
    throw [IO.IOException]::new('injected: unable to remove the file to be replaced', -2147023721)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    # The sentinel did not exist, another writer creates it, and this writer's Move fails for real.
    'competing_create_move': """
  if($script:calls -eq 1){
    Write-Competitor -Path $Path -Text (@{schema='wd.bridge-wake-observation.v1';requests=@(@{request_id='other-1';agent='operator';session_id='race';reply_ts_utc=''});correlation_complete=$true;authority_effect='none'}|ConvertTo-Json -Depth 5 -Compress)
    [IO.File]::Move($Temp, $Path)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    # Another writer publishes something unreadable: the retry must record the loss, not hide it.
    'corrupt_competitor': """
  if($script:calls -eq 1){
    Write-Competitor -Path $Path -Text '{broken'
    throw [IO.IOException]::new('injected: sharing violation', -2147024864)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    # The publication happened, then an error was reported: this writer's own bytes are at the path.
    'published_then_error': """
  & $script:realPublish -Temp $Temp -Path $Path
  if($script:calls -eq 1){throw [IO.IOException]::new('injected: late error after publication', -2147023719)}""",
    # A lasting IOException (a held file, a full disk) must stay visible after the bounded retries.
    'persistent_ioexception': """
  throw [IO.IOException]::new('injected: lasting sharing violation', -2147024864)""",
    # Access denied is not a race: it is thrown at once, without a retry.
    'access_denied': """
  throw [UnauthorizedAccessException]::new('injected: access denied')""",
    # A non-colliding interleaving with a writer that does not take the sentinel lock: it publishes after this
    # writer's read and this publication succeeds. Nothing fails, so nothing is retried, and the other writer's
    # binding is lost (a known limit for writers outside the lock).
    'lost_update': """
  if($script:calls -eq 1){
    Write-Competitor -Path $Path -Text (@{schema='wd.bridge-wake-observation.v1';requests=@(@{request_id='other-1';agent='operator';session_id='race';reply_ts_utc=''});correlation_complete=$true;authority_effect='none'}|ConvertTo-Json -Depth 5 -Compress)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    'none': """
  & $script:realPublish -Temp $Temp -Path $Path""",
    # A second real writer of the SAME sentinel, in this process, tries to start its own transaction while this
    # one is open: it must fail visibly after the bounded lock wait, never succeed and then be overwritten.
    'nested_same_sentinel': """
  if($script:calls -eq 1){
    try{Write-BridgeWakeObservation -Path $Path -Events @([pscustomobject]@{request_id='other-1';agent='operator';session_id='race'})}
    catch{$script:extra['nested']=Get-InnerType $_.Exception}
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    # A real writer of a DIFFERENT sentinel is not serialized behind this one.
    'other_sentinel': """
  if($script:calls -eq 1){
    Write-BridgeWakeObservation -Path (Join-Path (Split-Path -Parent $Path) 'wake_codex-tools-1') -Events @([pscustomobject]@{request_id='peer-1';agent='operator';session_id='race'})
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    # A second real writer in a child process of this shell starts while this transaction is open; this writer
    # publishes once the child has reached the sentinel lock (or, if there were no lock, has ended). The loop
    # bound is a hang guard only; CHILD_FINISH cleans up the child on every path.
    'child_writer': """
  if($script:calls -eq 1){
    $script:child=[Diagnostics.Process]::Start($script:childStart)
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::Exists($script:childMarker) -and -not $script:child.HasExited){
      if($clock.ElapsedMilliseconds -gt 20000){throw 'hang guard: the child writer neither reached the lock nor ended'}
      Start-Sleep -Milliseconds 10
    }
    $script:extra['child_waiting_at_publish']=(-not $script:child.HasExited)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
}

# The child writer of the cross-process test: the real writer, with its sentinel-lock step wrapped so the parent
# knows when the child has reached it. Without such a step (the old writer) the wrapper is never called.
CHILD = """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. {telemetry}
$lockCommand=Get-Command Open-BridgeWakeLock -CommandType Function -ErrorAction SilentlyContinue
$script:realLock=$(if($null -ne $lockCommand){{$lockCommand.ScriptBlock}}else{{$null}})
function Open-BridgeWakeLock {{
  param([string]$Path)
  [IO.File]::WriteAllText({marker},'at-lock')
  & $script:realLock -Path $Path
}}
$out=[ordered]@{{ok=$false;caught=$null}}
try{{Write-BridgeWakeObservation -Path {wake} -Events @([pscustomobject]@{{request_id='other-1';agent='operator';session_id='race'}});$out.ok=$true}}
catch{{$out.caught=$_.Exception.GetType().FullName}}
[IO.File]::WriteAllText({result},($out|ConvertTo-Json -Compress))
"""

# The finish step of the cross-process test, run in a finally after the writer whether it succeeded or failed. It
# records how the child ended, then cleans up that exact Process object (never by name or process id): killed only
# while it still runs after the bound, waited for boundedly, disposed on every path. A failed wait or cleanup is
# recorded next to the original result instead of replacing it.
CHILD_FINISH = """
if($null -ne $script:child){
  try{
    $script:extra['child_exited']=$script:child.WaitForExit(20000)
    $script:extra['child_exit_code']=$(if($script:child.HasExited){$script:child.ExitCode}else{$null})
  }catch{$script:extra['child_wait_error']=Get-InnerType $_.Exception}
  finally{
    try{
      if(-not $script:child.HasExited){
        $script:child.Kill()
        $script:extra['child_killed']=$script:child.WaitForExit(5000)
      }
    }catch{$script:extra['child_cleanup_error']=Get-InnerType $_.Exception}
    finally{$script:child.Dispose()}
  }
}"""

# Unwraps the MethodInvocationException PowerShell puts around a .NET method's exception.
GET_INNER_TYPE = """function Get-InnerType {
  param($Exception)
  while($Exception -is [Management.Automation.MethodInvocationException] -and $null -ne $Exception.InnerException){$Exception=$Exception.InnerException}
  $Exception.GetType().FullName
}"""

# A strict fake of the child Process object for the cleanup tests: only the members the finish step may use exist
# (under StrictMode any other member is an error), every use is logged, and each case scripts the answers. A failing
# answer is a failing .NET call, so its error reaches the finish step the way a real Process method error does.
FAKE_CHILD = """
$script:log=[Collections.Generic.List[string]]::new()
$script:answers=[Collections.Queue]::new()
foreach($answer in @({answers})){{$script:answers.Enqueue($answer)}}
$script:exited=$false
$script:killFails={kill_fails}
$fake=[pscustomobject]@{{}}
$fake|Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {{
  param([int]$Milliseconds)
  $script:log.Add("WaitForExit:$Milliseconds")
  $answer=$script:answers.Dequeue()
  if($answer -is [string]){{[void][Collections.Queue]::new().Dequeue()}}
  if($answer){{$script:exited=$true}}
  $answer
}}
$fake|Add-Member -MemberType ScriptProperty -Name HasExited -Value {{$script:log.Add('HasExited');$script:exited}}
$fake|Add-Member -MemberType ScriptProperty -Name ExitCode -Value {{$script:log.Add('ExitCode');0}}
$fake|Add-Member -MemberType ScriptMethod -Name Kill -Value {{
  $script:log.Add('Kill')
  if($script:killFails){{[void][Collections.Queue]::new().Dequeue()}}
}}
$fake|Add-Member -MemberType ScriptMethod -Name Dispose -Value {{$script:log.Add('Dispose')}}
$script:child=$fake"""


def _write(tmp_path, ps, fault, previous=None, setup='', finish=''):
    wake = tmp_path / 'wake_codex-lead-1'
    if previous is not None:
        wake.write_text(previous, encoding='utf-8')
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. {q(BIN / 'BridgeTelemetry.ps1')}
$script:calls=0
$script:extra=[ordered]@{{}}
$script:realPublish=${{function:Publish-BridgeWakeSnapshot}}
{GET_INNER_TYPE}
function Write-Competitor {{
  param([string]$Path,[string]$Text)
  $other=$Path+'.competitor.tmp'
  [IO.File]::WriteAllText($other,$Text,(New-Object Text.UTF8Encoding($false)))
  if([IO.File]::Exists($Path)){{[IO.File]::Replace($other,$Path,[NullString]::Value)}}else{{[IO.File]::Move($other,$Path)}}
}}
function Publish-BridgeWakeSnapshot {{
  param([string]$Temp,[string]$Path)
  $script:calls++
{FAULTS[fault]}
}}
{setup}
$caught=$null
try{{Write-BridgeWakeObservation -Path {q(wake)} -Events @([pscustomobject]@{{request_id='mine-1';agent='operator';session_id='race'}})}}
catch{{$caught=Get-InnerType $_.Exception}}
finally{{
{finish}
}}
$out=[ordered]@{{calls=$script:calls;caught=$caught;real=($null -ne $script:realPublish)}}
if($script:extra.Count){{$out['extra']=$script:extra}}
$out|ConvertTo-Json -Compress -Depth 4
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    text = wake.read_text(encoding='utf-8') if wake.exists() else None
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name.lower().endswith('.tmp'))
    return result, text, leftovers


def _finish(ps, child):
    # Only the finish step, against the given child setup: no writer and no real child process.
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
{GET_INNER_TYPE}
$script:extra=[ordered]@{{}}
{child}
$escaped=$null
try{{
{CHILD_FINISH}
}}catch{{$escaped=Get-InnerType $_.Exception}}
[ordered]@{{log=@($script:log);extra=$script:extra;escaped=$escaped}}|ConvertTo-Json -Compress -Depth 4
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)


def _ids(text):
    return [r['request_id'] for r in json.loads(text)['requests']]


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_publication_step_is_a_named_boundary(tmp_path, ps):
    result, text, leftovers = _write(tmp_path, ps, 'none')

    assert result == {'calls': 1, 'caught': None, 'real': True}
    assert _ids(text) == ['mine-1'] and json.loads(text)['correlation_complete'] is True
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('fault', ['competing_replace', 'competing_create_move'])
def test_a_concurrent_writer_is_merged_on_retry_instead_of_thrown(tmp_path, ps, fault):
    result, text, leftovers = _write(tmp_path, ps, fault)

    assert (result['caught'], result['calls']) == (None, 2), result
    value = json.loads(text)
    assert value['schema'] == SCHEMA and value['authority_effect'] == 'none'
    assert _ids(text) == ['other-1', 'mine-1']
    assert value['correlation_complete'] is True
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_unreadable_competitor_is_recorded_as_incomplete(tmp_path, ps):
    result, text, leftovers = _write(tmp_path, ps, 'corrupt_competitor')

    assert (result['caught'], result['calls']) == (None, 2), result
    assert _ids(text) == ['mine-1']
    assert json.loads(text)['correlation_complete'] is False
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_error_after_this_writers_bytes_were_published_is_not_retried(tmp_path, ps):
    result, text, leftovers = _write(tmp_path, ps, 'published_then_error')

    assert (result['caught'], result['calls']) == (None, 1), result
    assert _ids(text) == ['mine-1']
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_lasting_ioexception_is_thrown_after_bounded_retries_and_changes_nothing(tmp_path, ps):
    previous = json.dumps({'schema': SCHEMA, 'requests': [{'request_id': 'old-1', 'agent': 'operator',
                           'session_id': 'race', 'reply_ts_utc': ''}], 'correlation_complete': True,
                           'authority_effect': 'none'})
    result, text, leftovers = _write(tmp_path, ps, 'persistent_ioexception', previous=previous)

    assert result['caught'] == 'System.IO.IOException', result
    assert result['calls'] == 5
    assert text == previous
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_access_denied_is_thrown_at_once(tmp_path, ps):
    previous = json.dumps({'schema': SCHEMA, 'requests': [], 'correlation_complete': True,
                           'authority_effect': 'none'})
    result, text, leftovers = _write(tmp_path, ps, 'access_denied', previous=previous)

    assert (result['caught'], result['calls']) == ('System.UnauthorizedAccessException', 1), result
    assert text == previous
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_cooperating_writer_in_another_process_keeps_both_bindings(tmp_path, ps):
    # Regression for the former lost-update LIMIT, for writers that both go through Write-BridgeWakeObservation:
    # the child starts while this writer's transaction is open and has to wait for the sentinel lock, so it reads
    # this writer's publication and both bindings survive.
    wake, marker, child_result = tmp_path / 'wake_codex-lead-1', tmp_path / 'child-at-lock', tmp_path / 'child.json'
    child = tmp_path / 'child.ps1'
    child.write_text(CHILD.format(telemetry=q(BIN / 'BridgeTelemetry.ps1'), marker=q(marker), wake=q(wake),
                                  result=q(child_result)), encoding='utf-8')
    arguments = f'-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{child}"'
    setup = f"""
$script:childMarker={q(marker)}
$script:childStart=[Diagnostics.ProcessStartInfo]::new([Diagnostics.Process]::GetCurrentProcess().MainModule.FileName)
$script:childStart.UseShellExecute=$false
$script:childStart.Arguments={q(arguments)}
$script:child=$null"""
    result, text, leftovers = _write(tmp_path, ps, 'child_writer', setup=setup, finish=CHILD_FINISH)

    assert (_ids(text), result.get('extra')) == (
        ['mine-1', 'other-1'], {'child_waiting_at_publish': True, 'child_exited': True, 'child_exit_code': 0}), result
    assert (result['caught'], result['calls']) == (None, 1), result
    assert json.loads(child_result.read_text(encoding='utf-8')) == {'ok': True, 'caught': None}
    assert json.loads(text)['correlation_complete'] is True
    assert leftovers == []


# Cleanup of the child the cross-process test starts, for that exact Process object only (never by name or process
# id): scripted answers to WaitForExit, whether Kill fails, the expected member uses, and the record.
CLEANUP_CASES = {
    # The child ended within the bound (the success path): no Kill, and the object is disposed.
    'exited': ('$true', '$false',
               ['WaitForExit:20000', 'HasExited', 'ExitCode', 'HasExited', 'Dispose'],
               {'child_exited': True, 'child_exit_code': 0}),
    # Still running after the bound (an earlier failure or a hang): killed, waited for boundedly, disposed.
    'still_running': ('$false,$true', '$false',
                      ['WaitForExit:20000', 'HasExited', 'HasExited', 'Kill', 'WaitForExit:5000', 'Dispose'],
                      {'child_exited': False, 'child_exit_code': None, 'child_killed': True}),
    # The wait itself fails: that failure is recorded, then the same cleanup runs.
    'wait_failed': ("'fail',$true", '$false',
                    ['WaitForExit:20000', 'HasExited', 'Kill', 'WaitForExit:5000', 'Dispose'],
                    {'child_wait_error': 'System.InvalidOperationException', 'child_killed': True}),
    # Kill fails: the cleanup failure is recorded next to the original one, and the object is still disposed.
    'kill_failed': ('$false', '$true',
                    ['WaitForExit:20000', 'HasExited', 'HasExited', 'Kill', 'Dispose'],
                    {'child_exited': False, 'child_exit_code': None,
                     'child_cleanup_error': 'System.InvalidOperationException'}),
}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', sorted(CLEANUP_CASES))
def test_the_owned_child_is_disposed_on_every_path_and_killed_only_while_running(ps, case):
    answers, kill_fails, log, extra = CLEANUP_CASES[case]
    result = _finish(ps, FAKE_CHILD.format(answers=answers, kill_fails=kill_fails))

    assert result == {'log': log, 'extra': extra, 'escaped': None}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_without_a_started_child_the_finish_step_does_nothing(ps):
    # The early failure: the child was never started (Start threw), so there is no object to clean up.
    result = _finish(ps, '$script:log=[Collections.Generic.List[string]]::new()\n$script:child=$null')

    assert result == {'log': [], 'extra': {}, 'escaped': None}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_second_writer_cannot_enter_an_open_transaction_and_fails_visibly(tmp_path, ps):
    # The same-process variant: a dropped binding is never a false success. The second writer throws after its
    # bounded lock wait and publishes nothing; this writer's snapshot stays complete for what it did merge.
    result, text, leftovers = _write(tmp_path, ps, 'nested_same_sentinel')

    assert (_ids(text), result.get('extra')) == (['mine-1'], {'nested': 'System.IO.IOException'}), result
    assert (result['caught'], result['calls']) == (None, 1), result
    assert json.loads(text)['correlation_complete'] is True
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_writers_of_different_sentinels_are_not_serialized(tmp_path, ps):
    result, text, leftovers = _write(tmp_path, ps, 'other_sentinel')

    assert (result['caught'], result['calls']) == (None, 2), result
    assert _ids(text) == ['mine-1']
    assert _ids((tmp_path / 'wake_codex-tools-1').read_text(encoding='utf-8')) == ['peer-1']
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_held_sentinel_lock_is_waited_for_boundedly_and_changes_nothing(tmp_path, ps):
    previous = json.dumps({'schema': SCHEMA, 'requests': [{'request_id': 'old-1', 'agent': 'operator',
                           'session_id': 'race', 'reply_ts_utc': ''}], 'correlation_complete': True,
                           'authority_effect': 'none'})
    lock = q(tmp_path / 'wake_codex-lead-1.lock')
    setup = f"$held=[IO.File]::Open({lock},[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)"
    result, text, leftovers = _write(tmp_path, ps, 'none', previous=previous, setup=setup, finish='$held.Dispose()')

    assert (result['caught'], result['calls']) == ('System.IO.IOException', 0), result
    assert text == previous
    assert leftovers == []


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_non_colliding_lost_update_remains_a_known_limit(tmp_path, ps):
    # LIMIT, not a guarantee, for a writer that does not take the sentinel lock (Write-Competitor here): its
    # successful publication over a snapshot this writer never read is dropped and correlation_complete stays
    # true. The lock serializes cooperating writers only; the retry covers publication errors only.
    result, text, leftovers = _write(tmp_path, ps, 'lost_update')

    assert (result['caught'], result['calls']) == (None, 1), result
    assert _ids(text) == ['mine-1']
    assert json.loads(text)['correlation_complete'] is True
    assert leftovers == []
