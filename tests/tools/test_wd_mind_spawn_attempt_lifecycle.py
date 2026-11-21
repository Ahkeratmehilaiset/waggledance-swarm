"""Synthetic spawn-state tests: no native processes or live reservation."""

from types import SimpleNamespace
import threading

import pytest

from tools import wd_mind_e5_lifecycle as lifecycle


class _Job:
    kill_on_close = False

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_attempt_before_spawn_then_bound():
    owner = lifecycle._Owner()
    owner.set_job(_Job())
    assert owner._spawn_state == 'NOT_ENTERED'
    owner.mark_spawn_attempt()
    assert owner._spawn_state == 'ATTEMPTED'
    process = object()
    owner.bind(process)
    assert owner._spawn_state == 'BOUND'
    assert owner.resources.process is process


def test_ambiguous_attempt_is_retained_even_after_shutdown(monkeypatch):
    owner = lifecycle._Owner()
    job = _Job()
    owner.set_job(job)
    owner.mark_spawn_attempt()
    assert owner.shutdown(timeout=0)['owned'] is True

    class _ObservedWait(Exception):
        pass

    def stop_at_wait(*args, **kwargs):
        raise _ObservedWait

    monkeypatch.setattr(owner._condition, 'wait', stop_at_wait)
    with pytest.raises(_ObservedWait):
        owner._keep()
    assert owner.status()['state'] == 'HOLD'
    assert owner.status()['owned'] is True
    assert not job.closed


def test_legacy_bind_remains_compatible():
    owner = lifecycle._Owner()
    owner.set_job(_Job())
    process = object()
    owner.bind(process)
    assert owner._spawn_state == 'BOUND'


def test_unstarted_cleanup_still_cancels():
    owner = lifecycle._Owner()
    job = _Job()
    owner.set_job(job)
    with owner._condition:
        owner._closing = True
    owner._keep()
    assert owner.status()['state'] == 'CANCELLED'
    assert owner.status()['owned'] is False
    assert job.closed


def test_real_reserved_owner_without_spawn_can_close():
    owner = lifecycle.reserve(enabled=True)
    job = _Job()
    owner.set_job(job)
    result = owner.shutdown(timeout=2)
    owner._keeper.join(timeout=2)
    assert result['state'] == 'CANCELLED'
    assert result['owned'] is False
    assert job.closed
    assert not owner._keeper.is_alive()


def test_attempt_cannot_be_registered_twice_or_after_close():
    owner = lifecycle._Owner()
    owner.set_job(_Job())
    owner.mark_spawn_attempt()
    with pytest.raises(lifecycle.LifecycleRefused):
        owner.mark_spawn_attempt()
    owner.shutdown(timeout=0)
    owner.bind(SimpleNamespace(pid=123))
    assert owner._spawn_state == 'BOUND'
    with pytest.raises(lifecycle.LifecycleRefused):
        owner.mark_ready()
    assert owner.status()['owned'] is True


def test_unbound_process_after_attempt_is_not_released(monkeypatch):
    owner = lifecycle._Owner()
    owner.set_job(_Job())
    owner.mark_spawn_attempt()
    owner.resources.process = SimpleNamespace(pid=123)  # Synthetic partial assignment before bind.
    owner.shutdown(timeout=0)

    class _ObservedWait(Exception):
        pass

    def stop_at_wait(*args, **kwargs):
        raise _ObservedWait

    monkeypatch.setattr(owner._condition, 'wait', stop_at_wait)
    with pytest.raises(_ObservedWait):
        owner._keep()
    assert owner.status()['owned'] is True


def test_verified_owner_requires_attempt_before_bind():
    owner = lifecycle._Owner(require_spawn_attempt=True)
    owner.set_job(_Job())
    with pytest.raises(lifecycle.LifecycleRefused):
        owner.bind(object())
    assert owner._spawn_state == 'NOT_ENTERED'


def test_attempt_cannot_bind_none():
    owner = lifecycle._Owner(require_spawn_attempt=True)
    owner.set_job(_Job())
    owner.mark_spawn_attempt()
    with pytest.raises(lifecycle.LifecycleRefused):
        owner.bind(None)
    assert owner._spawn_state == 'ATTEMPTED'


def test_shutdown_between_attempt_and_bind_still_drains_cooperatively():
    from test_wd_mind_e5_lifecycle import Process

    owner = lifecycle._Owner(require_spawn_attempt=True)
    owner._keeper.daemon = True  # Only this synthetic test owner is daemonized.
    job, process = _Job(), Process()
    owner.set_job(job)
    owner._keeper.start()
    owner.mark_spawn_attempt()
    adoption_allowed = threading.Event()
    adopted = threading.Event()
    errors = []

    def adopt():
        adoption_allowed.wait(2)
        try:
            owner.bind(process)
        except BaseException as error:
            errors.append(type(error).__name__)
        finally:
            adopted.set()

    thread = threading.Thread(target=adopt, daemon=True)
    thread.start()
    try:
        assert owner.shutdown(timeout=0)['owned'] is True
        adoption_allowed.set()
        assert adopted.wait(2)
        assert not errors
        owner._keeper.join(timeout=2)
        assert not owner._keeper.is_alive()
        assert owner.status()['owned'] is False
        assert process.stdin.closed and job.closed
    finally:
        adoption_allowed.set()


def test_actual_keeper_waits_on_ambiguous_attempt_without_releasing(monkeypatch):
    owner, job = lifecycle._Owner(require_spawn_attempt=True), _Job()
    owner.mark_job_attempt()
    owner.set_job(job)
    owner.mark_spawn_attempt()
    assert owner.shutdown(timeout=0)['owned'] is True
    entered, release = threading.Event(), threading.Event()
    observed = []

    class _StopSyntheticWait(Exception):
        pass

    def barrier_wait(*args, **kwargs):
        entered.set()
        release.wait(2)
        raise _StopSyntheticWait

    def exercise_keeper():
        try:
            owner._keep()
        except _StopSyntheticWait:
            observed.append('waited')

    monkeypatch.setattr(owner._condition, 'wait', barrier_wait)
    thread = threading.Thread(target=exercise_keeper, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        assert not job.closed and owner._owned is True
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive() and observed == ['waited']
    assert owner.status()['state'] == 'HOLD' and owner.status()['owned'] is True


def test_actual_keeper_drains_bound_synthetic_worker_before_release():
    from test_wd_mind_e5_lifecycle import Process

    owner = lifecycle.reserve(enabled=True, require_spawn_attempt=True)
    job, process = _Job(), Process()
    try:
        owner.mark_job_attempt()
        owner.set_job(job)
        owner.mark_spawn_attempt()
        owner.bind(process)
        status = owner.shutdown(timeout=2)
        owner._keeper.join(timeout=2)
        assert status['owned'] is False and status['state'] == 'EXITED'
        assert process.stdin.closed and job.closed and not owner._keeper.is_alive()
    finally:
        process.finish()
