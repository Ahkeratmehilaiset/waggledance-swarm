"""Late peer replies must wake the same native Lead conversation."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_lead_reply_wakes_existing_thread_and_preserves_concurrent_reply(tmp_path, ps):
    wake = tmp_path / 'wake_codex-lead-1'
    state = tmp_path / 'native-bridge-wake.json'
    wake.write_text('Fable answer arrived after final snapshot')
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ['Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot']:
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', name)
    script += load(REBOOT / 'start-wd-tools-consumer.ps1', 'Invoke-WdNativeToolsWakeStep')
    script += f"""
$script:messages=@()
function Send-WdNativeToolsQueueMessage {{
 param($CliPath,$ThreadId,$Message,$Worktree)
 if($ThreadId -cne 'exact-lead-thread'){{throw 'wrong conversation'}}
 $script:messages+=,$Message
 [IO.File]::WriteAllText({q(wake)},'second reply during queue submission')
 return '01a0adff-4558-7e80-8936-6aad0d6df821'
}}
$result=Invoke-WdNativeToolsWakeStep -Agent codex-lead-1 -CliPath unused -ThreadId exact-lead-thread `
 -Worktree {q(tmp_path)} -WakePath {q(wake)} -StatePath {q(state)} -Generation pinned -NativePid 123
@{{result=$result;messages=$script:messages}}|ConvertTo-Json -Depth 8
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    message = result['messages'][0]
    assert 'Automatic bridge wake for codex-lead-1' in message
    assert 'Get-BridgeReplySnapshot.ps1' in message
    assert 'late' in message.lower() and 'pending' in message.lower()
    assert 'Automatic bridge wake for codex-tools-1' not in message
    assert wake.read_text() == 'second reply during queue submission'
    saved = json.loads(state.read_text(encoding='utf-8-sig'))
    assert saved['status'] == 'queued' and saved['agent'] == 'codex-lead-1'
    assert not saved['task_completion_verified']
