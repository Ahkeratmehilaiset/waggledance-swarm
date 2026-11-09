"""Synthetic situation-picture contract; no live bridge, Mind or provider calls."""
import importlib
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as reader

T0 = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
LEAD, TOOLS, RCO = 'codex-lead-1', 'codex-tools-1', 'claude-rco-1'
ROSTER = {
    lane: {'agent_uuid': '0000000%d-0000-4000-8000-000000000000' % i,
           'session_id': 'synthetic-session-%d' % i, 'run_id': 'synthetic-session-%d' % i}
    for i, lane in enumerate([LEAD, TOOLS, RCO, 'claude-rco-2', 'fable-5', 'operator', 'capacity-observer'], 1)
}


def stamp(seconds=0):
    return (T0 + timedelta(seconds=seconds)).isoformat()


def row(agent=LEAD, seconds=0, **changes):
    value = {'agent': agent, **ROSTER[agent], 'type': 'status', 'status': 'active',
             'task_id': 'synthetic-work', 'ts_utc': stamp(seconds), 'to': TOOLS,
             'message': 'Synthetic body is never part of the view.', 'payload': {}}
    value.update(changes)
    return value


def write(path, rows, *, mode='w'):
    with path.open(mode, encoding='utf-8', newline='\n') as stream:
        for value in rows:
            stream.write(json.dumps(value, sort_keys=True) + '\n')


def picture(**kwargs):
    module = importlib.import_module('tools.bridge_situation_picture')
    return module.SituationPicture(roster=ROSTER, ttl_seconds=60, **kwargs)


def test_existing_reader_control_is_cursor_neutral(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    before = path.read_bytes()
    result = reader.read_bridge_log(path)
    assert result.status is reader.BridgeReadStatus.OK and result.rows[0]['task_id'] == 'synthetic-work'
    assert result.candidate_cursor.offset == len(before)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_event_reference_control_uses_entire_canonical_event():
    value = row()
    assert event_id(value) == event_id(json.loads(json.dumps(value)))
    assert event_id(value) != event_id(dict(value, message='Different synthetic body.'))


def test_fresh_metadata_has_source_age_without_free_text(tmp_path):
    path = tmp_path / 'events.jsonl'
    event = row(payload={'head': 'a' * 40, 'secret': 'synthetic-sensitive-body'})
    write(path, [event])
    view = picture().update(path, now=stamp(10))
    task = view['lanes'][LEAD]['task']
    assert view['coverage'] == 'COMPLETE' and task['value'] == 'synthetic-work'
    assert task['source_event_ref'] == event_id(event) and task['age_seconds'] == 10
    assert view['lanes'][LEAD]['head']['value'] == 'a' * 40
    assert 'synthetic-sensitive-body' not in json.dumps(view) and event['message'] not in json.dumps(view)


@pytest.mark.parametrize('seconds, now', [(0, 61), (20, 10)])
def test_stale_and_future_fields_are_unknown_with_evidence(tmp_path, seconds, now):
    path = tmp_path / 'events.jsonl'
    event = row(seconds=seconds)
    write(path, [event])
    value = picture().update(path, now=stamp(now))['lanes'][LEAD]['task']
    assert value['state'] == 'UNKNOWN' and value['value'] is None
    assert value['source_event_ref'] == event_id(event)


def test_wrong_session_never_populates_a_lane(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row(session_id='retired-session')])
    value = picture().update(path, now=stamp(10))['lanes'][LEAD]['task']
    assert value['state'] == 'UNKNOWN' and value['value'] is None


def test_incremental_delta_equals_complete_replay(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    incremental = picture()
    first = incremental.update(path, now=stamp(10))
    write(path, [row(agent=TOOLS, seconds=11, task_id='synthetic-next')], mode='a')
    second = incremental.update(path, now=stamp(20))
    complete = picture().update(path, now=stamp(20))
    assert second['cursor']['offset'] > first['cursor']['offset']
    for field in ('lanes', 'waiting_for', 'prs_awaiting_rco', 'operator_directives', 'capacity', 'routing_suggestion'):
        assert second[field] == complete[field]


def test_partial_tail_is_incomplete_and_can_finish_without_replay(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    view = picture()
    view.update(path, now=stamp(10))
    next_row = row(agent=TOOLS, seconds=11)
    with path.open('ab') as stream:
        stream.write(json.dumps(next_row).encode('utf-8'))
    assert view.update(path, now=stamp(20))['coverage'] == 'UNKNOWN'
    with path.open('ab') as stream:
        stream.write(b'\n')
    assert view.update(path, now=stamp(21))['lanes'][TOOLS]['task']['value'] == 'synthetic-work'


@pytest.mark.parametrize('operation', ['rotate', 'truncate', 'rewrite_prefix'])
def test_changed_history_requires_explicit_reset(tmp_path, operation):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    view = picture()
    view.update(path, now=stamp(10))
    if operation == 'rotate':
        path.rename(tmp_path / 'old.jsonl')
        write(path, [row(seconds=11)])
    elif operation == 'truncate':
        path.write_bytes(b'')
    else:
        raw = path.read_bytes()
        assert b'synthetic-work' in raw
        path.write_bytes(raw.replace(b'synthetic-work', b'synthetic-fake'))
    report = view.update(path, now=stamp(20))
    assert report['coverage'] == 'UNKNOWN' and report['lanes'][LEAD]['task']['state'] == 'UNKNOWN'
    assert view.update(path, now=stamp(21))['reason'] == 'reset_required'
    view.reset()
    assert view.update(path, now=stamp(22))['coverage'] == 'COMPLETE'


def request():
    return row(type='message', request_id='synthetic-request', request_digest='b' * 64,
               nonce='synthetic-nonce', expected_responders={TOOLS: ROSTER[TOOLS]})


def reply(sent, **changes):
    return row(agent=TOOLS, seconds=1, to=LEAD, type='message', status='answered',
               in_reply_to_request_id=sent['request_id'], in_reply_to_request_digest=sent['request_digest'],
               in_reply_to_requester={key: sent[key] for key in ('agent', 'agent_uuid', 'session_id', 'run_id')},
               nonce='synthetic-nonce', payload={'result': {'status': 'done', 'head': 'c' * 40}}, **changes)


@pytest.mark.parametrize('variant', ['wrong_nonce', 'wrong_session', 'ack', 'unknown_status', 'private_status', 'callback', 'valid'])
def test_last_bound_reply_is_more_than_request_id_or_ack(tmp_path, variant):
    path = tmp_path / 'events.jsonl'
    sent = request()
    answered = reply(sent)
    if variant == 'wrong_nonce':
        answered['nonce'] = 'wrong-nonce'
    elif variant == 'wrong_session':
        answered['session_id'] = 'retired-session'
    elif variant == 'ack':
        answered['status'] = 'received'
        answered['payload'] = {}
    elif variant == 'unknown_status':
        answered['status'] = 'unknown-producer-state'
    elif variant == 'private_status':
        answered['status'] = 'synthetic-sensitive-status'
    elif variant == 'callback':
        answered['type'] = 'heartbeat'
    write(path, [sent, answered])
    value = picture().update(path, now=stamp(10))['lanes'][TOOLS]['last_bound_reply']
    if variant == 'valid':
        assert value['source_event_ref'] == event_id(answered)
        assert value['value']['content_verified'] is False
    else:
        assert value['state'] == 'UNKNOWN'
    assert 'synthetic-sensitive-status' not in json.dumps(value)


def test_pending_rco_request_exposes_only_evidenced_dependency(tmp_path):
    path = tmp_path / 'events.jsonl'
    event = row(type='peer_review_request', to=RCO, request_id='review-one', request_digest='d' * 64,
                expected_responders={RCO: ROSTER[RCO]}, payload={'pr': 123, 'head': 'e' * 40})
    write(path, [event])
    report = picture().update(path, now=stamp(10))
    assert report['waiting_for']['value'][0]['request_ref'] == event_id(event)
    assert report['prs_awaiting_rco']['value'][0]['pr'] == 123
    assert report['prs_awaiting_rco']['source_event_ref'] == event_id(event)


def test_last_reply_uses_reply_position_across_overlapping_requests(tmp_path):
    path = tmp_path / 'events.jsonl'
    first = request()
    second = dict(request(), request_id='synthetic-request-two', ts_utc=stamp(1))
    early = dict(reply(second), ts_utc=stamp(2))
    late = dict(reply(first), ts_utc=stamp(3))
    write(path, [first, second, early, late])
    result = picture().update(path, now=stamp(10))
    assert result['lanes'][TOOLS]['last_bound_reply']['source_event_ref'] == event_id(late)


def test_exact_rco_pass_clears_review_wait_but_later_veto_reopens_it(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = row(type='peer_review_request', to=RCO, request_id='review-one', request_digest='d' * 64,
               nonce='synthetic-nonce', expected_responders={RCO: ROSTER[RCO]}, payload={'pr': 123, 'head': 'e' * 40})
    passed = reply(sent)
    passed.update(agent=RCO, **ROSTER[RCO], type='decision', status='rco_pass')
    passed['payload'] = {'exact_head': 'e' * 40, 'result': {'status': 'rco_pass', 'head': 'e' * 40}}
    write(path, [sent, passed])
    view = picture()
    result = view.update(path, now=stamp(10))
    assert result['prs_awaiting_rco']['value'] == []
    veto = dict(passed, type='finding', status='changes_requested', ts_utc=stamp(11),
                payload={'result': {'status': 'changes_requested', 'head': 'e' * 40}})
    write(path, [veto], mode='a')
    assert view.update(path, now=stamp(20))['prs_awaiting_rco']['value'][0]['result_state'] == 'REPORTED_VETO'


def test_pass_after_veto_does_not_retract_the_veto(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = row(type='peer_review_request', to=RCO, request_id='review-one', request_digest='d' * 64,
               nonce='synthetic-nonce', expected_responders={RCO: ROSTER[RCO]}, payload={'pr': 123, 'head': 'e' * 40})
    veto = reply(sent)
    veto.update(agent=RCO, **ROSTER[RCO], type='finding', status='changes_requested')
    veto['payload']['result'] = {'status': 'changes_requested', 'head': 'e' * 40}
    passed = dict(veto, type='decision', status='rco_pass', ts_utc=stamp(2),
                  payload={'exact_head': 'e' * 40, 'result': {'status': 'rco_pass', 'head': 'e' * 40}})
    write(path, [sent, veto, passed])
    result = picture().update(path, now=stamp(10))
    assert result['prs_awaiting_rco']['value'][0]['result_state'] == 'REPORTED_VETO'


def test_binding_checks_are_indexed_by_target_and_request_id(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    module = importlib.import_module('tools.bridge_situation_picture')
    check = module.reply_matches_request
    calls = []
    def counted(*args, **kwargs):
        calls.append(None)
        return check(*args, **kwargs)
    monkeypatch.setattr(module, 'reply_matches_request', counted)
    requests = [dict(request(), request_id='request-%d' % i, ts_utc=stamp(i)) for i in range(15)]
    answers = [dict(reply(sent), ts_utc=stamp(15 + i)) for i, sent in enumerate(requests)]
    write(path, requests + answers)
    assert picture().update(path, now=stamp(45))['coverage'] == 'COMPLETE'
    assert len(calls) <= 30


def test_unverified_capacity_cannot_route_work(tmp_path):
    path = tmp_path / 'events.jsonl'
    event = row(agent='capacity-observer', type='capacity_observation', payload={
        'provider': 'codex', 'freshness': 'fresh', 'pool_identity_state': 'unknown',
        'quota_state': 'observed_headroom', 'used_percent': 1})
    write(path, [event])
    report = picture().update(path, now=stamp(10))
    assert report['capacity']['state'] == 'UNKNOWN' and report['routing_suggestion']['state'] == 'UNKNOWN'


def test_operator_directive_metadata_has_source_without_body(tmp_path):
    path = tmp_path / 'events.jsonl'
    event = row(agent='operator', type='decision', payload={'kind': 'operator_directive'})
    write(path, [event])
    report = picture().update(path, now=stamp(10))
    assert report['operator_directives']['source_event_ref'] == event_id(event)
    assert event['message'] not in json.dumps(report)


def test_malformed_log_never_advances_cursor_or_presents_old_state_as_current(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    view = picture()
    initial = view.update(path, now=stamp(10))
    with path.open('ab') as stream:
        stream.write(b'{not-json}\n')
    report = view.update(path, now=stamp(20))
    assert report['coverage'] == 'UNKNOWN'
    assert report['cursor'] is None or report['cursor']['offset'] == initial['cursor']['offset']
    assert report['lanes'][LEAD]['task']['state'] == 'UNKNOWN'


def test_snapshot_never_writes_log_or_generation_files(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    picture().update(path, now=stamp(10))
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_open_wait_survives_ttl_but_answered_request_is_not_waiting(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = request()
    write(path, [sent])
    view = picture()
    result = view.update(path, now=stamp(120))
    waiting = result['waiting_for']
    assert waiting['state'] == 'OBSERVED'
    assert waiting['value'][0]['requester'] == LEAD
    assert waiting['value'][0]['age_seconds'] == 120
    write(path, [dict(reply(sent), ts_utc=stamp(121))], mode='a')
    assert view.update(path, now=stamp(122))['waiting_for']['value'] == []


def test_multi_target_wait_keeps_only_unanswered_targets(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = dict(request(), to=TOOLS + ',' + RCO, expected_responders={TOOLS: ROSTER[TOOLS], RCO: ROSTER[RCO]})
    write(path, [sent, reply(sent)])
    result = picture().update(path, now=stamp(120))
    assert [item['responder'] for item in result['waiting_for']['value']] == [RCO]


@pytest.mark.parametrize('variant', ['cancelled', 'superseded', 'ack', 'chatter', 'wrong_nonce'])
def test_only_exact_requester_closeout_removes_an_open_wait(tmp_path, variant):
    path = tmp_path / 'events.jsonl'
    sent = request()
    closed = dict(reply(sent), **{key: sent[key] for key in ('agent', 'agent_uuid', 'session_id', 'run_id')},
                  to=TOOLS, status='cancelled', payload={})
    if variant == 'superseded':
        closed['status'] = 'superseded'
    elif variant == 'ack':
        closed['status'] = 'received'
    elif variant == 'chatter':
        closed['status'] = 'notice'
    elif variant == 'wrong_nonce':
        closed['nonce'] = 'wrong'
    write(path, [sent, closed])
    pending = picture().update(path, now=stamp(120))['waiting_for']['value']
    assert len(pending) == (0 if variant in ('cancelled', 'superseded') else 1)


def history_roster():
    old = dict(ROSTER[LEAD], current=False, source_event_ref='1' * 64)
    current = dict(ROSTER[LEAD], session_id='current-session', run_id='current-run',
                   current=True, source_event_ref='2' * 64)
    return dict(ROSTER, **{LEAD: [old, current]})


def test_pinned_prior_session_survives_in_history_without_becoming_current(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = request()
    write(path, [sent])
    module = importlib.import_module('tools.bridge_situation_picture')
    result = module.SituationPicture(roster=history_roster(), ttl_seconds=60).update(path, now=stamp(120))
    assert result['lanes'][LEAD]['task']['state'] == 'UNKNOWN'
    history = result['lanes'][LEAD]['history']
    assert history[0]['session_id'] == ROSTER[LEAD]['session_id']
    assert history[0]['identity_source_ref'] == '1' * 64
    assert history[0]['task']['source_event_ref'] == event_id(sent)
    assert result['waiting_for']['value'][0]['request_ref'] == event_id(sent)


@pytest.mark.parametrize('variant', ['missing_source', 'two_current', 'no_current', 'wrong_uuid'])
def test_history_roster_rejects_ambiguous_or_unsourced_session_tuples(variant):
    roster = history_roster()
    if variant == 'missing_source':
        roster[LEAD][0].pop('source_event_ref')
    elif variant == 'two_current':
        roster[LEAD][0]['current'] = True
    elif variant == 'no_current':
        roster[LEAD][1]['current'] = False
    else:
        roster[LEAD][0]['agent_uuid'] = ROSTER[TOOLS]['agent_uuid']
    module = importlib.import_module('tools.bridge_situation_picture')
    with pytest.raises(ValueError, match='roster'):
        module.SituationPicture(roster=roster)


def test_unapproved_history_session_still_cannot_populate_history(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row(session_id='injected-old-session'), row(session_id='current-session', run_id='current-run', seconds=1)])
    module = importlib.import_module('tools.bridge_situation_picture')
    view = module.SituationPicture(roster=history_roster()).update(path, now=stamp(10))
    assert view['lanes'][LEAD]['task']['source_event_ref'] == event_id(row(session_id='current-session', run_id='current-run', seconds=1))
    assert all(entry['session_id'] != 'injected-old-session' for entry in view['lanes'][LEAD]['history'])


def test_real_review_message_without_pr_is_visible_without_inventing_pr(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = row(type='message', request_id='real-shape-review', request_digest='d' * 64,
               to=RCO, expected_responders={RCO: ROSTER[RCO]},
               message='One requested review of Tools situation-picture head' + 'e' * 40 + ' (base ad58).')
    write(path, [sent])
    report = picture().update(path, now=stamp(120))
    item = report['review_requests_awaiting_rco']['value'][0]
    assert item['pr'] is None and item['pr_state'] == 'UNKNOWN'
    assert item['last_referenced_head'] == 'e' * 40 and item['git_verified'] is False
    assert item['request_ref'] == event_id(sent)
    assert report['prs_awaiting_rco']['value'] == []


@pytest.mark.parametrize('variant', ['scoped_safety_pass', 'conflicting_structured_head', 'two_prose_heads'])
def test_real_review_metadata_cannot_infer_gate_approval_or_rescue_conflicts(tmp_path, variant):
    path = tmp_path / 'events.jsonl'
    sent = row(type='message', request_id='real-review', request_digest='d' * 64, nonce='synthetic-nonce',
               to=RCO, expected_responders={RCO: ROSTER[RCO]}, payload={'pr_number': 123, 'head': 'e' * 40})
    answered = reply(sent)
    answered.update(agent=RCO, **ROSTER[RCO], status='done')
    answered['payload']['result'] = {'status': 'PASS_SAFETY_SCOPE_FUNCTIONAL_GAPS_NOT_YET_MEETING_REQUIREMENTS', 'head': 'e' * 40}
    if variant == 'conflicting_structured_head':
        sent['payload']['exact_head'] = 'f' * 40
        sent['message'] = 'head' + 'e' * 40
    elif variant == 'two_prose_heads':
        sent['payload'].pop('head')
        sent['message'] = 'head' + 'e' * 40 + ' and head' + 'f' * 40
    write(path, [sent, answered])
    view = picture().update(path, now=stamp(10))
    item = view['review_requests_awaiting_rco']['value'][0]
    assert item['gate_pass_observed'] is False
    if variant != 'scoped_safety_pass':
        assert item['last_referenced_head'] is None


def test_typed_gate_pass_is_distinct_from_a_scoped_bound_result(tmp_path):
    path = tmp_path / 'events.jsonl'
    sent = row(type='message', request_id='typed-review', request_digest='d' * 64, nonce='synthetic-nonce',
               to=RCO, expected_responders={RCO: ROSTER[RCO]}, payload={'pr_number': 123, 'head': 'e' * 40})
    passed = reply(sent)
    passed.update(agent=RCO, **ROSTER[RCO], type='decision', status='rco_pass')
    passed['payload'] = {'pr_number': 123, 'head': 'e' * 40, 'exact_head': 'e' * 40,
                         'result': {'status': 'rco_pass', 'head': 'e' * 40}}
    write(path, [sent, passed])
    assert picture().update(path, now=stamp(10))['review_requests_awaiting_rco']['value'] == []


def test_selected_real_directive_relay_retains_actual_author_and_unknown_operator_origin(tmp_path):
    path = tmp_path / 'events.jsonl'
    event = row(type='message', message='OPERATOR DIRECTIVE: synthetic content must stay out of the view.')
    write(path, [event])
    module = importlib.import_module('tools.bridge_situation_picture')
    view = module.SituationPicture(roster=ROSTER, operator_relay_refs=[event_id(event)]).update(path, now=stamp(10))
    item = view['operator_directives']['value'][0]
    assert item['relay_author'] == LEAD and item['operator_origin_verified'] is False
    assert item['source_event_ref'] == event_id(event)
    assert event['message'] not in json.dumps(view)


def test_prose_operator_mention_is_not_a_selected_directive(tmp_path):
    path = tmp_path / 'events.jsonl'
    write(path, [row(message='OPERATOR DIRECTIVE: merely mentioning it is not provenance.')])
    assert picture().update(path, now=stamp(10))['operator_directives']['state'] == 'UNKNOWN'


def capacity_fixture(module, tmp_path, monkeypatch, *, fresh=True, verified_pool=False):
    script = tmp_path / 'Get-WdCapacityStatus.ps1'
    script.write_bytes(b'# synthetic verified reader, never executed')
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    monkeypatch.setattr(module, 'CAPACITY_READER', script, raising=False)
    payload = {'schema': 'wd.capacity-status.v1', 'observed_at': stamp(),
               'installation': {'source_verified': True, 'manifest_sha256': 'a' * 64},
               'observations': [{'provider': 'codex', 'observed_at': stamp(0 if fresh else -120),
                  'freshness': 'fresh', 'pool_identity_state': 'verified' if verified_pool else 'unverified_auth_context',
                  'quota_state': 'observed_headroom', 'execution_allowed': False,
                  'quota_windows': [{'limit_id': 'codex', 'name': 'primary', 'used_percent': 28}]}]}
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert '-NoProfile' in argv and '-NonInteractive' in argv and '-File' in argv and '-Json' in argv
        assert kwargs['cwd'] == module.ROOT
        return type('Result', (), {'returncode': 0, 'stdout': json.dumps(payload), 'stderr': ''})()
    monkeypatch.setattr(module, 'subprocess', importlib.import_module('subprocess'), raising=False)
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    return script, digest, calls


def test_real_capacity_status_shape_exposes_quota_even_when_pool_identity_is_unknown(tmp_path, monkeypatch):
    module = importlib.import_module('tools.bridge_situation_picture')
    script, digest, calls = capacity_fixture(module, tmp_path, monkeypatch)
    snapshot = module.read_capacity_status(reader_path=script, expected_sha256=digest)
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    view = picture().update(path, now=stamp(10), capacity_snapshot=snapshot)
    provider = view['capacity']['value']['codex']
    assert provider['state'] == 'OBSERVED' and provider['value']['quota_windows'][0]['used_percent'] == 28
    assert provider['source_ref']['reader_sha256'] == digest and provider['age_seconds'] == 10
    assert view['routing_suggestion']['state'] == 'UNKNOWN'
    assert len(calls) == 1


def test_capacity_reader_pin_mismatch_never_starts_process(tmp_path, monkeypatch):
    module = importlib.import_module('tools.bridge_situation_picture')
    script, digest, calls = capacity_fixture(module, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='pin'):
        module.read_capacity_status(reader_path=script, expected_sha256='b' * 64)
    assert calls == []


def test_stale_provider_timestamp_stays_unknown_despite_fresh_wrapper(tmp_path, monkeypatch):
    module = importlib.import_module('tools.bridge_situation_picture')
    script, digest, _ = capacity_fixture(module, tmp_path, monkeypatch, fresh=False)
    snapshot = module.read_capacity_status(reader_path=script, expected_sha256=digest)
    path = tmp_path / 'events.jsonl'
    write(path, [row()])
    view = picture().update(path, now=stamp(10), capacity_snapshot=snapshot)
    assert view['capacity']['value']['codex']['state'] == 'UNKNOWN'


def test_optional_capacity_reader_failure_never_blocks_the_bridge_view(tmp_path, monkeypatch, capsys):
    module = importlib.import_module('tools.bridge_situation_picture')
    path, roster = tmp_path / 'events.jsonl', tmp_path / 'roster.json'
    write(path, [row()])
    roster.write_text(json.dumps(ROSTER))
    def unavailable(**kwargs):
        raise ValueError('capacity_reader_unavailable')
    monkeypatch.setattr(module, 'read_capacity_status', unavailable)
    code = module.main(['--events', str(path), '--roster', str(roster), '--as-of', stamp(10), '--capacity-status'])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['coverage'] == 'COMPLETE' and result['lanes'][LEAD]['task']['state'] == 'OBSERVED'
    assert result['capacity']['state'] == 'UNKNOWN'


def test_cli_historical_roster_requires_and_checks_its_supplied_hash(tmp_path, capsys):
    module = importlib.import_module('tools.bridge_situation_picture')
    path, roster = tmp_path / 'events.jsonl', tmp_path / 'roster.json'
    write(path, [row()])
    roster.write_text(json.dumps(history_roster()))
    argv = ['--events', str(path), '--roster', str(roster), '--as-of', stamp(10)]
    assert module.main(argv) == 2
    capsys.readouterr()
    assert module.main(argv + ['--roster-sha256', 'f' * 64]) == 2
    capsys.readouterr()
    digest = hashlib.sha256(roster.read_bytes()).hexdigest()
    assert module.main(argv + ['--roster-sha256', digest]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['roster_source_ref']['sha256'] == digest


@pytest.mark.parametrize('agent', [None, {}, [], 'unregistered-agent'])
def test_unrecognized_identity_shape_is_unknown_not_an_exception(tmp_path, agent):
    path = tmp_path / 'events.jsonl'
    event = row()
    event['agent'] = agent
    write(path, [event])
    result = picture().update(path, now=stamp(10))
    assert result['coverage'] == 'COMPLETE'
    assert all(lane['task']['state'] == 'UNKNOWN' for lane in result['lanes'].values())
