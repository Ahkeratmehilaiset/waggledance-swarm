"""Synthetic refresh, real retained prewarm and candidate source accounting.

The source, controls, encoder and worker are synthetic. No subprocess,
model, live source, installed bundle or real bridge is touched.
"""

import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest

from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_full_runtime as runtime


_spec = importlib.util.spec_from_file_location(
    'refresh_budget_fixtures', Path(__file__).with_name('test_wd_mind_owner_source_budget.py'))
_fixtures = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _fixtures
_spec.loader.exec_module(_fixtures)
source_fixtures = _fixtures.source_fixtures
flat_consumer_case = _fixtures.flat_consumer_case
synthetic_checkout_pins = _fixtures.synthetic_checkout_pins


@pytest.mark.parametrize('fault', ['none', 'prewarm_source_mutation', 'prewarm_budget', 'prewarm_mid_budget', 'candidate_source_mutation'])
def test_refresh_uses_real_prewarm_and_candidate_with_metered_source(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch, tmp_path, fault):
    package, original_owner, _proposals, original_snapshot, clock, _response = flat_consumer_case
    snapshot = dict(original_snapshot, messages=dict(original_snapshot['messages']))
    source = Path(snapshot['messages']['source_path'])
    sidecar = Path(snapshot['messages']['generation_path'])
    assert tmp_path.resolve() in source.resolve().parents
    assert tmp_path.resolve() in sidecar.resolve().parents
    old_cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(old_cursor.offset, old_cursor.file_identity, None)
    sidecar.unlink()
    sidecar = source.with_name('events.generation.json')
    snapshot['messages']['generation_path'] = str(sidecar)
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {key: value for key, value in snapshot.items()
              if key not in ('projection_bytes', 'projection_sha256')}
    projection = reader.rederive_complete_message_projection(**inputs)
    snapshot['projection_bytes'] = reader._serialized(projection)
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    controls = (str(source), str(sidecar), original_owner._controls[2],
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_args: controls)
    gate = original_owner._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)

    _rt, owner, _old, _unused = _fixtures._full_fixtures._refresh_generation_test_owner(monkeypatch, tmp_path)
    old_generation, old_handler, worker = owner._generation, owner._handler, owner._owner
    policy_pin = reader._sha(snapshot['identifier_policy_binding'][0])
    old_binding = owner._binding
    old_binding._PrivateBinding__bundle = Path(original_owner._root)
    old_binding._PrivateBinding__manifest = original_owner._pin
    old_binding._PrivateBinding__policy_pin = policy_pin
    new_binding = runtime._PrivateBinding(snapshot, Path(original_owner._root), original_owner._pin,
                                         old_binding._PrivateBinding__controls, {}, policy_pin)
    memory = runtime._PrivateFlatMemory(new_binding, snapshot, flat_bytes, gate._ids, gate._vectors, gate._encoder)
    class Adapter:
        def query(self, _text):
            return [1.0] + [0.0] * 383
    owner._adapter = old_handler._adapter = Adapter()
    staged = (new_binding, snapshot, memory)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    if fault == 'prewarm_budget':
        monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', 3 * old_cursor.offset + 1)
    elif fault == 'prewarm_mid_budget':
        # Inject exhaustion after the real preflight has accepted the budget.
        original_prewarm = runtime._prewarm_source_budget
        def limited_prewarm(value):
            budget = original_prewarm(value)
            budget._remaining = 3 * old_cursor.offset + 1
            return budget
        monkeypatch.setattr(runtime, '_prewarm_source_budget', limited_prewarm)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _self: None)
    monkeypatch.setattr(runtime, '_stage_refresh_generation',
                        lambda current: _fixtures._full_fixtures._test_staged_refresh(runtime, current, staged))
    monkeypatch.setattr(reader.dt, 'datetime', clock)

    dependencies = reader._narrow_dependencies()
    original_open = prefix.bridge._open_log
    readings, budgets, created_budgets = [], [], []
    original_init = prefix._SourceReadBudget.__init__
    def record_init(budget, limit):
        original_init(budget, limit)
        created_budgets.append(budget)
    monkeypatch.setattr(prefix._SourceReadBudget, '__init__', record_init)
    original_reserve = prefix._SourceReadBudget.reserve
    def reserve(budget, size):
        if not any(item is budget for item in budgets):
            budgets.append(budget)
        return original_reserve(budget, size)
    monkeypatch.setattr(prefix._SourceReadBudget, 'reserve', reserve)
    class Stream:
        def __init__(self, wrapped): self._wrapped = wrapped
        def __enter__(self): return self
        def __exit__(self, *_args): self._wrapped.close()
        def __getattr__(self, name): return getattr(self._wrapped, name)
        def _record(self, data):
            readings.append((owner._state, len(data)))
            if fault == 'prewarm_source_mutation' and owner._state == 'STARTING' and not sidecar.exists():
                sidecar.write_bytes(b'{}')
            return data
        def read(self, *args): return self._record(self._wrapped.read(*args))
        def readline(self, *args): return self._record(self._wrapped.readline(*args))
    def opened(path):
        stream = original_open(path)
        return Stream(stream) if Path(path).resolve() == source.resolve() else stream
    monkeypatch.setattr(prefix.bridge, '_open_log', opened)
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: dependencies)

    if fault in ('prewarm_source_mutation', 'prewarm_budget', 'prewarm_mid_budget'):
        with pytest.raises(runtime.FullRuntimeRefused) as refused:
            owner._refresh_generation(enabled=True)
        assert str(refused.value) == 'mind_full_runtime_refused'
        assert refused.value.__cause__ is None
        assert refused.value.__suppress_context__ is True
        assert owner._state == 'HOLD' and owner._generation is old_generation and owner._owner is worker
        assert owner._retained is None and old_handler._retained is None
        if fault == 'prewarm_budget':
            # The worst-case check must refuse before source I/O or allocation.
            assert not readings and not budgets and not created_budgets and owner._source_budget is None
        else:
            assert readings and owner._source_budget is budgets[0]
            assert sum(size for state, size in readings if state == 'STARTING') <= 2 * 1024 ** 3
            if fault == 'prewarm_mid_budget':
                assert sum(size for state, size in readings if state == 'STARTING') == 3 * old_cursor.offset
                assert budgets[0]._remaining == 1
        return

    assert owner._refresh_generation(enabled=True)['status'] == 'REFRESHED'
    assert owner._state == 'READY' and owner._prewarm_generation is owner._generation
    assert type(owner._retained) is runtime._RetainedFlatQueryState
    prewarm_bytes = sum(size for state, size in readings if state == 'STARTING')
    assert prewarm_bytes == 5 * old_cursor.offset
    assert budgets[0]._remaining == 2 * 1024 ** 3 - prewarm_bytes
    request = {'schema': 'wd.mind-pipe-request.v2', 'query_id': 'startup_work_overview',
               'nonce': '0' * 64, 'lane': 'codex-lead-1', 'scope': 'shared'}
    if fault == 'candidate_source_mutation':
        raw = source.read_bytes()
        modified = raw.replace(b'"message"', b'"messagE"', 1)
        assert modified != raw and len(modified) == len(raw)
        source.write_bytes(modified)
        with pytest.raises(runtime.FullRuntimeRefused):
            owner.located_candidates(request)
        assert owner._state == 'HOLD' and owner._retained is None and owner._owner is worker
        assert owner._source_budget is budgets[1]
        return
    reply = owner.located_candidates(request)
    assert reply['status'] == 'CANDIDATES' and reply['candidates']
    query_bytes = sum(size for state, size in readings if state == 'READY')
    assert query_bytes > 3 * old_cursor.offset
    assert len(budgets) == 2 and budgets[1]._remaining == 1024 ** 3 - query_bytes
    assert owner._state == 'READY' and owner._owner is worker
