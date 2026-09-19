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
