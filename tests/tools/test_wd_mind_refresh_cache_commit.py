"""Synthetic refresh/cache commit; no native worker, installed bundle or live log.

These tests use the real vector-cache codec and immutable builder. Deployment
pins, worker, admission and refresh-generation validation are synthetic seams.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import hashlib
import copy
import threading

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_vector_cache as cache_module
from test_wd_mind_cache_builder_integration import _synthetic_case, VECTOR, fixtures
from test_wd_mind_owner_source_budget import flat_consumer_case, synthetic_checkout_pins
from test_wd_mind_owner_source_budget import source_fixtures, _instrument_source_streams, _full_fixtures


@pytest.fixture
def refresh_case(tmp_path, monkeypatch):
    # The production pins describe Git-blob bytes; a Windows checkout may use
    # CRLF. Use checkout-local pins only for this synthetic fixture, never for
    # the production verifier or another checkout.
    root = Path(__file__).resolve().parents[2]
    assert Path(runtime.reader.__file__).resolve().parents[1] == root
    pins = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in runtime.reader._REQUIRED_CODE}
    monkeypatch.setattr(runtime.reader, '_REQUIRED_CODE', pins)
    audit_root = tmp_path / '.codex-audit'
    audit_root.mkdir()
    case = _synthetic_case(audit_root, monkeypatch)
    original_snapshot = case.snapshot
    def snapshot_with_realistic_source_args():
        binding, projection = original_snapshot()
        # _synthetic_case mutates a shared messages dict while advancing the
        # cursor. Each production generation retains its own frozen snapshot.
        binding._PrivateBinding__snapshot = copy.deepcopy(binding._PrivateBinding__snapshot)
        args = binding._PrivateBinding__args
        source = Path(case.case['messages']['source_path'])
        sidecar = Path(case.case['messages']['generation_path'])
        assert source == case.source and tmp_path.resolve() in source.resolve().parents
        assert tmp_path.resolve() in sidecar.resolve().parents
        assert args['cursor'] == binding._PrivateBinding__snapshot['messages']['end_cursor']
        args.update(enabled=True, bundle_root=str(binding._PrivateBinding__bundle),
                    expected_manifest_sha256=binding._PrivateBinding__manifest,
                    source_path=str(source), generation_path=str(sidecar))
        return binding, projection
    case.snapshot = snapshot_with_realistic_source_args
    binding, _ = case.snapshot()
    memory = case.build(binding)
    case.passages.clear()
    def encode(values):
        case.passages.extend(values)
        return VECTOR * len(values)
    adapter = SimpleNamespace(passage=encode, encoder_binding_bytes=memory.encoder_binding,
                              encoder_binding_sha256=runtime.reader._sha(memory.encoder_binding))
    rotator = SimpleNamespace(owner=object())
    factory = SimpleNamespace(_bulk='synthetic')
    handler = runtime._FlatRequestHandler(binding, memory.snapshot, 'codex-lead-1',
        {'codex-lead-1'}, rotator, adapter, factory, 0, 'synthetic', None, case.adapter)
    handler._memory = memory
    handler._frozen_flat = (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock = threading.Lock()
    owner._generation = (binding, memory.snapshot, handler)
    owner._state, owner._lane, owner._lanes = 'READY', 'codex-lead-1', frozenset({'codex-lead-1'})
    owner._client, owner._adapter, owner._factory = rotator, adapter, factory
    owner._timeout, owner._watcher_lease, owner._retained = 0, None, None
    owner._rotation_enabled, owner._vector_cache_enabled = False, True
    owner._source_budget, owner._prewarm_generation = None, None
    owner._cache_status, owner._policy_pins = memory.cache_status, None
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
    monkeypatch.setattr(runtime, '_verify_refresh_basis', lambda _: ('synthetic', 'a'*64, b'policy', 'b'*64))
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **_: case.snapshot()[0])
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda _: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory', lambda _: None)
    monkeypatch.setattr(runtime, '_validate_refresh_generation', lambda _owner, staged:
                        (staged['binding'], staged['snapshot'], staged['handler']))
    return case, owner


def append(case, text):
    row = fixtures._row(text)
    with case.source.open('ab') as output:
        output.write(fixtures._raw(row) + b'\n')


def test_refresh_commits_cache_and_reuses_it_without_reembedding(refresh_case):
    case, owner = refresh_case
    for message in ('synthetic append one', 'synthetic append two'):
        case.passages.clear()
        append(case, message)
        assert owner._refresh_generation(enabled=True)['status'] == 'REFRESHED'
        assert owner._state == 'READY' and owner._cache_status == 'REFRESH_STORED'
        assert owner._handler._vector_cache is case.adapter
        assert case.passages == ['passage: ' + message]
        raw = case.cache_path.read_bytes()
        assert message.encode() not in raw and b'synthetic allowed alpha work' not in raw
        # New immutable binding and cache reload approximate a new owner, not
        # an actual process restart or a pinned production worker.
        case.passages.clear()
        fresh_binding, _ = case.snapshot()
        reloaded = case.build(fresh_binding)
        assert reloaded.cache_status == 'HIT_STORED' and case.passages == []


@pytest.mark.parametrize('fault', ['before_replace', 'after_replace', 'postcheck_interrupt'])
def test_failed_cache_commit_holds_owner_and_does_not_report_ready(refresh_case, monkeypatch, fault):
    case, owner = refresh_case
    previous = owner._generation
    append(case, 'synthetic cache write failure')
    original_write = cache_module.write_cache
    original_verify = case.adapter._verify
    published = []
    def refused(*args):
        if fault in ('after_replace', 'postcheck_interrupt'):
            original_write(*args)
            published.append(True)
        if fault == 'postcheck_interrupt':
            return
        raise OSError('synthetic cache failure')
    def verify():
        original_verify()
        if fault == 'postcheck_interrupt' and published:
            raise KeyboardInterrupt('synthetic post-publication interruption')
    monkeypatch.setattr(cache_module, 'write_cache', refused)
    monkeypatch.setattr(case.adapter, '_verify', verify)
    expected = KeyboardInterrupt if fault == 'postcheck_interrupt' else runtime.FullRuntimeRefused
    with pytest.raises(expected):
        owner._refresh_generation(enabled=True)
    assert owner._state == 'HOLD' and owner._generation is previous
    assert owner._cache_status == 'REFRESH_UNKNOWN'
    # In particular, do not assert that the old cache survived after replace.


def test_cache_refresh_preflight_accounts_for_two_more_prefix_passes(monkeypatch):
    offset = 210_000_000
    snapshot = {'messages': {'end_cursor': SimpleNamespace(offset=offset)}}
    runtime._prewarm_source_fits(snapshot, extra_prefix_passes=2)
    monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', 7 * offset + 4 * 1024 * 1024)
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        runtime._prewarm_source_fits(snapshot, extra_prefix_passes=2)
    # The old five-pass no-cache path remains admissible under the same limit.
    runtime._prewarm_source_fits(snapshot)


def test_refresh_budget_refuses_before_any_passage_encoding(refresh_case, monkeypatch):
    case, owner = refresh_case
    append(case, 'synthetic new passage')
    offset = case.source.stat().st_size
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(runtime, '_STARTUP_PREWARM_SOURCE_READ_LIMIT', 7 * offset + 4 * 1024 * 1024)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner._refresh_generation(enabled=True)
    assert owner._state == 'HOLD' and case.passages == []


def test_gate_on_refresh_cache_checks_charge_same_budget(
        synthetic_checkout_pins, flat_consumer_case, monkeypatch, tmp_path):
    """Real prewarm/source prefix and cache-check accounting; writer and pins are fixtures."""
    prefix, reader = runtime.prefix, runtime.reader
    package, original, _, old, clock, _ = flat_consumer_case
    snapshot = dict(old, messages=dict(old['messages']))
    source, sidecar = (Path(snapshot['messages']['source_path']),
                       Path(snapshot['messages']['generation_path']))
    assert tmp_path.resolve() in source.resolve().parents and tmp_path.resolve() in sidecar.resolve().parents
    cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(cursor.offset, cursor.file_identity, None)
    sidecar.unlink()
    snapshot['messages']['generation_path'] = str(source.with_name('events.generation.json'))
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {key:value for key,value in snapshot.items()
              if key not in ('projection_bytes', 'projection_sha256')}
    snapshot['projection_bytes'] = reader._serialized(reader.rederive_complete_message_projection(**inputs))
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    controls = (str(source), snapshot['messages']['generation_path'], original._controls[2],
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_:controls)
    # This fixture supplies a synthetic consumer with a dummy manifest pin.
    # Exercise real source-budget accounting without implying that its bundle
    # passes the independent verified snapshot-module loader contract.
    monkeypatch.setattr(runtime._PrivateBinding, '_snapshot_support', lambda _self: package)
    gate = original._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)
    _, owner, _, _ = _full_fixtures._refresh_generation_test_owner(monkeypatch, tmp_path)
    old_handler = owner._handler
    old_binding = owner._binding
    old_binding._PrivateBinding__bundle = Path(original._root)
    old_binding._PrivateBinding__manifest = original._pin
    policy_pin = reader._sha(snapshot['identifier_policy_binding'][0])
    old_binding._PrivateBinding__policy_pin = policy_pin
    binding = runtime._PrivateBinding(snapshot, Path(original._root), original._pin,
                                     old_binding._PrivateBinding__controls, {}, policy_pin)
    memory = runtime._PrivateFlatMemory(binding, snapshot, flat_bytes, gate._ids, gate._vectors, gate._encoder)
    owner._adapter = old_handler._adapter = SimpleNamespace(query=lambda _: [1.0] + [0.0] * 383)
    events, written = [], []
    class SyntheticCacheModule:
        def load_cache(self, *_args): raise AssertionError('unexpected load')
        def write_cache(self, _path, binding_value, rows):
            events.append('write')
            written.append((binding_value, rows))
        def preflight(self, *_args): return 'MISSING'
    provider = runtime._PrivateVectorCache(SyntheticCacheModule(), str(tmp_path / 'never-written'), lambda:None)
    old_handler._vector_cache = provider
    owner._vector_cache_enabled = True
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _:None)
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    monkeypatch.setattr(runtime, '_cache_snapshot_binding', lambda *_:'synthetic-binding')
    def stage(current):
        result = _full_fixtures._test_staged_refresh(runtime, current, (binding, snapshot, memory))
        result['handler']._vector_cache = provider
        return result
    monkeypatch.setattr(runtime, '_stage_refresh_generation', stage)
    budgets = []
    original_init = prefix._SourceReadBudget.__init__
    def budget_init(self, limit):
        original_init(self, limit)
        budgets.append(self)
    monkeypatch.setattr(prefix._SourceReadBudget, '__init__', budget_init)
    original_prefix = runtime._RetainedFlatQueryState._prefix
    def traced_prefix(self, context, *, source_budget=None):
        events.append(('prefix_start', source_budget))
        result = original_prefix(self, context, source_budget=source_budget)
        events.append(('prefix_end', source_budget))
        return result
    monkeypatch.setattr(runtime._RetainedFlatQueryState, '_prefix', traced_prefix)
    readings = _instrument_source_streams(monkeypatch, source)
    assert owner._refresh_generation(enabled=True)['status'] == 'REFRESHED'
    assert owner._state == 'READY' and owner._cache_status == 'REFRESH_STORED'
    assert len(budgets) == 1 and sum(size for _, size in readings) == 7 * cursor.offset
    assert budgets[0]._remaining == 2 * 1024**3 - 7 * cursor.offset
    assert events == [('prefix_start', budgets[0]), ('prefix_end', budgets[0]), 'write',
                      ('prefix_start', budgets[0]), ('prefix_end', budgets[0])]
    assert len(written) == 1 and written[0][0] == 'synthetic-binding'
    assert all(len(record_sha) == len(passage_sha) == 64 and type(vector) is bytes
               for record_sha, passage_sha, vector in written[0][1])
