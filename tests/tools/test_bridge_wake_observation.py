"""Wake correlation is observation only; legacy wakes and bursts still deliver."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

BIN = REBOOT.parents[2]/'.agent-bridge/bin'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['request', 'reply', 'legacy', 'overflow'])
def test_wake_correlation_survives_snapshot_without_authorizing_execution(tmp_path, ps, case):
    wake = tmp_path/'wake_codex-tools-1'
    state = tmp_path/'relay.json'
    if case=='legacy': wake.write_text('legacy timestamp')
    request = dict(request_id='request-1',agent='operator',session_id='operator-session')
    event = dict(in_reply_to_request_id='request-1',in_reply_to_requester=dict(agent='operator',session_id='operator-session'),
                 ts_utc='2026-09-19T21:00:00Z') if case=='reply' else request
    script = f"$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n. {q(BIN/'BridgeTelemetry.ps1')}\n"
    for name in ('Assert-WdTurnPath','Write-WdTurnJson','Move-WdWakeSnapshot'):
        script += load(REBOOT/'Invoke-WdLaneTurnLoop.ps1',name)
    script += load(REBOOT/'start-wd-tools-consumer.ps1','Invoke-WdNativeToolsWakeStep')
    script += f"""
$env:WD_BRIDGE_BIN={q(BIN)}
$event={q(json.dumps(event))}|ConvertFrom-Json
Write-BridgeStageObservation -BridgeRoot {q(tmp_path)} -Stage watcher_seen -Request $event -Target codex-tools-1
Write-BridgeWakeObservation -Path {q(wake)} -Events @($event)
if('{case}' -ceq 'overflow'){{
  $events=@(1..300|ForEach-Object {{[pscustomobject]@{{request_id=('extra-'+$_);agent='operator';session_id='operator-session'}}}})
  Write-BridgeWakeObservation -Path {q(wake)} -Events $events
}}
$hint=Get-Content -LiteralPath {q(wake)} -Raw|ConvertFrom-Json
function Send-WdNativeToolsQueueMessage {{param($CliPath,$ThreadId,$Message,$Worktree) return 'queue-1'}}
$result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId exact-existing-thread -Worktree {q(tmp_path)} `
  -WakePath {q(wake)} -StatePath {q(state)} -Generation pinned -NativePid 123
@{{result=$result;hint=$hint}}|ConvertTo-Json -Depth 12
"""
    value = json.loads(_run_powershell(script,executable=ps).stdout)
    assert value['result']=='queued'
    assert value['hint']['authority_effect']=='none'
    assert value['hint']['correlation_complete'] is (case not in ('legacy','overflow'))
    assert len(value['hint']['requests'])==(256 if case=='overflow' else 1)
    stages=[json.loads(p.read_text()) for p in (tmp_path/'shared/telemetry').glob('*.json')]
    bound=[s for s in stages if s['stage']=='relay_enqueued' and s['request_id']]
    assert len(bound)==(256 if case=='overflow' else 1)
    if case!='overflow':
        assert bound[0]['request_id']=='request-1'
        assert bound[0]['requester_session_id']=='operator-session'
        if case=='reply':
            assert datetime.fromisoformat(bound[0]['reply_ts_utc'].replace('Z','+00:00'))==datetime(2026,9,19,21,tzinfo=timezone.utc)
        else:
            assert bound[0]['reply_ts_utc']==''
    assert all(s['authority_effect']=='none' for s in stages)
    assert json.loads(state.read_text(encoding='utf-8-sig'))['task_completion_verified'] is False


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_pending_wake_merge_keeps_both_revisions(tmp_path, ps):
    path=tmp_path/'wake'
    script=f"""
$ErrorActionPreference='Stop'
. {q(BIN/'BridgeTelemetry.ps1')}
foreach($id in @('v1','v2')){{
  Write-BridgeWakeObservation -Path {q(path)} -Events @([pscustomobject]@{{request_id=$id;agent='operator';session_id='session'}})
}}
Get-Content -LiteralPath {q(path)} -Raw
"""
    value=json.loads(_run_powershell(script,executable=ps).stdout)
    assert [r['request_id'] for r in value['requests']]==['v1','v2']
    assert not list(tmp_path.glob('*.tmp'))
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('legacy', ['ordinary wake text','2026-09-19T21:00:00Z','{broken-json'])
def test_bare_legacy_wake_with_telemetry_enabled_does_not_warn(tmp_path, ps, legacy):
    wake=tmp_path/'wake_codex-tools-1'
    wake.write_text(legacy)
    state=tmp_path/'relay.json'
    script=f"$ErrorActionPreference='Stop'\n$env:WD_BRIDGE_BIN={q(BIN)}\n"
    for name in ('Assert-WdTurnPath','Write-WdTurnJson','Move-WdWakeSnapshot'):
        script+=load(REBOOT/'Invoke-WdLaneTurnLoop.ps1',name)
    script+=load(REBOOT/'start-wd-tools-consumer.ps1','Invoke-WdNativeToolsWakeStep')
    script+=f"""
function Send-WdNativeToolsQueueMessage {{param($CliPath,$ThreadId,$Message,$Worktree) return 'queue-legacy'}}
$result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId existing-thread -Worktree {q(tmp_path)} `
    -WakePath {q(wake)} -StatePath {q(state)} -Generation pinned -NativePid 123
@{{result=$result}}|ConvertTo-Json
"""
    output=_run_powershell(script,executable=ps)
    assert json.loads(output.stdout)=={'result':'queued'}
    assert 'WARNING' not in output.stdout and 'latency observation unavailable' not in output.stderr
    stages=[json.loads(p.read_text()) for p in (tmp_path/'shared/telemetry').glob('*.json')]
    assert len(stages)==1 and stages[0]['request_id'] is None
