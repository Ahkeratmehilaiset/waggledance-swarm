"""The audit's late-v1 reply must leave v2 pending in both routers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_next_action import recommend_next_action
from tools.report_unanswered_bridge_requests import report_unanswered_requests
from waggledance.core.bridge_request_contract import reply_matches_request

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


@pytest.mark.parametrize('case', ['correct', 'old_id', 'missing_id', 'wrong_requester_session', 'wrong_responder_session', 'wrong_digest', 'conflicting_envelope'])
def test_explicit_id_also_binds_requester_and_responder_identity(case):
    _, request, reply = events()
    request.update(request_id='request-v2', request_digest='digest-v2')
    reply.update(in_reply_to_request_id='request-v2', in_reply_to_request_digest='digest-v2',
                 in_reply_to_requester={k: request[k] for k in ('agent','agent_uuid','session_id','run_id')})
    if case == 'old_id': reply['in_reply_to_request_id'] = 'request-v1'
    if case == 'missing_id': del reply['in_reply_to_request_id']
    if case == 'wrong_requester_session': reply['in_reply_to_requester']['session_id'] = 'wrong'
    if case == 'wrong_responder_session': reply['session_id'] = 'wrong'
    if case == 'wrong_digest': reply['in_reply_to_request_digest'] = 'old'
    if case == 'conflicting_envelope': reply['payload']['in_reply_to_request_id'] = 'different'
    assert reply_matches_request(request, reply, 'codex-tools-1') is (case == 'correct')


@pytest.mark.skipif(os.name != 'nt', reason='canonical append is Windows only')
@pytest.mark.parametrize('engine', SHELLS)
def test_real_writer_persists_id_and_full_request_reply_binding(tmp_path, engine):
    CODEX_TOOLS_UUID = json.loads((ROOT / 'configs/bridge_identity_registry.json').read_text())['identities']['codex-tools-1']
    env = {k:v for k,v in os.environ.items() if not k.startswith('AGENT_BRIDGE_')}
    env['AGENT_BRIDGE_RUNTIME_ROOT'] = str(tmp_path)
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'last_codex-tools-1.json').write_text(json.dumps(dict(
        agent='codex-tools-1', agent_uuid=CODEX_TOOLS_UUID, session_id='tools-session', run_id='tools-run')))
    writer = ROOT / '.agent-bridge/bin/Write-AgentEvent.ps1'
    def write(*args):
        proc = subprocess.run([engine, '-NoProfile', '-File', str(writer), '-TaskId', 'fixture/new-id', *args],
                              env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads((shared / 'events.jsonl').read_text().splitlines()[-1])
    request = write('-Agent','operator','-Type','wake_request','-Status','request','-To','codex-tools-1',
                    '-SessionId','operator-session','-RunId','operator-run')
    assert request['request_id'] and request['request_digest']
    reply = write('-Agent','codex-tools-1','-AgentUuid',CODEX_TOOLS_UUID,'-SessionId','tools-session','-RunId','tools-run',
                  '-Type','message','-Status','answered','-To','operator','-ReplyToEventJson',json.dumps(request))
    assert reply_matches_request(request, reply, 'codex-tools-1')
    assert reply['in_reply_to_request_id'] == request['request_id']
    observations = [json.loads(p.read_text()) for p in (shared / 'telemetry').glob('*.json')]
    assert {o['stage'] for o in observations} == {'request_durable', 'answer_durable'}


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
@pytest.mark.parametrize('case', ['late_old', 'correct', 'duplicate_request', 'conflicting_retry', 'ack', 'wrong_session'])
def test_modern_request_ids_keep_revisions_separate(tmp_path, engine, case):
    first, newer, reply = events()
    first['request_id'], newer['request_id'] = 'request-v1', 'request-v2'
    reply.update(in_reply_to_request_id='request-v1',
                 in_reply_to_requester={k:first[k] for k in ('agent','agent_uuid','session_id','run_id')})
    reply['payload'] = {'nonce': 'v1', 'request_ts_utc': first['ts_utc']}
    rows = [first, newer, reply]
    if case in ('correct', 'duplicate_request'):
        answer2 = deepcopy(reply)
        answer2.update(in_reply_to_request_id='request-v2', payload={'nonce':'v2','request_ts_utc':newer['ts_utc']})
        rows.append(answer2)
    if case in ('duplicate_request', 'conflicting_retry'):
        retry = deepcopy(newer)
        retry['ts_utc'] = '2026-09-18T07:30:03Z'
        if case == 'conflicting_retry': retry['message'] = 'different intent with same id'
        rows.append(retry)
    if case == 'ack': reply['status'] = 'received'
    if case == 'wrong_session': reply['session_id'] = 'foreign'
    expected = 0 if case in ('correct','duplicate_request') else 2 if case in ('ack','wrong_session') else 1
    if engine == 'python':
        result = recommend_next_action(agent='codex-tools-1', events=rows, claims=[])
        assert report_unanswered_requests(events=rows, min_age_minutes=0)['unanswered_count'] == expected
    else:
        (tmp_path / 'shared').mkdir()
        (tmp_path / 'shared/events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        env = dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path))
        proc = subprocess.run([engine,'-NoProfile','-File',str(ROOT / '.agent-bridge/bin/Get-BridgeNextAction.ps1'),
                               '-Agent','codex-tools-1','-Now','2026-09-18T07:31:00Z','-Json'],
                              env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
    assert result['open_incoming_count'] == expected


@pytest.mark.parametrize('engine', SHELLS)
def test_status_and_continuity_do_not_hide_new_request_after_late_reply(tmp_path, engine):
    first, newer, reply = events()
    first['request_id'], newer['request_id'] = 'request-v1', 'request-v2'
    reply.update(in_reply_to_request_id='request-v1',
                 in_reply_to_requester={k:first[k] for k in ('agent','agent_uuid','session_id','run_id')})
    reply['payload'] = {'nonce':'v1', 'request_ts_utc':first['ts_utc']}
    rows = [dict(dict(severity='', paths=[], write_scope=[], cwd='', pid=0, message=''), **r) for r in (first,newer,reply)]
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    env = dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path))
    for script, args in [('Get-AgentBridgeStatus.ps1',['-Json']),
                         ('Read-AgentBridge.ps1',['-Agent','codex-tools-1','-NoAckReceived'])]:
        proc = subprocess.run([engine,'-NoProfile','-File',str(ROOT / '.agent-bridge/bin' / script),*args],
                              env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        if script.startswith('Read'): assert 'OPEN fixture/request' in proc.stdout
        else:
            report = json.loads(proc.stdout)
            assert 'request-v2' in proc.stdout
            assert 'waiting' in proc.stdout
