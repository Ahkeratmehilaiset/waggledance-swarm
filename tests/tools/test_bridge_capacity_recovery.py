# SPDX-License-Identifier: BUSL-1.1
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.bridge_capacity_advisor import InputError
from tools.bridge_capacity_recovery import BINDING_FIELDS, RecoveryStore, advance, recovery_advice


def plan():
    binding = {k: 'fixture-' + k for k in BINDING_FIELDS}
    binding.update(native_pid=4242, native_process_started_at='2026-09-24T10:00:00+00:00')
    return {'binding': binding,
            'from_profile': 'primary', 'to_profile': 'fallback',
            'profiles': {'primary': {'model': 'model-a', 'effort': 'high'},
                         'fallback': {'model': 'model-b', 'effort': 'high'}},
            'qualified': True, 'qualification_ref': 'fixture-only',
            'trusted_adapter_identity': {
                'principal': 'fixture-trusted-adapter',
                'verification_ref': 'fixture-boundary-attestation',
            },
            'owning_adapter_verified': True, 'hold': False, 'cancelled': False,
            'billing': 'subscription', 'required_reviewers': ['rco1', 'rco2'],
            'pools': [['codex', 'verified-account', 'codex', 'primary']]}


class FakeOwner:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.profile = value['from_profile']
        self.applied, self.resumed = None, None
        self.apply_count = self.resume_count = 0
        self.crash = None
        self.override = {}

    def inspect(self, tid):
        return {**self.value['binding'], 'profile': self.profile,
                **self.value['profiles'][self.profile],
                'hold': False, 'cancelled': False, 'idle': True, 'pending_effects': False,
                'required_reviewers': self.value['required_reviewers'],
                'qualification_ref': self.value['qualification_ref'],
                'quota_available': True, 'catalog_verified': True,
                'trusted_adapter_identity': deepcopy(self.value['trusted_adapter_identity']),
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'applied_transition': self.applied, 'resumed_transition': self.resumed,
                **self.override}

    def checkpoint(self, tid, binding):
        return 'durable-fixture:' + tid

    def apply(self, tid, profile):
        self.apply_count += 1
        if self.crash == 'before_apply':
            raise TimeoutError()
        self.profile, self.applied = profile, tid
        if self.crash == 'after_apply':
            raise TimeoutError()

    def resume(self, tid):
        self.resume_count += 1
        if self.crash == 'before_resume':
            raise TimeoutError()
        self.resumed = tid
        if self.crash == 'after_resume':
            raise TimeoutError()


@pytest.mark.parametrize('crash,expected', [(None, 'resumed'), ('after_apply', 'resumed'),
                                         ('after_resume', 'resumed'),
                                         ('before_apply', 'apply_pending'),
                                         ('before_resume', 'resume_pending')])
def test_ambiguous_outcome_never_replays_side_effect(tmp_path, crash, expected):
    value = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('request1', value)
    owner = FakeOwner(value)
    owner.crash = crash
    for _ in range(12):
        # Reopen durable state as after a controller crash.
        store = RecoveryStore(tmp_path / 'recovery.db')
        advance(store, tid, owner)
    assert store.get(tid)['phase'] == expected
    assert owner.apply_count == 1
    assert owner.resume_count <= 1
    assert store.plan('request1', value) == tid


@pytest.mark.parametrize('stage', range(5))
@pytest.mark.parametrize('field,value', [('hold', True), ('cancelled', True),
                                        ('pending_effects', True), ('head', 'changed'),
                                        ('permission_digest', 'changed'),
                                        ('required_reviewers', []),
                                        ('quota_available', False)])
def test_changed_guard_never_advances(tmp_path, stage, field, value):
    value_plan = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('r', value_plan)
    owner = FakeOwner(value_plan)
    for _ in range(stage):
        advance(store, tid, owner)
    counts = owner.apply_count, owner.resume_count
    owner.override[field] = value
    advance(store, tid, owner)
    assert (owner.apply_count, owner.resume_count) == counts
    assert store.get(tid)['phase'] != 'resumed'


def test_shared_pool_admission_is_atomic(tmp_path):
    store = RecoveryStore(tmp_path / 'recovery.db')
    def attempt(i):
        try:
            return store.plan('request' + str(i), plan())
        except InputError:
            return None
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(attempt, range(5)))
    assert sum(r is not None for r in results) == 1


def test_request_revision_cannot_mutate_transition(tmp_path):
    store = RecoveryStore(tmp_path / 'recovery.db')
    value = plan()
    store.plan('request', value)
    value['binding']['head'] = 'changed'
    with pytest.raises(InputError, match='revision'):
        store.plan('request', value)


@pytest.mark.parametrize('pool', [['CODEX', 'pool', 'codex', 'primary'],
                                  ['codex', ' pool ', 'codex', 'primary']])
def test_noncanonical_pool_components_are_rejected(tmp_path, pool):
    value = plan()
    value['pools'] = [pool]
    with pytest.raises(InputError, match='canonical'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


@pytest.mark.parametrize('field', ['native_pid', 'native_process_started_at'])
def test_plan_rejects_missing_process_epoch(tmp_path, field):
    value = plan()
    del value['binding'][field]
    with pytest.raises(InputError, match='binding incomplete'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


@pytest.mark.parametrize('reviewers', [[], ['rco1', 'rco1'], ['fixture-agent_id']])
def test_plan_requires_nonempty_independent_reviewers(tmp_path, reviewers):
    value = plan()
    value['required_reviewers'] = reviewers
    with pytest.raises(InputError, match='binding incomplete'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


def test_caller_boolean_cannot_replace_trusted_adapter_identity(tmp_path):
    value = plan()
    value.pop('trusted_adapter_identity')
    value['owning_adapter_verified'] = True
    with pytest.raises(InputError, match='binding incomplete'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


@pytest.mark.parametrize('verified', [False, None])
def test_plan_requires_explicit_true_adapter_verification(tmp_path, verified):
    value = plan()
    value['owning_adapter_verified'] = verified
    with pytest.raises(InputError, match='binding incomplete'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


def test_plan_rejects_missing_adapter_verification(tmp_path):
    value = plan()
    value.pop('owning_adapter_verified')
    with pytest.raises(InputError, match='binding incomplete'):
        RecoveryStore(tmp_path / 'db').plan('request', value)


@pytest.mark.parametrize('stage', range(6))
@pytest.mark.parametrize('field,value', [
    ('native_pid', 7777),
    # Same PID with a new start epoch models PID reuse after a restart.
    ('native_process_started_at', '2026-09-24T10:00:01+00:00'),
])
def test_changed_process_epoch_is_fenced_at_every_phase(tmp_path, stage, field, value):
    value_plan = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('request', value_plan)
    owner = FakeOwner(value_plan)
    for _ in range(stage):
        advance(store, tid, owner)
    counts = owner.apply_count, owner.resume_count
    owner.override[field] = value
    advance(store, tid, owner)
    row = store.get(tid)
    assert (owner.apply_count, owner.resume_count) == counts
    if stage < 3:
        assert row['phase'] == 'cancelled_before_apply'
        assert RecoveryStore(tmp_path / 'recovery.db').plan('new-request', plan()) != tid
    else:
        # After apply intent, a changed epoch is ambiguous: retain its pool reservation.
        assert row['phase'] in {'apply_pending', 'verified', 'resume_pending'}
        with pytest.raises(InputError, match='reserved'):
            RecoveryStore(tmp_path / 'recovery.db').plan('new-request', plan())


def test_observed_trusted_identity_change_is_fenced_before_apply(tmp_path):
    value = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('request', value)
    owner = FakeOwner(value)
    owner.override['trusted_adapter_identity'] = {
        'principal': 'different-principal',
        'verification_ref': 'fixture-boundary-attestation',
    }
    advance(store, tid, owner)
    assert store.get(tid)['phase'] == 'cancelled_before_apply'
    assert owner.apply_count == 0


def test_unknown_actual_effort_cannot_apply(tmp_path):
    value = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('request', value)
    owner = FakeOwner(value)
    owner.override['effort'] = None
    advance(store, tid, owner)
    assert store.get(tid)['phase'] == 'planned'


def test_resumed_busy_task_receipt_reconciles_without_dispatch(tmp_path):
    value = plan()
    store = RecoveryStore(tmp_path / 'recovery.db')
    tid = store.plan('request', value)
    owner = FakeOwner(value)
    for _ in range(5):
        advance(store, tid, owner)
    assert store.get(tid)['phase'] == 'resume_pending'
    owner.override.update(idle=False, pending_effects=True, quota_available=False)
    advance(store, tid, owner)
    assert store.get(tid)['phase'] == 'resumed'
    assert owner.resume_count == 1


def capacity(now):
    return dict(provider='codex', account_pool='pool1', state='exhausted',
                observed_at=now.isoformat(), windows=[
                    dict(limit_id='codex', name='primary', state='exhausted',
                         resets_at=(now + timedelta(hours=1)).timestamp()),
                    dict(limit_id='codex', name='secondary', state='exhausted',
                         resets_at=(now + timedelta(hours=2)).timestamp())])


def test_wait_uses_latest_reset_and_does_not_grant_execution():
    now = datetime.now(timezone.utc)
    result = recovery_advice(capacity(now), failure=None, now=now)
    assert result['recheck_at'] == (now + timedelta(hours=2)).isoformat()
    assert result['execution_allowed'] is False


def test_newer_quota_error_invalidates_headroom():
    now = datetime.now(timezone.utc)
    cap = capacity(now - timedelta(seconds=1))
    cap['state'] = 'available'
    failure = dict(provider='codex', account_pool='pool1', limit_id='codex',
                   observed_at=now.isoformat(), kind='quota')
    assert recovery_advice(cap, failure=failure, now=now)['action'] == 'wait_capacity'
    cap['observed_at'] = (now + timedelta(seconds=1)).isoformat()
    assert recovery_advice(cap, failure=failure, now=now + timedelta(seconds=1))['action'] == 'recheck_safe_boundary'


@pytest.mark.parametrize('kind', ['auth', 'billing', 'permission', 'safety', 'tool'])
def test_nonquota_failure_never_switches(kind):
    now = datetime.now(timezone.utc)
    failure = dict(provider='codex', account_pool='pool1', limit_id='codex',
                   observed_at=now.isoformat(), kind=kind)
    assert recovery_advice(capacity(now), failure=failure, now=now)['action'] == 'operator_required'
