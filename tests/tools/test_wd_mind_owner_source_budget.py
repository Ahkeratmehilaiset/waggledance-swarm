"""Startup location requests must refuse until all owner reads are budgeted."""

import threading
from types import SimpleNamespace
from io import BytesIO

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader


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


def test_narrow_index_reserves_before_readline(monkeypatch):
    class Source(BytesIO):
        def close(self):
            pass

    source = Source(b'{}\n')
    opened = SimpleNamespace(_open_log=lambda _path: source,
                             _parse_rows=lambda _line, **_kwargs: [{}])
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (opened, prefix))
    monkeypatch.setattr(reader, '_narrow_fence', lambda *_args: None)
    cursor = SimpleNamespace(offset=3)
    context = {'messages': {'source_path': 'synthetic', 'generation_path': 'synthetic-generation',
                            'end_cursor': cursor},
               'decision': {'source_prefix': {'sha256': reader._sha(b'{}\n')}}}
    references = {reader._sha(reader._canonical({}))}
    with pytest.raises(ValueError, match='source_budget'):
        reader._narrow_row_index(context, references, source_budget=prefix._SourceReadBudget(3))
    assert source.tell() == 0


def test_short_read_refunds_unused_reservation():
    budget = prefix._SourceReadBudget(6)
    assert prefix._budgeted_read(BytesIO(b'a'), 6, budget) == b'a'
    budget.reserve(5)
    with pytest.raises(ValueError, match='source_budget'):
        budget.reserve(1)


def test_retained_prefix_reserves_before_open_source_read(monkeypatch):
    class Source(BytesIO):
        def __init__(self):
            super().__init__(b'abc')
            self.reads = []

        def close(self):
            pass

        def fileno(self):
            return 7

        def read(self, count):
            self.reads.append(count)
            return super().read(count)

    source = Source()
    opened = SimpleNamespace(_open_log=lambda _path: source, _file_identity=lambda _stream: 'id')
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (opened, prefix))
    monkeypatch.setattr(reader, '_narrow_fence', lambda *_args: None)
    monkeypatch.setattr(runtime.os, 'fstat', lambda _fd: SimpleNamespace(st_size=3, st_mtime_ns=1))
    context = {'messages': {'source_path': 'synthetic', 'generation_path': 'synthetic-generation',
                            'end_cursor': SimpleNamespace(offset=3)},
               'decision': {'source_prefix': {'sha256': reader._sha(b'abc')}}}
    retained = object.__new__(runtime._RetainedFlatQueryState)
    with pytest.raises(ValueError, match='source_budget'):
        retained._prefix(context, source_budget=prefix._SourceReadBudget(2))
    assert source.reads == []


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
