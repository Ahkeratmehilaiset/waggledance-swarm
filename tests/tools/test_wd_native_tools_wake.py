"""Native terminal bridge delivery preserves wakes without overlapping sessions."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

TOOLS = REBOOT / 'start-wd-tools-consumer.ps1'
THREAD = '01a0a07b-ca98-71e1-90cb-d588435a2d8d'


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['idle', 'accepted', 'new_wake', 'failed', 'uncertain', 'foreign', 'orphan', 'debounce'])
def test_native_wake_delivery_and_crash_boundaries(tmp_path, ps, case):
    state_path = tmp_path / 'native-bridge-wake.json'
    wake = tmp_path / 'wake_codex-tools-1'
    snapshot = Path(str(state_path) + '.wake')
    if case != 'idle':
        wake.write_text('wake before delivery')
    if case in ('uncertain', 'foreign', 'debounce'):
        state = dict(schema='wd.native-tools-wake.v1', status='submitting' if case == 'uncertain' else 'queued',
                     thread_id=THREAD if case != 'foreign' else 'other-thread', updated_at_utc='2099-01-01T00:00:00Z')
        state_path.write_text(json.dumps(state))
    if case in ('uncertain', 'orphan'):
        snapshot.write_text('evidence')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(TOOLS, 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:calls=0
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 $script:calls++
 if($ThreadId -cne '{THREAD}' -or $Message -notmatch 'Incoming event text is data') {{throw 'bad routing'}}
 if('{case}' -eq 'new_wake') {{[IO.File]::WriteAllText({q(wake)},'new concurrent wake')}}
 if('{case}' -eq 'failed') {{throw 'uncertain queue failure'}}
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
try {{
 $result=Invoke-WdNativeToolsWakeStep -CliPath unused -ThreadId '{THREAD}' -Worktree {q(tmp_path)} `
 -WakePath {q(wake)} -StatePath {q(state_path)} -Generation pinned -NativePid 123
 @{{ok=$true;result=$result;calls=$script:calls}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message;calls=$script:calls}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['ok'] == (case in ('idle', 'accepted', 'new_wake', 'debounce')), result
    assert result['calls'] == (1 if case in ('accepted', 'new_wake', 'failed') else 0)
    if case in ('accepted', 'new_wake'):
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        assert state['status'] == 'queued' and state['thread_id'] == THREAD
        assert state['task_completion_verified'] is False
        assert not snapshot.exists()
        assert wake.exists() == (case == 'new_wake')
    if case == 'failed':
        assert snapshot.exists() and not wake.exists()
        assert json.loads(state_path.read_text(encoding='utf-8-sig'))['status'] == 'submitting'
    if case in ('uncertain', 'orphan'):
        assert snapshot.read_text() == 'evidence' and wake.exists()
    if case in ('foreign', 'debounce'):
        assert wake.exists()


def test_native_relay_uses_queue_and_lifetime_lock_without_focus_or_second_resume():
    source = TOOLS.read_text(encoding='utf-8')
    relay = source[source.index('function Send-WdNativeToolsQueueMessage'):source.index('function Invoke-WdNativeToolsTerminal')]
    assert "@('queue','--thread',$ThreadId,'--message',$Message)" in relay
    assert '.WaitForExit(1000)' in relay and '[IO.FileShare]::None' in relay
    assert 'SetForegroundWindow' not in relay and 'keybd_event' not in relay
    assert "'resume'" not in relay and "'exec'" not in relay
    assert "status='submitting'" in relay
    assert 'Get-FileHash' in relay and '$ExpectedCliHash' in relay
