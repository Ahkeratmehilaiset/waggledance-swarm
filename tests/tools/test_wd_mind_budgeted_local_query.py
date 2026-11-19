"""Fast synthetic budget preflight tests; no source files or workers started."""
from types import SimpleNamespace
import threading
import pytest
from tools import wd_mind_full_runtime as runtime


def snapshot(offset):
    return {'messages': {'end_cursor': SimpleNamespace(offset=offset)}}


@pytest.mark.parametrize('offset', [0, -1, True, 1.2, '100'])
def test_offsets_fail_closed(offset):
    with pytest.raises((ValueError, runtime._StartupSourceBudgetRefused)):
        runtime._local_query_source_budget(snapshot(offset))
    with pytest.raises((ValueError, runtime._StartupSourceBudgetRefused)):
        runtime._prewarm_source_budget(snapshot(offset))


def test_query_reserves_two_selected_row_passes(monkeypatch):
    # Strict control mode; the segment-mode boundary is in test_wd_mind_owner_segment_checks.py.
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', None)
    offset = 100
    expected = 3 * offset + 6 * (4 * 1024 * 1024 + 1) + 3
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', expected - 1)
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        runtime._local_query_source_budget(snapshot(offset))
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', expected)
    assert runtime._local_query_source_budget(snapshot(offset))._remaining == expected


def test_prewarm_reserves_line_read_headroom(monkeypatch):
    offset = 100
    expected = 5 * offset + 4 * 1024 * 1024 + 1
    monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', expected - 1)
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        runtime._prewarm_source_budget(snapshot(offset))
    monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', expected)
    assert runtime._prewarm_source_budget(snapshot(offset))._remaining == expected


def test_default_off_does_not_touch_lock_or_source(monkeypatch):
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    class Unexpected:
        def acquire(self, **kwargs):
            raise AssertionError('must not acquire owner lock')
    owner._lock = Unexpected()
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='synthetic')


def test_missing_prewarm_holds_owner_and_clears_budget(monkeypatch):
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._lock = threading.Lock()
    owner._state = 'READY'
    owner._generation = (object(), snapshot(100), object.__new__(runtime._FlatRequestHandler))
    owner._prewarm_generation = None
    owner._retained, owner._handler._retained = None, None
    owner._source_budget = None
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='synthetic')
    assert owner._state == 'HOLD' and owner._source_budget is None


def query_owner(monkeypatch, fault=None):
    """Exercise routing/evidence with fakes; not end-to-end source accounting."""
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lanes = frozenset({'codex-lead-1'})
    owner._lock = threading.Lock()
    owner._state, owner._source_budget = 'READY', None
    calls = []
    class Binding:
        _PrivateBinding__controls = (b'synthetic-fleet',)
        def _immutable_checkpoint(self, _snapshot, *, source_budget=None):
            assert source_budget is not None
            calls.append(('callback', source_budget))
            source_budget.reserve(1)
    binding = Binding()
    snap = snapshot(100)
    snap['projection_sha256'] = 'f'*64
    handler = object.__new__(runtime._FlatRequestHandler)
    owner._generation = (binding, snap, handler)
    owner._prewarm_generation = owner._generation
    record_id = 'synthetic-id'
    doc = runtime.reader._sha(record_id.encode())
    record = {'time_utc':'2020-01-01T00:00:00Z', 'meta':{'agent':'codex-lead-1'},
              'provenance':{'source_event_ref':'b'*64}}
    def resolve(*, proposals, lane, scope, source_budget=None):
        assert source_budget is not None
        calls.append(('resolve', source_budget))
        source_budget.reserve(5)
        if fault == 'source' or (fault == 'second_source' and sum(name == 'resolve' for name, _ in calls) == 2):
            raise ValueError('synthetic source mutation')
        return {'results':[{'text':'synthetic', 'source_event_sha256':'b'*64}]}
    def prefix(_context, *, source_budget=None):
        assert source_budget is not None
        calls.append(('prefix', source_budget))
        source_budget.reserve(100)
    retained = SimpleNamespace(_source_owner=SimpleNamespace(resolve=resolve),
        _records={record_id:record}, _controls=lambda: None, _prefix=prefix)
    owner._retained, handler._retained = retained, retained
    hit = {'ref_sha256':'a'*64, 'doc_id_sha256':doc, 'stored_text':'synthetic',
           'source_reference':{'verified':'MATCH_SELECTED_ROW', 'sha256':'b'*64},
           'source_agent':'codex-lead-1', 'source_time_utc':record['time_utc']}
    def handle(self, request, lane):
        # Stand in for the retained handler's first resolve; callback below is
        # the real owner's rotation callback with a synthetic binding.
        resolve(proposals=[{}], lane=lane, scope='own', source_budget=owner._source_budget)
        owner._rotation_source_check()
        return {'schema':'wd.mind-flat-owner-response.v1', 'lane':lane, 'scope':'own',
                'authority':'none', 'snapshot_freshness':'UNKNOWN',
                'result':{'schema':'wd.mind-flat-f32-query.v1', 'authority':'none',
                          'status':'SEMANTIC_CANDIDATE', 'results':[hit]}}
    monkeypatch.setattr(runtime._FlatRequestHandler, '_handle_for_lane', handle)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: retained)
    monkeypatch.setattr(runtime, '_local_generation_policy_pin', lambda owner: 'c'*64)
    monkeypatch.setattr(runtime.reader, 'decode_text', lambda text: text)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    return owner, calls


def test_two_resolutions_and_callback_share_query_budget(monkeypatch):
    owner, calls = query_owner(monkeypatch)
    result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert result['results'][0]['source_event_sha256'] == 'b'*64
    assert [name for name, _ in calls] == ['resolve', 'callback', 'resolve', 'prefix']
    assert len({id(budget) for _, budget in calls}) == 1
    assert owner._source_budget is None and owner._state == 'READY'


def test_bad_caller_keeps_ready_and_source_failure_holds(monkeypatch):
    owner, calls = query_owner(monkeypatch, fault='source')
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='')
    assert owner._state == 'READY' and calls == [] and owner._source_budget is None
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert owner._state == 'HOLD' and owner._source_budget is not None
    assert owner._retained is None and owner._handler._retained is None


def test_second_source_resolution_failure_holds(monkeypatch):
    owner, calls = query_owner(monkeypatch, fault='second_source')
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert [name for name, _ in calls] == ['resolve', 'callback', 'resolve']
    assert owner._state == 'HOLD' and owner._source_budget is not None
    assert owner._retained is None and owner._handler._retained is None


def test_malformed_snapshot_holds_and_known_size_refusal_stays_ready(monkeypatch):
    owner, _ = query_owner(monkeypatch)
    binding, _, handler = owner._generation
    owner._generation = (binding, {}, handler)
    owner._prewarm_generation = owner._generation
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert owner._state == 'HOLD' and owner._retained is None
    owner, _ = query_owner(monkeypatch)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', 1)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert owner._state == 'READY' and owner._retained is not None and owner._source_budget is None


def test_possible_late_rotation_callback_keeps_held_budget(monkeypatch):
    owner, calls = query_owner(monkeypatch, fault='second_source')
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    budget = owner._source_budget
    remaining = budget._remaining
    owner._rotation_source_check()
    assert owner._source_budget is budget
    assert calls[-1] == ('callback', budget) and budget._remaining == remaining - 1


def test_each_successful_query_gets_fresh_budget(monkeypatch):
    owner, calls = query_owner(monkeypatch)
    for _ in range(2):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert calls[0][1] is calls[3][1]
    assert calls[4][1] is calls[7][1]
    assert calls[0][1] is not calls[4][1]
    assert owner._state == 'READY' and owner._source_budget is None
