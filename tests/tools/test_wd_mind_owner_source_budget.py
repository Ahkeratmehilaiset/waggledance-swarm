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


def _instrument_source_streams(monkeypatch, source, tagger=None):
    """Count every source read, including reads bypassing a budget helper."""
    dependencies = reader._narrow_dependencies()
    original = prefix.bridge._open_log
    target = Path(source).resolve()
    reads = []
    lock = threading.Lock()

    class Stream:
        def __init__(self, wrapped):
            self._wrapped = wrapped
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self._wrapped.close()
        def __getattr__(self, name):
            return getattr(self._wrapped, name)
        def read(self, *args):
            data = self._wrapped.read(*args)
            with lock:
                reads.append(('read', len(data)) if tagger is None else ('read', len(data), tagger()))
            return data
        def readline(self, *args):
            data = self._wrapped.readline(*args)
            with lock:
                reads.append(('readline', len(data)) if tagger is None else ('readline', len(data), tagger()))
            return data

    def opened(path):
        wrapped = original(path)
        return Stream(wrapped) if Path(path).resolve() == target else wrapped

    monkeypatch.setattr(prefix.bridge, '_open_log', opened)
    # These tests deliberately wrap the reader entry point; preserve its
    # previously checked dependencies for the synthetic instrumentation.
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: dependencies)
    return reads


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


@pytest.mark.parametrize('segment_bytes', [None, 1024 * 1024], ids=['whole-prefix', 'segments'])
def test_retained_prefix_reserves_before_open_source_read(monkeypatch, segment_bytes):
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', segment_bytes)

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
                            'end_cursor': SimpleNamespace(offset=3, file_identity='id', generation=None)},
               'decision': {'source_prefix': {'sha256': reader._sha(b'abc')}}}
    retained = object.__new__(runtime._RetainedFlatQueryState)
    with pytest.raises(ValueError, match='source_budget'):
        retained._prefix(context, source_budget=prefix._SourceReadBudget(2))
    assert source.reads == []
    assert retained._segments is None


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


@pytest.mark.parametrize('exhaust', [False, True], ids=['complete', 'mid_prewarm_exhausted'])
def test_real_generationless_prewarm_charges_every_source_stream_read(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch, tmp_path, exhaust):
    import json

    package, original_owner, _proposals, original_snapshot, _clock, _response = flat_consumer_case
    snapshot = dict(original_snapshot, messages=dict(original_snapshot['messages']))
    source = Path(snapshot['messages']['source_path'])
    sidecar = Path(snapshot['messages']['generation_path'])
    assert tmp_path.resolve() in source.resolve().parents and tmp_path.resolve() in sidecar.resolve().parents
    old_cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(old_cursor.offset, old_cursor.file_identity, None)
    sidecar.unlink()
    snapshot['messages']['generation_path'] = str(source.with_name('events.generation.json'))
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {key: value for key, value in snapshot.items()
              if key not in ('projection_bytes', 'projection_sha256')}
    projection = reader.rederive_complete_message_projection(**inputs)
    snapshot['projection_bytes'] = reader._serialized(projection)
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    controls = (str(source), snapshot['messages']['generation_path'], original_owner._controls[2],
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_args: controls)
    gate = original_owner._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)
    reads = _instrument_source_streams(monkeypatch, source)
    limit = (3 * old_cursor.offset + 1) if exhaust else (5 * old_cursor.offset)
    budget = prefix._SourceReadBudget(limit)
    def rebuild():
        return package.create_consumer_flat_source_owner(enabled=True, bundle_root=original_owner._root,
            expected_manifest_sha256=original_owner._pin, snapshot=snapshot, flat_manifest_bytes=flat_bytes,
            flat_manifest_sha256=reader._sha(flat_bytes), ids=gate._ids, vectors=gate._vectors,
            encoder_binding_bytes=gate._encoder, source_budget=budget)
    if exhaust:
        with pytest.raises(package.SnapshotPackageRefused) as refused:
            rebuild()
        assert str(refused.value) == 'content_snapshot_package_refused'
        assert refused.value.__cause__ is None
        assert refused.value.__suppress_context__ is True
    else:
        assert rebuild() is not None
    assert any(kind == 'readline' for kind, _ in reads)
    assert sum(size for _, size in reads) == (3 if exhaust else 5) * old_cursor.offset
    assert budget._remaining == limit - sum(size for _, size in reads)


@pytest.mark.parametrize('scenario', ['success', 'append', 'shorter', 'prefix_changed', 'bad_request', 'over_budget'])
def test_synthetic_owner_start_prewarm_then_candidates_account_source_reads(
        synthetic_checkout_pins, flat_consumer_case, tmp_path, monkeypatch, scenario):
    """Real owner/prewarm/query with synthetic source, pins and worker doubles.

    This deliberately opts in only inside the test; it does not certify a live
    model, process boundary, operator policy anchor, or the deployed default.
    """
    import json

    package, original, _proposals, old, clock, _response = flat_consumer_case
    # The consumer fixture uses a dummy manifest pin; isolate the verified
    # module loader from this source-budget test, whose real source gates and
    # read-accounting assertions remain in use. Loader coverage is separate.
    monkeypatch.setattr(runtime._PrivateBinding, '_snapshot_support', lambda _self: package)
    snapshot = dict(old, messages=dict(old['messages']))
    source = Path(snapshot['messages']['source_path'])
    sidecar = Path(snapshot['messages']['generation_path'])
    assert tmp_path.resolve() in source.resolve().parents and tmp_path.resolve() in sidecar.resolve().parents
    cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(cursor.offset, cursor.file_identity, None)
    sidecar.unlink()
    snapshot['messages']['generation_path'] = str(source.with_name('events.generation.json'))
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {key: value for key, value in snapshot.items()
              if key not in ('projection_bytes', 'projection_sha256')}
    snapshot['projection_bytes'] = reader._serialized(reader.rederive_complete_message_projection(**inputs))
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    controls = (str(source), snapshot['messages']['generation_path'], original._controls[2],
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_args: controls)
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    gate = original._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)

    _, adapter_module, binding, factory, launches = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    binding._PrivateBinding__snapshot = snapshot
    binding._PrivateBinding__bundle = Path(original._root)
    binding._PrivateBinding__manifest = original._pin
    binding._PrivateBinding__policy_pin = reader._sha(snapshot['identifier_policy_binding'][0])
    factory._bundle, factory._manifest = str(original._root), original._pin
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda _self: snapshot)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    class Adapter:
        def query(self, _text):
            return [1.0] + [0.0] * 383
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **_kwargs: Adapter())
    def build(handler):
        handler._memory = runtime._PrivateFlatMemory(binding, snapshot, flat_bytes, gate._ids,
                                                    gate._vectors, gate._encoder)
        handler._frozen_flat = (flat_bytes, gate._ids, gate._vectors, gate._encoder)
        return {'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'}
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)

    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
        worker_factory=factory, shutdown_timeout=0)
    reads = _instrument_source_streams(monkeypatch, source)
    budgets = []
    original_init = prefix._SourceReadBudget.__init__
    def budget_init(self, limit):
        original_init(self, limit)
        budgets.append((self, limit))
    monkeypatch.setattr(prefix._SourceReadBudget, '__init__', budget_init)
    assert owner.start()['state'] == 'READY'
    assert launches == [True] and len(budgets) == 1
    prewarm_read = sum(size for _kind, size in reads)
    assert prewarm_read > 0 and budgets[0][0]._remaining == budgets[0][1] - prewarm_read
    assert owner._prewarm_generation is owner._generation and owner._source_budget is None

    request = {'schema':'wd.mind-pipe-request.v2', 'query_id':'startup_work_overview',
               'nonce':'0' * 64, 'lane':'codex-lead-1', 'scope':'shared'}
    if scenario == 'append':
        with source.open('ab') as stream:
            stream.write(b'{"synthetic":"appended"}\n')
    elif scenario == 'shorter':
        with source.open('r+b') as stream:
            stream.truncate(cursor.offset - 1)
    elif scenario == 'prefix_changed':
        with source.open('r+b') as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
    elif scenario == 'bad_request':
        bad = dict(request, nonce='invalid')
        with pytest.raises(runtime.FullRuntimeRefused):
            owner.located_candidates(bad)
        assert owner.status()['state'] == 'READY' and len(budgets) == 1
        assert sum(size for _kind, size in reads) == prewarm_read
    elif scenario == 'over_budget':
        monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', 1)
    if scenario in ('shorter', 'prefix_changed', 'over_budget'):
        with pytest.raises(runtime.FullRuntimeRefused):
            owner.located_candidates(request)
        assert owner.status()['state'] == ('READY' if scenario == 'over_budget' else 'HOLD')
        if scenario == 'over_budget':
            assert len(budgets) == 1 and sum(size for _kind, size in reads) == prewarm_read
        owner.close()
        return
    result = owner.located_candidates(request)
    assert result['status'] == 'CANDIDATES' and result['candidates']
    assert len(budgets) == 2
    query_read = sum(size for _kind, size in reads) - prewarm_read
    assert query_read > 0 and budgets[1][0]._remaining == budgets[1][1] - query_read
    assert owner._source_budget is None and owner.status()['state'] == 'READY'
    owner.close()


def test_real_retained_query_charges_three_prefixes_and_selected_rows(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch):
    import json

    # Strict control mode: three whole-prefix hashes. The segment mode is stated in
    # test_wd_mind_owner_segment_checks.py and by the request test below.
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', None)
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


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
@pytest.mark.parametrize('exhaust', [False, True], ids=['complete', 'mid_query_exhausted'])
@pytest.mark.parametrize('segment_bytes', [None, 1024 * 1024], ids=['whole-prefix', 'segments'])
def test_candidate_request_charges_real_synthetic_source_reads(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch, located, exhaust, segment_bytes):
    import json

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', segment_bytes)
    _package, source_owner, _proposals, snapshot, clock, _response = flat_consumer_case
    _projection, context, _ = source_owner._gate.verify_snapshot(snapshot)
    records = json.loads(snapshot['projection_bytes'])['messages']['records']
    owner = object.__new__(runtime._SingleManifestOwner)
    retained = object.__new__(runtime._RetainedFlatQueryState)
    handler = object.__new__(runtime._FlatRequestHandler)
    memory = SimpleNamespace(ids=source_owner._gate._ids, vectors=source_owner._gate._vectors)
    handler._retained, handler._memory = retained, memory
    handler._adapter = SimpleNamespace(query=lambda _text: [1.0] + [0.0] * 383)
    handler._lane, handler._lanes = 'codex-lead-1', frozenset({'codex-lead-1'})
    handler._lock, handler._closed, handler._failed = threading.Lock(), False, False
    owner._generation = (None, snapshot, handler)
    owner._prewarm_generation, owner._retained = owner._generation, retained
    owner._lanes, owner._state, owner._lock = handler._lanes, 'READY', threading.Lock()
    owner._source_budget = None
    retained._owner, retained._handler, retained._memory = owner, handler, memory
    retained._records = {record['record_id']: record for record in records}
    retained._source_owner = source_owner
    monkeypatch.setattr(runtime._RetainedFlatQueryState, '_controls', lambda _self: context)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory_or_hold', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    actual_reads = _instrument_source_streams(monkeypatch, snapshot['messages']['source_path'])
    budgets, flags = [], []
    original = runtime._startup_owner_source_budget
    def capture(snapshot, **kwargs):
        budget = (prefix._SourceReadBudget(context['messages']['end_cursor'].offset + 1)
                  if exhaust else original(snapshot, **kwargs))
        budgets.append(budget)
        flags.append(kwargs)
        return budget
    monkeypatch.setattr(runtime, '_startup_owner_source_budget', capture)
    request = {'schema': 'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id': 'startup_work_overview', 'nonce': '0' * 64,
               'lane': 'codex-lead-1', 'scope': 'shared'}
    call = owner.located_candidates if located else owner.candidates
    if exhaust:
        with pytest.raises(runtime.FullRuntimeRefused) as refused:
            call(request)
        assert str(refused.value) == 'mind_full_runtime_refused'
        assert refused.value.__cause__ is None
        assert refused.value.__suppress_context__ is True
        consumed = sum(size for _, size in actual_reads)
        limit = context['messages']['end_cursor'].offset + 1
        assert context['messages']['end_cursor'].offset <= consumed <= limit
        assert budgets[0]._remaining == limit - consumed
        assert owner._state == 'HOLD' and owner._source_budget is budgets[0]
        return
    result = call(request)
    assert result['status'] == 'CANDIDATES' and result['candidates']
    assert len({id(item) for item in budgets}) == 1
    assert sum(size for _, size in actual_reads) > 3 * context['messages']['end_cursor'].offset
    assert budgets[0]._remaining == 1024 ** 3 - sum(size for _, size in actual_reads)
    assert owner._state == 'READY' and owner._source_budget is None
    # The first request is preflighted without a segment table. In segment mode it built one, so the
    # preflight of the second request is told that no whole pass is needed; strict mode never has one.
    assert flags == [{'segments_ready': False}]
    first_reads = sum(size for _, size in actual_reads)
    assert call(dict(request))['status'] == 'CANDIDATES'
    assert flags[1] == {'segments_ready': segment_bytes is not None} and len(budgets) == 2
    assert budgets[1]._remaining == 1024 ** 3 - (sum(size for _, size in actual_reads) - first_reads)
    assert owner._state == 'READY' and owner._source_budget is None


def test_startup_client_and_owner_account_separate_real_source_reads(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch):
    """In-process synthetic IPC; no live log, process, pipe or native model."""
    import io
    import json
    from tools import wd_bridge_mind_session as session
    from tools import wd_mind_runtime_process as process

    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    package, source_owner, _proposals, snapshot, clock, _response = flat_consumer_case
    _fixtures._located_candidates(flat_consumer_case, monkeypatch)
    source_owner._controls = package._consumer_bundle_controls(source_owner._root, source_owner._pin)
    _projection, context, _ = source_owner._gate.verify_snapshot(snapshot)
    records = json.loads(snapshot['projection_bytes'])['messages']['records']
    owner = object.__new__(runtime._SingleManifestOwner)
    retained = object.__new__(runtime._RetainedFlatQueryState)
    handler = object.__new__(runtime._FlatRequestHandler)
    memory = SimpleNamespace(ids=source_owner._gate._ids, vectors=source_owner._gate._vectors)
    handler._retained, handler._memory = retained, memory
    handler._adapter = SimpleNamespace(query=lambda _text: [1.0] + [0.0] * 383)
    handler._lane, handler._lanes = 'claude-rco-1', frozenset({'claude-rco-1'})
    handler._lock, handler._closed, handler._failed = threading.Lock(), False, False
    owner._generation = (None, snapshot, handler)
    owner._prewarm_generation, owner._retained = owner._generation, retained
    owner._lanes, owner._state, owner._lock = handler._lanes, 'READY', threading.Lock()
    owner._source_budget = None
    retained._owner, retained._handler, retained._memory = owner, handler, memory
    retained._records = {record['record_id']: record for record in records}
    retained._source_owner = source_owner
    monkeypatch.setattr(runtime._RetainedFlatQueryState, '_controls', lambda _self: context)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory_or_hold', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(reader.dt, 'datetime', clock)

    owner_budgets, clients = [], []
    original_budget = runtime._startup_owner_source_budget
    def capture_owner(snapshot, **kwargs):
        budget = original_budget(snapshot, **kwargs)
        owner_budgets.append(budget)
        return budget
    monkeypatch.setattr(runtime, '_startup_owner_source_budget', capture_owner)
    original_resolver = session.create_selected_row_resolver
    def capture_resolver(**kwargs):
        resolver = original_resolver(**kwargs)
        clients.append(resolver)
        return resolver
    monkeypatch.setattr(session, 'create_selected_row_resolver', capture_resolver)
    policy = snapshot['identifier_policy_binding'][0]
    monkeypatch.setattr(session, '_selected_pin_read', lambda *_args: policy)

    class InProcessQuery:
        def __init__(self, request): self.request = request
        def start(self): self.frame = reader._canonical(owner.located_candidates(self.request))
        def wait(self, **_kwargs):
            return {'status': 'CANDIDATES', 'authority': 'none', 'sender_authentication': 'UNKNOWN',
                    'source_verification': 'UNVERIFIED', 'candidate_frame': self.frame}
        def status(self): return {'state': 'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_query', lambda **kwargs: InProcessQuery(kwargs['request']))
    reads = _instrument_source_streams(monkeypatch, snapshot['messages']['source_path'],
        tagger=lambda: 'owner' if owner._source_budget is not None else 'client')
    args = ['--mind-local-candidate-read', '--policy-path', 'synthetic-policy',
            '--policy-sha256', reader._sha(policy), '--approval-reference-sha256', 'c' * 64,
            '--lane', 'claude-rco-1', '--timeout-seconds', '90']
    output, error = io.BytesIO(), io.BytesIO()
    assert session._local_candidate_entry(args, output, error, source_owner._root, source_owner._pin) == 0
    result = json.loads(output.getvalue())
    assert result['results'] and result['authority'] == 'none'
    owner_read = sum(size for _kind, size, tag in reads if tag == 'owner')
    client_read = sum(size for _kind, size, tag in reads if tag == 'client')
    assert owner_read and client_read
    assert owner_read == 1024 ** 3 - owner_budgets[0]._remaining
    assert client_read <= 1024 ** 3 - clients[0]._source_budget._remaining
    assert owner_read + client_read <= 2 * 1024 ** 3


def test_rotation_callback_charges_its_real_checkpoint_read(tmp_path, monkeypatch, capsys):
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
    seen = []
    def callback():
        seen.append((threading.get_ident(), owner._rotation_source_check()))
    worker = threading.Thread(target=callback)
    worker.start()
    worker.join()
    assert seen == [(worker.ident, snapshot['projection_sha256'])]
    assert seen[0][0] != threading.get_ident()
    assert stream.reads == 1 and owner._source_budget._remaining == 0
    with pytest.raises(ValueError, match='^source_budget$') as refused:
        owner._rotation_source_check()
    assert str(refused.value) == 'source_budget'
    assert stream.reads == 1
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


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
    with pytest.raises(runtime.FullRuntimeRefused) as refused:
        owner.located_candidates(request)
    assert str(refused.value) == 'mind_full_runtime_refused'
    assert refused.value.__cause__ is None
    assert refused.value.__suppress_context__ is True
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


@pytest.mark.parametrize('fault', ['missing_retained', 'source_error'])
def test_candidate_integrity_error_holds_without_rebuilding(monkeypatch, fault):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    owner = object.__new__(runtime._SingleManifestOwner)
    retained = object() if fault == 'source_error' else None
    handler = SimpleNamespace(_retained=retained)
    owner._generation = (None, {'messages': {'end_cursor': SimpleNamespace(offset=5)}}, handler)
    owner._prewarm_generation, owner._retained = owner._generation, retained
    owner._lanes, owner._state, owner._lock = frozenset({'codex-lead-1'}), 'READY', threading.Lock()
    owner._source_budget = None
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory_or_hold', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked',
                        lambda _self: pytest.fail('retained must never be rebuilt in candidate request'))
    monkeypatch.setattr(runtime, 'map_pipe_candidates',
                        lambda **_kwargs: (_ for _ in ()).throw(ValueError('synthetic source failure')))
    request = {'schema': 'wd.mind-pipe-request.v2', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.located_candidates(request)
    assert owner._state == 'HOLD' and owner._retained is None and handler._retained is None
    assert type(owner._source_budget) is prefix._SourceReadBudget


def test_lost_prewarm_generation_is_internal_fault_and_holds(monkeypatch):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    owner = object.__new__(runtime._SingleManifestOwner)
    handler = SimpleNamespace(_retained=object())
    owner._generation = (None, {'messages': {'end_cursor': SimpleNamespace(offset=5)}}, handler)
    owner._prewarm_generation, owner._retained = object(), handler._retained
    owner._lanes, owner._state, owner._lock = frozenset({'codex-lead-1'}), 'READY', threading.Lock()
    owner._source_budget = None
    request = {'schema': 'wd.mind-pipe-request.v2', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.located_candidates(request)
    assert owner._state == 'HOLD' and owner._retained is None and handler._retained is None


@pytest.mark.parametrize('fault', [None, 'generic', 'budget'], ids=['prewarm_ok', 'prewarm_fails', 'prewarm_budget'])
def test_owner_prewarm_runs_before_ready_and_uses_separate_budget(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    _runtime, _adapter, binding, factory, _calls = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    # Real prewarm budget requires an exact positive source-prefix offset.
    binding._PrivateBinding__snapshot['messages'] = {'end_cursor': SimpleNamespace(offset=5)}
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda _self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    seen = []
    def prewarm(owner, *, source_budget=None):
        seen.append((owner._state, source_budget, owner._source_budget))
        if fault == 'budget':
            raise runtime._StartupSourceBudgetRefused('source_budget')
        if fault == 'generic':
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
        if fault == 'budget':
            assert owner.start_diagnostic() == {'reason':'SOURCE_PREWARM', 'exception_type':'UNKNOWN',
                                                 'authority':'none'}
    else:
        assert status['state'] == 'READY' and owner._source_budget is None
        assert owner._prewarm_generation is owner._generation


@pytest.mark.parametrize('gate_on', [False, True], ids=['off', 'preflight_over_budget'])
def test_startup_preflight_budget_diagnostic_before_retained_prewarm(tmp_path, monkeypatch, gate_on):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', gate_on)
    monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', 64)
    _runtime, _adapter, binding, factory, launches = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    binding._PrivateBinding__snapshot['messages'] = {'end_cursor': SimpleNamespace(offset=5)}
    builds = []
    def build(_self):
        builds.append(True)
        return {'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'}
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked',
                        lambda *_a, **_kw: pytest.fail('prewarm must not read'))
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    state = owner.start()
    assert owner._source_budget is None
    if gate_on:
        assert launches == [] and builds == []
        assert state['state'] == 'HOLD'
        assert owner.start_diagnostic() == {'reason':'SOURCE_BUDGET', 'exception_type':'UNKNOWN', 'authority':'none'}
    else:
        assert launches == [True] and builds == [True]
        assert state['state'] == 'READY'
        assert owner.start_diagnostic() == {'reason':'NONE', 'exception_type':'NONE', 'authority':'none'}


@pytest.mark.parametrize('fault', ['none', 'budget_refusal', 'source_error'])
@pytest.mark.parametrize('rotating', [False, True], ids=['single', 'rotating'])
def test_refresh_prewarm_precedes_ready_and_preserves_worker(tmp_path, monkeypatch, fault, rotating):
    _runtime, owner, old, new = _full_fixtures._refresh_generation_test_owner(monkeypatch, tmp_path)
    # Only the replacement snapshot is prewarmed after the generation swap.
    new[1]['messages'] = {'end_cursor': SimpleNamespace(offset=5)}
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    old_generation, old_handler, worker = owner._generation, owner._handler, owner._owner
    if rotating:
        class Rotator:
            def __init__(self):
                self._lock, self._snapshot, self.owner = threading.Lock(), old[1]['projection_sha256'], worker
            def _check_worker_identity(self): pass
            def _check_source(self): pass
        class Adapter:
            def __init__(self, rotator):
                self._rotator, self._snapshot = rotator, old[1]['projection_sha256']
            def _static_check(self): pass
        rotator = Rotator()
        adapter = Adapter(rotator)
        owner._rotation_enabled = True
        owner._rotation_type, owner._rotation_anchor = Rotator, rotator
        owner._client, owner._adapter = rotator, adapter
        old_handler._rotator, old_handler._adapter = rotator, adapter
    owner._prewarm_generation = old_generation
    owner._retained = old_handler._retained = object()
    monkeypatch.setattr(runtime, '_stage_refresh_generation',
                        lambda current: _full_fixtures._test_staged_refresh(runtime, current, new))
    seen = []
    def prewarm(current, *, source_budget=None):
        seen.append((current._state, current._generation, source_budget, current._source_budget,
                     current._retained, current._owner))
        assert not current._lock.acquire(blocking=False)
        assert not old_handler._lock.acquire(blocking=False)
        if rotating:
            assert not rotator._lock.acquire(blocking=False)
            # This real handler method accesses the retained worker while the
            # rotator lock is held; it must not re-acquire that lock.
            current._handler._running_owner()
        if fault == 'budget_refusal':
            raise runtime._StartupSourceBudgetRefused('source_budget')
        if fault == 'source_error':
            raise ValueError('synthetic source failure')
        current._retained = current._handler._retained = object()
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', prewarm)
    if fault == 'none':
        assert owner._refresh_generation(enabled=True)['status'] == 'REFRESHED'
        assert owner._state == 'READY' and owner._generation is not old_generation
        assert owner._prewarm_generation is owner._generation and owner._source_budget is None
        assert owner._retained is owner._handler._retained and owner._retained is not None
    else:
        with pytest.raises(runtime.FullRuntimeRefused):
            owner._refresh_generation(enabled=True)
        assert owner._state == 'HOLD' and owner._generation is old_generation
        assert owner._retained is None and old_handler._retained is None
        assert owner._source_budget is seen[0][2]
    assert len(seen) == 1 and seen[0][0] == 'STARTING'
    assert seen[0][1] is not old_generation and seen[0][2] is seen[0][3]
    assert type(seen[0][2]) is prefix._SourceReadBudget and seen[0][2]._remaining == 2 * 1024 ** 3
    assert seen[0][4] is None and seen[0][5] is worker and owner._owner is worker
    if rotating:
        expected = new[1]['projection_sha256'] if fault == 'none' else old[1]['projection_sha256']
        assert rotator._snapshot == adapter._snapshot == expected
        assert rotator._lock.acquire(blocking=False)
        rotator._lock.release()


@pytest.mark.parametrize('direct', [False, True])
def test_located_startup_request_refuses_before_owner_io_and_preserves_ready(monkeypatch, direct):
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
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
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
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
