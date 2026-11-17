"""Startup location requests must refuse until all owner reads are budgeted."""

import threading
from types import SimpleNamespace
from io import BytesIO
import importlib.util
from pathlib import Path
import sys
import hashlib

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader


_spec = importlib.util.spec_from_file_location(
    'owner_budget_synthetic_fixtures', Path(__file__).with_name('test_wd_mind_session_content.py'))
_fixtures = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _fixtures
_spec.loader.exec_module(_fixtures)
source_fixtures = _fixtures.source_fixtures
flat_consumer_case = _fixtures.flat_consumer_case
_full_spec = importlib.util.spec_from_file_location(
    'owner_budget_runtime_fixtures', Path(__file__).with_name('test_wd_mind_full_runtime.py'))
_full_fixtures = importlib.util.module_from_spec(_full_spec)
sys.modules[_full_spec.name] = _full_fixtures
_full_spec.loader.exec_module(_full_fixtures)


@pytest.fixture
def synthetic_checkout_pins(monkeypatch):
    root = Path(reader.__file__).resolve().parents[1]
    pins = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in reader._REQUIRED_CODE}
    monkeypatch.setattr(reader, '_REQUIRED_CODE', pins)


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


def test_startup_owner_preflight_refuses_known_overbudget_without_io():
    snapshot = {'messages': {'end_cursor': SimpleNamespace(offset=1024 * 1024 * 1024)}}
    with pytest.raises(ValueError, match='source_budget'):
        runtime._startup_owner_source_budget(snapshot)
    accepted = {'messages': {'end_cursor': SimpleNamespace(offset=1)}}
    assert type(runtime._startup_owner_source_budget(accepted)) is prefix._SourceReadBudget


def test_real_synthetic_owner_selected_rows_charge_source_budget(synthetic_checkout_pins, flat_consumer_case, monkeypatch):
    _package, owner, proposals, _snapshot, _clock, _response = flat_consumer_case
    budget = prefix._SourceReadBudget(1024 * 1024)
    original = prefix._budgeted_read
    charged = []

    def tracked(stream, size, source_budget=None, *, line=False):
        data = original(stream, size, source_budget, line=line)
        if source_budget is budget:
            charged.append(len(data))
        return data

    monkeypatch.setattr(prefix, '_budgeted_read', tracked)
    result = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared', source_budget=budget)
    assert result['results'] and charged
    assert budget._remaining == 1024 * 1024 - sum(charged)


def test_generationless_prewarm_scan_and_hash_share_one_allowance(tmp_path):
    source = tmp_path / 'events.jsonl'
    raw = b'{}\n'
    source.write_bytes(raw)
    with prefix.bridge._open_log(source) as stream:
        identity = prefix.bridge._file_identity(stream)
    cursor = prefix.bridge.BridgeCursor(len(raw), identity, None)
    budget = prefix._SourceReadBudget(2 * len(raw))
    rows = prefix.read_complete_generationless_prefix_rows(
        str(source), str(tmp_path / 'events.generation.json'), cursor, reader._sha(raw), source_budget=budget)
    assert rows and budget._remaining == 0
    with pytest.raises(prefix.PrefixSnapshotRefused):
        prefix.read_complete_generationless_prefix_rows(
            str(source), str(tmp_path / 'events.generation.json'), cursor, reader._sha(raw),
            source_budget=prefix._SourceReadBudget(2 * len(raw) - 1))


def test_real_retained_query_charges_three_prefixes_and_selected_rows(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch):
    import json

    _package, source_owner, _proposals, snapshot, clock, _response = flat_consumer_case
    _projection, context, _ = source_owner._gate.verify_snapshot(snapshot)
    records = json.loads(snapshot['projection_bytes'])['messages']['records']
    owner = object.__new__(runtime._SingleManifestOwner)
    retained = object.__new__(runtime._RetainedFlatQueryState)
    adapter = SimpleNamespace(query=lambda _text: [1.0] + [0.0] * 383)
    handler = SimpleNamespace(_retained=retained, _adapter=adapter)
    owner._generation = (None, snapshot, handler)
    owner._retained = retained
    budget = prefix._SourceReadBudget(1024 * 1024)
    owner._source_budget = budget
    retained._owner, retained._handler = owner, handler
    retained._memory = SimpleNamespace(ids=source_owner._gate._ids, vectors=source_owner._gate._vectors)
    retained._records = {record['record_id']: record for record in records}
    retained._source_owner = source_owner
    monkeypatch.setattr(runtime._RetainedFlatQueryState, '_controls', lambda _self: context)
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    charged = []
    original = prefix._budgeted_read
    def tracked(stream, size, source_budget=None, *, line=False):
        data = original(stream, size, source_budget, line=line)
        if source_budget is budget:
            charged.append(len(data))
        return data
    monkeypatch.setattr(prefix, '_budgeted_read', tracked)
    result = retained.query(lane='codex-lead-1', scope='shared', query='current task active work next step recent status')
    owner._check_candidate_delivery(source_budget=budget)
    assert result['results']
    assert sum(charged) > 3 * context['messages']['end_cursor'].offset
    assert budget._remaining == 1024 * 1024 - sum(charged)


def test_rotation_callback_charges_its_real_checkpoint_read(tmp_path, monkeypatch):
    class Source(BytesIO):
        def __init__(self):
            super().__init__(b'\n')
            self.reads = 0
        def close(self):
            pass
        def read(self, size):
            self.reads += 1
            return super().read(size)

    source = tmp_path / 'events.jsonl'
    sidecar = tmp_path / 'events.generation.json'
    cursor = SimpleNamespace(offset=1, file_identity='synthetic-id', generation=None)
    snapshot = {'projection_bytes': b'synthetic', 'projection_sha256': 'a' * 64}
    controls = (b'controls',)
    binding = object.__new__(runtime._PrivateBinding)
    binding._PrivateBinding__snapshot = snapshot
    binding._PrivateBinding__bundle = tmp_path
    binding._PrivateBinding__manifest = 'b' * 64
    binding._PrivateBinding__controls = controls
    binding._PrivateBinding__args = {'enabled': True, 'bundle_root': str(tmp_path),
        'expected_manifest_sha256': 'b' * 64, 'source_path': str(source), 'generation_path': str(sidecar),
        'cursor': cursor, 'expected_prefix_sha256': 'c' * 64}
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._generation = (binding, snapshot, None)
    owner._source_budget = prefix._SourceReadBudget(1)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *_args: controls)
    monkeypatch.setattr(prefix, '_deployment_source', lambda *_args: (source, sidecar, 'd' * 64))
    monkeypatch.setattr(reader, '_inputs', lambda *_args, **_kwargs: {
        'decision': {'source_prefix': {'sha256': 'c' * 64}},
        'messages': {'end_cursor': cursor, 'source_path': str(source), 'generation_path': str(sidecar)}})
    stream = Source()
    monkeypatch.setattr(runtime.bridge, '_open_log', lambda _source: stream)
    monkeypatch.setattr(prefix, '_fence', lambda *_args: 1)
    assert owner._rotation_source_check() == snapshot['projection_sha256']
    assert stream.reads == 1 and owner._source_budget._remaining == 0
    with pytest.raises(ValueError, match='source_budget'):
        owner._rotation_source_check()
    assert stream.reads == 1


def test_generationless_pin_matches_canonical_repository_bytes():
    raw = Path(prefix.__file__).read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw).hexdigest() == reader._REQUIRED_CODE[
        'tools/wd_mind_absent_generation_prefix_readonly.py']


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
def test_located_request_uses_same_budget_for_query_and_final_check(monkeypatch, located):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._generation = (None, {'messages': {'end_cursor': SimpleNamespace(offset=5)}},
                         SimpleNamespace(_retained=None))
    owner._state = 'READY'
    owner._lock = threading.Lock()
    owner._source_budget = None
    owner._prewarm_generation = owner._generation
    retained = object()
    owner._retained = owner._handler._retained = retained
    for name in ('_check_factory_or_hold', '_check_policy_anchor'):
        monkeypatch.setattr(runtime._SingleManifestOwner, name, lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: self._retained)
    seen = []
    def query(**_kwargs):
        seen.append(owner._source_budget)
        return {'status': 'ok'}
    def final_check(_self, *, source_budget=None):
        seen.append(source_budget)
    monkeypatch.setattr(runtime, 'map_pipe_candidates', query)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_candidate_delivery', final_check)
    request = {'schema': 'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    assert (owner.located_candidates if located else owner.candidates)(request) == {'status': 'ok'}
    assert type(seen[0]) is prefix._SourceReadBudget and seen[0] is seen[1]
    assert owner._source_budget is None and owner._state == 'READY'


def test_located_request_preflight_refusal_preserves_ready(monkeypatch):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._generation = (None, {'messages': {'end_cursor': SimpleNamespace(offset=1024 ** 3)}},
                         SimpleNamespace(_retained=None))
    owner._state = 'READY'
    owner._lock = threading.Lock()
    owner._source_budget = None
    owner._prewarm_generation = owner._generation
    retained = object()
    owner._retained = owner._handler._retained = retained
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory_or_hold',
                        lambda _self: pytest.fail('no source or factory work is allowed'))
    request = {'schema': 'wd.mind-pipe-request.v2', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.located_candidates(request)
    assert owner._state == 'READY' and owner._retained is retained


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
def test_bad_candidate_request_preserves_ready(monkeypatch, located):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._state = 'READY'
    owner._retained = object()
    retained = owner._retained
    request = {'schema': 'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id': 'startup_work_overview', 'nonce': 'invalid', 'lane': 'codex-lead-1', 'scope': 'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        (owner.located_candidates if located else owner.candidates)(request)
    assert owner._state == 'READY' and owner._retained is retained


@pytest.mark.parametrize('fault', [False, True], ids=['prewarm_ok', 'prewarm_fails'])
def test_owner_prewarm_runs_before_ready_and_uses_separate_budget(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    _runtime, _adapter, binding, factory, _calls = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda _self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    seen = []
    def prewarm(owner, *, source_budget=None):
        seen.append((owner._state, source_budget, owner._source_budget))
        if fault:
            raise ValueError('synthetic prewarm refusal')
        owner._retained = owner._handler._retained = object()
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', prewarm)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    status = owner.start()
    assert len(seen) == 1 and seen[0][0] == 'STARTING'
    assert seen[0][1] is seen[0][2] and type(seen[0][1]) is prefix._SourceReadBudget
    assert seen[0][1]._remaining == 2 * 1024 ** 3
    if fault:
        assert status['state'] == 'HOLD' and owner._source_budget is seen[0][1]
    else:
        assert status['state'] == 'READY' and owner._source_budget is None
        assert owner._prewarm_generation is owner._generation


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


def test_nonlocated_request_refuses_without_owner_io(monkeypatch):
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
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.candidates(request)
    assert owner._state == 'READY'
