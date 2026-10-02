# SPDX-License-Identifier: BUSL-1.1
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone, tzinfo
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
# Every clock here is fixed and injected (Tools a673ecb4, 5b2af2cd): collection time via a
# stubbed utcnow, binding time via bind_pool(now=...), and apply time via the callable
# apply_pool_binding(clock=...) / pool_clock=..., which is sampled AFTER the binder. No sleeps.

POOL_NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_collection_time(monkeypatch):
    monkeypatch.setattr(collector, 'utcnow', lambda: POOL_NOW.isoformat())
    return POOL_NOW


def _pool_registry(**overrides):
    import copy
    from tools.wd_model_registry import load_registry
    registry, _ = load_registry(Path(__file__).resolve().parents[2] / 'configs' / 'model_registry.json')
    registry = copy.deepcopy(registry)
    pool = {'provider': 'codex', 'limit_id': 'codex', 'window': 'weekly', 'tier': 'standard',
            'verification': 'verified',
            'provenance': {'kind': 'operator_reading', 'reference': 'plan page', 'observer': 'operator'}}
    # A verified pool is a dated measurement with a TTL (registry af1d0ef8).
    pool.update(measured_at=(POOL_NOW - timedelta(days=1)).strftime('%Y-%m-%d'), ttl_seconds=30 * 86400)
    pool.update(overrides)
    registry['pools']['codex-plus-weekly'] = pool
    return registry


def _pool_receipt(subject):
    return {'schema': 'wd.pool-binding-receipt.v1', 'receipt_id': 'b' * 32, 'provider': 'codex',
            'pool': 'codex-plus-weekly', 'limit_ids': ['codex'],
            'subject': {'kind': 'auth_context', 'id': subject},
            'issued_at_utc': (POOL_NOW - timedelta(hours=1)).isoformat(),
            'expires_at_utc': (POOL_NOW + timedelta(hours=1)).isoformat(),
            'provenance': {'kind': 'operator_reading', 'reference': 'reading by ops@example.test', 'observer': None}}


def _pool_binder(subject, verifier=lambda receipt: True, **pool_overrides):
    from tools.bridge_pool_binding import bind_pool
    registry = _pool_registry(**pool_overrides)
    return lambda observation: bind_pool(observation, _pool_receipt(subject), registry, verifier=verifier,
                                         now=POOL_NOW)


def _fixed(moment=POOL_NOW):
    return lambda: moment


def _collect(binder):
    return asyncio.run(collect_codex(Client(), 'context', pool_binder=binder, pool_clock=_fixed()))


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


def test_a_verified_receipt_binds_the_collected_codex_pool_end_to_end(fixed_collection_time):
    observation = _collect(_pool_binder(POOL_SUBJECT))
    assert observation['auth_context_id'] == POOL_SUBJECT and observation['observed_at'] == POOL_NOW.isoformat()
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
    row = collect_claude({'session_id': 'thread1'}, pool_binder=binder, pool_clock=_fixed())
    assert row['account_pool'] is None and row['pool_identity_state'] == 'unknown'
    assert row['pool_binding'] == {'state': 'unverified', 'reason': reason}


def test_a_failing_binder_never_fails_the_collection():
    def broken(observation):
        raise RuntimeError('verifier backend down: secret-token')
    row = collect_claude({'session_id': 'thread1'}, pool_binder=broken, pool_clock=_fixed())
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


OBSERVATION = {'provider': 'codex', 'auth_context_id': POOL_SUBJECT, 'account_pool': None}


@pytest.mark.parametrize('change', [
    {'subject_id': 'd' * 64}, {'provider': 'claude'}, {'execution_allowed': True}, {'expires_at_utc': 'soon'},
    {'account_pool': ''}, {'account_pool': 'Pool With Spaces'}, {'schema': 'other'},
    {'receipt_id': 'reading by ops@example.test'}, {'receipt_sha256': None},
    {'provenance_kind': 'plan_transcription'}, {'pool_identity_state': 'unverified'}])
def test_a_verified_decision_for_another_subject_or_provider_is_not_applied(change):
    good = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(), clock=_fixed())
    assert good['account_pool'] == 'codex-plus-weekly'  # success twin
    assert good['pool_binding']['receipt_id'] == 'b' * 32
    row = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(**change), clock=_fixed())
    assert row['account_pool'] is None and row['pool_binding']['state'] == 'unverified'
    assert 'ops@example.test' not in json.dumps(row)


@pytest.mark.parametrize('expires,applied', [
    (POOL_NOW - timedelta(seconds=1), False),     # a cached or delayed decision that already expired
    (POOL_NOW, False),                            # the exact boundary is expired (strictly before)
    (POOL_NOW + timedelta(seconds=1), True),      # success twin
])
def test_the_apply_boundary_requires_now_strictly_before_the_decision_expiry(expires, applied):
    row = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(expires_at_utc=expires.isoformat()),
                                       clock=_fixed())
    assert (row['account_pool'] == 'codex-plus-weekly') is applied
    if not applied:
        assert row['pool_binding'] == {'state': 'unverified', 'reason': 'decision_expired'}


class SteppedClock:
    """Deterministic apply-time source that a fixture binder advances (no sleeps); counts reads."""

    def __init__(self, moment):
        self.moment, self.reads = moment, []

    def __call__(self):
        self.reads.append(self.moment)
        return self.moment


@pytest.mark.parametrize('elapsed,applied', [
    (timedelta(seconds=2), False),              # expired while the binder ran (Tools 5b2af2cd counterexample)
    (timedelta(seconds=1), False),              # expires exactly at the apply boundary: strictly before is required
    (timedelta(microseconds=999999), True),     # success twin: still valid when applied
    (timedelta(0), True),                       # success twin: a binder that took no time
])
def test_time_spent_in_the_binder_counts_at_the_apply_boundary(elapsed, applied):
    clock = SteppedClock(POOL_NOW)
    reads_seen_by_binder = []

    def slow_binder(observation):
        reads_seen_by_binder.append(list(clock.reads))   # recorded, asserted outside (a raise here is caught)
        clock.moment = POOL_NOW + elapsed   # the binder and its verifier "took" this long
        return _verified_decision(expires_at_utc=(POOL_NOW + timedelta(seconds=1)).isoformat())
    row = collector.apply_pool_binding(OBSERVATION, slow_binder, clock=clock)
    assert reads_seen_by_binder == [[]]          # nothing was sampled before the binder returned
    assert clock.reads == [POOL_NOW + elapsed]   # sampled exactly once, after the binder
    assert (row['account_pool'] == 'codex-plus-weekly') is applied
    assert (row.get('pool_identity_state') == 'verified_binding') is applied
    if not applied:
        assert row['pool_binding'] == {'state': 'unverified', 'reason': 'decision_expired'}


def test_the_default_clock_is_sampled_once_after_the_binder_returns(monkeypatch):
    order = []
    monkeypatch.setattr(collector, '_utc_now', lambda: order.append('clock') or POOL_NOW)

    def binder(observation):
        order.append('binder')
        return _verified_decision()
    row = collector.apply_pool_binding(OBSERVATION, binder)
    assert order == ['binder', 'clock'] and row['account_pool'] == 'codex-plus-weekly'
    expired = collector.apply_pool_binding(OBSERVATION, lambda o: order.append('binder') or _verified_decision(
        expires_at_utc=POOL_NOW.isoformat()))
    assert order[2:] == ['binder', 'clock'] and expired['pool_binding']['reason'] == 'decision_expired'


def test_without_a_binder_the_same_object_returns_and_no_clock_is_read_or_validated(monkeypatch):
    reads = []
    monkeypatch.setattr(collector, '_utc_now', lambda: reads.append('default') or POOL_NOW)
    value = {'provider': 'codex', 'account_pool': None}
    assert collector.apply_pool_binding(value) is value
    assert collector.apply_pool_binding(value, None, clock=lambda: reads.append('injected') or POOL_NOW) is value
    assert collector.apply_pool_binding(value, None, clock='not a clock') is value
    assert reads == []                                   # no clock of any kind was read
    collector.apply_pool_binding(value, lambda o: {'reason': 'x'})
    assert reads == ['default']                          # control: with a binder the default clock IS read once


def test_a_clock_that_edits_the_kept_decision_or_the_callers_observation_changes_nothing():
    # RCO1 7f32cfea S1: validated values are local snapshots before the clock (caller code) runs.
    kept = _verified_decision()
    caller_observation = dict(OBSERVATION, payload={'nested': {'value': 1}})
    edits = []

    def binder(observation):
        observation['payload']['nested']['value'] = 'binder was here'   # its own deep copy (N6)
        return kept                                                     # and it keeps its returned dict

    def clock():
        kept.update(expires_at_utc='x', account_pool='Free Text', receipt_id='z' * 32,
                    provenance_kind='plan_transcription', subject_id='d' * 64)
        caller_observation.update(provider='claude', auth_context_id='d' * 64, account_pool='other')
        edits.append('done')
        return POOL_NOW
    row = collector.apply_pool_binding(caller_observation, binder, clock=clock)
    assert edits == ['done']                                            # the edits really happened first
    assert row['account_pool'] == 'codex-plus-weekly' and row['pool_identity_state'] == 'verified_binding'
    assert row['provider'] == 'codex' and row['auth_context_id'] == POOL_SUBJECT
    assert row['pool_binding'] == {'receipt_id': 'b' * 32, 'receipt_sha256': 'f' * 64,
                                   'provenance_kind': 'operator_reading',
                                   'expires_at_utc': (POOL_NOW + timedelta(hours=1)).isoformat()}
    assert row['payload']['nested']['value'] == 1                        # the binder only saw a copy
    assert caller_observation['payload']['nested']['value'] == 1


def test_a_clock_edit_after_an_expired_decision_still_reports_the_expiry():
    kept = _verified_decision(expires_at_utc=POOL_NOW.isoformat())

    def clock():
        kept.update(expires_at_utc=(POOL_NOW + timedelta(hours=9)).isoformat(), reason='mail ops@example.test')
        return POOL_NOW
    row = collector.apply_pool_binding(OBSERVATION, lambda o: kept, clock=clock)
    assert row['account_pool'] is None
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'decision_expired'}   # the snapshot expiry


def test_an_aware_non_utc_clock_compares_as_the_same_instant():
    plus3 = timezone(timedelta(hours=3))
    expires = POOL_NOW + timedelta(seconds=1)

    def decide(observation):
        return _verified_decision(expires_at_utc=expires.isoformat())
    early = collector.apply_pool_binding(OBSERVATION, decide, clock=_fixed(POOL_NOW.astimezone(plus3)))
    assert early['account_pool'] == 'codex-plus-weekly'   # 01:00+03:00 is 22:00Z, before the expiry
    late = collector.apply_pool_binding(OBSERVATION, decide, clock=_fixed(expires.astimezone(plus3)))
    assert late['pool_binding'] == {'state': 'unverified', 'reason': 'decision_expired'}   # the same instant


class _Offsetless(tzinfo):
    def utcoffset(self, dt):
        return None   # "aware-looking" but offsetless: astimezone would read it as LOCAL time


class _BrokenZone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError('zone database unavailable')


class _IntOffset(tzinfo):
    def utcoffset(self, dt):
        return 3600   # datetime.utcoffset() itself raises TypeError for a non-timedelta


class _TimedeltaSubclass(timedelta):
    pass


class _SubclassOffset(tzinfo):
    def utcoffset(self, dt):
        return _TimedeltaSubclass(0)   # not exactly a timedelta


class _Moment(datetime):
    """A datetime subclass: it could override utcoffset/astimezone, so it is never a clock value."""


class _StatefulOffset(tzinfo):
    """An offset on the first read, None afterwards: a second read would fall back to LOCAL time."""

    def __init__(self, first):
        self.first, self.calls = first, 0

    def utcoffset(self, dt):
        self.calls += 1
        return self.first if self.calls == 1 else None


def _failing_clock():
    raise OSError('clock source down')


@pytest.mark.parametrize('clock', [
    _fixed(datetime(2026, 9, 29, 22)),                          # naive
    _fixed(datetime(2026, 9, 29, 22, tzinfo=_Offsetless())),
    _fixed(datetime(2026, 9, 29, 22, tzinfo=_BrokenZone())),
    _fixed(datetime(2026, 9, 29, 22, tzinfo=_IntOffset())),
    _fixed(datetime(2026, 9, 29, 22, tzinfo=tzinfo())),        # the base tzinfo raises NotImplementedError
    _fixed(datetime(2026, 9, 29, 22, tzinfo=_SubclassOffset())),
    _fixed(_Moment(2026, 9, 29, 22, tzinfo=timezone.utc)),     # a subclass, even with a real UTC zone
    _fixed(datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))),   # not representable in UTC
    _fixed('2026-09-29T22:00:00Z'), _fixed(None), _failing_clock,
], ids=['naive', 'offsetless', 'broken', 'int_offset', 'not_implemented', 'timedelta_subclass', 'subclass',
        'unrepresentable', 'text', 'none', 'failing'])
def test_an_invalid_clock_sample_refuses_with_input_error_never_type_error(clock):
    with pytest.raises(InputError):   # a TypeError (or any other type) would fail this test
        collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(), clock=clock)


@pytest.mark.parametrize('first,applied', [
    (timedelta(0), True),                        # read once: UTC 22:00, before the 23:00 expiry
    (timedelta(hours=-2), False),                # read once: 24:00Z, after it (never local time)
])
def test_the_clock_offset_is_read_exactly_once_and_never_falls_back_to_local_time(first, applied):
    zone = _StatefulOffset(first)
    row = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(),
                                       clock=_fixed(datetime(2026, 9, 29, 22, tzinfo=zone)))
    assert zone.calls == 1
    assert (row['account_pool'] == 'codex-plus-weekly') is applied


def test_the_collector_and_pool_binding_clock_normalizers_agree():
    from tools import bridge_pool_binding as binding
    corpus = [POOL_NOW, POOL_NOW.astimezone(timezone(timedelta(hours=-7))), datetime(2026, 9, 29, 22),
              datetime(2026, 9, 29, 22, tzinfo=_Offsetless()), datetime(2026, 9, 29, 22, tzinfo=_BrokenZone()),
              datetime(2026, 9, 29, 22, tzinfo=_IntOffset()), datetime(2026, 9, 29, 22, tzinfo=tzinfo()),
              datetime(2026, 9, 29, 22, tzinfo=_SubclassOffset()), _Moment(2026, 9, 29, 22, tzinfo=timezone.utc),
              datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))), '2026-09-29T22:00:00Z', None]
    for moment in corpus:
        expected = binding._aware_utc(moment)
        if expected is None:
            with pytest.raises(InputError):
                collector._aware_utc(moment)
        else:
            assert collector._aware_utc(moment) == expected and expected.tzinfo is timezone.utc
    assert binding._aware_utc(POOL_NOW.astimezone(timezone(timedelta(hours=-7)))) == POOL_NOW   # twin


@pytest.mark.parametrize('clock', [POOL_NOW, 'utcnow', 0])
def test_a_non_callable_clock_is_refused_before_the_binder_runs(clock):
    calls = []
    with pytest.raises(InputError, match='callable'):
        collector.apply_pool_binding(OBSERVATION, lambda o: calls.append(o) or _verified_decision(), clock=clock)
    assert calls == []


def test_only_a_code_shaped_refusal_reason_is_kept():
    row = collector.apply_pool_binding(OBSERVATION, lambda o: {'reason': 'mail ops@example.test'}, clock=_fixed())
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binding_refused'}
    row = collector.apply_pool_binding(OBSERVATION, lambda o: {'reason': 'receipt_expired'}, clock=_fixed())
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'receipt_expired'}  # success twin


def test_a_receipt_for_another_auth_context_does_not_bind(fixed_collection_time):
    observation = _collect(_pool_binder('e' * 64))
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


def test_a_binding_expires_with_the_pool_verification_when_that_ends_first(tmp_path, fixed_collection_time):
    fresh_until = POOL_NOW + timedelta(minutes=30)
    measured = (fresh_until - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    observation = _collect(_pool_binder(POOL_SUBJECT, measured_at=measured, ttl_seconds=3600))
    assert observation['account_pool'] == 'codex-plus-weekly'
    # The receipt runs to POOL_NOW + 1 h; the stored binding ends with the pool TTL instead.
    assert observation['pool_binding']['expires_at_utc'] == fresh_until.isoformat()
    path = tmp_path / 'observations.db'
    save_observation(path, observation)
    live = status(path, now=fresh_until - timedelta(seconds=1))['observations'][0]
    assert live['account_pool'] == 'codex-plus-weekly'
    gone = status(path, now=fresh_until)['observations'][0]
    assert gone['account_pool'] is None and gone['pool_identity_state'] == 'binding_expired'


def test_a_stale_registry_pool_never_binds_and_the_missing_verifier_still_refuses(fixed_collection_time):
    stale = _collect(_pool_binder(POOL_SUBJECT, measured_at='2026-01-01', ttl_seconds=3600))
    assert stale['account_pool'] is None and stale['pool_binding']['reason'] == 'pool_state_stale'
    missing = _collect(_pool_binder(POOL_SUBJECT, verifier=None))
    assert missing['account_pool'] is None and missing['pool_binding']['reason'] == 'verifier_missing'

# -- Tools 7e: plain-data entry snapshots before the binder and the clock; an exact-dict decision --

class _AliasingDict(dict):
    def __deepcopy__(self, memo):
        return self


@pytest.mark.parametrize('observation', [
    _AliasingDict(OBSERVATION),                                   # a subclass with an aliasing copy hook
    dict(OBSERVATION, payload={'bad': {1: 'non-str key'}}),
    dict(OBSERVATION, payload={'bad': float('nan')}),
    dict(OBSERVATION, payload={'bad': object()}),
], ids=['aliasing_subclass', 'int_key', 'nan', 'custom_object'])
def test_a_non_plain_observation_is_refused_before_the_binder_or_clock_runs(observation):
    calls = []
    with pytest.raises(InputError, match='plain-data'):
        collector.apply_pool_binding(observation, lambda o: calls.append('binder') or _verified_decision(),
                                     clock=lambda: calls.append('clock') or POOL_NOW)
    assert calls == []


class _DecisionDict(dict):
    def get(self, key, default=None):   # a subclass could answer differently on each read
        return 'unverified' if key == 'pool_identity_state' else super().get(key, default)


def test_only_an_exact_dict_decision_can_bind():
    row = collector.apply_pool_binding(OBSERVATION, lambda o: _DecisionDict(_verified_decision()), clock=_fixed())
    assert row['account_pool'] is None and row['pool_binding'] == {'state': 'unverified', 'reason': 'binding_refused'}
    good = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(), clock=_fixed())
    assert good['account_pool'] == 'codex-plus-weekly'             # success twin: an exact dict binds


def test_the_collector_and_pool_binding_plain_copies_agree():
    from tools import bridge_pool_binding as binding
    for value in [{'a': [1, 2.5, None, True, ('t', {'k': 'v'})]}, [], 'x', 3, None]:
        assert collector._plain_copy(value) == binding.plain_snapshot(value) == value
    for value in [_AliasingDict(a=1), {1: 'x'}, float('nan'), object(), {'deep': float('inf')}]:
        with pytest.raises(InputError):
            collector._plain_copy(value)
        with pytest.raises(binding.Refused):
            binding.plain_snapshot(value)


# -- Lead 0468acc6 (Tools f559): an exact str schema and pool_identity_state BEFORE either comparison --
# A decision value's own __eq__ is caller code. Each tag below counts and raises on any equality
# call, so a comparison made before the exact-type guard shows up as a 'hook' entry or a raise.

class _HookRan(Exception):
    pass


def _equality_tags(calls):
    class EqualityTag:                      # a custom object: never a str
        __hash__ = object.__hash__

        def __eq__(self, other):
            calls.append('hook')
            raise _HookRan('__eq__')

        def __ne__(self, other):
            calls.append('hook')
            raise _HookRan('__ne__')

    class StrTag(str):                      # a str subclass carrying the exact valid text
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append('hook')
            raise _HookRan('__eq__')

        def __ne__(self, other):
            calls.append('hook')
            raise _HookRan('__ne__')

    return EqualityTag, StrTag


@pytest.mark.parametrize('kind', ['custom_object', 'str_subclass'])
@pytest.mark.parametrize('field,valid', [('schema', 'wd.pool-binding-decision.v1'),
                                         ('pool_identity_state', 'verified_binding')])
def test_a_non_str_decision_tag_is_refused_before_any_equality_hook_runs(field, valid, kind):
    calls = []
    equality_tag, str_tag = _equality_tags(calls)
    tag = equality_tag() if kind == 'custom_object' else str_tag(valid)
    row = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(**{field: tag}),
                                       clock=lambda: calls.append('clock') or POOL_NOW)
    # Zero hook calls. The apply boundary still samples the clock exactly once (the 90a contract:
    # with a binder the clock is always read once, after the binder, even for a refusal).
    assert calls == ['clock']
    assert row['account_pool'] is None and 'pool_identity_state' not in row
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binding_refused'}
    good = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(**{field: valid}),
                                        clock=lambda: calls.append('clock') or POOL_NOW)
    assert calls == ['clock', 'clock']      # exact-string positive twin: binds, and still no hook ran
    assert good['account_pool'] == 'codex-plus-weekly' and good['pool_identity_state'] == 'verified_binding'
    assert type(good['pool_identity_state']) is str


# -- Lead 7a0871c1: every decision KEY is an exact str BEFORE any lookup (authored, NOT run) ----------------
# A key object whose hash equals a field name's is met first by dict.get('schema') and would run its own
# __eq__ there. It is disarmed while the fixture builds the decision and armed before the binder returns it.

def _colliding_key(calls, name):
    class CollidingKey:                     # a custom key object: never a str, with a field name's hash
        armed = False

        def __hash__(self):
            return hash(name)

        def __eq__(self, other):
            if self.armed:
                calls.append('key_hook')
                raise _HookRan('key __eq__')
            return False

    return CollidingKey()


@pytest.mark.parametrize('kind', ['colliding_key', 'int_key'])
def test_a_non_str_decision_key_binds_nothing_and_runs_no_key_hook(kind):
    calls = []
    key = _colliding_key(calls, 'schema') if kind == 'colliding_key' else 7
    decision = {key: 'extra', **_verified_decision()}   # inserted FIRST, so a lookup of 'schema' meets it first
    if kind == 'colliding_key':
        key.armed = True
    row = collector.apply_pool_binding(OBSERVATION, lambda o: decision,
                                       clock=lambda: calls.append('clock') or POOL_NOW)
    assert calls == ['clock']               # zero key hooks; the apply boundary still samples the clock once
    assert row['account_pool'] is None and 'pool_identity_state' not in row
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binding_refused'}
    good = collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(extra='ignored'),
                                        clock=lambda: calls.append('clock') or POOL_NOW)
    assert calls == ['clock', 'clock']      # exact-str-key positive twin: an unused extra str key still binds
    assert good['account_pool'] == 'codex-plus-weekly' and good['pool_identity_state'] == 'verified_binding'


# -- Lead 45b66035 (RCO2 f766 N2): a failure diagnostic never runs a hook of the exception's class ---------

class _NameHookRan(Exception):
    pass


def _hostile_exception_classes(calls):
    class Meta(type):
        @property
        def __name__(cls):                          # a metaclass __name__: caller code on every lookup
            calls.append('name_hook')
            raise _NameHookRan('metaclass __name__')

    class ViaMetaclass(Exception, metaclass=Meta):
        pass

    class NameStr(str):                             # a class name stored as a str subclass
        def __add__(self, other):
            calls.append('name_hook')
            raise _NameHookRan('__add__')

        def __radd__(self, other):
            calls.append('name_hook')
            raise _NameHookRan('__radd__')

    class ViaNameStr(Exception):
        pass
    ViaNameStr.__name__ = NameStr('ViaNameStr')     # type's own setter accepts a str subclass
    return {'metaclass': (ViaMetaclass, 'ViaMetaclass'), 'str_subclass_name': (ViaNameStr, 'ViaNameStr')}


@pytest.mark.parametrize('variant', ['metaclass', 'str_subclass_name'])
def test_a_failure_diagnostic_never_runs_a_hook_of_the_exception_class(variant):
    """At f766 the old type(exc).__name__ ran the metaclass property, and '+' ran the str subclass's
    __radd__, raising out of the refusal instead of returning it. Here neither hook runs."""
    calls = []
    hostile, name = _hostile_exception_classes(calls)[variant]

    def broken_binder(observation):
        raise hostile('verifier backend down: secret-token')
    row = collector.apply_pool_binding(OBSERVATION, broken_binder, clock=lambda: calls.append('clock') or POOL_NOW)
    assert calls == ['clock']                        # one apply-boundary clock sample and zero name hooks
    assert row['account_pool'] is None and 'secret-token' not in json.dumps(row)
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binder_failed:' + name}

    def broken_clock():
        calls.append('clock')
        raise hostile('clock down')
    with pytest.raises(InputError, match='clock failed: ' + name + '$'):
        collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(), clock=broken_clock)
    assert calls == ['clock', 'clock']               # still no name hook


def test_an_ordinary_failure_diagnostic_keeps_its_class_name():
    """The ordinary twin: the same contract as before (binder_failed:<Name>; clock failed: <Name>)."""
    def binder(observation):
        raise KeyError('x')

    def clock():
        raise OSError('down')
    row = collector.apply_pool_binding(OBSERVATION, binder, clock=_fixed())
    assert row['pool_binding'] == {'state': 'unverified', 'reason': 'binder_failed:KeyError'}
    with pytest.raises(InputError, match='clock failed: OSError$'):
        collector.apply_pool_binding(OBSERVATION, lambda o: _verified_decision(), clock=clock)
    assert collector._exception_name(ValueError('v')) == 'ValueError'


# -- Lead 5aa78f56 (RCO1 a39cb2aa N1/N2): status normalizes the caller's now ONCE, up front (authored, NOT run) --

class _RaisingZone(tzinfo):
    """A tzinfo whose utcoffset raises the given exception instance."""

    def __init__(self, error):
        self.error = error

    def utcoffset(self, dt):
        raise self.error


def _binding_store(tmp_path):
    path = tmp_path / 'observations.db'
    for context, expires in (('live', POOL_NOW + timedelta(hours=1)), ('gone', POOL_NOW - timedelta(seconds=1))):
        save_observation(path, dict(provider='codex', observed_at=POOL_NOW.isoformat(), auth_context_id=context,
                                    account_pool='codex-plus-weekly', pool_identity_state='verified_binding',
                                    pool_binding={'expires_at_utc': expires.isoformat()}))
    return path


def _plain_store(tmp_path):
    path = tmp_path / 'plain.db'
    save_observation(path, dict(provider='codex', observed_at=POOL_NOW.isoformat(), auth_context_id='x'))
    return path


@pytest.mark.parametrize('now', [POOL_NOW, POOL_NOW.astimezone(timezone(timedelta(hours=3))),
                                 datetime(2026, 9, 30, 1, 0, tzinfo=_StatefulOffset(timedelta(hours=3)))],
                         ids=['utc', 'plus3', 'stateful_plus3'])
def test_status_judges_every_row_at_the_same_utc_instant_for_any_aware_now(tmp_path, now):
    path = _binding_store(tmp_path)
    before = path.read_bytes()
    report = status(path, now=now)
    rows = {row['auth_context_id']: row for row in report['observations']}
    assert rows['live']['pool_identity_state'] == 'verified_binding'
    assert rows['live']['account_pool'] == 'codex-plus-weekly' and rows['live']['observation_age_seconds'] == 0
    assert rows['gone']['pool_identity_state'] == 'binding_expired' and rows['gone']['account_pool'] is None
    assert report['observed_at'] == POOL_NOW.isoformat()                            # normalized to UTC
    assert path.read_bytes() == before                                              # read-only either way
    if isinstance(now.tzinfo, _StatefulOffset):
        assert now.tzinfo.calls == 1                         # one offset read: a second would have given None


@pytest.mark.parametrize('now', [datetime(2026, 9, 29, 22), datetime(2026, 9, 29, 22, tzinfo=_Offsetless()),
                                 datetime(2026, 9, 29, 22, tzinfo=_BrokenZone()),
                                 datetime(2026, 9, 29, 22, tzinfo=_RaisingZone(NotImplementedError())),
                                 datetime(2026, 9, 29, 22, tzinfo=_IntOffset()),
                                 datetime(2026, 9, 29, 22, tzinfo=_SubclassOffset()),
                                 _Moment(2026, 9, 29, 22, tzinfo=timezone.utc)],
                         ids=['naive', 'offsetless', 'broken', 'not_implemented', 'int_offset', 'subclass_offset',
                              'datetime_subclass'])
@pytest.mark.parametrize('store', [_binding_store, _plain_store], ids=['binding_store', 'plain_store'])
def test_status_refuses_an_unjudgeable_now_before_any_arithmetic(tmp_path, now, store):
    path = store(tmp_path)
    before = path.read_bytes()
    with pytest.raises(InputError, match='status needs now as exactly a timezone-aware datetime'):
        status(path, now=now)                        # was a raw TypeError/NotImplementedError/RuntimeError, or accepted
    assert path.read_bytes() == before


def test_a_keyboard_interrupt_from_the_callers_tzinfo_still_propagates(tmp_path):
    path = _binding_store(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        status(path, now=datetime(2026, 9, 29, 22, tzinfo=_RaisingZone(KeyboardInterrupt())))


def test_status_normalizes_now_exactly_once_per_call(tmp_path, monkeypatch):
    bound, plain = _binding_store(tmp_path), _plain_store(tmp_path)
    calls, real = [], collector._aware_utc
    monkeypatch.setattr(collector, '_aware_utc', lambda moment: calls.append(moment) or real(moment))
    status(bound, now=POOL_NOW)                                                     # two stored bindings
    status(plain, now=POOL_NOW)                                                     # no binding at all
    assert calls == [POOL_NOW, POOL_NOW]                                            # exactly once per call, always


def test_the_pool_provenance_kinds_equal_the_registry_measuring_kinds():
    from tools.wd_model_registry import MEASURING_KINDS   # test-only: the collector never imports it
    assert type(collector.POOL_PROVENANCE_KINDS) is tuple and collector.POOL_PROVENANCE_KINDS == MEASURING_KINDS
    source = Path(collector.__file__).read_text(encoding='utf-8')
    assert source.count("'operator_reading'") == 1                                  # no second inline copy
