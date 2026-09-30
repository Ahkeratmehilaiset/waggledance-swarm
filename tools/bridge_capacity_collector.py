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
import re
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
# F3 (dormant): the decision schema of tools/bridge_pool_binding.py. It is named, not
# imported, so this module's imports and every existing code path stay unchanged.
POOL_DECISION_SCHEMA = 'wd.pool-binding-decision.v1'
POOL_SUBJECT_FIELDS = {'codex': 'auth_context_id', 'claude': 'native_thread_id'}
# The measuring provenance kinds a verified binding may carry: they must EQUAL
# tools.wd_model_registry.MEASURING_KINDS (pinned by a fixture; named, not imported, as above).
POOL_PROVENANCE_KINDS = ('operator_reading', 'local_measurement', 'f21_receipt')


def read_native_codex(home: Path, thread: str, *, now: datetime | None = None) -> dict:
    """Bounded, read-only native telemetry. Never return conversation content.

    Native thread quota observations are attributable to that conversation, not
    proof of account identity or of an independent quota pool. Missing tail
    evidence remains unknown; reading an old observation does not refresh it.
    """
    now = now or datetime.now(timezone.utc)
    result = dict(provider='codex', native_thread_id=thread,
                  source='codex_native_rollout', activity_state='unknown',
                  auth_state='unknown', availability_state='unknown',
                  observed_at=None, model=None, effort=None, quota_windows=[],
                  quota_state='unknown', quota_observed_at=None,
                  quota_pool_binding='unverified', execution_allowed=False)
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', thread):
        raise InputError('invalid native thread identity')

    def plain(path: Path) -> None:
        for component in (path, *path.parents):
            info = component.lstat()
            if component.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise InputError('native telemetry reparse point')

    root = home.absolute() / 'sessions'
    plain(root)
    found = []
    # Only the canonical year/month/day layout, with bounded enumeration.
    pending, visited = [(root, 0)], 0
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 100000:
                    raise InputError('native telemetry inventory bound exceeded')
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                    continue
                if depth < 3 and entry.is_dir(follow_symlinks=False) and entry.name.isdigit():
                    pending.append((Path(entry.path), depth + 1))
                elif depth == 3 and entry.is_file(follow_symlinks=False) and entry.name.endswith('-' + thread + '.jsonl'):
                    found.append(Path(entry.path))
    if len(found) != 1:
        result['reason'] = 'native_rollout_missing_or_ambiguous'
        return result
    path = found[0]
    plain(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        header = stream.readline(1024 * 1024 + 1)
        if len(header) > 1024 * 1024 or not header.endswith(b'\n'):
            raise InputError('native telemetry header bound exceeded')
        meta = json.loads(header)
        if (not isinstance(meta, dict) or meta.get('type') != 'session_meta' or
                _dict(meta.get('payload')).get('id') != thread or
                _dict(meta.get('payload')).get('model_provider') != 'openai'):
            raise InputError('native telemetry identity mismatch')
        result['cwd'] = _dict(meta.get('payload')).get('cwd')
        start = max(len(header), before.st_size - 4 * 1024 * 1024)
        stream.seek(start)
        if start > len(header):
            stream.readline(4 * 1024 * 1024)  # discard the first partial row
        data = stream.read(max(0, before.st_size - stream.tell()))
        after = os.fstat(stream.fileno())
        current = path.stat()
        if ((before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
                or after.st_size < before.st_size):
            raise InputError('native telemetry identity changed')
        stream.seek(0)
        if stream.read(len(header)) != header:
            raise InputError('native telemetry header changed')
    latest = None
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b'\n'):
            result['partial_record'] = True
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise InputError('invalid native telemetry row')
        stamp = _time(row.get('timestamp'))
        if stamp is None or stamp > now:
            continue
        payload = _dict(row.get('payload'))
        if row.get('type') == 'turn_context':
            result.update(model=payload.get('model'), effort=payload.get('effort'))
        if row.get('type') != 'event_msg':
            continue
        kind = payload.get('type')
        if kind in {'task_started', 'task_complete', 'turn_aborted'}:
            if latest is None or stamp >= latest:
                latest = stamp
                result.update(observed_at=stamp.isoformat(), activity_state={
                    'task_started': 'turn_started', 'task_complete': 'turn_completed',
                    'turn_aborted': 'turn_aborted'}[kind])
        if kind == 'token_count' and isinstance(payload.get('rate_limits'), dict):
            old = _time(result['quota_observed_at'])
            if old is not None and stamp < old:
                continue
            limits = payload['rate_limits']
            mapped = dict(limitId=limits.get('limit_id'), rateLimitReachedType=limits.get('rate_limit_reached_type'))
            for name in ('primary', 'secondary'):
                w = limits.get(name)
                mapped[name] = None if w is None else dict(
                    usedPercent=_dict(w).get('used_percent'), resetsAt=_dict(w).get('resets_at'),
                    windowDurationMins=_dict(w).get('window_minutes'))
            freshness = 'fresh' if 0 <= (now - stamp).total_seconds() <= 300 else 'unknown_or_stale'
            state, windows = quota_details(dict(provider='codex', freshness=freshness,
                                                payload={'rateLimits': mapped}), now)
            result.update(quota_observed_at=stamp.isoformat(), quota_state=state,
                          quota_windows=windows, quota_freshness=freshness)
    result['reason'] = None if latest else 'native_activity_outside_bounded_tail'
    return result


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


def _utc_now() -> datetime:
    """The default apply clock. It is looked up at call time, never frozen into a default."""
    return datetime.now(timezone.utc)


def _aware_utc(moment: Any) -> datetime:
    """``moment`` as aware UTC (RCO1 7f32cfea S2): exactly a ``datetime`` (no subclass), its UTC
    offset read ONCE and required to be exactly a ``timedelta``, subtracted from the naive wall
    time and marked UTC. There is no ``astimezone``, so a missing, stateful or broken offset can
    never fall back to LOCAL time. A naive time, a None or non-timedelta offset, a tzinfo that
    raises (even NotImplementedError) or an unrepresentable time is InputError, never TypeError.
    The same rule as ``tools.bridge_pool_binding._aware_utc`` (there: None, clock_invalid)."""
    if type(moment) is not datetime:
        raise InputError('apply_pool_binding clock must return exactly a datetime')
    try:
        offset = moment.utcoffset()
        current = None if type(offset) is not timedelta else \
            (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - an unreadable offset or an unrepresentable time is not a time
        current = None
    if current is None:
        raise InputError('apply_pool_binding needs a timezone-aware time with a real UTC offset')
    return current


_DECISION_FIELDS = ('schema', 'pool_identity_state', 'execution_allowed', 'provider', 'subject_id', 'account_pool',
                    'receipt_id', 'receipt_sha256', 'provenance_kind', 'expires_at_utc', 'reason')
_STORED_FIELDS = ('account_pool', 'receipt_id', 'receipt_sha256', 'provenance_kind', 'expires_at_utc')


_MAX_PLAIN_DEPTH = 32
_MAX_PLAIN_ITEMS = 200_000


def _plain_copy(value: Any) -> Any:
    """A deterministic PLAIN-DATA copy (Tools 7e; the same rule as
    tools.bridge_pool_binding.plain_snapshot): exact dict with str keys, list, tuple, str, bool,
    int, finite float and None, bounded; anything else (a subclass, a custom object with its own
    copy hooks, a non-str key, NaN) is InputError, so no caller hook runs and no alias survives."""
    budget = [_MAX_PLAIN_ITEMS]

    def copy_value(item: Any, depth: int) -> Any:
        budget[0] -= 1
        if depth > _MAX_PLAIN_DEPTH or budget[0] < 0:
            raise InputError('apply_pool_binding needs a plain-data observation record')
        kind = type(item)
        if item is None or kind is bool or kind is str or kind is int:
            return item
        if kind is float:
            if item != item or item in (float('inf'), float('-inf')):
                raise InputError('apply_pool_binding needs a plain-data observation record')
            return item
        if kind is list:
            return [copy_value(element, depth + 1) for element in item]
        if kind is tuple:
            return tuple(copy_value(element, depth + 1) for element in item)
        if kind is dict:
            copied = {}
            for key, element in item.items():
                if type(key) is not str:
                    raise InputError('apply_pool_binding needs a plain-data observation record')
                copied[key] = copy_value(element, depth + 1)
            return copied
        raise InputError('apply_pool_binding needs a plain-data observation record')

    return copy_value(value, 0)


def _decision_fields(decision: Any) -> dict:
    """Every field the apply step uses, read from the binder's decision exactly ONCE, before any
    caller code runs again (RCO1 7f32cfea S1). Only an EXACT dict counts (a subclass could
    override ``get``); anything else, or an unreadable decision, has none."""
    if type(decision) is not dict:
        return {}
    try:
        return {key: decision.get(key) for key in _DECISION_FIELDS}
    except Exception:  # noqa: BLE001 - an unreadable decision is a refusal
        return {}


def _applied_binding(fields: dict, own: dict) -> dict | None:
    """The validated binding as immutable locals: exact ``str`` values for everything stored and
    the parsed expiry. None unless this is a verified binding for this exact provider and subject.

    Every field compared below is required to be EXACTLY a ``str`` BEFORE any comparison (Lead
    0468acc6, Tools f559): a custom object or a ``str`` subclass could run its own ``__eq__`` hook
    (caller code) inside ``==``, so it is refused without being compared. This is about hooks of
    decision VALUES during validation only: a hash-colliding decision KEY can still run its ``__eq__``
    in _decision_fields' dict.get (disclosed, not fixed), and what a binder decides is still
    trusted, not authenticated here (Lead 45b66035)."""
    provider = own.get('provider')
    subject_field = POOL_SUBJECT_FIELDS.get(provider) if type(provider) is str else None
    subject = own.get(subject_field) if subject_field is not None else None
    stored = {key: fields.get(key) for key in _STORED_FIELDS}
    if not (all(type(value) is str for value in stored.values())
            and all(type(fields.get(key)) is str
                    for key in ('schema', 'pool_identity_state', 'provider', 'subject_id'))):
        return None
    expires = _time(stored['expires_at_utc'])
    if not (fields.get('schema') == POOL_DECISION_SCHEMA
            and fields.get('pool_identity_state') == 'verified_binding'
            and fields.get('execution_allowed') is False
            and subject_field is not None and fields['provider'] == provider
            and type(subject) is str and subject.strip() and fields['subject_id'] == subject
            and own.get('account_pool') is None
            and _token(stored['account_pool'], r'[a-z0-9][a-z0-9._-]{0,63}')
            and _token(stored['receipt_id'], r'[0-9a-f]{32}')
            and _token(stored['receipt_sha256'], r'[0-9a-f]{64}')
            and stored['provenance_kind'] in POOL_PROVENANCE_KINDS
            and expires is not None):
        return None
    return dict(stored, expires=expires)


def apply_pool_binding(observation: dict, binder=None, *, clock=None) -> dict:
    """F3, additive and dormant. Without a binder (the default on every existing path) the
    observation is returned unchanged, as the very same object, and the clock is never read,
    so account_pool stays None: the raw auth context or session is never a pool. A binder
    is a trusted caller's closure over tools.bridge_pool_binding.bind_pool (receipt,
    registry, reviewed verifier, clock). Only its verified decision for this exact provider
    and subject sets account_pool; any other outcome, including a binder failure, keeps
    None and records a bounded reason. Only the receipt id, digest, provenance kind and
    expiry are kept, never its text.

    The decision must still be valid at the apply boundary (Tools a673ecb4, 5b2af2cd):
    ``clock`` (an injectable zero-argument callable; the current UTC time by default) is
    sampled exactly once, AFTER the binder returns and immediately before the expiry
    comparison, and that time must be STRICTLY before the decision's expiry. Time spent in
    the binder or its verifier therefore counts, so a slow, cached or delayed decision never
    sets even a momentarily expired pool. The sample must be exactly a datetime whose one
    offset read is exactly a timedelta; it is normalized to UTC without astimezone (never a
    local-time fallback). A non-callable clock (refused before the binder runs), a failing
    clock, a subclass or non-datetime, a naive time, an offsetless, stateful-None or broken
    tzinfo is refused with InputError, never a TypeError.

    No caller code can change a validated value (RCO1 7f32cfea S1, Tools 7e): the observation is
    copied as PLAIN DATA once into a private record (the result is built from it) before any
    callback, and the binder gets its own plain copy (no shared nested payload). A non-plain
    observation (a dict subclass, a custom object with copy hooks) is InputError before the binder
    or the clock runs. Every decision field of an EXACT dict decision is read ONCE and validated
    into immutable locals BEFORE the clock runs; nothing is re-read afterwards, so a binder that
    keeps its returned dict and a clock that edits it (or the caller's observation) changes
    nothing. Every compared decision field VALUE must be exactly a str before any comparison, so no
    ``__eq__`` hook of a custom-object or str-subclass VALUE runs while it is validated (Lead 0468acc6).
    That guard covers the values only. It does NOT prove the decision's KEYS safe: reading an exact-dict
    decision (dict.get) can still run a hash-colliding key object's ``__eq__``. Nor does it authenticate
    the binder, its verifier or the clock, which stay trusted callbacks (Lead 45b66035). A binder or
    clock failure is named without any hook of the exception's class (see _exception_name).

    A verified binding is pool IDENTITY only. It says nothing about the numeric quota, its
    windows, freshness or headroom, which stay unknown unless separately evidenced."""
    if binder is None:
        return observation
    sample = _utc_now if clock is None else clock
    if not callable(sample):
        raise InputError('apply_pool_binding clock must be callable')
    # Plain-data entry snapshots BEFORE any caller code (binder, clock) runs (Tools 7e); a
    # non-plain observation is InputError before either callback is invoked.
    own = _plain_copy(observation)       # private: neither the binder nor the clock can reach it
    argument = _plain_copy(own)
    try:
        decision = binder(argument)
    except Exception as exc:  # noqa: BLE001 - a binder failure never fails the collection
        decision = {'reason': 'binder_failed:' + _exception_name(exc)}   # no hook of the exception's class
    fields = _decision_fields(decision)
    applied = _applied_binding(fields, own)   # validated immutable locals, before any caller code runs again
    reason = fields.get('reason') if type(fields.get('reason')) is str else None
    # The apply boundary: sampled once, after the binder returned, right before the comparison.
    try:
        moment = sample()
    except Exception as exc:  # noqa: BLE001 - an unreadable clock is refused, never guessed
        raise InputError('apply_pool_binding clock failed: ' + _exception_name(exc)) from None
    current = _aware_utc(moment)
    expired = applied is not None and not current < applied['expires']
    if applied is not None and not expired:
        own.update(account_pool=applied['account_pool'], pool_identity_state='verified_binding',
                   pool_binding={'receipt_id': applied['receipt_id'], 'receipt_sha256': applied['receipt_sha256'],
                                 'provenance_kind': applied['provenance_kind'],
                                 'expires_at_utc': applied['expires_at_utc']})
    else:
        # Only a code-shaped reason is kept: no spaces, '@' or other free text is saved.
        reason = 'decision_expired' if expired else reason
        own['pool_binding'] = {'state': 'unverified', 'reason': reason if _token(reason, r'[A-Za-z0-9_.:-]{1,128}')
                               else 'binding_refused'}
    return own


_TYPE_NAME = type.__dict__['__name__']   # type's OWN __name__ descriptor, never a metaclass override


def _exception_name(exc: BaseException) -> str:
    """The exception's class name for a refusal diagnostic, read with NO hook of that class (Lead 45b66035,
    RCO2 f766 N2). type() is the C-level type of the object, and type's own __name__ descriptor is called
    directly, so a metaclass __name__ property (which could run code, raise, or return a non-str) is never
    consulted. A name stored as a str subclass is copied to an exact str by str.__str__, so no __add__ or
    __radd__ of it runs in the concatenation. The same contract as before for ordinary exceptions."""
    return str.__str__(_TYPE_NAME.__get__(type(exc)))


def _token(value: Any, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


async def collect_codex(client: MetadataClient, auth_context: str, *, pool_binder=None,
                        pool_clock=None) -> dict:
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
    return apply_pool_binding({'schema': 'wd.capacity-observation.v1', 'provider': 'codex',
            'source_ref': 'codex:account/rateLimits/read', 'collection_started_at': started,
            'observed_at': utcnow(), 'auth_context_id': context_id,
            'account_pool': None, 'pool_identity_state': 'unverified_auth_context',
            'account_type': account['type'], 'plan_type': account.get('planType'),
            'payload': quota_payload(limits, 'codex'), 'catalog': catalog,
            'quota_freshness_basis': 'provider_metadata_request',
            'execution_allowed': False}, pool_binder, clock=pool_clock)


def collect_claude(payload: dict, *, pool_binder=None, pool_clock=None) -> dict:
    session = payload.get('session_id')
    if not _text(session):
        raise InputError('statusline session identity missing')
    return apply_pool_binding({'schema': 'wd.capacity-observation.v1', 'provider': 'claude',
            'source_ref': 'claude:statusline', 'observed_at': utcnow(),
            'native_thread_id': session, 'account_pool': None,
            'pool_identity_state': 'unknown', 'execution_allowed': False,
            'quota_freshness_basis': 'statusline_callback_provider_timestamp_unknown',
            'model': _dict(payload.get('model')).get('id'),
            'effort': _dict(payload.get('effort')).get('level'),
            'payload': quota_payload(payload, 'claude'),
            'usage': {k: _dict(payload.get('context_window')).get(k)
                      for k in ('total_input_tokens', 'total_output_tokens')}}, pool_binder, clock=pool_clock)


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
    """Read-only recent observations; session/auth context is not a quota identity.

    ``now`` (the current UTC time by default) is normalized ONCE, before the store is opened, by the apply
    clock's rule (_aware_utc: exactly a datetime, one offset read, subtraction, no astimezone), and every age,
    expiry and quota computation below uses that exact aware-UTC value. A naive, offsetless, subclass,
    non-timedelta, broken or unrepresentable ``now`` is InputError (Lead 5aa78f56; RCO1 a39cb2aa N1); a
    KeyboardInterrupt or SystemExit raised by a tzinfo still propagates. The report's observed_at is UTC."""
    try:
        now = _aware_utc(datetime.now(timezone.utc) if now is None else now)
    except InputError:
        raise InputError('status needs now as exactly a timezone-aware datetime with a real UTC offset') from None
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
        # F3: a stored verified pool binding holds only until its receipt expires; after
        # that the pool is unknown again (the stored row itself is never rewritten).
        if row.get('pool_identity_state') == 'verified_binding':
            expires = _time(_dict(row.get('pool_binding')).get('expires_at_utc'))
            if expires is None or now >= expires:
                row.update(account_pool=None, pool_identity_state='binding_expired')
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
    parser.add_argument('--attribution', action='store_true',
                        help='Augment --status with an additive, read-only attribution block. '
                             'Off by default; existing output is unchanged without it.')
    parser.add_argument('--scheduled', action='store_true', help='Budgeted Codex metadata poll, safe to repeat.')
    parser.add_argument('--statusline', action='store_true', help='Compact Claude statusline output after ingestion.')
    parser.add_argument('--claude-hook', action='store_true', help='Record native lifecycle metadata; no model decision or retry.')
    parser.add_argument('--emit-alert', action='store_true', help='Return sanitized native failure metadata to the explicitly enabled bridge-notice hook.')
    parser.add_argument('--emit-lifecycle', action='store_true', help='Return sanitized lifecycle metadata to the explicitly enabled native cron guard.')
    parser.add_argument('--codex-executable')
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--native-codex-thread')
    parser.add_argument('--native-codex-home', type=Path)
    args = parser.parse_args(argv)
    if args.native_codex_thread:
        try:
            if args.native_codex_home is None:
                raise InputError('explicit native Codex home required')
            value = read_native_codex(args.native_codex_home, args.native_codex_thread)
            print(json.dumps(value, allow_nan=False))
            return 0
        except (InputError, OSError, ValueError, TypeError, KeyError):
            print(json.dumps(dict(provider='codex', native_thread_id=args.native_codex_thread,
                                  reason='native_telemetry_unavailable', execution_allowed=False)))
            return 2
    # A status failure is not a collection attempt. Keep both exits outside every
    # path that reserves a poll, starts a provider, or saves an observation.
    if args.status:
        try:
            result = status(args.store)
            if args.attribution:
                # Additive, read-only. Imported lazily and failure-isolated so a
                # classifier problem can never change the status exit path or
                # reach any code that reserves a poll or saves an observation.
                try:
                    if __package__:
                        from .bridge_capacity_attribution import attribution_block
                    else:
                        from bridge_capacity_attribution import attribution_block
                    result['attribution'] = attribution_block(result)
                except Exception as exc:
                    result['attribution'] = {'state': 'unavailable',
                                             'reason': type(exc).__name__}
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
            if args.emit_lifecycle:
                print(json.dumps(observation, allow_nan=False))
            elif args.emit_alert:
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
