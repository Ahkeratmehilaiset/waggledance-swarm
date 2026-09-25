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
