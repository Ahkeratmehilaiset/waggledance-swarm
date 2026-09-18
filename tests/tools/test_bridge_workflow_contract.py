from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tools.bridge_next_action import recommend_next_action
from waggledance.core.bridge_workflow import prepare_request, prepare_handoff, latency_report, STAGES


def plan():
    return {'role': 'planner', 'target': 'fable-5', 'authorization_ref': 'operator/task-42',
            'task_id': 'fixture/workflow', 'revision': 'v1', 'instruction': 'Prepare the authorized plan',
            'requester': {'agent': 'codex-lead-1', 'agent_uuid': 'lead', 'session_id': 's1', 'run_id': 'r1'},
            'responder': {'agent': 'fable-5', 'agent_uuid': 'fable', 'session_id': 's2', 'run_id': 'r2'},
            'consumes_fields': ['requirements'], 'result_fields': ['cost'],
            'data': {'requirements': [1, 2, 3], 'evidence': 'reviewer-only'}}


def test_role_request_only_contains_declared_consumed_fields():
    request = prepare_request(plan())
    assert request['payload']['inputs'] == {'requirements': [1, 2, 3]}
    assert 'reviewer-only' not in str(request)
    assert request['payload']['authority_effect'] == 'none'
    assert prepare_request(plan())['request_id'] != request['request_id']


@pytest.mark.parametrize('bad', ['role', 'identity', 'cross_role', 'authorization', 'grok'])
def test_invalid_role_request_is_rejected(bad):
    value = plan()
    if bad == 'role': value['role'] = 'reviewer'
    if bad == 'identity': value['responder']['session_id'] = ''
    if bad == 'cross_role': value['consumes_fields'] = ['evidence']
    if bad == 'authorization': value['authorization_ref'] = ''
    if bad == 'grok': value.update(role='advisor', target='grok-scout-1')
    with pytest.raises(ValueError): prepare_request(value)


@pytest.mark.parametrize('bad', ['', 'ack', 'old_id', 'wrong_session', 'wrong_result', 'new_authority'])
def test_handoff_validates_exact_result_before_preparing_next_step(bad):
    request = prepare_request(plan(), now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    reply = dict(request['expected_responders']['fable-5'], task_id=request['task_id'],
                 ts_utc='2026-09-18T00:01:00Z', to='codex-lead-1', type='message', status='answered',
                 in_reply_to_request_id=request['request_id'], in_reply_to_requester=plan()['requester'],
                 payload={'result': {'cost': 45}})
    next_plan = plan()
    next_plan.update(role='tools', target='codex-tools-1', consumes_fields=['handoff'])
    next_plan['responder']['agent'] = 'codex-tools-1'
    if bad == 'ack': reply['status'] = 'received'
    if bad == 'old_id': reply['in_reply_to_request_id'] = 'old-id'
    if bad == 'wrong_session': reply['session_id'] = 'other'
    if bad == 'wrong_result': reply['payload']['result'] = {'wrong': 45}
    if bad == 'new_authority': next_plan['authorization_ref'] = 'invented'
    if bad:
        with pytest.raises(ValueError): prepare_handoff(request, reply, next_plan)
    else:
        result = prepare_handoff(request, reply, next_plan)
        assert result['payload']['inputs']['handoff']['result'] == {'cost': 45}
        assert result['request_id'] != request['request_id']


def test_latency_reports_missing_stages_unknown_and_joins_exact_delivery():
    request = prepare_request(plan())
    observations = [dict(request_id=request['request_id'], target='fable-5', requester='codex-lead-1',
                         requester_session_id='s1', stage=stage, delivery_id='delivery-1',
                         observed_at_utc=f'2026-09-18T00:00:0{i}Z') for i, stage in enumerate(STAGES)]
    observations[2]['request_id'] = None
    report = latency_report(request, observations, target='fable-5')
    assert list(report['seconds'].values()) == [1, 1, 1, 1]
    missing = latency_report(request, observations[:2], target='fable-5')
    assert missing['unknown_stages'] == list(STAGES[2:])
    assert missing['task_completion_verified'] is False
    wrong = deepcopy(observations)
    wrong[3]['delivery_id'] = 'other'
    assert 'relay_enqueued' in latency_report(request, wrong, target='fable-5')['unknown_stages']


def test_historical_helpers_and_quiet_on_demand_grok_are_not_stalled_workers():
    rows = [dict(agent=agent, ts_utc='2026-09-18T00:00:00Z', type='status', status='idle', task_id='old')
            for agent in ('grok-scout-1', 'grok-watchdog', 'wd-stall-monitor')]
    report = recommend_next_action(agent='codex-lead-1', events=rows, claims=[],
                                   now_utc=datetime(2026, 9, 18, 2, tzinfo=timezone.utc))
    assert 'production_liveness' not in report
    assert report['worker_class'] == 'active'


def test_open_request_age_is_reported_even_with_heartbeats():
    request = prepare_request(plan(), now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    report = recommend_next_action(agent='fable-5', events=[request], claims=[],
                                   now_utc=datetime(2026, 9, 18, 0, 3, tzinfo=timezone.utc))
    assert report['oldest_open_request_age_seconds'] == 180
