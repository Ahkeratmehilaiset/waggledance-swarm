#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bounded metadata collection. No turns, login, model switches or paid fallback.

Codex uses a private stdio app-server, never attaches to an existing terminal.
Its auth context is NOT a verified quota pool. Account/workspace-to-lane mapping
requires separate evidence; no email, credential, prompt or transcript is saved.
Claude accepts the documented statusline JSON on stdin and reports absent data
as unknown. It cannot refresh an idle Claude subscription via a model call.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

try:
    from tools.bridge_capacity_advisor import InputError, _load, _dict, _text
except ModuleNotFoundError:
    from bridge_capacity_advisor import InputError, _load, _dict, _text

MAX_RESPONSE = 2 * 1024 * 1024
READ_METHODS = frozenset({'account/read', 'account/rateLimits/read', 'model/list'})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def quota_payload(payload: dict, provider: str) -> dict:
    """Only documented quota fields; discard unknown/sensitive provider extras."""
    def window(value: Any, percent: str, reset: str) -> Any:
        if not isinstance(value, dict):
            return None
        # Preserve malformed types for the fail-closed normalizer; do not coerce.
        return {key: value.get(key) for key in (percent, reset)}

    def bucket(value: Any) -> dict:
        value = _dict(value)
        return {'limitId': value.get('limitId'),
                'rateLimitReachedType': value.get('rateLimitReachedType'),
                **{k: window(value.get(k), 'usedPercent', 'resetsAt')
                   for k in ('primary', 'secondary')}}

    if provider == 'codex':
        if 'rateLimitsByLimitId' in payload:
            return {'rateLimitsByLimitId': {
                k: bucket(v) for k, v in _dict(payload['rateLimitsByLimitId']).items()
                if _text(k)}}
        return {'rateLimits': bucket(payload.get('rateLimits'))}
    return {'rate_limits': {
        k: window(v, 'used_percentage', 'resets_at')
        for k, v in _dict(payload.get('rate_limits')).items()}}


class MetadataClient:
    """Private child with a strict read-method allowlist and whole-call deadline."""
    def __init__(self, executable: str, timeout: float = 30):
        self.executable = executable
        self.timeout = timeout
        self.process = None
        self.sequence = 0
        self.bytes_read = 0

    async def __aenter__(self):
        path = Path(self.executable)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() in {'.cmd', '.bat', '.ps1'}:
            raise InputError('explicit absolute native Codex executable required')
        self.process = await asyncio.create_subprocess_exec(
            str(path), 'app-server', '--listen', 'stdio://',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_RESPONSE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            await self._request('initialize', {
                'clientInfo': {'name': 'wd_capacity_observer', 'version': '1.0.0'}})
            self.process.stdin.write(b'{"method":"initialized"}\n')
            await self.process.stdin.drain()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        if self.process is not None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                self.process.kill()  # Only the exact child this client created.
                await self.process.wait()

    async def request(self, method: str, params: dict | None = None) -> dict:
        if method not in READ_METHODS:
            raise InputError('metadata collector forbids mutation methods')
        return await self._request(method, params or {})

    async def _request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        self.process.stdin.write((json.dumps(dict(id=request_id, method=method,
                                                  params=params)) + '\n').encode())
        await self.process.stdin.drain()

        async def receive():
            for _ in range(200):
                line = await self.process.stdout.readline()
                self.bytes_read += len(line)
                if not line or self.bytes_read > MAX_RESPONSE:
                    raise InputError('metadata transport closed or exceeded size bound')
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise InputError('invalid metadata response')
                if reply.get('id') == request_id:
                    if 'error' in reply or not isinstance(reply.get('result'), dict):
                        raise InputError('metadata request failed')
                    return reply['result']
            raise InputError('metadata notification bound exceeded')

        return await asyncio.wait_for(receive(), self.timeout)


async def collect_codex(client: MetadataClient, auth_context: str) -> dict:
    started = utcnow()
    before = await client.request('account/read', {'refreshToken': False})
    if _dict(before.get('account')).get('type') != 'chatgpt':
        raise InputError('subscription account not observed; no API fallback')
    limits = await client.request('account/rateLimits/read')
    catalog, cursor, seen = [], None, set()
    for _ in range(10):
        page = await client.request('model/list', {'limit': 100, 'cursor': cursor})
        if not isinstance(page.get('data'), list):
            raise InputError('model catalog unavailable')
        for row in page['data']:
            if isinstance(row, dict):
                catalog.append({k: row.get(k) for k in
                                ('id', 'model', 'supportedReasoningEfforts', 'defaultReasoningEffort')})
        cursor = page.get('nextCursor')
        if cursor is None:
            break
        if not _text(cursor) or cursor in seen:
            raise InputError('invalid catalog pagination')
        seen.add(cursor)
    else:
        raise InputError('catalog page bound exceeded')
    after = await client.request('account/read', {'refreshToken': False})
    if before != after:
        raise InputError('account changed during metadata collection')
    account = _dict(before.get('account'))
    # Hash context + visible account shape, not a credential or asserted account ID.
    context_id = digest([auth_context, account])
    return {'schema': 'wd.capacity-observation.v1', 'provider': 'codex',
            'source_ref': 'codex:account/rateLimits/read', 'collection_started_at': started,
            'observed_at': utcnow(), 'auth_context_id': context_id,
            'account_pool': None, 'pool_identity_state': 'unverified_auth_context',
            'account_type': account['type'], 'plan_type': account.get('planType'),
            'payload': quota_payload(limits, 'codex'), 'catalog': catalog,
            'execution_allowed': False}


def collect_claude(payload: dict) -> dict:
    session = payload.get('session_id')
    if not _text(session):
        raise InputError('statusline session identity missing')
    return {'schema': 'wd.capacity-observation.v1', 'provider': 'claude',
            'source_ref': 'claude:statusline', 'observed_at': utcnow(),
            'native_thread_id': session, 'account_pool': None,
            'pool_identity_state': 'unknown', 'execution_allowed': False,
            'model': _dict(payload.get('model')).get('id'),
            'effort': _dict(payload.get('effort')).get('level'),
            'payload': quota_payload(payload, 'claude'),
            'usage': {k: _dict(payload.get('context_window')).get(k)
                      for k in ('total_input_tokens', 'total_output_tokens')}}


def save_observation(path: Path, observation: dict) -> None:
    """Atomic bounded history; a failed collector records unknown, not stale success."""
    with sqlite3.connect(path, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS observations '
                   '(sequence INTEGER PRIMARY KEY, provider TEXT NOT NULL, data TEXT NOT NULL)')
        db.execute('INSERT INTO observations(provider,data) VALUES (?,?)',
                   (observation['provider'], json.dumps(observation, allow_nan=False)))
        db.execute('DELETE FROM observations WHERE sequence <= '
                   '(SELECT COALESCE(MAX(sequence),0)-2048 FROM observations)')


def status(path: Path, *, now: datetime | None = None) -> dict:
    """Read-only recent observations; session/auth context is not a quota identity."""
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        rows = db.execute('SELECT sequence,data FROM observations ORDER BY sequence DESC LIMIT 2048').fetchall()
    latest, failed = {}, {}
    for sequence, raw in rows:
        row = json.loads(raw)
        provider = row['provider']
        key = (provider, row.get('auth_context_id'), row.get('native_thread_id'))
        if row.get('reason') == 'collection_failed':
            failed.setdefault(provider, sequence)
            continue
        if key in latest:
            continue
        try:
            observed = datetime.fromisoformat(row['observed_at'])
            age = (now - observed).total_seconds()
        except (ValueError, TypeError, KeyError):
            age = -1
        row['freshness'] = 'fresh' if 0 <= age <= 300 else 'unknown_or_stale'
        if failed.get(provider, 0) > sequence:
            row['freshness'] = 'superseded_by_collection_failure'
        row['sequence'] = sequence
        latest[key] = row
    return {'schema': 'wd.capacity-status.v1', 'observed_at': now.isoformat(),
            'execution_allowed': False, 'observations': list(latest.values()),
            'failed_providers': list(failed),
            'limitations': ['quota_pool_mapping_unverified', 'no_live_model_switch',
                            'statusline_does_not_refresh_idle_provider']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['codex', 'claude'])
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--codex-executable')
    parser.add_argument('--store', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.status:
            print(json.dumps(status(args.store), allow_nan=False))
            return 0
        if args.provider is None:
            raise InputError('provider required for collection')
        if args.provider == 'codex':
            async def run():
                async with MetadataClient(args.codex_executable or '') as client:
                    context = os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))
                    return await collect_codex(client, str(Path(context).resolve()))
            observation = asyncio.run(asyncio.wait_for(run(), timeout=45))
        else:
            observation = collect_claude(_load(sys.stdin.buffer))
        code = 0
    except (InputError, OSError, ValueError, asyncio.TimeoutError):
        observation = {'schema': 'wd.capacity-observation.v1', 'provider': args.provider,
                       'observed_at': utcnow(), 'state': 'unknown',
                       'reason': 'collection_failed', 'account_pool': None,
                       'execution_allowed': False}
        code = 2
    try:
        save_observation(args.store, observation)
    except (sqlite3.Error, OSError):
        print(json.dumps({'state': 'unknown', 'reason': 'observation_store_unavailable',
                          'execution_allowed': False}))
        return 2
    print(json.dumps(observation, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
