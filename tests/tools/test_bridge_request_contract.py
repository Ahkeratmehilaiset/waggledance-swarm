"""The audit's late-v1 reply must leave v2 pending in both routers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_next_action import recommend_next_action

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which('pwsh'), shutil.which('powershell.exe')])))


def events():
    request = dict(ts_utc='2026-09-18T07:30:00Z', agent='codex-lead-1',
                   agent_uuid='lead-uuid', session_id='lead-session', run_id='lead-run',
                   to='codex-tools-1', type='wake_request', status='request',
                   task_id='fixture/request', message='version 1',
                   payload={'nonce': 'v1', 'expected_responders': {'codex-tools-1': {
                       'agent_uuid': 'tools-uuid', 'session_id': 'tools-session', 'run_id': 'tools-run'}}})
    newer = deepcopy(request)
    newer.update(ts_utc='2026-09-18T07:30:01Z', message='version 2')
    newer['payload']['nonce'] = 'v2'
    reply = dict(ts_utc='2026-09-18T07:30:02Z', agent='codex-tools-1',
                 agent_uuid='tools-uuid', session_id='tools-session', run_id='tools-run',
                 to='codex-lead-1', type='message', status='answered', task_id='fixture/request',
                 payload={'nonce': 'v2', 'request_ts_utc': newer['ts_utc']})
    return request, newer, reply


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
@pytest.mark.parametrize('case', ['late_old', 'correct', 'ack', 'wrong_session', 'wrong_uuid', 'wrong_to', 'missing_binding', 'duplicate'])
def test_revised_legacy_request_remains_pending_until_matching_reply(tmp_path, engine, case):
    first, newer, reply = events()
    if case == 'late_old':
        reply['payload'] = {'nonce': 'v1', 'request_ts_utc': first['ts_utc']}
    elif case == 'ack':
        reply['status'] = 'received'
    elif case == 'wrong_session':
        reply['session_id'] = 'old-session'
    elif case == 'wrong_uuid':
        reply['agent_uuid'] = 'foreign-uuid'
    elif case == 'wrong_to':
        reply['to'] = 'someone-else'
    elif case == 'missing_binding':
        reply['payload'] = {}
    rows = [first, newer, reply] + ([reply] if case == 'duplicate' else [])
    if engine == 'python':
        result = recommend_next_action(agent='codex-tools-1', events=rows, claims=[])
    else:
        shared = tmp_path / 'shared'
        shared.mkdir()
        (shared / 'events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        env = dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path))
        proc = subprocess.run([engine, '-NoProfile', '-File', str(ROOT / '.agent-bridge/bin/Get-BridgeNextAction.ps1'),
                               '-Agent', 'codex-tools-1', '-Now', '2026-09-18T07:31:00Z', '-Json'],
                              env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
    assert result['open_incoming_count'] == (0 if case in ('correct', 'duplicate') else 1)
    assert result['action'] == ('claim_unblocked_work' if case in ('correct', 'duplicate') else 'answer_incoming')
