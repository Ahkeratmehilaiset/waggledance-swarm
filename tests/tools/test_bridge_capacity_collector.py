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
