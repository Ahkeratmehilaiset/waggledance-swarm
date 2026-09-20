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
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
from typing import Any

try:
    from tools.bridge_capacity_advisor import InputError, _load, _dict, _text, _time, _number, REACHED_TYPES
except ModuleNotFoundError:
    from bridge_capacity_advisor import InputError, _load, _dict, _text, _time, _number, REACHED_TYPES

MAX_RESPONSE = 2 * 1024 * 1024
READ_METHODS = frozenset({'account/read', 'account/rateLimits/read', 'model/list'})


class MetadataFailure(InputError):
    def __init__(self, state: str):
        super().__init__('metadata unavailable')
        self.state = state


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
        keys = (percent, reset, 'windowDurationMins') if provider == 'codex' else (percent, reset)
        return {key: value.get(key) for key in keys}

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
                    if 'error' in reply:
                        code = _dict(reply['error']).get('code')
                        # RPC failures include unsupported methods and bad parameters;
                        # an arbitrary numeric code does not establish a transport fault.
                        raise MetadataFailure({401: 'auth_required', 429: 'rate_limited'}.get(code, 'unknown')
                                              if type(code) is int else 'unknown')
                    if not isinstance(reply.get('result'), dict):
                        raise InputError('metadata request failed')
                    return reply['result']
            raise InputError('metadata notification bound exceeded')

        return await asyncio.wait_for(receive(), self.timeout)


async def collect_codex(client: MetadataClient, auth_context: str) -> dict:
    started = utcnow()
    before = await client.request('account/read', {'refreshToken': False})
    if before.get('account') is None:
        raise MetadataFailure('auth_required')
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
            'quota_freshness_basis': 'provider_metadata_request',
            'execution_allowed': False}


def collect_claude(payload: dict) -> dict:
    session = payload.get('session_id')
    if not _text(session):
        raise InputError('statusline session identity missing')
    return {'schema': 'wd.capacity-observation.v1', 'provider': 'claude',
            'source_ref': 'claude:statusline', 'observed_at': utcnow(),
            'native_thread_id': session, 'account_pool': None,
            'pool_identity_state': 'unknown', 'execution_allowed': False,
            'quota_freshness_basis': 'statusline_callback_provider_timestamp_unknown',
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


def record_claude_hook(path: Path, payload: dict) -> dict:
    """Native hook metadata only. Never save prompts, error text or transcripts.

    A statusline callback cannot clear authentication failures. Only a normal
    Stop (as opposed to StopFailure) observes a completed provider response.
    This records past observations, not a promise that the next turn will work.
    """
    session, event = payload.get('session_id'), payload.get('hook_event_name')
    if not _text(session) or len(session) > 128 or event not in ('Stop', 'StopFailure', 'UserPromptSubmit'):
        raise InputError('unsupported native hook identity/event')
    error = payload.get('error') if event == 'StopFailure' else None
    if not isinstance(error, str):
        error = None
    error_state = {'authentication_failed': 'auth_required', 'cloud_credential_error': 'auth_required',
                   'oauth_org_not_allowed': 'access_denied', 'account_on_hold': 'account_on_hold',
                   'billing_error': 'billing_error', 'rate_limit': 'rate_limited',
                   'overloaded': 'transport_error', 'server_error': 'transport_error'}.get(error, 'unknown')
    with sqlite3.connect(path, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS activity (session TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT data FROM activity WHERE session=?', (session,)).fetchone()
        row = json.loads(previous[0]) if previous else dict(
            provider='claude', native_thread_id=session, auth_state='unknown', availability_state='unknown',
            alert_id=None, first_error_at=None, last_successful_turn_at=None)
        stamp = utcnow()
        if event == 'StopFailure':
            if row.get('availability_state') != error_state or not row.get('alert_id'):
                row.update(alert_id=uuid.uuid4().hex, first_error_at=stamp)
            row.update(availability_state=error_state, activity_state='blocked', error_type=error if isinstance(error, str) and error in {
                'authentication_failed', 'cloud_credential_error', 'oauth_org_not_allowed', 'account_on_hold',
                'billing_error', 'rate_limit', 'overloaded', 'server_error', 'invalid_request', 'model_not_found',
                'max_output_tokens', 'unknown'} else 'unknown')
            if error_state == 'auth_required':
                row['auth_state'] = 'auth_required'
        elif event == 'Stop':
            row.update(availability_state='successful_turn_observed', auth_state='authenticated_at_successful_turn',
                       activity_state='idle_observed', last_successful_turn_at=stamp, alert_id=None,
                       first_error_at=None, error_type=None)
        else:
            row['activity_state'] = 'work_requested'  # Not actual inference start.
        row.update(observed_at=stamp, source='claude_native_hook', hook_event_name=event,
                   execution_allowed=False, automatic_retry_allowed=False, next_turn_success_verified=False)
        db.execute('INSERT OR REPLACE INTO activity VALUES (?,?,?)',
                   (session, json.dumps(row, allow_nan=False), datetime.now(timezone.utc).timestamp()))
        db.execute('DELETE FROM activity WHERE session NOT IN (SELECT session FROM activity ORDER BY updated DESC LIMIT 256)')
    return row


def quota_details(row: dict, now: datetime) -> tuple[str, list]:
    """Describe the observed provider windows without assigning them to agents."""
    payload, windows, unknown, exhausted = _dict(row.get('payload')), [], False, False
    if row['provider'] == 'codex':
        buckets = (_dict(payload['rateLimitsByLimitId']) if 'rateLimitsByLimitId' in payload else
                   {_dict(payload.get('rateLimits')).get('limitId'): payload.get('rateLimits')})
        for limit_id, raw in buckets.items():
            bucket = _dict(raw)
            if not _text(limit_id) or bucket.get('limitId') != limit_id:
                unknown = True
                continue
            reached = bucket.get('rateLimitReachedType')
            if reached is not None:
                valid = isinstance(reached, str) and reached in REACHED_TYPES
                exhausted |= valid
                unknown |= not valid
            for name in ('primary', 'secondary'):
                value = bucket.get(name)
                if value is None:
                    continue
                value = _dict(value)
                duration = value.get('windowDurationMins')
                duration_valid = type(duration) is int and 0 < duration <= 2147483647
                windows.append(dict(limit_id=limit_id, name=name, used_percent=value.get('usedPercent'),
                                    resets_at=value.get('resetsAt'), window_duration_minutes=duration if duration_valid else None,
                                    window_duration_state='valid' if duration_valid else 'unknown'))
    else:
        for name, value in _dict(payload.get('rate_limits')).items():
            value = _dict(value)
            windows.append(dict(limit_id='claude', name=name, used_percent=value.get('used_percentage'),
                                resets_at=value.get('resets_at'), window_duration_minutes=None,
                                window_duration_state='not_supplied'))
    for value in windows:
        used, reset = value['used_percent'], value['resets_at']
        valid = _number(used) and used >= 0 and _number(reset) and now.timestamp() < reset <= 253402300799
        unknown |= not valid
        exhausted |= valid and used >= 100
    if row['freshness'] != 'fresh':
        return 'unknown', windows
    return ('exhausted' if exhausted else 'unknown' if unknown or not windows else 'observed_headroom'), windows


def status(path: Path, *, now: datetime | None = None) -> dict:
    """Read-only recent observations; session/auth context is not a quota identity."""
    now = now or datetime.now(timezone.utc)
    # Our producer uses rollback journals. SQLite's read-only WAL connections
    # can create/update shared-memory sidecars; never silently do that to a
    # foreign database, or ignore its WAL by claiming an immutable snapshot.
    with path.open('rb') as source:
        header = source.read(20)
    if header[:16] == b'SQLite format 3\x00' and 2 in header[18:20]:
        raise InputError('WAL status is unsupported without an existing read-only snapshot')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        db.execute('BEGIN')  # One read snapshot for tables, budget and observations.
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables.intersection({'observations', 'activity', 'poll_budget'}):
            raise InputError('not an observer store')
        rows = db.execute('SELECT sequence,data FROM observations ORDER BY sequence DESC LIMIT 2048').fetchall() if 'observations' in tables else []
        poll = db.execute('SELECT started FROM poll_budget WHERE id=1').fetchone() if 'poll_budget' in tables else None
        activity = [json.loads(r[0]) for r in db.execute('SELECT data FROM activity ORDER BY updated DESC LIMIT 256')] if 'activity' in tables else []
    latest, failed, newest_provider = {}, {}, {}
    for sequence, raw in rows:
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get('provider') not in ('codex', 'claude'):
            raise InputError('invalid observation row')
        provider = row['provider']
        newest_provider.setdefault(provider, sequence)
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
        if provider == 'claude':
            row['freshness'] = 'provider_timestamp_unknown'
        if failed.get(provider, 0) > sequence:
            row['freshness'] = 'superseded_by_collection_failure'
        row['sequence'] = sequence
        row['observation_age_seconds'] = age if age >= 0 else None
        row['auth_state'] = ('authenticated_at_metadata_observation' if provider == 'codex' and row['freshness'] == 'fresh' else 'unknown')
        row['quota_state'], row['quota_windows'] = quota_details(row, now)
        row['agent_activity_state'] = 'unknown'  # Process/callback existence is not progress.
        latest[key] = row
    last_attempt, next_poll = None, None
    if poll and _number(poll[0]) and 0 <= poll[0] <= 253402300499:
        last_attempt = datetime.fromtimestamp(poll[0], timezone.utc).isoformat()
        next_poll = datetime.fromtimestamp(poll[0] + 300, timezone.utc).isoformat()
    newest = {provider: json.loads(next(raw for seq, raw in rows if seq == sequence))
              for provider, sequence in newest_provider.items()}
    if poll and 'codex' not in newest:
        newest['codex'] = {'reason': 'poll_without_observation'}
    collection = {provider: dict(
        last_attempt=last_attempt if provider == 'codex' else None,
        next_eligible_poll=next_poll if provider == 'codex' else None,
        last_success=next((row['observed_at'] for row in latest.values() if row['provider'] == provider and row.get('observed_at')), None),
        collection_state=('failed' if row.get('reason') == 'collection_failed' else
                          'pending_or_interrupted' if row.get('reason') == 'poll_without_observation' else 'observed'),
        availability_state=row.get('availability_state', 'unknown'),
        auth_state='auth_required' if row.get('availability_state') == 'auth_required' else 'unknown',
        provider_budget_seconds=300 if provider == 'codex' else None,
        freshness_ttl_seconds=300 if provider == 'codex' else None,
        queue_replay_allowed=False) for provider, row in newest.items()}
    for row in activity:
        if not isinstance(row, dict) or row.get('provider') != 'claude':
            raise InputError('invalid activity row')
        observed = _time(row.get('observed_at'))
        row['observation_age_seconds'] = (now - observed).total_seconds() if observed and now >= observed else None
    return {'schema': 'wd.capacity-status.v1', 'observed_at': now.isoformat(),
            'execution_allowed': False, 'observations': list(latest.values()),
            'collection': collection, 'native_activity': activity,
            'alerts': [dict(alert_id=row['alert_id'], provider=row['provider'], native_thread_id=row['native_thread_id'],
                            state=row['availability_state'], first_observed_at=row.get('first_error_at'))
                       for row in activity if row.get('alert_id')],
            'failed_providers': [provider for provider, sequence in failed.items()
                                 if newest_provider[provider] == sequence],
            'limitations': ['quota_pool_mapping_unverified', 'no_live_model_switch',
                            'statusline_does_not_refresh_idle_provider']}


def reserve_poll(path: Path, *, now: datetime | None = None) -> str | None:
    """At most one metadata attempt per five minutes, including failed attempts.

    A backward clock refuses new attempts until the saved time is reached. The
    45-second whole-call timeout is less than the minimum polling interval.
    """
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(path, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS poll_budget '
                   '(id INTEGER PRIMARY KEY CHECK(id=1), started REAL, token TEXT)')
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT started FROM poll_budget WHERE id=1').fetchone()
        if previous and now.timestamp() - previous[0] < 300:
            return None
        token = uuid.uuid4().hex
        db.execute('INSERT OR REPLACE INTO poll_budget VALUES (1,?,?)', (now.timestamp(), token))
        return token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['codex', 'claude'])
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--scheduled', action='store_true', help='Budgeted Codex metadata poll, safe to repeat.')
    parser.add_argument('--statusline', action='store_true', help='Compact Claude statusline output after ingestion.')
    parser.add_argument('--claude-hook', action='store_true', help='Record native lifecycle metadata; no model decision or retry.')
    parser.add_argument('--emit-alert', action='store_true', help='Return sanitized native failure metadata to the explicitly enabled bridge-notice hook.')
    parser.add_argument('--codex-executable')
    parser.add_argument('--store', type=Path, required=True)
    args = parser.parse_args(argv)
    # A status failure is not a collection attempt. Keep both exits outside every
    # path that reserves a poll, starts a provider, or saves an observation.
    if args.status:
        try:
            result = status(args.store)
            encoded = json.dumps(result, allow_nan=False)
        except (InputError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            print(json.dumps({'schema': 'wd.capacity-status.v1', 'state': 'unknown',
                              'reason': 'status_unavailable', 'execution_allowed': False,
                              'observed_at': utcnow(), 'observations': []}))
            return 2
        print(encoded)
        return 0
    if args.claude_hook:
        # A telemetry failure must not make a Stop hook block or prompt a model.
        try:
            observation = record_claude_hook(args.store, _load(sys.stdin.buffer))
            if args.emit_alert:
                print(json.dumps(observation if observation.get('hook_event_name') == 'StopFailure'
                                 and observation.get('alert_id') else None, allow_nan=False))
        except (InputError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            print('WD native lifecycle observation unavailable', file=sys.stderr)
        return 0
    try:
        if args.provider is None:
            raise InputError('provider required for collection')
        if args.statusline and args.provider != 'claude':
            raise InputError('statusline ingestion requires Claude')
        if args.scheduled:
            if args.provider != 'codex':
                raise InputError('scheduled Claude generation probes are not supported')
            if reserve_poll(args.store) is None:
                print(json.dumps({'state': 'not_due', 'execution_allowed': False}))
                return 0
        if args.provider == 'codex':
            async def run():
                async with MetadataClient(args.codex_executable or '') as client:
                    context = os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))
                    return await collect_codex(client, str(Path(context).resolve()))
            observation = asyncio.run(asyncio.wait_for(run(), timeout=45))
        else:
            observation = collect_claude(_load(sys.stdin.buffer))
        code = 0
    except (InputError, OSError, ValueError, sqlite3.Error, asyncio.TimeoutError) as exc:
        observation = {'schema': 'wd.capacity-observation.v1', 'provider': args.provider,
                       'observed_at': utcnow(), 'state': 'unknown',
                       'reason': 'collection_failed', 'account_pool': None,
                       'availability_state': exc.state if isinstance(exc, MetadataFailure) else 'unknown',
                       'execution_allowed': False}
        code = 2
    try:
        save_observation(args.store, observation)
    except (sqlite3.Error, OSError):
        print(json.dumps({'state': 'unknown', 'reason': 'observation_store_unavailable',
                          'execution_allowed': False}))
        return 2
    if args.statusline:
        model = observation.get('model')
        effort = observation.get('effort')
        # JSON strings avoid terminal-control sequences from arbitrary metadata.
        print('WD capacity | model=' + json.dumps(model, ensure_ascii=True) +
              ' effort=' + json.dumps(effort, ensure_ascii=True) +
              ' | quota age unknown; observation saved' + native_alert_summary(args.store, observation.get('native_thread_id')))
    else:
        print(json.dumps(observation, allow_nan=False))
    return code


def native_alert_summary(path: Path, session: str | None) -> str:
    """A callback never clears a latched failure or proves readiness."""
    try:
        value = status(path)
        alert = next((r for r in value['alerts'] if r['native_thread_id'] == session), None)
        return (' | blocked=' + json.dumps(alert['state']) + ' alert=' + json.dumps(alert['alert_id'])) if alert else ''
    except (InputError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return ' | lifecycle status unknown'


if __name__ == '__main__':
    raise SystemExit(main())
