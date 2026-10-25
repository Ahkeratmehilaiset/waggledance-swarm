"""Concurrent wake-sentinel writers: a collision at the publication step is retried from a fresh read.

Earlier author and RCO reports (RCO2 repair-train preflight, Codex P1 on #1864) describe transient IOException
when two writers publish the same sentinel; that is not reproduced or measured here. The tests below inject the
other writer deterministically at the named publication boundary (Publish-BridgeWakeSnapshot) instead of relying
on timing, on every lane test shell, with inert temp files only. A non-colliding lost update stays a known limit
(the last test): nothing here claims complete correlation or any watcher outcome.
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
    # A non-colliding interleaving: another writer publishes after this writer's read and this publication
    # succeeds. Nothing fails, so nothing is retried, and the other writer's binding is lost (a known limit).
    'lost_update': """
  if($script:calls -eq 1){
    Write-Competitor -Path $Path -Text (@{schema='wd.bridge-wake-observation.v1';requests=@(@{request_id='other-1';agent='operator';session_id='race';reply_ts_utc=''});correlation_complete=$true;authority_effect='none'}|ConvertTo-Json -Depth 5 -Compress)
  }
  & $script:realPublish -Temp $Temp -Path $Path""",
    'none': """
  & $script:realPublish -Temp $Temp -Path $Path""",
}


def _write(tmp_path, ps, fault, previous=None):
    wake = tmp_path / 'wake_codex-lead-1'
    if previous is not None:
        wake.write_text(previous, encoding='utf-8')
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. {q(BIN / 'BridgeTelemetry.ps1')}
$script:calls=0
$script:realPublish=${{function:Publish-BridgeWakeSnapshot}}
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
$caught=$null
try{{Write-BridgeWakeObservation -Path {q(wake)} -Events @([pscustomobject]@{{request_id='mine-1';agent='operator';session_id='race'}})}}
catch{{$caught=$_.Exception.GetType().FullName}}
[ordered]@{{calls=$script:calls;caught=$caught;real=($null -ne $script:realPublish)}}|ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    text = wake.read_text(encoding='utf-8') if wake.exists() else None
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name.lower().endswith('.tmp'))
    return result, text, leftovers


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
def test_a_non_colliding_lost_update_remains_a_known_limit(tmp_path, ps):
    # LIMIT, not a guarantee: a successful publication over a snapshot this writer never read drops the other
    # writer's binding and correlation_complete stays true. The retry covers publication errors only.
    result, text, leftovers = _write(tmp_path, ps, 'lost_update')

    assert (result['caught'], result['calls']) == (None, 1), result
    assert _ids(text) == ['mine-1']
    assert json.loads(text)['correlation_complete'] is True
    assert leftovers == []
