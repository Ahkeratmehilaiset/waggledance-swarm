#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Capacity recovery primitives for a trusted owning-session adapter.

Not attached to live terminals. No CLI executes transitions. Unknown identity,
policy, outcomes or observations never grant dispatch. Durable intent precedes
each side effect; ambiguous outcomes require observation, never blind replay.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol

try:
    from tools.bridge_capacity_advisor import InputError, _time, _text, _number
    from tools.bridge_capacity_collector import digest
except ModuleNotFoundError:
    from bridge_capacity_advisor import InputError, _time, _text, _number
    from bridge_capacity_collector import digest

BINDING_FIELDS = ('agent_id', 'session_id', 'native_thread_id', 'task_id', 'request_id',
                  'head', 'claim_id', 'scope_digest', 'authority_ref', 'policy_digest',
                  'permission_digest')


def recovery_advice(capacity: dict, *, failure: dict | None, now: datetime,
                    attempts: int = 0, deadline: datetime | None = None) -> dict:
    """A deadline is a recheck time, never a promise that quota will be available."""
    result = dict(action='blocked', reason='invalid_capacity', recheck_at=None,
                  execution_allowed=False)
    observed = _time(capacity.get('observed_at'))
    if observed is None or not 0 <= (now - observed).total_seconds() <= 300:
        result['reason'] = 'capacity_stale_or_unknown'
        return result
    if failure:
        stamp = _time(failure.get('observed_at'))
        if (stamp is None or stamp > now or
                failure.get('provider') != capacity.get('provider') or
                failure.get('account_pool') != capacity.get('account_pool') or
                not _text(failure.get('limit_id')) or
                failure['limit_id'] not in {w.get('limit_id') for w in capacity.get('windows', [])}):
            result['reason'] = 'failure_binding_or_time_unknown'
            return result
        kind = failure.get('kind')
        if kind in {'billing', 'auth', 'permission', 'tool', 'safety'}:
            result.update(action='operator_required', reason=kind)
            return result
        if kind == 'throttled':
            retry = _time(failure.get('retry_after'))
            if (type(attempts) is not int or not 0 <= attempts < 3 or deadline is None or
                    retry is None or retry < now or retry >= deadline or now >= deadline):
                result['reason'] = 'retry_budget_or_server_delay_unknown'
                return result
            result.update(action='retry_same_profile', reason='bounded_throttle',
                          recheck_at=retry.isoformat())
            return result
        if kind != 'quota':
            result['reason'] = 'unclassified_failure'
            return result
        if stamp >= observed:
            result.update(action='wait_capacity', reason='fresh_observation_required_after_failure')
            return result
    if capacity.get('state') == 'available':
        result.update(action='recheck_safe_boundary', reason='observed_headroom')
    elif capacity.get('state') == 'exhausted':
        windows = capacity.get('windows', [])
        blocking = [w for w in windows if w.get('state') == 'exhausted']
        signals = capacity.get('limit_signals', [])
        if any(isinstance(s.get('value'), str) and s['value'].startswith('workspace_') for s in signals):
            result.update(action='operator_required', reason='workspace_limit')
        elif (not blocking or any(w.get('state') == 'unknown' for w in windows) or
              any(w.get('name') == 'spend_limit' for w in blocking) or
              any(not _number(w.get('resets_at')) or
                  not now.timestamp() < w['resets_at'] <= 253402300799 for w in blocking)):
            result.update(action='wait_capacity', reason='reset_unknown_or_spend_limit')
        else:
            latest = max(w['resets_at'] for w in blocking)
            result.update(action='wait_capacity', reason='all_blocking_windows_must_reset',
                          recheck_at=datetime.fromtimestamp(latest, timezone.utc).isoformat())
    else:
        result['reason'] = 'capacity_unknown'
    return result


class OwningSessionAdapter(Protocol):
    """An actual adapter must prove ownership and idempotent checkpoint/resume.

    inspect returns exact binding + profile, hold/cancelled/idle/pending_effects,
    and applied_transition/resumed_transition IDs from actual observations.
    Applying a profile changes configuration only: it must not execute task work.
    """
    def inspect(self, transition_id: str) -> dict: ...
    def checkpoint(self, transition_id: str, binding: dict) -> str: ...
    def apply(self, transition_id: str, profile: str) -> None: ...
    def resume(self, transition_id: str) -> None: ...


class RecoveryStore:
    """One local SQLite transaction for shared-pool admission and journal changes.

    Pool keys include provider, verified account pool, limit ID and window name.
    The fixed conservative bound is one transition per overlapping quota window.
    Capacity percentages are never converted to invented token allowances.
    An in-flight lease never expires into reuse: explicit reconciliation closes it.
    """
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                    phase TEXT NOT NULL, checkpoint TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS reservations (
                    pool TEXT PRIMARY KEY, transition_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS journal (
                    sequence INTEGER PRIMARY KEY, transition_id INTEGER NOT NULL,
                    phase TEXT NOT NULL, observed_at TEXT NOT NULL, reason TEXT);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def plan(self, request_key: str, plan: dict) -> int:
        binding = plan.get('binding', {})
        if (not _text(request_key) or any(not _text(binding.get(k)) for k in BINDING_FIELDS)
                or not _text(plan.get('from_profile')) or not _text(plan.get('to_profile'))
                or plan['from_profile'] == plan['to_profile']
                or plan.get('qualified') is not True or not _text(plan.get('qualification_ref'))
                or plan.get('owning_adapter_verified') is not True
                or plan.get('hold') is not False or plan.get('cancelled') is not False
                or plan.get('billing') != 'subscription'
                or not isinstance(plan.get('required_reviewers'), list)
                or not all(_text(x) for x in plan['required_reviewers'])):
            raise InputError('transition authorization or binding incomplete')
        profiles = plan.get('profiles', {})
        for key in (plan['from_profile'], plan['to_profile']):
            profile = profiles.get(key, {}) if isinstance(profiles, dict) else {}
            if any(not _text(profile.get(k)) for k in ('model', 'effort')):
                raise InputError('exact model and effort required for both profiles')
        pools = plan.get('pools')
        if (not isinstance(pools, list) or not pools or
                any(not isinstance(p, list) or len(p) != 4 or not all(_text(x) for x in p)
                    for p in pools) or len({tuple(p) for p in pools}) != len(pools)):
            raise InputError('verified quota window keys required')
        fingerprint = digest(plan)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM transitions WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise InputError('request revision reused with changed transition')
                return prior['id']
            row = db.execute('INSERT INTO transitions(request_key,fingerprint,plan,phase) '
                             'VALUES (?,?,?,?)', (request_key, fingerprint, json.dumps(plan), 'planned'))
            tid = row.lastrowid
            try:
                db.executemany('INSERT INTO reservations VALUES (?,?)',
                               [(json.dumps(p), tid) for p in pools])
            except sqlite3.IntegrityError as exc:
                raise InputError('shared quota window already reserved') from exc
            self._log(db, tid, 'planned', None)
            return tid

    @staticmethod
    def _log(db, tid, phase, reason):
        db.execute('INSERT INTO journal(transition_id,phase,observed_at,reason) VALUES (?,?,?,?)',
                   (tid, phase, datetime.now(timezone.utc).isoformat(), reason))

    def get(self, tid: int) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM transitions WHERE id=?', (tid,)).fetchone()
            if row is None:
                raise InputError('unknown transition')
            return dict(row)

    def move(self, tid: int, expected: str, phase: str, *, reason=None, checkpoint=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE transitions SET phase=?,reason=?,checkpoint=COALESCE(?,checkpoint) '
                                 'WHERE id=? AND phase=?', (phase, reason, checkpoint, tid, expected))
            if changed.rowcount != 1:
                raise InputError('transition changed concurrently; inspect before continuing')
            self._log(db, tid, phase, reason)
            if phase in {'resumed', 'cancelled_before_apply'}:
                db.execute('DELETE FROM reservations WHERE transition_id=?', (tid,))


def advance(store: RecoveryStore, tid: int, adapter: OwningSessionAdapter) -> dict:
    """Advance one bounded step. Repeated ambiguous apply/resume only observes.

    This is a library entry point for a trusted controller, not a peer command.
    No default/production adapter is supplied until the live transport qualifies.
    """
    row = store.get(tid)
    phase = row['phase']
    if phase in {'resumed', 'cancelled_before_apply'}:
        return row
    plan = json.loads(row['plan'])
    operation = str(tid) + ':' + row['fingerprint']
    observed = adapter.inspect(operation)
    stamp = _time(observed.get('observed_at'))
    fresh = stamp is not None and 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 15
    same = all(observed.get(k) == v for k, v in plan['binding'].items())
    safe = (fresh and same and observed.get('hold') is False and observed.get('cancelled') is False
            and observed.get('idle') is True and observed.get('pending_effects') is False
            and observed.get('quota_available') is True and observed.get('catalog_verified') is True
            and observed.get('required_reviewers') == plan['required_reviewers']
            and observed.get('qualification_ref') == plan['qualification_ref'])
    if not safe:
        # Keep admission reserved after an ambiguous apply; never free capacity blindly.
        if phase in {'planned', 'quiesced', 'checkpointed'}:
            store.move(tid, phase, 'cancelled_before_apply', reason='safe_boundary_or_binding_changed')
        return store.get(tid)
    profile = plan['profiles'].get(observed.get('profile'), {})
    actual_verified = (observed.get('model') == profile.get('model') and
                       observed.get('effort') == profile.get('effort') and bool(profile))
    if not actual_verified:
        return row
    if phase in {'planned', 'quiesced', 'checkpointed'} and observed.get('profile') != plan['from_profile']:
        store.move(tid, phase, 'cancelled_before_apply', reason='source_profile_changed')
    elif phase == 'planned':
        store.move(tid, phase, 'quiesced')
    elif phase == 'quiesced':
        # Contract: checkpoint is durable and idempotent for this operation ID.
        reference = adapter.checkpoint(operation, plan['binding'])
        if not _text(reference):
            raise InputError('durable checkpoint not confirmed')
        store.move(tid, phase, 'checkpointed', checkpoint=reference)
    elif phase == 'checkpointed':
        store.move(tid, phase, 'apply_pending')
        try:
            adapter.apply(operation, plan['to_profile'])
        except Exception:
            # Intent is already durable; next call observes, never applies again.
            return store.get(tid)
    elif phase == 'apply_pending':
        if (observed.get('profile') == plan['to_profile']
                and observed.get('applied_transition') == operation):
            store.move(tid, phase, 'verified')
    elif phase == 'verified':
        if observed.get('profile') != plan['to_profile'] or observed.get('applied_transition') != operation:
            return row
        store.move(tid, phase, 'resume_pending')
        try:
            adapter.resume(operation)
        except Exception:
            return store.get(tid)
    elif phase == 'resume_pending':
        if (observed.get('profile') == plan['to_profile']
                and observed.get('resumed_transition') == operation):
            store.move(tid, phase, 'resumed')
    return store.get(tid)
