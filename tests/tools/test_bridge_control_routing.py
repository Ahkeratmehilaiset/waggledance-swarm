"""Routing closure is not reviewer-veto retraction or new task authority."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import bridge_next_action as routing
from waggledance.core.bridge_request_contract import reply_matches_request


def control(sender='codex-tools-1', stamp='2026-09-25T01:42:17Z'):
    return dict(agent=sender, to='codex-lead-1', type='decision',
                task_id='audit/revision', status='changes_requested', ts_utc=stamp)


def done():
    return dict(agent='codex-lead-1', type='done', status='done',
                task_id='audit/revision', ts_utc='2026-09-25T02:26:58Z')


@pytest.mark.parametrize('repeat', [False, True])
def test_unlinked_done_cannot_close_control_regardless_of_author_count(repeat):
    rows = [control(), control('claude-rco-1')]
    if repeat:
        rows.insert(0, control(stamp='2026-09-25T01:40:00Z'))
    expected = len(rows)
    rows.append(done())
    assert len(routing._open_requests_for_agent(agent='codex-lead-1', events=rows)) == expected


def test_bound_negative_reply_is_answer_not_new_assignment():
    event = control()
    event.update(in_reply_to_request_id='review-1', in_reply_to_request_digest='a' * 64)
    assert routing._is_answer_like(event)
    assert not routing._is_request_like(event)


def test_negative_reply_with_explicit_new_request_remains_actionable():
    event = control()
    event.update(request_id='new-fix', in_reply_to_request_id='review-1')
    assert routing._is_request_like(event)


def test_control_processing_requires_explicit_timestamp_correlation():
    request = control()
    answer = done()
    answer['to'] = request['agent']
    answer['payload'] = {'request_ts_utc': request['ts_utc']}
    assert routing._request_closed_by_index(request=request, agent='codex-lead-1',
        closure_index=routing._build_request_closure_index([request, answer]))
    wrong = deepcopy(answer)
    wrong['payload']['request_ts_utc'] = '2026-09-25T01:40:00Z'
    assert not routing._request_closed_by_index(request=request, agent='codex-lead-1',
        closure_index=routing._build_request_closure_index([request, wrong]))


def test_bound_negative_reply_still_closes_original_review():
    request = dict(agent='codex-lead-1', to='codex-tools-1', type='message',
        status='review_requested', task_id='audit/revision', request_id='review-1',
        request_digest='a' * 64, ts_utc='2026-09-25T01:40:00Z')
    answer = control()
    answer.update(in_reply_to_request_id='review-1', in_reply_to_request_digest='a' * 64,
                  in_reply_to_requester={'agent': 'codex-lead-1'})
    assert reply_matches_request(request, answer, 'codex-tools-1')
    assert routing._open_requests_for_agent(agent='codex-tools-1', events=[request, answer]) == []


def test_ordinary_single_legacy_request_keeps_compatibility():
    request = control()
    request.update(type='message', status='request')
    assert routing._open_requests_for_agent(agent='codex-lead-1', events=[request, done()]) == []


@pytest.mark.parametrize('target', ['codex-tools-1', 'claude-rco-1'])
@pytest.mark.parametrize('closer', ['target', 'requester'])
def test_shared_pr_number_cannot_close_a_different_named_task(target, closer):
    request = dict(agent='codex-lead-1', to=target, type='wake_request',
        task_id='audit/review-a', status='rco_review_requested' if 'rco' in target else 'review_requested',
        message='Review PR #1721 RCO_PASS or BLOCK', payload={'pr': 1721},
        ts_utc='2026-09-25T01:40:00Z')
    reply = dict(agent=target if closer == 'target' else 'codex-lead-1',
        to='codex-lead-1' if closer == 'target' else target,
        type='decision', task_id='audit/review-b',
        status='rco_pass' if closer == 'target' else 'superseded',
        message='PR #1721', payload={'pr': 1721}, ts_utc='2026-09-25T01:41:00Z')
    assert routing._open_requests_for_agent(agent=target, events=[request, reply]) == [request]


@pytest.mark.parametrize('reference', [None, 'wrong', 'exact'])
@pytest.mark.parametrize('actor', ['codex-lead-1', 'fable-5'])
def test_powershell_control_binding_cannot_silently_close_unlinked_request(reference, actor):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell unavailable')
    request, reply = control(), done()
    reply['agent'] = actor
    reply['to'] = request['agent']
    if reference:
        reply['payload'] = {'request_ts_utc': request['ts_utc'] if reference == 'exact'
                            else '2026-09-25T01:40:00Z'}
    contract = Path(__file__).resolve().parents[2] / '.agent-bridge/bin/BridgeRequestContract.ps1'
    script = ". '" + str(contract).replace("'", "''") + "'; $e = [Console]::In.ReadToEnd() | ConvertFrom-Json; Test-BridgeReplyBinding $e.request $e.reply 'codex-lead-1' | ConvertTo-Json -Compress"
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
        input=json.dumps({'request': request, 'reply': reply}), text=True,
        capture_output=True, check=True, timeout=30)
    assert json.loads(result.stdout) == (reference == 'exact' and actor == 'codex-lead-1')


@pytest.mark.parametrize('reference', [None, 'wrong', 'exact'])
def test_full_powershell_router_control_closure_parity(tmp_path, reference):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell unavailable')
    request, reply = control(), done()
    reply['to'] = request['agent']
    if reference:
        reply['payload'] = {'request_ts_utc': request['ts_utc'] if reference == 'exact'
                            else '2026-09-25T01:40:00Z'}
    rows = [request, reply]
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/events.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    script = Path(__file__).resolve().parents[2] / '.agent-bridge/bin/Get-BridgeNextAction.ps1'
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(script),
        '-Agent', 'codex-lead-1', '-Now', '2026-09-25T02:30:00Z', '-Json'],
        env={**os.environ, 'AGENT_BRIDGE_RUNTIME_ROOT': str(tmp_path)},
        text=True, capture_output=True, check=True, timeout=30)
    assert json.loads(result.stdout)['open_incoming_count'] == len(
        routing._open_requests_for_agent(agent='codex-lead-1', events=rows))


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('new_request', [False, True])
def test_powershell_python_negative_reply_classification_parity(nested, new_request):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell unavailable')
    event = control()
    container = event.setdefault('payload', {}) if nested else event
    container['in_reply_to_request_id'] = 'review-1'
    if new_request:
        event['request_id'] = 'new-fix'
    classifier = Path(__file__).resolve().parents[2] / '.agent-bridge/bin/BridgeEventClassifier.ps1'
    script = ". '" + str(classifier).replace("'", "''") + "'; $e = [Console]::In.ReadToEnd() | ConvertFrom-Json; @(Test-BridgeRequestLikeEvent $e; Test-BridgeAnswerEvent $e) | ConvertTo-Json -Compress"
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
        input=json.dumps(event), text=True, capture_output=True, check=True, timeout=30)
    assert json.loads(result.stdout) == [routing._is_request_like(event), routing._is_answer_like(event)]


@pytest.mark.parametrize('closer', ['claude-rco-1', 'fable-5'])
def test_exact_link_does_not_allow_unrelated_actor(closer):
    request = control()
    answer = done()
    answer.update(agent=closer, to=request['agent'],
                  payload={'request_ts_utc': request['ts_utc']})
    assert not routing._request_closed_by_index(request=request, agent='codex-lead-1',
        closure_index=routing._build_request_closure_index([request, answer]))


def test_sanitized_september25_canonical_reply_is_not_a_new_assignment():
    # Pinned Read-AgentBridge -Raw -NoAckReceived -NoContinuity -Tail 1200,
    # observed 2026-09-25. Minimal projection of real 01:42:17.9175705Z reply
    # and 02:26:58.1371303Z done. Content/IDs sanitized; binding shape retained.
    reply = control(stamp='2026-09-25T01:42:17.9175705Z')
    reply.update(in_reply_to_request_id='sanitized-original-review',
                 in_reply_to_request_digest='a' * 64,
                 in_reply_to_requester={'agent': 'codex-lead-1'})
    reply['payload'] = {'head': 'b' * 40, 'decision': 'changes_requested',
                        'nonce': 'sanitized-original-nonce'}
    terminal = done()
    terminal['ts_utc'] = '2026-09-25T02:26:58.1371303Z'
    # Unlike Tools' reply, the real RCO event also had its OWN request ID.
    # Keep that explicit task actionable, regardless of a later generic done.
    rco = control('claude-rco-1', '2026-09-25T01:41:54Z')
    rco.update(request_id='sanitized-rco-followup', request_digest='c' * 64)
    result = routing._open_requests_for_agent(agent='codex-lead-1',
                                              events=[rco, reply, terminal])
    assert result == [rco]
    assert routing._is_answer_like(reply)  # Still available to reply/gate readers.


# --- v1 whole_request cancellation in the PowerShell selector (RCO1 4223 reproduction, Lead 22:26Z) --------------
# A closed wd.request-cancellation.v1 whole_request row by the exact requester agent AND agent_uuid, later on the same
# task, naming the exact request_id and the stored request_digest WITHHOLDS the request from routing. It is not an
# answer and grants nothing; anything else (wrong id/digest/position/identity, legacy or malformed shapes) leaves it open.
_SHELLS = [shell for shell in dict.fromkeys(filter(None, (shutil.which('powershell.exe'), shutil.which('pwsh'))))]
_LEAD = {'agent': 'codex-lead-1', 'agent_uuid': 'uuid-lead-1', 'session_id': 'sess-a', 'run_id': 'run-a'}
_RID, _DIGEST, _TASK = 'req-6ba-fixture', 'a' * 64, 'codex-lead-1/fixture-review'


def _request(**over):
    row = dict(_LEAD, ts_utc='2026-10-01T21:54:05Z', type='wake_request', status='assigned', task_id=_TASK,
               to='codex-tools-1', request_id=_RID, request_digest=_DIGEST, message='synthetic review',
               payload={'task_revision': 'rev-1'})
    row.update(over)
    return row


def _cancel(payload_over=None, drop=(), **over):
    payload = {'schema': 'wd.request-cancellation.v1', 'cancelled_request_id': _RID,
               'cancelled_request_digest': _DIGEST, 'scope': 'whole_request'}
    for key in drop:
        payload.pop(key)
    payload.update(payload_over or {})
    row = dict(_LEAD, ts_utc='2026-10-01T21:55:04Z', type='message', status='cancelled', task_id=_TASK,
               to='codex-tools-1', message='withdraws only', payload=payload)
    row.update(over)
    return row


def _answer(rid=_RID, digest=_DIGEST, task=_TASK, stamp='2026-10-01T22:02:41Z'):
    return {'agent': 'codex-tools-1', 'agent_uuid': 'uuid-tools', 'session_id': 's-t', 'run_id': 'r-t',
            'ts_utc': stamp, 'type': 'message', 'status': 'answered', 'task_id': task, 'to': 'codex-lead-1',
            'message': 'result', 'in_reply_to_request_id': rid, 'in_reply_to_request_digest': digest,
            'in_reply_to_requester': {key: _LEAD[key] for key in ('agent', 'agent_uuid', 'session_id', 'run_id')},
            'payload': {'result': {}}}


def _select(shell, tmp_path, rows):
    (tmp_path / 'shared').mkdir(parents=True)
    (tmp_path / 'shared/events.jsonl').write_text(
        ''.join(json.dumps(row, separators=(',', ':')) + '\n' for row in rows), encoding='utf-8')
    script = Path(__file__).resolve().parents[2] / '.agent-bridge/bin/Get-BridgeNextAction.ps1'
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('AGENT_BRIDGE_', 'WD_', 'PSMODULEPATH'))}
    env['AGENT_BRIDGE_RUNTIME_ROOT'] = str(tmp_path)
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                             '-Agent', 'codex-tools-1', '-Now', '2026-10-01T22:20:00Z', '-Json'],
                            env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    selected = (out.get('incoming') or {}).get('request_id') if out['action'] == 'answer_incoming' else None
    return out, selected


_OTHER_SESSION = {'session_id': 'sess-b', 'run_id': 'run-b'}
_WITHHELD = [
    ('C1 exact v1 cancel', [_request(), _cancel()]),
    ('C2 v1 cancel + corrected r2 answered', [_request(), _cancel(),
        _request(request_id='req-8c8', request_digest='b' * 64, task_id=_TASK + '-r2', ts_utc='2026-10-01T21:55:05Z'),
        _answer('req-8c8', 'b' * 64, _TASK + '-r2')]),
    ('C9 v1 cancel from a later Lead session, same agent_uuid', [_request(), _cancel(**_OTHER_SESSION)]),
    ('duplicate identical cancels', [_request(), _cancel(), _cancel(ts_utc='2026-10-01T21:55:30Z')]),
]
_OPEN = [
    ('C3 another digest', [_request(), _cancel({'cancelled_request_digest': 'c' * 64})]),
    ('C4 another request id', [_request(), _cancel({'cancelled_request_id': 'other-id'})]),
    ('C5 cancel before the request', [_cancel(ts_utc='2026-10-01T21:54:00Z'), _request()]),
    ('C10 legacy free-form cancel', [_request(), _cancel(drop=('schema', 'scope', 'cancelled_request_digest'),
                                                         type='decision')]),
    ('foreign agent_uuid', [_request(), _cancel(agent_uuid='uuid-someone-else')]),
    ('rekey same label new uuid', [_request(), _cancel(agent_uuid='uuid-lead-2', **_OTHER_SESSION)]),
    ('blank agent_uuid on the cancel', [_request(), _cancel(agent_uuid='')]),
    ('agent_uuid missing on the cancel', [_request(), {k: v for k, v in _cancel().items() if k != 'agent_uuid'}]),
    ('blank agent_uuid on the request', [_request(agent_uuid=''), _cancel(agent_uuid='')]),
    ('non-string agent_uuid', [_request(), _cancel(agent_uuid=['uuid-lead-1'])]),
    ('another label with the lead uuid', [_request(), _cancel(agent='operator')]),
    ('extra payload key', [_request(), _cancel({'production_hold': True})]),
    ('scope not whole_request', [_request(), _cancel({'scope': 'source_implementation_only'})]),
    ('status Cancelled case', [_request(), _cancel(status='Cancelled')]),
    ('status canceled', [_request(), _cancel(status='canceled')]),
    ('schema v0', [_request(), _cancel({'schema': 'wd.request-cancellation.v0'})]),
    ('payload key case Schema', [_request(), _cancel({'Schema': 'wd.request-cancellation.v1'}, drop=('schema',))]),
    ('list id', [_request(), _cancel({'cancelled_request_id': [_RID]})]),
    ('request without stored digest', [{k: v for k, v in _request().items() if k != 'request_digest'},
                                       _cancel({'cancelled_request_digest': ''})]),
    ('cancel on another task', [_request(), _cancel(task_id=_TASK + '-other')]),
    ('malformed stored digest copied verbatim', [_request(request_digest='A' * 64), _cancel({'cancelled_request_digest': 'A' * 64})]),
    ('short stored digest copied verbatim', [_request(request_digest='abc'), _cancel({'cancelled_request_digest': 'abc'})]),
]
_CLOSED_AS_BEFORE = [
    ('C6 late answer after the cancel', [_request(), _cancel(), _answer(stamp='2026-10-01T21:59:00Z')]),
    ('C11 answered no cancel', [_request(), _answer()]),
]


@pytest.mark.parametrize('shell', _SHELLS)
@pytest.mark.parametrize('name, rows', _WITHHELD, ids=[case[0] for case in _WITHHELD])
def test_a_v1_whole_request_cancellation_withholds_the_request_from_routing(shell, tmp_path, name, rows):
    out, selected = _select(shell, tmp_path, rows)
    assert selected != _RID and out['open_incoming_count'] == 0, out
    assert out['cancelled_withheld_count'] == 1 and out['cancelled_withheld_request_ids'] == [_RID], out


@pytest.mark.parametrize('shell', _SHELLS)
@pytest.mark.parametrize('name, rows', _OPEN, ids=[case[0] for case in _OPEN])
def test_anything_but_the_exact_v1_cancellation_leaves_the_request_open(shell, tmp_path, name, rows):
    out, selected = _select(shell, tmp_path, rows)
    assert selected == _RID and out['open_incoming_count'] == 1, out
    assert out.get('cancelled_withheld_count', 0) == 0, out


@pytest.mark.parametrize('shell', _SHELLS)
@pytest.mark.parametrize('name, rows', _CLOSED_AS_BEFORE, ids=[case[0] for case in _CLOSED_AS_BEFORE])
def test_bound_answers_still_close_and_are_not_counted_as_cancellations(shell, tmp_path, name, rows):
    out, selected = _select(shell, tmp_path, rows)
    assert selected is None and out['open_incoming_count'] == 0, out
    assert out.get('cancelled_withheld_count', 0) == 0, out


@pytest.mark.parametrize('shell', _SHELLS)
def test_a_stale_cancelled_request_is_withheld_from_the_stale_count_too(shell, tmp_path):
    rows = [_request(ts_utc='2026-09-30T01:00:00Z'), _cancel(ts_utc='2026-09-30T01:05:00Z')]
    out, _ = _select(shell, tmp_path, rows)
    assert out['stale_incoming_count'] == 0 and out['cancelled_withheld_count'] == 1, out
