"""Startup location requests must refuse until all owner reads are budgeted."""

import threading
from types import SimpleNamespace
from io import BytesIO

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_absent_generation_prefix_readonly as prefix


def test_prefix_hash_reserves_before_every_read():
    class Source(BytesIO):
        def __init__(self):
            super().__init__(b'abcdef')
            self.reads = []

        def read(self, count):
            self.reads.append(count)
            return super().read(count)

    source = Source()
    with pytest.raises(ValueError):
        prefix._hash_prefix(source, 6, source_budget=prefix._SourceReadBudget(5))
    assert source.reads == []
    prefix._hash_prefix(source, 6, source_budget=prefix._SourceReadBudget(6))
    assert source.reads == [6]


def test_budget_is_atomic_across_threads():
    budget = prefix._SourceReadBudget(1)
    barrier = threading.Barrier(2)
    outcomes = []

    def reserve():
        barrier.wait()
        try:
            budget.reserve(1)
            outcomes.append('accepted')
        except ValueError:
            outcomes.append('refused')

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ['accepted', 'refused']


@pytest.mark.parametrize('direct', [False, True])
def test_located_startup_request_refuses_before_owner_io_and_preserves_ready(monkeypatch, direct):
    owner = object.__new__(runtime._SingleManifestOwner)
    handler = SimpleNamespace(_retained=None)
    retained = object()
    owner._lanes = frozenset({'codex-lead-1'})
    owner._generation = (None, None, handler)
    owner._state = 'READY'
    owner._lock = threading.Lock()
    owner._retained = retained
    entered = []

    def forbidden(*_args, **_kwargs):
        entered.append(True)
        raise AssertionError('owner source path must not be entered')

    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory_or_hold', forbidden)
    request = {'schema': 'wd.mind-pipe-request.v2', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        if direct:
            owner._candidate_request(request, located=True)
        else:
            owner.located_candidates(request)
    assert entered == []
    assert owner._state == 'READY'
    assert owner._retained is retained
    assert owner._handler is handler
    assert owner._lock.acquire(blocking=False)
    owner._lock.release()


def test_nonlocated_request_keeps_existing_behavior(monkeypatch):
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._generation = (None, None, SimpleNamespace(_retained=None))
    owner._state = 'READY'
    owner._lock = threading.Lock()
    owner._retained = object()
    for name in ('_check_factory_or_hold', '_check_policy_anchor',
                 '_ensure_retained_locked', '_check_candidate_delivery'):
        monkeypatch.setattr(runtime._SingleManifestOwner, name, lambda _self: None)
    monkeypatch.setattr(runtime, 'map_pipe_candidates', lambda **_kwargs: {'status': 'ok'})
    request = {'schema': 'wd.mind-pipe-request.v1', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    assert owner.candidates(request) == {'status': 'ok'}
    assert owner._state == 'READY'
