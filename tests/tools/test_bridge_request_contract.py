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
def test_request_id_routing_match_does_not_accept_wrong_nonce(tmp_path, engine):
    _, request, reply = events()
    request.update(request_id='exact-request', request_digest='digest')
    reply.update(in_reply_to_request_id='exact-request', in_reply_to_request_digest='digest',
                 in_reply_to_requester={k: request[k] for k in ('agent','agent_uuid','session_id','run_id')})
    assert reply_matches_request(request, reply, 'codex-tools-1')
    reply['payload']['nonce'] = 'wrong'
    assert reply['in_reply_to_request_id'] == request['request_id']  # routing_match
    if engine == 'python':
        assert not reply_matches_request(request, reply, 'codex-tools-1')
    else:
        fixture = tmp_path/'binding.json'
        fixture.write_text(json.dumps(dict(request=request,reply=reply)))
        command = f". '{ROOT / '.agent-bridge/bin/BridgeRequestContract.ps1'}'; $f=Get-Content '{fixture}' -Raw|ConvertFrom-Json; Test-BridgeReplyBinding $f.request $f.reply 'codex-tools-1'"
        result = subprocess.run([engine,'-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'False'


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
@pytest.mark.parametrize('partial_target', [False, True])
def test_real_writer_persists_id_and_full_request_reply_binding(tmp_path, engine, partial_target):
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
    request = write('-Agent','operator','-Type','message','-Status','handoff_ready','-To',
                    'codex-tools-1,fable-5' if partial_target else 'codex-tools-1',
                    '-SessionId','operator-session','-RunId','operator-run')
    assert request['request_id'] and request['request_digest']
    assert 'codex-tools-1' in request['expected_responders']
    assert 'fable-5' not in request['expected_responders']
    reply = write('-Agent','codex-tools-1','-AgentUuid',CODEX_TOOLS_UUID,'-SessionId','tools-session','-RunId','tools-run',
                  '-Type','message','-Status','answered','-To','operator','-ReplyToEventJson',json.dumps(request))
    assert reply_matches_request(request, reply, 'codex-tools-1')
    assert reply['in_reply_to_request_id'] == request['request_id']
    observations = [json.loads(p.read_text()) for p in (shared / 'telemetry').glob('*.json')]
    assert {o['stage'] for o in observations} == {'request_durable', 'answer_durable'}
    if partial_target:
        foreign = dict(reply, agent='fable-5')
        assert not reply_matches_request(request, foreign, 'fable-5')


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
@pytest.mark.parametrize('case', ['late_old', 'conflicting_digest'])
def test_status_and_continuity_do_not_hide_new_request_after_late_reply(tmp_path, engine, case):
    first, newer, reply = events()
    first['request_id'], newer['request_id'] = 'request-v1', 'request-v2'
    reply.update(in_reply_to_request_id='request-v1',
                 in_reply_to_requester={k:first[k] for k in ('agent','agent_uuid','session_id','run_id')})
    reply['payload'] = {'nonce':'v1', 'request_ts_utc':first['ts_utc']}
    events_in_order = [first,newer,reply]
    if case == 'conflicting_digest':
        newer['request_digest'] = 'digest-v2'
        answer2 = deepcopy(reply)
        answer2.update(in_reply_to_request_id='request-v2', in_reply_to_request_digest='digest-v2',
                       payload={'nonce':'v2','request_ts_utc':newer['ts_utc']})
        retry = deepcopy(newer)
        retry.update(ts_utc='2026-09-18T07:30:03Z', request_digest='changed-digest')
        events_in_order += [answer2, retry]
    rows = [dict(dict(severity='', paths=[], write_scope=[], cwd='', pid=0, message=''), **r) for r in events_in_order]
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


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
def test_explicit_id_does_not_turn_closed_notification_into_new_work(tmp_path, engine):
    first, _, _ = events()
    first.update(request_id='closed-notification', status='closed')
    if engine == 'python':
        result = recommend_next_action(agent='codex-tools-1', events=[first], claims=[])
    else:
        (tmp_path / 'shared').mkdir()
        (tmp_path / 'shared/events.jsonl').write_text(json.dumps(first)+'\n')
        proc = subprocess.run([engine,'-NoProfile','-File',str(ROOT / '.agent-bridge/bin/Get-BridgeNextAction.ps1'),
                               '-Agent','codex-tools-1','-Now','2026-09-18T07:31:00Z','-Json'],
                              env=dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path)),
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
    assert result['open_incoming_count'] == 0


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
@pytest.mark.parametrize('identity', [None, 'not-an-object', 42, [], {}, {'session_id':'tools-session'}])
def test_malformed_responder_identity_fails_closed(tmp_path, engine, identity):
    _, request, reply = events()
    request['payload']['expected_responders']['codex-tools-1'] = identity
    if engine == 'python':
        assert not reply_matches_request(request, reply, 'codex-tools-1')
    else:
        fixture = tmp_path / 'events.json'
        fixture.write_text(json.dumps([request, reply]))
        script = f". '{ROOT / '.agent-bridge/bin/BridgeRequestContract.ps1'}'; $rows=Get-Content -LiteralPath '{fixture}' -Raw | ConvertFrom-Json; Test-BridgeReplyBinding $rows[0] $rows[1] 'codex-tools-1' | ConvertTo-Json"
        proc = subprocess.run([engine,'-NoProfile','-Command',script], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) is False


# --- Python reply binding exact types (fable-5 ec85): mirror the PowerShell exact-type contract. Identity, digest and
# context values are exact strings (ordinal); nonce/token/task_revision match only the SAME exact bool/int/float
# (finite, |float| < 2**53); only an exact empty-string label is a documented blank; field() conflicts are type-strict.
def _py_pair(**request_over):
    _, request, reply = events()
    request.update(request_id='request-v2', request_digest='d' * 64)
    request.update(request_over)
    reply.update(in_reply_to_request_id='request-v2', in_reply_to_request_digest='d' * 64,
                 in_reply_to_requester={k: request[k] for k in ('agent', 'agent_uuid', 'session_id', 'run_id')
                                        if request.get(k)})
    return request, reply


def _py_text(request, reply):
    """Round-trip through JSON TEXT, as every real reader does."""
    return json.loads(json.dumps(request)), json.loads(json.dumps(reply))


def _py_cases():
    cases = {'exact_twin': (_py_pair(), True)}
    for name, (sent, echoed, expected) in {
            'nonce_true_true': (True, True, True), 'nonce_3_3': (3, 3, True), 'nonce_1_5': (1.5, 1.5, True),
            'nonce_neg_zero': (-0.0, 0.0, True), 'nonce_big_int_exact': (10 ** 30 + 1, 10 ** 30 + 1, True),
            'nonce_true_vs_1': (True, 1, False), 'nonce_false_vs_0': (False, 0, False), 'nonce_1_vs_true': (1, True, False),
            'nonce_3_vs_3_0': (3, 3.0, False), 'nonce_list_equal': ([1], [1], False), 'nonce_dict_equal': ({'k': 1}, {'k': 1}, False),
            'nonce_2pow53_float': (9007199254740992.0, 9007199254740992.0, False), 'nonce_1e16': (1e16, 1e16, False),
            'nonce_string_vs_int': ('3', 3, False), 'nonce_wrong': ('v2', 'v1', False),
            'nonce_case': ('v2', 'V2', False), 'nonce_zwsp': ('v2', 'v2​', False)}.items():
        request, reply = _py_pair()
        request['payload']['nonce'] = sent
        reply['payload'] = dict(reply['payload'], nonce=echoed)
        cases[name] = ((request, reply), expected)
    request, reply = _py_pair()
    request['payload']['nonce'] = float('nan')
    reply['payload'] = dict(reply['payload'], nonce=float('nan'))
    cases['nonce_nan'] = ((request, reply), False)
    request, reply = _py_pair()
    del reply['payload']['nonce']
    cases['nonce_absent_on_id_bound_reply'] = ((request, reply), True)
    for name, edit in {'digest_equal_int': ('request_digest', 7, 'in_reply_to_request_digest', 7),
                       'digest_equal_list': ('request_digest', ['d'], 'in_reply_to_request_digest', ['d'])}.items():
        request, reply = _py_pair(**{edit[0]: edit[1]})
        reply[edit[2]] = deepcopy(edit[3])
        cases[name] = ((request, reply), False)
    for label, value in {'false': False, 'zero': 0, 'list': ['x']}.items():   # falsy/non-str request labels: malformed
        request, reply = _py_pair(agent_uuid=value)
        reply['in_reply_to_requester'].pop('agent_uuid', None)
        cases[f'request_label_{label}'] = ((request, reply), False)
    request, reply = _py_pair(agent_uuid='')                               # documented blank: writer omits it
    cases['request_label_blank_omitted'] = ((request, reply), True)
    request, reply = _py_pair(session_id=True)
    reply['in_reply_to_requester']['session_id'] = True
    cases['request_label_equal_bool'] = ((request, reply), False)
    request, reply = _py_pair()
    reply['agent_uuid'] = ['tools-uuid']
    cases['responder_label_list'] = ((request, reply), False)
    request, reply = _py_pair()
    reply['task_id'] = ['fixture/request']
    cases['task_id_list'] = ((request, reply), False)
    request, reply = _py_pair()
    reply['to'] = ['codex-lead-1']
    cases['to_list'] = ((request, reply), False)
    request, reply = _py_pair()
    request['nonce'] = True                                                # top-level vs payload: true vs 1 conflict
    request['payload']['nonce'] = 1
    reply['payload'] = dict(reply['payload'], nonce=1)
    cases['field_conflict_true_vs_1'] = ((request, reply), False)
    request, reply = _py_pair()
    reply['in_reply_to_request_id'] = 'Request-v2'
    cases['id_case'] = ((request, reply), False)
    return cases


@pytest.mark.parametrize('name', sorted(_py_cases()))
def test_python_reply_binding_uses_exact_types(name):
    (request, reply), expected = _py_cases()[name]
    request, reply = _py_text(request, reply) if name != 'nonce_nan' else (request, reply)
    assert bool(reply_matches_request(request, reply, 'codex-tools-1')) is expected


def test_python_requester_closure_blank_and_set_labels():
    for blank, expected in ((True, True), (False, False)):
        request, _ = _py_pair(**({'agent_uuid': ''} if blank else {}))
        closure = dict(ts_utc='2026-09-18T07:30:05Z', agent='codex-lead-1', agent_uuid='lead-uuid-other',
                       session_id=request['session_id'], run_id=request['run_id'], to='codex-tools-1', type='message',
                       status='cancelled', task_id=request['task_id'], in_reply_to_request_id='request-v2',
                       in_reply_to_request_digest='d' * 64,
                       in_reply_to_requester={k: request[k] for k in ('agent', 'agent_uuid', 'session_id', 'run_id') if request[k]},
                       payload={'nonce': request['payload']['nonce']})
        assert bool(reply_matches_request(request, closure, 'codex-tools-1', requester_closure=True)) is expected


def test_python_routing_consumer_keeps_a_type_mismatched_reply_open():
    from tools.bridge_next_action import _open_requests_for_agent
    request, reply = _py_pair()
    request['payload']['nonce'] = True
    reply['payload'] = dict(reply['payload'], nonce=1)
    request, reply = _py_text(request, reply)
    assert _open_requests_for_agent(agent='codex-tools-1', events=[request, reply]) == [request]
    reply['payload']['nonce'] = True
    assert _open_requests_for_agent(agent='codex-tools-1', events=[request, reply]) == []


def test_python_field_conflict_true_vs_1_is_not_collapsed_even_when_the_reply_echoes_true():
    request, reply = _py_pair()
    request['nonce'] = True                      # top-level true, payload 1: a conflict, never a value
    request['payload']['nonce'] = 1
    reply['payload'] = dict(reply['payload'], nonce=True)
    request, reply = _py_text(request, reply)
    assert not reply_matches_request(request, reply, 'codex-tools-1')


@pytest.mark.parametrize('label, expected', [('', True), (False, False), (0, False)])
def test_python_legacy_requester_closure_identity_skips_only_an_exact_blank(label, expected):
    _, request, _ = events()                     # legacy: no request_id, so only the identity loop checks labels
    request['agent_uuid'] = label
    closure = dict(ts_utc='2026-09-18T07:30:05Z', agent='codex-lead-1', agent_uuid='lead-uuid-other',
                   session_id=request['session_id'], run_id=request['run_id'], to='codex-tools-1', type='message',
                   status='cancelled', task_id=request['task_id'], payload={'nonce': request['payload']['nonce']})
    request, closure = _py_text(request, closure)
    assert bool(reply_matches_request(request, closure, 'codex-tools-1', requester_closure=True)) is expected
