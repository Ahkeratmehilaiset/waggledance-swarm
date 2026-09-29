# SPDX-License-Identifier: BUSL-1.1
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.bridge_capacity_advisor import InputError
from tools import bridge_capacity_collector as collector
from tools.bridge_capacity_collector import (MetadataClient, collect_codex, collect_claude,
                                             quota_payload, save_observation, status, reserve_poll)


class Client:
    def __init__(self, *, changed=False, cursor=None):
        self.calls = []
        self.changed, self.cursor = changed, cursor

    async def request(self, method, params=None):
        self.calls.append(method)
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'email':
                    'changed' if self.changed and len(self.calls) > 1 else 'private@example.test'}}
        if method == 'account/rateLimits/read':
            return {'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 25,
                     'resetsAt': 1790328816}, 'secret': 'DO NOT STORE'}, 'credits': 'private'}
        return {'data': [{'model': 'example', 'supportedReasoningEfforts': []}],
                'nextCursor': self.cursor}


def native_fixture(tmp_path, rows=()):
    thread = '11111111-2222-3333-4444-555555555555'
    folder = tmp_path / 'sessions/2026/09/21'
    folder.mkdir(parents=True)
    path = folder / ('rollout-fixture-' + thread + '.jsonl')
    header = dict(type='session_meta', payload=dict(id=thread, cwd='C:/fixture', model_provider='openai',
                                                   base_instructions='DO NOT RETURN PRIVATE CONTENT'))
    path.write_text(''.join(json.dumps(r) + '\n' for r in (header, *rows)), encoding='utf-8')
    return thread, path


def test_native_codex_projects_actual_activity_model_and_quota_without_content(tmp_path):
    now = datetime.now(timezone.utc)
    rows = [dict(type='turn_context', timestamp=now.isoformat(), payload=dict(model='observed-model', effort='high')),
            dict(type='event_msg', timestamp=now.isoformat(), payload=dict(type='token_count', rate_limits={
                'limit_id': 'codex', 'primary': {'used_percent': 31, 'window_minutes': 10080,
                                              'resets_at': int(now.timestamp()) + 3600},
                'credits': 'DO NOT RETURN PRIVATE CONTENT'})),
            dict(type='event_msg', timestamp=now.isoformat(), payload=dict(type='task_complete',
                                                                         last_agent_message='DO NOT RETURN PRIVATE CONTENT'))]
    thread, path = native_fixture(tmp_path, rows)
    before = path.read_bytes()
    value = collector.read_native_codex(tmp_path, thread, now=now)
    assert value['activity_state'] == 'turn_completed'
    assert (value['model'], value['effort']) == ('observed-model', 'high')
    assert value['quota_state'] == 'observed_headroom'
    assert value['quota_windows'][0]['used_percent'] == 31
    assert value['quota_pool_binding'] == 'unverified' and not value['execution_allowed']
    assert 'PRIVATE' not in json.dumps(value) and path.read_bytes() == before


def test_native_codex_old_data_is_not_refreshed_and_partial_append_is_not_failure(tmp_path):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=1)).isoformat()
    thread, path = native_fixture(tmp_path, [dict(type='event_msg', timestamp=old, payload=dict(
        type='token_count', rate_limits={'limit_id': 'codex', 'primary': {
            'used_percent': 2, 'window_minutes': 300, 'resets_at': int(now.timestamp()) + 3600}}))])
    with path.open('ab') as f:
        f.write(b'{"unfinished":')
    value = collector.read_native_codex(tmp_path, thread, now=now)
    assert value['partial_record'] and value['quota_state'] == 'unknown'
    assert value['quota_observed_at'] == old


@pytest.mark.parametrize('case', ['wrong_id', 'wrong_provider', 'corrupt', 'oversized_header', 'duplicate'])
def test_native_codex_ambiguous_and_malformed_evidence_never_grants_capacity(tmp_path, case):
    thread, path = native_fixture(tmp_path)
    if case == 'duplicate':
        (path.parent / ('another-' + thread + '.jsonl')).write_bytes(path.read_bytes())
        assert collector.read_native_codex(tmp_path, thread)['reason'] == 'native_rollout_missing_or_ambiguous'
        return
    if case == 'corrupt':
        path.write_bytes(b'not-json\n')
    elif case == 'oversized_header':
        path.write_bytes(b'x' * (1024 * 1024 + 1))
    else:
        row = json.loads(path.read_text())
        row['payload']['id' if case == 'wrong_id' else 'model_provider'] = 'different'
        path.write_text(json.dumps(row) + '\n')
    with pytest.raises((InputError, ValueError)):
        collector.read_native_codex(tmp_path, thread)


def test_native_codex_rejects_traversal_and_does_not_create_store(tmp_path, capsys):
    store = tmp_path / 'missing.sqlite'
    code = collector.main(['--store', str(store), '--native-codex-home', str(tmp_path),
                           '--native-codex-thread', '../outside'])
    assert code == 2 and not store.exists()
    assert json.loads(capsys.readouterr().out)['execution_allowed'] is False


def test_codex_metadata_does_not_start_turn_or_infer_pool():
    client = Client()
    observation = asyncio.run(collect_codex(client, 'context'))
    assert set(client.calls) == {'account/read', 'account/rateLimits/read', 'model/list'}
    assert observation['account_pool'] is None
    assert observation['execution_allowed'] is False
    serialized = json.dumps(observation)
    assert 'private@example' not in serialized and 'DO NOT STORE' not in serialized
    assert 'credits' not in serialized


@pytest.mark.parametrize('method', ['turn/start', 'thread/resume', 'account/login/start',
                                   'config/value/write', 'account/logout'])
def test_rpc_mutations_are_rejected_before_transport(method):
    with pytest.raises(InputError, match='forbids'):
        asyncio.run(MetadataClient('unused').request(method))


def test_account_change_during_collection_is_unknown():
    with pytest.raises(InputError, match='account changed'):
        asyncio.run(collect_codex(Client(changed=True), 'context'))


def test_catalog_cursor_cycle_is_bounded():
    with pytest.raises(InputError, match='pagination'):
        asyncio.run(collect_codex(Client(cursor='repeat'), 'context'))


def test_claude_statusline_preserves_missing_quota_and_actual_model():
    row = collect_claude({'session_id': 'thread1', 'model': {'id': 'sonnet'},
                          'effort': {'level': 'max'}, 'transcript_path': 'private'})
    assert row['account_pool'] is None and row['payload'] == {'rate_limits': {}}
    assert row['model'] == 'sonnet' and row['effort'] == 'max'
    assert 'transcript_path' not in row
    assert row['quota_freshness_basis'] == 'statusline_callback_provider_timestamp_unknown'


def test_failed_collection_supersedes_prior_good_snapshot(tmp_path):
    path = tmp_path / 'observations.db'
    save_observation(path, {'provider': 'codex', 'state': 'observed'})
    save_observation(path, {'provider': 'codex', 'state': 'unknown'})
    with sqlite3.connect(path) as db:
        newest = json.loads(db.execute('SELECT data FROM observations ORDER BY sequence DESC LIMIT 1').fetchone()[0])
    assert newest['state'] == 'unknown'


def test_authoritative_empty_multibucket_map_does_not_fall_back():
    assert quota_payload({'rateLimitsByLimitId': {}, 'rateLimits': {'limitId': 'codex'}},
                         'codex') == {'rateLimitsByLimitId': {}}


def test_status_newer_failure_invalidates_headroom(tmp_path):
    path = tmp_path / 'observations.db'
    now = datetime.now(timezone.utc)
    save_observation(path, dict(provider='codex', observed_at=now.isoformat(),
                                auth_context_id='one', payload={'unused': 25}))
    save_observation(path, dict(provider='codex', reason='collection_failed'))
    report = status(path, now=now)
    assert report['observations'][0]['freshness'] == 'superseded_by_collection_failure'
    assert report['execution_allowed'] is False


def test_poll_budget_atomic_and_clock_rollback_safe(tmp_path):
    path = tmp_path / 'observations.db'
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: reserve_poll(path, now=now), range(5)))
    assert sum(x is not None for x in results) == 1
    assert reserve_poll(path, now=now - timedelta(hours=1)) is None
    assert reserve_poll(path, now=now + timedelta(seconds=299)) is None
    assert reserve_poll(path, now=now + timedelta(seconds=300)) is not None


def test_claude_callback_does_not_fabricate_provider_freshness(tmp_path):
    path = tmp_path / 'observations.db'
    row = collect_claude({'session_id': 'session', 'rate_limits': {
        'five_hour': {'used_percentage': 10, 'resets_at': 1790328816}}})
    save_observation(path, row)
    assert status(path)['observations'][0]['freshness'] == 'provider_timestamp_unknown'


def test_successful_recollection_clears_historical_error(tmp_path):
    path = tmp_path / 'observations.db'
    now = datetime.now(timezone.utc)
    save_observation(path, dict(provider='codex', reason='collection_failed'))
    save_observation(path, dict(provider='codex', observed_at=now.isoformat(), auth_context_id='one'))
    report = status(path, now=now)
    assert report['failed_providers'] == []
    assert report['observations'][0]['freshness'] == 'fresh'


@pytest.mark.parametrize('kind', ['missing', 'foreign', 'corrupt', 'bad_json', 'bad_shape', 'valid'])
@pytest.mark.parametrize('extra', [[], ['--provider', 'codex', '--scheduled', '--statusline']])
def test_status_cli_never_mutates_even_on_error(tmp_path, monkeypatch, capsys, kind, extra):
    path = tmp_path / 'status.sqlite'
    if kind == 'foreign':
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE unrelated(value TEXT)')
    elif kind == 'corrupt':
        path.write_bytes(b'not a database')
    elif kind in ('bad_json', 'bad_shape', 'valid'):
        save_observation(path, dict(provider='codex', observed_at=datetime.now(timezone.utc).isoformat()))
        if kind != 'valid':
            with sqlite3.connect(path) as db:
                db.execute('UPDATE observations SET data=?', ('not json' if kind == 'bad_json' else '[]',))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    def forbidden(*args, **kwargs):
        pytest.fail('read-only status reached a collection/write path')
    monkeypatch.setattr(collector, 'save_observation', forbidden)
    monkeypatch.setattr(collector, 'reserve_poll', forbidden)
    monkeypatch.setattr(collector, 'MetadataClient', forbidden)
    assert collector.main(['--status', '--store', str(path), *extra]) == (0 if kind == 'valid' else 2)
    result = json.loads(capsys.readouterr().out)
    assert result['execution_allowed'] is False
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_status_access_denied_does_not_try_to_save(tmp_path, monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionError('fixture')
    monkeypatch.setattr(collector, 'status', denied)
    monkeypatch.setattr(collector, 'save_observation', lambda *a: pytest.fail('status wrote'))
    assert collector.main(['--status', '--store', str(tmp_path / 'denied')]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'status_unavailable'
    assert not list(tmp_path.iterdir())


def test_status_does_not_create_wal_shared_memory(tmp_path, capsys):
    path = tmp_path / 'wal.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE observations(sequence INTEGER PRIMARY KEY,provider TEXT,data TEXT)')
        db.commit()
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
        assert collector.main(['--status', '--store', str(path)]) == 2
        assert json.loads(capsys.readouterr().out)['reason'] == 'status_unavailable'
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('duration,expected', [(300, 300), (10080, 10080), (False, None), (-1, None), ('300', None), (None, None)])
def test_status_preserves_and_validates_window_duration(tmp_path, duration, expected):
    path = tmp_path / 'windows.sqlite'
    now = datetime.now(timezone.utc)
    payload = quota_payload({'rateLimits': {'limitId': 'codex', 'primary': {
        'usedPercent': 10, 'resetsAt': now.timestamp() + 1000, 'windowDurationMins': duration}}}, 'codex')
    save_observation(path, dict(provider='codex', payload=payload, observed_at=now.isoformat()))
    row = status(path, now=now)['observations'][0]
    assert row['quota_windows'][0]['window_duration_minutes'] == expected
    assert row['quota_state'] == 'observed_headroom'
    assert row['agent_activity_state'] == 'unknown'


@pytest.mark.parametrize('age,expected', [(300, 'fresh'), (301, 'unknown_or_stale'), (361, 'unknown_or_stale'), (-1, 'unknown_or_stale')])
def test_status_reports_age_and_next_poll_without_extending_freshness(tmp_path, age, expected):
    path = tmp_path / 'age.sqlite'
    now = datetime.now(timezone.utc)
    observed = now - timedelta(seconds=age)
    reserve_poll(path, now=observed)
    save_observation(path, dict(provider='codex', observed_at=observed.isoformat()))
    result = status(path, now=now)
    assert result['observations'][0]['freshness'] == expected
    assert result['observations'][0]['observation_age_seconds'] == (age if age >= 0 else None)
    collection = result['collection']['codex']
    assert datetime.fromisoformat(collection['next_eligible_poll']) == observed + timedelta(seconds=300)
    assert collection['queue_replay_allowed'] is False


def test_claude_auth_alert_is_latched_deduplicated_and_not_cleared_by_callback(tmp_path):
    path = tmp_path / 'hooks.sqlite'
    failure = dict(session_id='native-session', hook_event_name='StopFailure', error='authentication_failed',
                   last_assistant_message='PRIVATE CONTENT', transcript_path='PRIVATE PATH')
    collector.record_claude_hook(path, failure)
    save_observation(path, collect_claude(dict(session_id='native-session')))
    first = status(path)
    alert_id = first['alerts'][0]['alert_id']
    collector.record_claude_hook(path, failure)
    collector.record_claude_hook(path, dict(session_id='native-session', hook_event_name='UserPromptSubmit', prompt='PRIVATE'))
    save_observation(path, collect_claude(dict(session_id='native-session')))
    repeated = status(path)
    assert repeated['alerts'][0]['alert_id'] == alert_id
    activity = repeated['native_activity'][0]
    assert activity['auth_state'] == 'auth_required'
    assert activity['activity_state'] == 'work_requested'
    assert activity['automatic_retry_allowed'] is False
    assert 'PRIVATE' not in json.dumps(repeated)
    collector.record_claude_hook(path, dict(session_id='native-session', hook_event_name='Stop'))
    recovered = status(path)
    assert recovered['alerts'] == []
    assert recovered['native_activity'][0]['last_successful_turn_at']
    assert recovered['native_activity'][0]['next_turn_success_verified'] is False


@pytest.mark.parametrize('error,state', [('rate_limit','rate_limited'),('server_error','transport_error'),
                                      ('billing_error','billing_error'),('oauth_org_not_allowed','access_denied')])
def test_native_error_is_not_automatically_quota_or_login(tmp_path, error, state):
    path = tmp_path / 'error.sqlite'
    save_observation(path, collect_claude(dict(session_id='native')))
    collector.record_claude_hook(path, dict(session_id='native', hook_event_name='StopFailure', error=error))
    result = status(path)
    assert result['native_activity'][0]['availability_state'] == state
    assert result['native_activity'][0]['auth_state'] == 'unknown'
    assert result['execution_allowed'] is False


def test_missing_subscription_is_auth_failure_without_login_or_fallback():
    class LoggedOut(Client):
        async def request(self, method, params=None):
            assert method == 'account/read'
            return {'account': None}
    with pytest.raises(collector.MetadataFailure) as failure:
        asyncio.run(collect_codex(LoggedOut(), 'context'))
    assert failure.value.state == 'auth_required'
@pytest.mark.parametrize('error', ['authentication_failed', {}, [], None])
def test_hook_only_store_is_readable_without_creating_observation_table(tmp_path, error):
    from tools.bridge_capacity_collector import record_claude_hook, status, native_alert_summary
    path = tmp_path / 'hooks.sqlite'
    record_claude_hook(path, dict(session_id='native', hook_event_name='StopFailure', error=error))
    before = path.read_bytes()
    result = status(path)
    assert result['observations'] == []
    assert result['alerts'][0]['state'] == ('auth_required' if error == 'authentication_failed' else 'unknown')
    assert 'blocked=' in native_alert_summary(path, 'native')
    assert path.read_bytes() == before
def test_status_retains_budget_after_interrupted_first_poll(tmp_path):
    path = tmp_path / 'interrupted.sqlite'
    now = datetime.now(timezone.utc)
    assert reserve_poll(path, now=now)
    before=path.read_bytes()
    result=status(path, now=now)
    assert result['collection']['codex']['collection_state']=='pending_or_interrupted'
    assert result['collection']['codex']['last_attempt']==now.isoformat()
    assert result['collection']['codex']['next_eligible_poll']==(now+timedelta(seconds=300)).isoformat()
    assert result['collection']['codex']['last_success'] is None
    assert result['observations']==[] and path.read_bytes()==before
@pytest.mark.parametrize('code,expected', [(-32601,'unknown'),(-32600,'unknown'),(500,'unknown'),
                                         (401,'auth_required'),(429,'rate_limited'),({},'unknown'),(True,'unknown')])
def test_rpc_errors_do_not_invent_transport_failures(code, expected):
    from types import SimpleNamespace
    class Writer:
        def write(self, _): pass
        async def drain(self): pass
    async def invoke():
        client=MetadataClient('unused')
        reader=asyncio.StreamReader()
        reader.feed_data((json.dumps(dict(id=1,error=dict(code=code,message='private detail')))+'\n').encode())
        reader.feed_eof()
        client.process=SimpleNamespace(stdin=Writer(),stdout=reader)
        with pytest.raises(collector.MetadataFailure) as failure:
            await client.request('account/read')
        assert failure.value.state==expected
        assert str(failure.value)=='metadata unavailable'
    asyncio.run(invoke())


# -- F3 pool binding (dormant, additive); authored per operator directive, NOT executed yet --

POOL_NOW = datetime.now(timezone.utc)


def _pool_registry():
    import copy
    from tools.wd_model_registry import load_registry
    registry, _ = load_registry(Path(__file__).resolve().parents[2] / 'configs' / 'model_registry.json')
    registry = copy.deepcopy(registry)
    pool = {'provider': 'codex', 'limit_id': 'codex', 'window': 'weekly', 'tier': 'standard',
            'verification': 'verified',
            'provenance': {'kind': 'operator_reading', 'reference': 'plan page', 'observer': 'operator'}}
    from tools import wd_model_registry
    if 'ttl_seconds' in wd_model_registry.POOL_KEYS:  # pools carry freshness from RCO1 af1d0ef8 on
        pool.update(measured_at=(POOL_NOW - timedelta(days=1)).strftime('%Y-%m-%d'), ttl_seconds=30 * 86400)
    registry['pools']['codex-plus-weekly'] = pool
    return registry


def _pool_receipt(subject):
    return {'schema': 'wd.pool-binding-receipt.v1', 'receipt_id': 'b' * 32, 'provider': 'codex',
            'pool': 'codex-plus-weekly', 'limit_ids': ['codex'],
            'subject': {'kind': 'auth_context', 'id': subject},
            'issued_at_utc': (POOL_NOW - timedelta(hours=1)).isoformat(),
            'expires_at_utc': (POOL_NOW + timedelta(hours=1)).isoformat(),
            'provenance': {'kind': 'operator_reading', 'reference': 'reading by ops@example.test', 'observer': None}}


def _pool_binder(subject, verifier=lambda receipt: True):
    from tools.bridge_pool_binding import bind_pool
    registry = _pool_registry()
    return lambda observation: bind_pool(observation, _pool_receipt(subject), registry, verifier=verifier,
                                         now=datetime.now(timezone.utc))


# The collector's own auth context digest for the Client() fixture.
POOL_SUBJECT = collector.digest(['context', {'type': 'chatgpt', 'email': 'private@example.test'}])


def test_default_collection_is_unchanged_and_never_binds_a_pool():
    row = collect_claude({'session_id': 'thread1'})
    assert row['account_pool'] is None and row['pool_identity_state'] == 'unknown'
    assert 'pool_binding' not in row
    observation = asyncio.run(collect_codex(Client(), 'context'))
    assert observation['account_pool'] is None and 'pool_binding' not in observation
    assert observation['pool_identity_state'] == 'unverified_auth_context'
    value = {'provider': 'codex', 'account_pool': None}
    assert collector.apply_pool_binding(value) is value  # no binder: the very same object


def test_a_verified_receipt_binds_the_collected_codex_pool_end_to_end():
    observation = asyncio.run(collect_codex(Client(), 'context', pool_binder=_pool_binder(POOL_SUBJECT)))
    assert observation['auth_context_id'] == POOL_SUBJECT
    assert observation['account_pool'] == 'codex-plus-weekly'
    assert observation['pool_identity_state'] == 'verified_binding'
    assert set(observation['pool_binding']) == {'receipt_id', 'receipt_sha256', 'provenance_kind', 'expires_at_utc'}
    serialized = json.dumps(observation)
    assert 'ops@example.test' not in serialized and 'private@example' not in serialized


@pytest.mark.parametrize('binder,reason', [
    (lambda observation: None, 'binding_refused'),
    (lambda observation: ['verified_binding'], 'binding_refused'),
    (lambda observation: {'schema': 'wd.pool-binding-decision.v1', 'pool_identity_state': 'unverified',
                          'reason': 'receipt_expired'}, 'receipt_expired'),
])
def test_a_refused_or_malformed_decision_keeps_the_pool_unknown(binder, reason):
    row = collect_claude({'session_id': 'thread1'}, pool_binder=binder)
    assert row['account_pool'] is None and row['pool_identity_state'] == 'unknown'
    assert row['pool_binding'] == {'state': 'unverified', 'reason': reason}


def test_a_failing_binder_never_fails_the_collection():
    def broken(observation):
        raise RuntimeError('verifier backend down: secret-token')
    row = collect_claude({'session_id': 'thread1'}, pool_binder=broken)
    assert row['account_pool'] is None
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binder_failed:RuntimeError'}
    assert 'secret-token' not in json.dumps(row)


def _verified_decision(**changes):
    decision = {'schema': 'wd.pool-binding-decision.v1', 'pool_identity_state': 'verified_binding',
                'execution_allowed': False, 'provider': 'codex', 'subject_id': POOL_SUBJECT,
                'account_pool': 'codex-plus-weekly', 'receipt_id': 'b' * 32, 'receipt_sha256': 'f' * 64,
                'provenance_kind': 'operator_reading',
                'expires_at_utc': (POOL_NOW + timedelta(hours=1)).isoformat()}
    decision.update(changes)
    return decision


@pytest.mark.parametrize('change', [
    {'subject_id': 'd' * 64}, {'provider': 'claude'}, {'execution_allowed': True}, {'expires_at_utc': 'soon'},
    {'account_pool': ''}, {'account_pool': 'Pool With Spaces'}, {'schema': 'other'},
    {'receipt_id': 'reading by ops@example.test'}, {'receipt_sha256': None},
    {'provenance_kind': 'plan_transcription'}, {'pool_identity_state': 'unverified'}])
def test_a_verified_decision_for_another_subject_or_provider_is_not_applied(change):
    observation = {'provider': 'codex', 'auth_context_id': POOL_SUBJECT, 'account_pool': None}
    good = collector.apply_pool_binding(observation, lambda o: _verified_decision())
    assert good['account_pool'] == 'codex-plus-weekly'  # success twin
    assert good['pool_binding']['receipt_id'] == 'b' * 32
    row = collector.apply_pool_binding(observation, lambda o: _verified_decision(**change))
    assert row['account_pool'] is None and row['pool_binding']['state'] == 'unverified'
    assert 'ops@example.test' not in json.dumps(row)


def test_only_a_code_shaped_refusal_reason_is_kept():
    observation = {'provider': 'codex', 'auth_context_id': POOL_SUBJECT, 'account_pool': None}
    row = collector.apply_pool_binding(observation, lambda o: {'reason': 'mail ops@example.test'})
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binding_refused'}
    row = collector.apply_pool_binding(observation, lambda o: {'reason': 'receipt_expired'})
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'receipt_expired'}  # success twin

def test_a_receipt_for_another_auth_context_does_not_bind():
    observation = asyncio.run(collect_codex(Client(), 'context', pool_binder=_pool_binder('e' * 64)))
    assert observation['account_pool'] is None
    assert observation['pool_binding'] == {'state': 'unverified', 'reason': 'subject_mismatch'}


def test_status_expires_a_stored_binding_without_rewriting_the_store(tmp_path):
    path = tmp_path / 'observations.db'
    now = datetime(2026, 9, 29, 22, tzinfo=timezone.utc)
    for context, expires in (('live', now + timedelta(hours=1)), ('gone', now - timedelta(seconds=1))):
        save_observation(path, dict(provider='codex', observed_at=now.isoformat(), auth_context_id=context,
                                    account_pool='codex-plus-weekly', pool_identity_state='verified_binding',
                                    pool_binding={'expires_at_utc': expires.isoformat()}))
    before = path.read_bytes()
    rows = {row['auth_context_id']: row for row in status(path, now=now)['observations']}
    assert rows['live']['account_pool'] == 'codex-plus-weekly'
    assert rows['live']['pool_identity_state'] == 'verified_binding'
    assert rows['gone']['account_pool'] is None and rows['gone']['pool_identity_state'] == 'binding_expired'
    assert path.read_bytes() == before