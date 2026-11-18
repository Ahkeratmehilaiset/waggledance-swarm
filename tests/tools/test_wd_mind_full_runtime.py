"""Synthetic installed-bundle tests; never read an operator bridge log."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import wd_mind_content_query_readonly as reader

ROOT = Path(__file__).resolve().parents[2]


def test_cached_vectors_match_screened_records_and_ignore_new_records(monkeypatch):
    import struct
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(reader, 'decode_text', lambda value: value)
    first = {'record_id': 'old', 'stored_text': 'screened old content'}
    added = {'record_id': 'new', 'stored_text': 'screened new content'}
    key, passage = runtime._cache_record_keys(first)
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    assert runtime._cache_vectors_from_rows({'old': first, 'new': added},
                                             ((key, passage, vector),)) == {'old': vector}
    assert 'screened' not in repr((key, passage, vector))


def test_cached_vectors_reject_changed_or_missing_screened_record(monkeypatch):
    import struct
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(reader, 'decode_text', lambda value: value)
    first = {'record_id': 'old', 'stored_text': 'screened old content'}
    key, passage = runtime._cache_record_keys(first)
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    with pytest.raises(ValueError, match='cache_projection_changed'):
        runtime._cache_vectors_from_rows({}, ((key, passage, vector),))
    with pytest.raises(ValueError, match='cache_projection_changed'):
        runtime._cache_vectors_from_rows({'old': first}, ((key, '0' * 64, vector),))
    with pytest.raises(ValueError, match='cache_row'):
        runtime._cache_vectors_from_rows({'old': first}, ((key, passage, vector), (key, passage, vector)))
    with pytest.raises(ValueError, match='cache_record'):
        runtime._cache_vectors_from_rows({'old': None}, ((key, passage, vector),))


def test_cache_binding_ignores_unrelated_bundle_but_binds_policy_and_model(monkeypatch):
    from types import SimpleNamespace
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(runtime, '_checked_encoder_binding', lambda raw, digest: None)
    monkeypatch.setattr(reader, 'module_sha256s', lambda: {'content_module_sha256': '1' * 64})
    monkeypatch.setattr(runtime, '_cache_content_pins', lambda binding: {'synthetic-code': '9' * 64})
    cursor = SimpleNamespace(offset=12, file_identity='synthetic-file', generation=None)
    snapshot = {'messages': {'end_cursor': cursor}, 'approved_tasks_bytes': b'synthetic-scope',
                'projection_sha256': '7' * 64}
    args = {'cursor': cursor, 'expected_prefix_sha256': '2' * 64}
    controls = (b'irrelevant-fleet-metadata', b'roster', b'policy')
    projection = {'messages': {'records': [], 'identifier_policy_hmac': 'ephemeral'}}
    first = runtime._PrivateBinding(snapshot, Path('bundle-a'), 'a' * 64, controls, args, '3' * 64)
    second = runtime._PrivateBinding(snapshot, Path('bundle-b'), 'b' * 64, controls, args, '3' * 64)
    one = runtime._cache_snapshot_binding(first, snapshot, projection, b'encoder', '4' * 64)
    projection['messages']['identifier_policy_hmac'] = 'another-ephemeral-key'
    assert one == runtime._cache_snapshot_binding(second, snapshot, projection, b'encoder', '4' * 64)
    changed = runtime._PrivateBinding(snapshot, Path('bundle-b'), 'b' * 64, controls, args, '5' * 64)
    assert one['policy_sha256'] != runtime._cache_snapshot_binding(changed, snapshot, projection, b'encoder', '4' * 64)['policy_sha256']
    assert one['model_sha256'] != runtime._cache_snapshot_binding(first, snapshot, projection, b'encoder', '6' * 64)['model_sha256']
    projection['messages']['records'].append({'record_id': 'synthetic'})
    assert one['projection_sha256'] != runtime._cache_snapshot_binding(first, snapshot, projection, b'encoder', '4' * 64)['projection_sha256']


def test_cache_runtime_content_ignores_generation_but_not_tokenizer():
    from tools import wd_mind_full_runtime as runtime
    manifest = {'schema': 'wd.q4-runtime-manifest.v1', 'generation': 'a' * 40,
                'revision': 'b' * 40, 'files': [{'path': 'model/tokenizer.json',
                                                    'size': 12, 'sha256': '1' * 64}]}
    def fingerprint(value):
        return runtime._cache_descriptor_content_sha(json.dumps(value).encode(),
                                                     'wd-mind-runtime-manifest.json')
    original = fingerprint(manifest)
    manifest['generation'] = 'c' * 40
    assert fingerprint(manifest) == original
    manifest['files'][0]['sha256'] = '2' * 64
    assert fingerprint(manifest) != original


def test_cache_content_pins_accept_installer_uppercase_sha256(tmp_path):
    from tools import wd_mind_full_runtime as runtime
    bundle = tmp_path / 'bundle'
    files = {}
    for relative in runtime._CACHE_CONTENT_PATHS:
        path = bundle.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == 'wd-mind-runtime-manifest.json':
            data = json.dumps({'schema': 'wd.q4-runtime-manifest.v1', 'generation': 'a' * 40,
                               'revision': 'b' * 40, 'files': [{'path': 'model/tokenizer.json',
                                   'size': 1, 'sha256': 'A' * 64}]}).encode()
        elif relative == 'wd-mind-interpreter-identity.json':
            data = json.dumps({'schema': 'wd.q4-interpreter-identity.v1', 'files': [
                {'path': 'python.exe', 'size': 1, 'sha256': 'b' * 64, 'role': 'interpreter'}]}).encode()
        else:
            data = ('synthetic ' + relative).encode()
        path.write_bytes(data)
        files[relative] = hashlib.sha256(data).hexdigest().upper()
    raw = json.dumps({'files': files}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    binding = runtime._PrivateBinding({}, bundle, hashlib.sha256(raw).hexdigest().upper(), (), {})
    pins = runtime._cache_content_pins(binding)
    assert set(pins) == set(runtime._CACHE_CONTENT_PATHS)
    assert all(value == value.lower() and len(value) == 64 for value in pins.values())


def test_cache_requires_complete_screened_old_prefix_set(monkeypatch):
    import struct
    from types import SimpleNamespace
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(reader, 'decode_text', lambda value: value)
    old = {'record_id': 'old', 'stored_text': 'synthetic old'}
    new = {'record_id': 'new', 'stored_text': 'synthetic new'}
    previous = {'messages': {'records': [old]}}
    current = {'messages': {'records': [old, new]}}
    monkeypatch.setattr(runtime, '_cache_old_projection', lambda snapshot, offset, sha: previous)
    snapshot = {'messages': {'end_cursor': SimpleNamespace(offset=20)}}
    key, passage = runtime._cache_record_keys(old)
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    binding = {'prefix_offset': 10, 'prefix_sha256': 'a' * 64,
               'projection_sha256': runtime._cache_projection_sha256(previous)}
    result = SimpleNamespace(binding=binding, rows=((key, passage, vector),))
    assert runtime._cache_verified_reusable(snapshot, current, {'old': old, 'new': new}, result) == {'old': vector}
    result.rows = ()
    with pytest.raises(ValueError, match='cache_record_set_changed'):
        runtime._cache_verified_reusable(snapshot, current, {'old': old, 'new': new}, result)
    result.rows = ((key, passage, vector),)
    binding['projection_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='cache_projection_changed'):
        runtime._cache_verified_reusable(snapshot, current, {'old': old, 'new': new}, result)


def test_cache_rows_are_hashes_and_vectors_only(monkeypatch):
    import struct
    from tools import wd_mind_full_runtime as runtime
    monkeypatch.setattr(reader, 'decode_text', lambda value: value)
    record = {'record_id': 'synthetic-id', 'stored_text': 'synthetic-private-content'}
    projection = {'messages': {'records': [record]}}
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    rows = runtime._cache_rows_from_screened_projection(projection, ('synthetic-id',), vector)
    assert len(rows) == 1 and rows[0][2] == vector
    assert all('synthetic' not in field for field in rows[0][:2])
    with pytest.raises(ValueError, match='cache_records'):
        runtime._cache_rows_from_screened_projection(projection, ('other',), vector)


def test_private_cache_provider_checks_module_before_and_after_io():
    from types import SimpleNamespace
    from tools import wd_mind_full_runtime as runtime
    calls = []
    module = SimpleNamespace(load_cache=lambda path, binding, check: calls.append('load') or 'result',
                             write_cache=lambda path, binding, rows: calls.append('write'),
                             preflight=lambda path: calls.append('preflight') or 'MISSING')
    provider = runtime._PrivateVectorCache(module, 'synthetic-path', lambda: calls.append('verify'))
    assert provider.preflight() == 'MISSING'
    assert provider.load({}, lambda *_: True) == 'result'
    provider.write({}, ())
    assert calls == ['verify', 'preflight', 'verify', 'verify', 'load', 'verify', 'verify', 'write', 'verify']
    with pytest.raises(ValueError, match='cache_provider'):
        runtime._PrivateVectorCache(module, 'synthetic-path', None)


@pytest.mark.parametrize('operation', ['load', 'write', 'preflight'])
def test_private_cache_provider_rechecks_pins_after_io_refusal(operation):
    from types import SimpleNamespace
    from tools import wd_mind_full_runtime as runtime
    class CacheRefused(ValueError): pass
    calls = []
    def fail(*_args): raise CacheRefused('synthetic cache failure')
    def verify():
        calls.append('verify')
        if len(calls) == 2: raise RuntimeError('synthetic pin changed')
    provider = runtime._PrivateVectorCache(SimpleNamespace(load_cache=fail, write_cache=fail, preflight=fail),
                                            'synthetic-path', verify)
    with pytest.raises(RuntimeError, match='pin changed'):
        if operation == 'load': provider.load({}, lambda *_: True)
        elif operation == 'write': provider.write({}, ())
        else: provider.preflight()
    assert calls == ['verify', 'verify']


def test_cache_loader_uses_pinned_source_and_rejects_tampering(monkeypatch, tmp_path):
    import types
    import tools
    from tools import wd_mind_full_runtime as runtime
    name, attr = 'tools.wd_mind_vector_cache', 'wd_mind_vector_cache'
    previous_module = sys.modules.pop(name, None)
    previous_attr = tools.__dict__.pop(attr, None)
    previous_attestation = runtime._CACHE_MODULE_ATTESTATION
    bundle = tmp_path / 'bundle'
    relative = 'tools-bootstrap/tools/wd_mind_vector_cache.py'
    path = bundle.joinpath(*relative.split('/'))
    path.parent.mkdir(parents=True)
    code = b'def load_cache(path, binding, verify): return "synthetic"\ndef write_cache(path, binding, rows): pass\n'
    path.write_bytes(code)
    manifest = json.dumps({'files': {relative: hashlib.sha256(code).hexdigest().upper()}}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    binding = runtime._PrivateBinding({}, bundle, hashlib.sha256(manifest).hexdigest().upper(), (), {})
    monkeypatch.setattr(runtime.importlib.util, 'find_spec', lambda _: types.SimpleNamespace(origin=str(path)))
    try:
        provider = runtime._load_pinned_vector_cache(binding)
        assert provider._path == r'C:\wd-mind-private\vector-cache\vectors.bin'
        assert provider.load({}, lambda *_: True) == 'synthetic'
        path.write_bytes(code + b'# modified\n')
        with pytest.raises(Exception):
            provider.load({}, lambda *_: True)
        path.write_bytes(code)
        sys.modules[name] = types.ModuleType(name)
        with pytest.raises(ValueError, match='cache_unverified_preload'):
            runtime._load_pinned_vector_cache(binding)
    finally:
        sys.modules.pop(name, None)
        tools.__dict__.pop(attr, None)
        if previous_module is not None:
            sys.modules[name] = previous_module
        if previous_attr is not None:
            setattr(tools, attr, previous_attr)
        runtime._CACHE_MODULE_ATTESTATION = previous_attestation


@pytest.mark.parametrize('operation', ['load_cache', 'write_cache'])
def test_cache_security_refusal_does_not_become_miss_or_memory(monkeypatch, tmp_path, operation):
    from tools import wd_mind_vector_cache as vector_cache
    from test_wd_mind_cache_builder_integration import _synthetic_case
    # The Windows checkout may use different line endings from the declared
    # repository dependency pins. This synthetic cache test binds its fixture
    # to actual local bytes; it is not a test of installed code provenance.
    root = Path(reader.__file__).resolve().parents[1]
    monkeypatch.setattr(reader, '_REQUIRED_CODE', {
        relative: reader._sha((root / relative).read_bytes()) for relative in reader._REQUIRED_CODE
    })
    case = _synthetic_case(tmp_path, monkeypatch)
    binding, _ = case.snapshot()
    def refuse(*args, **kwargs):
        raise vector_cache.CacheRefused('acl_not_user_only')
    monkeypatch.setattr(vector_cache, operation, refuse)
    with pytest.raises(ValueError, match='snapshot_build_refused'):
        case.build(binding)
    if operation == 'load_cache':
        assert case.passages == []
    assert not case.cache_path.exists()


@pytest.mark.parametrize('reason', ['missing', 'binding_changed', 'prefix_changed'])
def test_verified_cache_miss_rebuilds_without_relaxing_refusal(monkeypatch, tmp_path, reason):
    from tools import wd_mind_vector_cache as vector_cache
    from test_wd_mind_cache_builder_integration import _synthetic_case
    root = Path(reader.__file__).resolve().parents[1]
    monkeypatch.setattr(reader, '_REQUIRED_CODE', {
        relative: reader._sha((root / relative).read_bytes()) for relative in reader._REQUIRED_CODE
    })
    case = _synthetic_case(tmp_path, monkeypatch)
    binding, _ = case.snapshot()
    if reason != 'missing':
        def stale(*args, **kwargs):
            raise vector_cache.CacheMiss(reason)
        monkeypatch.setattr(vector_cache, 'load_cache', stale)
    memory = case.build(binding)
    assert memory.cache_status == 'MISS_STORED'
    assert len(case.passages) == 2 and case.cache_path.exists()


def test_corrupt_cache_payload_rebuilds_only_after_source_verification(monkeypatch, tmp_path):
    from test_wd_mind_cache_builder_integration import _synthetic_case
    root = Path(reader.__file__).resolve().parents[1]
    monkeypatch.setattr(reader, '_REQUIRED_CODE', {
        relative: reader._sha((root / relative).read_bytes()) for relative in reader._REQUIRED_CODE
    })
    case = _synthetic_case(tmp_path, monkeypatch)
    binding, _ = case.snapshot()
    assert case.build(binding).cache_status == 'MISS_STORED'
    raw = bytearray(case.cache_path.read_bytes())
    raw[-1] ^= 1
    case.cache_path.write_bytes(raw)
    case.passages.clear()
    binding, _ = case.snapshot()
    assert case.build(binding).cache_status == 'MISS_STORED'
    assert len(case.passages) == 2


def test_single_manifest_owner_defaults_off_and_is_inert(tmp_path, monkeypatch):
    """Constructing a disabled/default owner must not start a worker."""
    from tools import wd_mind_full_runtime as runtime
    calls = []
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', lambda self: calls.append('spawn'))
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.create_single_manifest_owner()
    assert calls == []


def _synthetic_admitted_factory(monkeypatch, bundle, manifest, location, lock, timeout, lock_timeout, bulk):
    """In-process admission double; does not prove the OS reservation."""
    from tools import wd_mind_full_runtime as runtime, wd_mind_runtime_child as child
    guard = object.__new__(child._LocalOwnerReservation)
    guard.fd = 1  # Inert sentinel; no native handle is opened or closed.
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    lease, token = child._LocalAdmission(guard), object()
    lease.bind_owner(token)
    factory = runtime._ManifestWorkerFactory(bundle, manifest, location, lock, timeout, lock_timeout, bulk, lease, token)
    lease.bind_factory(token, factory)
    return factory


def test_worker_factory_passes_only_remaining_call_deadline(monkeypatch, tmp_path):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_child as child
    now, calls = [100.0], []
    location = {'runtime_root': 'root', 'runtime_manifest': 'manifest', 'runtime_manifest_sha256': 'a' * 64,
                'runtime_generation': 'b' * 40, 'runtime_revision': 'c' * 40, 'interpreter': 'python',
                'interpreter_sha256': 'd' * 64, 'interpreter_root': 'python-root'}
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: now[0])
    def resolve(**_kwargs):
        now[0] = 102.0
        return location
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', resolve)
    monkeypatch.setattr(child, 'start_e5_worker_graceful', lambda **kwargs: calls.append(kwargs) or {'status': 'synthetic'})
    monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *_args: child)
    factory = _synthetic_admitted_factory(monkeypatch, str(tmp_path), 'e' * 64, location, tmp_path / 'lock', 300, 1, 'f' * 64)
    assert factory(owner_sink=lambda _owner: None, call_deadline=110.0) == {'status': 'synthetic'}
    assert calls[0]['timeout'] == 8.0
    assert factory._admission._unknown is True  # The fake returns no captured worker.
    calls.clear()
    with pytest.raises(runtime.FullRuntimeRefused):
        factory(owner_sink=lambda _owner: None, call_deadline=101.0)
    assert not calls


@pytest.mark.parametrize('policy,pin', [(None, None), (b'{}', '0' * 64), (b'{}', None),
                                        (b'{}', hashlib.sha256(b'{}').hexdigest())])
def test_runtime_requires_independently_pinned_identifier_policy_before_source_read(tmp_path, monkeypatch, policy, pin):
    from tools import wd_mind_full_runtime as runtime
    def forbidden(*args, **kwargs):
        raise AssertionError('unapproved policy must not read source')
    monkeypatch.setattr(runtime, '_bundle_inputs', forbidden)
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.assemble_full_runtime(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256='a' * 64, identifier_policy_bytes=policy,
            expected_identifier_policy_sha256=pin)


@pytest.mark.parametrize('signal', ['error', 'keyboard', 'generator', 'async', 'group'])
def test_runtime_cancellation_does_not_expose_policy_or_hmac_key_in_traceback(tmp_path, monkeypatch, signal):
    import asyncio
    import traceback
    from tools import wd_mind_full_runtime as runtime
    policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
    key = b'synthetic-private-hmac-key-12345'
    assert len(key) == 32
    monkeypatch.setattr(runtime.secrets, 'token_bytes', lambda _: key)
    def interrupt(*args, **kwargs):
        if signal == 'keyboard': raise KeyboardInterrupt()
        if signal == 'generator': raise GeneratorExit()
        if signal == 'async': raise asyncio.CancelledError()
        if signal == 'group': raise BaseExceptionGroup('synthetic-private-detail', [KeyboardInterrupt()])
        raise RuntimeError('synthetic-private-detail')
    monkeypatch.setattr(runtime, '_bundle_inputs', interrupt)
    expected = {'keyboard': KeyboardInterrupt, 'generator': GeneratorExit,
                'async': asyncio.CancelledError, 'group': BaseExceptionGroup}.get(signal, runtime.FullRuntimeRefused)
    with pytest.raises(expected) as caught:
        runtime.assemble_full_runtime(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256='a' * 64, identifier_policy_bytes=policy,
            expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
    stack = traceback.TracebackException.from_exception(caught.value, capture_locals=True).stack
    frame = next(frame for frame in stack if frame.name == 'assemble_full_runtime')
    assert 'synthetic-private-marker-52' not in repr(frame.locals)
    assert 'synthetic-private-hmac-key-12345' not in repr(frame.locals)
    assert caught.value.__context__ is None
    if signal == 'group':
        assert type(caught.value) is BaseExceptionGroup
        assert str(caught.value).startswith('provider_cancelled')
        assert type(caught.value.exceptions[0]) is KeyboardInterrupt


def test_owner_group_cancellation_avoids_provider_exception_hooks():
    from tools import wd_mind_full_runtime as runtime
    class HostileKeyboard(KeyboardInterrupt):
        def __init__(self): raise AssertionError('constructor called')
    class HostileGroup(BaseExceptionGroup):
        def __getattribute__(self, name): raise AssertionError('group attribute read')
        def __setattr__(self, name, value): raise AssertionError('group attribute write')
    original = HostileGroup('private provider detail', [KeyboardInterrupt.__new__(HostileKeyboard)])
    def deny_read(self, name): raise AssertionError('attribute read')
    def deny_write(self, name, value): raise AssertionError('attribute write')
    HostileKeyboard.__getattribute__ = deny_read
    HostileKeyboard.__setattr__ = deny_write
    sanitized = runtime._fixed_group_cancellation(original)
    assert type(sanitized) is BaseExceptionGroup
    assert type(sanitized.exceptions[0]) is KeyboardInterrupt
    assert 'private provider detail' not in str(sanitized)


def test_parent_control_requires_independent_pins_and_exact_length():
    import io
    import struct
    from tools import wd_mind_full_runtime as runtime
    policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
    manifest, nonce, channel = bytes.fromhex('aa' * 32), b'n' * 32, b'k' * 32
    policy_sha = hashlib.sha256(policy).digest()
    control = struct.pack('>4sI', b'MCT1', len(policy)) + manifest + nonce + channel + policy_sha + policy
    pins = {'expected_manifest_sha256': manifest.hex(), 'expected_identifier_policy_sha256': policy_sha.hex()}
    def buffered(raw):
        private = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(raw)))
        private.read_from(enabled=True)
        return private
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime._parse_parent_control(control_buffer=buffered(control), **pins)
    for bad, override in [(control[:-1], {}), (control + b'x', {}), (b'BAD!' + control[4:], {}),
                          (control, {'expected_manifest_sha256': 'b' * 64}),
                          (control, {'expected_identifier_policy_sha256': 'b' * 64})]:
        with pytest.raises(runtime.FullRuntimeRefused):
            runtime._parse_parent_control(enabled=True, control_buffer=buffered(bad), **(pins | override))
    bootstrap = runtime._parse_parent_control(enabled=True, control_buffer=buffered(control), **pins)
    assert 'synthetic-private-marker-52' not in repr(bootstrap)
    assert bootstrap.take() == (manifest.hex(), nonce, channel, policy_sha.hex(), policy)
    with pytest.raises(runtime.FullRuntimeRefused): bootstrap.take()


def test_parent_control_parser_interruptions_never_expose_raw_control():
    import dis
    import io
    import struct
    import sys
    import traceback
    from tools import wd_mind_full_runtime as runtime
    policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
    manifest, nonce, channel = b'a' * 32, b'n' * 32, b'k' * 32
    sha = hashlib.sha256(policy).digest()
    control = struct.pack('>4sI', b'MCT1', len(policy)) + manifest + nonce + channel + sha + policy
    code = runtime._parse_parent_control.__code__
    observed = []
    for line in sorted(set(line for _, line in dis.findlinestarts(code) if line is not None)):
        box = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(control)))
        box.read_from(enabled=True)
        def trace(frame, event, arg):
            if frame.f_code is code and event == 'line' and frame.f_lineno == line:
                observed.append(line)
                raise KeyboardInterrupt()
            return trace
        sys.settrace(trace)
        try:
            runtime._parse_parent_control(enabled=True, control_buffer=box,
                expected_manifest_sha256=manifest.hex(), expected_identifier_policy_sha256=sha.hex())
        except BaseException as error:
            frames = traceback.TracebackException.from_exception(error, capture_locals=True).stack
            for frame in frames:
                if frame.name in ('_parse_parent_control', '_parse_parent_control_bytes'):
                    assert 'synthetic-private-marker-52' not in repr(frame.locals)
        finally:
            sys.settrace(None)
    assert len(observed) >= 5


def test_parent_control_buffer_interruptions_never_expose_raw_control():
    import dis
    import io
    import sys
    import traceback
    from tools import wd_mind_full_runtime as runtime
    control = b'synthetic-private-marker-52' * 10
    code = runtime._ParentControlBuffer.read_from.__code__
    observed = []
    for line in sorted(set(line for _, line in dis.findlinestarts(code) if line is not None)):
        box = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(control)))
        def trace(frame, event, arg):
            if frame.f_code is code and event == 'line' and frame.f_lineno == line:
                observed.append(line)
                raise KeyboardInterrupt()
            return trace
        sys.settrace(trace)
        try:
            box.read_from(enabled=True)
        except BaseException as error:
            frames = traceback.TracebackException.from_exception(error, capture_locals=True).stack
            for frame in frames:
                if frame.name in ('read_from', 'readinto'):
                    assert 'synthetic-private-marker-52' not in repr(frame.locals)
        finally:
            sys.settrace(None)
    assert len(observed) >= 8


def test_parent_control_buffer_refuses_oversize_and_accepts_partial_reads():
    import io
    from tools import wd_mind_full_runtime as runtime
    private = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(b'x' * (136 + 65536 + 1))))
    with pytest.raises(runtime.FullRuntimeRefused): private.read_from(enabled=True)
    with pytest.raises(runtime.FullRuntimeRefused): private.snapshot()
    class PartialStream:
        def __init__(self): self.parts = [b'a', b'b', b'']
        def readinto(self, view):
            chunk = self.parts.pop(0)
            view[:len(chunk)] = chunk
            return len(chunk)
    private = runtime._ParentControlBuffer(runtime._PrivateParentStream(PartialStream()))
    private.read_from(enabled=True)
    assert private.snapshot() == b'ab'


def test_parent_control_buffer_only_one_concurrent_consumer():
    import io
    import threading
    from tools import wd_mind_full_runtime as runtime
    private = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(b'synthetic')))
    private.read_from(enabled=True)
    barrier, results = threading.Barrier(3), []
    def consume():
        barrier.wait()
        try:
            private.take_snapshot()
            results.append('accepted')
        except runtime.FullRuntimeRefused:
            results.append('refused')
    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ['accepted', 'refused']


def test_manifest_policy_candidate_requires_pinned_descriptor_and_is_not_approval(tmp_path):
    from tools import wd_mind_full_runtime as runtime
    descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
                            'identifier_policy_sha256':'a' * 64,
                            'approval_reference_sha256':'b' * 64})
    path = tmp_path / 'wd-mind-identifier-policy-approval.json'
    path.write_bytes(descriptor)
    manifest = canonical({'schema_version':1, 'files':{path.name:hashlib.sha256(descriptor).hexdigest()}})
    (tmp_path / 'deployment-manifest.json').write_bytes(manifest)
    pin = hashlib.sha256(manifest).hexdigest()
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.inspect_manifest_policy_candidate(bundle_root=str(tmp_path), expected_manifest_sha256=pin)
    candidate = runtime.inspect_manifest_policy_candidate(enabled=True, bundle_root=str(tmp_path),
        expected_manifest_sha256=pin)
    assert candidate.status() == {'state':'CANDIDATE', 'approval':'UNVERIFIED', 'authority':'none',
        'identifier_policy_sha256':'a' * 64, 'approval_reference_sha256':'b' * 64}
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.start_manifest_bound_owner(enabled=True, policy_anchor=candidate)
    args = dict(bundle_root=str(tmp_path), expected_manifest_sha256=pin,
                operator_policy_sha256='a' * 64, operator_approval_reference_sha256='b' * 64)
    with pytest.raises(runtime.FullRuntimeRefused): runtime.create_operator_pinned_policy_anchor(**args)
    for changes in ({'operator_policy_sha256':'c'*64}, {'operator_approval_reference_sha256':'c'*64},
                    {'expected_manifest_sha256':'c'*64}):
        with pytest.raises(runtime.FullRuntimeRefused):
            runtime.create_operator_pinned_policy_anchor(enabled=True, **(args | changes))
    anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, **args)
    assert type(anchor) is runtime._TrustedManifestPolicyAnchor
    assert anchor.status() == {'policy_binding':'MATCH', 'approval_reference_binding':'MATCH',
        'authorization_source':'operator_supplied_pins', 'operator_authentication':'UNKNOWN',
        'observation':'observed_at_creation', 'reaudit_required':True}
    with pytest.raises(AttributeError): anchor.policy_sha256 = 'c' * 64
    path.write_bytes(b'{}')
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.inspect_manifest_policy_candidate(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256=pin)


def test_export_sender_interrupted_after_lock_acquisition_releases_lock():
    import inspect
    import sys
    import threading
    import time
    from tools import wd_mind_full_runtime as runtime
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock, owner._state = threading.Lock(), 'READY'
    prepared = runtime._PrivateConsumerExport(b'm', (), 1, owner, object())
    lines, start = inspect.getsourcelines(runtime._PrivateConsumerExport._send_parent_stream_inner)
    target = start + next(i for i, line in enumerate(lines) if line.strip() == 'if not acquired:')
    def trace(frame, event, arg):
        if (frame.f_code is runtime._PrivateConsumerExport._send_parent_stream_inner.__code__
                and event == 'line' and frame.f_lineno == target):
            raise KeyboardInterrupt()
        return trace
    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt):
            prepared._send_parent_stream_inner(None, ('a', b'n'*32, b'k'*32, 'b', b'p'),
                                               1000, time.monotonic() + 5)
    finally:
        sys.settrace(None)
    assert not owner._lock.locked()
    assert owner._state == 'HOLD'


def test_export_sender_acquire_return_interruption_cannot_report_ready():
    import dis
    import sys
    import threading
    import time
    from tools import wd_mind_full_runtime as runtime
    if not hasattr(sys, 'monitoring'):
        pytest.skip('instruction monitoring needs Python 3.12+')
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock, owner._state = threading.Lock(), 'READY'
    prepared = runtime._PrivateConsumerExport(b'm', (), 1, owner, object())
    code = runtime._PrivateConsumerExport._send_parent_stream_inner.__code__
    instructions = list(dis.get_instructions(code))
    # Select the store immediately after the acquire call.
    target = next(item.offset for index, item in enumerate(instructions)
                  if item.opname == 'STORE_FAST' and item.argval == 'acquired'
                  and index and instructions[index-1].opname.startswith('CALL'))
    tool_id = 5
    sys.monitoring.use_tool_id(tool_id, 'mind-lock-interrupt-test')
    def interrupt(code_obj, offset):
        if code_obj is code and offset == target:
            raise KeyboardInterrupt()
    try:
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.INSTRUCTION, interrupt)
        sys.monitoring.set_local_events(tool_id, code, sys.monitoring.events.INSTRUCTION)
        with pytest.raises(KeyboardInterrupt):
            prepared._send_parent_stream_inner(None, ('a', b'n'*32, b'k'*32, 'b', b'p'),
                                               1000, time.monotonic()+5)
    finally:
        sys.monitoring.set_local_events(tool_id, code, 0)
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.INSTRUCTION, None)
        sys.monitoring.free_tool_id(tool_id)
    locked = owner._lock.locked()
    if locked: owner._lock.release()  # Synthetic test cleanup only.
    assert owner._state == 'HOLD'


def test_export_sender_write_failure_closes_stream_and_retains_hold(monkeypatch):
    import threading
    import time
    import types
    from tools import wd_mind_full_runtime as runtime
    policy, nonce, key = b'policy', b'n' * 32, b'k' * 32
    snapshot = {'identifier_policy_binding': (policy, b'z' * 32, 'a' * 64)}
    binding = types.SimpleNamespace(_PrivateBinding__manifest='a' * 64,
                                    snapshot_for_query=lambda: snapshot)
    memory = types.SimpleNamespace(binding=binding, snapshot=snapshot)
    handler = types.SimpleNamespace(_memory=memory, _running_owner=lambda: None)
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock, owner._state, owner._handler, owner._binding = threading.Lock(), 'READY', handler, binding
    metadata = canonical({'nonce':nonce.hex()})
    blocks = tuple((str(i), b'') for i in range(12))
    prepared = runtime._PrivateConsumerExport(metadata, blocks, len(metadata), owner, memory)
    class FailingSink:
        closed = False
        def write(self, data): raise OSError('synthetic write failure')
        def close(self): self.closed = True
    sink = FailingSink()
    monkeypatch.setattr(runtime, '_require_trusted_parent_export', lambda **_: None)
    monkeypatch.setattr(runtime, '_flat_check', lambda _: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_factory', lambda _: None)
    with pytest.raises(runtime.FullRuntimeRefused):
        prepared._send_parent_stream(enabled=True, stream=runtime._PrivateExportStream(sink),
            bootstrap=runtime._ParentBootstrap(('a' * 64, nonce, key, hashlib.sha256(policy).hexdigest(), policy)),
            max_wire_bytes=10000, deadline_monotonic=time.monotonic()+5)
    assert sink.closed and not owner._lock.locked() and owner._state == 'HOLD'
    assert prepared.status()['state'] == 'DISCARDED'


def test_parent_bootstrap_interrupted_take_does_not_leak_values_in_traceback():
    import traceback
    from tools import wd_mind_full_runtime as runtime
    class InterruptLock:
        def __enter__(self): return self
        def __exit__(self, *args): raise KeyboardInterrupt()
    bootstrap = runtime._ParentBootstrap((b'synthetic-bootstrap-secret-52',))
    bootstrap._ParentBootstrap__lock = InterruptLock()
    with pytest.raises(KeyboardInterrupt) as caught:
        bootstrap.take()
    stack = traceback.TracebackException.from_exception(caught.value, capture_locals=True).stack
    frame = next(frame for frame in stack if frame.name == 'take')
    assert 'synthetic-bootstrap-secret-52' not in repr(frame.locals)
    assert caught.value.__context__ is None


def test_parent_bootstrap_take_traced_interrupts_never_expose_values():
    import dis
    import sys
    import traceback
    from tools import wd_mind_full_runtime as runtime
    code = runtime._ParentBootstrap.take.__code__
    candidate_lines = sorted(set(line for _, line in dis.findlinestarts(code) if line is not None))
    observed = []
    for candidate in candidate_lines:
        bootstrap = runtime._ParentBootstrap((b'synthetic-bootstrap-secret-52',))
        def trace(frame, event, arg):
            if (frame.f_code is code and event == 'line' and frame.f_lineno == candidate
                    and frame.f_locals.get('values') is not None):
                observed.append(candidate)
                raise KeyboardInterrupt()
            return trace
        sys.settrace(trace)
        try:
            bootstrap.take()
        except KeyboardInterrupt as error:
            stack = traceback.TracebackException.from_exception(error, capture_locals=True).stack
            for frame in stack:
                if frame.name == 'take':
                    assert 'synthetic-bootstrap-secret-52' not in repr(frame.locals)
        finally:
            sys.settrace(None)
    assert len(observed) >= 3


@pytest.mark.parametrize('fault', ['vectors', 'policy_pin'])
def test_single_adapter_symbol_and_manifest_pins_without_rotator(synthetic_bundle, fault):
    """Import real bundle bytes; no model process or fake adapter is started."""
    bundle = synthetic_bundle['bundle']
    import datetime
    source = synthetic_bundle['source']
    rows = [json.loads(row) for row in source.read_bytes().splitlines()]
    for row in rows:
        row['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
    source.write_bytes(b''.join(canonical(row) + b'\n' for row in rows))
    event_sha = hashlib.sha256(canonical(rows[0])).hexdigest()
    path = bundle / 'deployment-manifest.json'
    manifest = json.loads(path.read_bytes())
    legacy_rel = 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    assert legacy_rel not in manifest['files'] and not bundle.joinpath(*legacy_rel.split('/')).exists()
    fixture_policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
    descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256':hashlib.sha256(fixture_policy).hexdigest(),
        'approval_reference_sha256':'b'*64})
    (bundle / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
    manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
    raw = canonical(manifest)
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    script = r'''
import hashlib, pathlib, sys, types
root, pin = pathlib.Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / 'tools-bootstrap'))
from tools import wd_mind_e5_snapshot_adapter as adapter
from tools import wd_mind_e5_lifecycle as lifecycle
from tools import wd_mind_e5_client as client_module
assert pathlib.Path(adapter.__file__).resolve() == (root / 'tools-bootstrap/tools/wd_mind_e5_snapshot_adapter.py').resolve()
assert callable(adapter.create_single_flat_adapter)
binding_hash = hashlib.sha256((root / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
owner = lifecycle._Owner(client_module.BULK_CONTRACT_SHA256)
owner.resources.process = types.SimpleNamespace(pid=1)
owner.resources.job = object()
owner._state = 'RUNNING'
handshake = client_module._RESPONSE.pack(b'ME5R', 0, 0, 0, b'\0' * 32, b'\0' * 32)
owner._output.extend(client_module._LENGTH.pack(len(handshake)) + handshake)
client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=binding_hash,
                                             bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
single = adapter.create_single_flat_adapter(enabled=True, bundle_root=str(root),
                                            expected_manifest_sha256=pin, client=client)
assert type(single) is adapter._SingleFlatAdapter
# This part uses the real bundled runtime, source binding and adapter. Only
# process launch and vector construction are synthetic; no model is started.
from tools import wd_mind_full_runtime as runtime
policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=str(root), expected_manifest_sha256=pin,
    identifier_policy_bytes=policy, expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
assert binding._PrivateBinding__snapshot['identifier_policy_binding'][0] == policy
factory = runtime._ManifestWorkerFactory(str(root), pin, {}, root / 'synthetic-lock', 1, 0,
                                         client_module.BULK_CONTRACT_SHA256)
def launch(self, *, owner_sink, call_deadline=None):
    owner_sink(owner)
    return {'status':'READY', 'client':client, 'owner':owner, 'lifecycle':owner.status(), 'authority':'none'}
runtime._ManifestWorkerFactory.__call__ = launch
# Synthetic encoder responses, but real snapshot, adapter and flat-memory build.
import struct
vector = struct.pack('<384f', 1.0, *([0.0] * 383))
def encode(payload, **kwargs):
    check = kwargs.get('frame_check')
    if check is not None: check()
    count = len(payload['passages']) if payload['operation'] == 'refresh' else 1
    return {'nonce':payload['nonce'], 'request_sha256':payload['request_sha256'], 'vector_bytes':vector * count}
client.encode_supervised = encode
import io
policy_pin = hashlib.sha256(policy).hexdigest()
anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=str(root),
    expected_manifest_sha256=pin, operator_policy_sha256=policy_pin,
    operator_approval_reference_sha256='b'*64)  # Synthetic operator inputs only.
control = struct.pack('>4sI', b'MCT1', len(policy)) + bytes.fromhex(pin) + bytes.fromhex('e'*64) + b'k'*32 + bytes.fromhex(policy_pin) + policy
control_buffer = runtime._ParentControlBuffer(runtime._PrivateParentStream(io.BytesIO(control)))
control_buffer.read_from(enabled=True)
entry_args = dict(enabled=True, policy_anchor=anchor, control_buffer=control_buffer,
    bundle_root=str(root), expected_manifest_sha256=pin, lane='codex-lead-1',
    worker_timeout_seconds=1, lock_timeout_seconds=0, shutdown_timeout=0,
    bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
try:
    runtime.prepare_manifest_bound_owner(**entry_args)
    raise AssertionError('untrusted production parent must refuse')
except runtime.FullRuntimeRefused:
    pass
original_provider = runtime._require_trusted_parent_export
def synthetic_entry_provider(*, owner, stream, bootstrap):
    assert owner is None and stream is None and bootstrap is control_buffer
runtime._require_trusted_parent_export = synthetic_entry_provider
runtime.create_manifest_worker_factory = lambda **kwargs: factory
entry = runtime.prepare_manifest_bound_owner(**entry_args)
runtime._require_trusted_parent_export = original_provider
assert entry['state'] == 'PREPARED_NOT_RUNNING' and entry['authority'] == 'none'
wrapper, binding, entry_bootstrap = entry['owner'], entry['owner']._binding, entry['bootstrap']
assert wrapper.status()['state'] == 'NEW'
assert wrapper.start() == {'state':'READY','owned':True,'authority':'none'}
assert type(wrapper._adapter) is adapter._SingleFlatAdapter and wrapper._client is client
assert wrapper._handler._memory is not None and wrapper._handler._memory.binding is binding
policy_pin = hashlib.sha256(binding._PrivateBinding__snapshot['identifier_policy_binding'][0]).hexdigest()
for bad in ({}, {'enabled':True,'expected_nonce':'x'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':10000000},
            {'enabled':True,'expected_nonce':'e'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':1},
            {'enabled':True,'expected_nonce':'e'*64,'expected_identifier_policy_sha256':policy_pin,'max_total_bytes':True}):
    try:
        wrapper.prepare_consumer_export(**bad)
        raise AssertionError('bad export input accepted')
    except runtime.FullRuntimeRefused:
        pass
import traceback
synthetic_private_key = binding._PrivateBinding__snapshot['identifier_policy_binding'][1]
try:
    wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=1)
    raise AssertionError('size limit accepted')
except runtime.FullRuntimeRefused as exc:
    captured = traceback.TracebackException.from_exception(exc, capture_locals=True)
    frames = [frame for frame in captured.stack if frame.name == 'prepare_consumer_export']
    assert frames and all(repr(synthetic_private_key) not in value for frame in frames for value in (frame.locals or {}).values()), 'traceback locals exposed identifier key'
prepared = wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=10000000)
info = prepared.status()
assert info['state'] == 'PREPARED_LOCAL_ONLY' and info['authority'] == 'none'
assert info['receiver_authentication'] == 'UNKNOWN' and info['transmission'] == 'NOT_AVAILABLE'
assert info['block_count'] == 12 and info['total_bytes'] > 0
import json
metadata_raw = prepared._PrivateConsumerExport__metadata
metadata = json.loads(metadata_raw)
blocks = prepared._PrivateConsumerExport__blocks
assert metadata['schema'] == 'wd.mind-owner-export.v1' and metadata['authority'] == 'none'
assert metadata['nonce'] == 'e'*64
assert [item['name'] for item in metadata['blocks']] == [name for name, value in blocks]
assert all(item['length'] == len(value) for item, (_, value) in zip(metadata['blocks'], blocks))
assert info['total_bytes'] == len(metadata_raw) + sum(len(value) for _, value in blocks)
export = dict(blocks)
assert export['identifier_key'] == binding._PrivateBinding__snapshot['identifier_policy_binding'][1]
assert export['projection'] == binding._PrivateBinding__snapshot['projection_bytes']
assert export['flat_manifest'] == wrapper._handler._memory.manifest
assert export['vectors'] == wrapper._handler._memory.vectors
assert export['ids'] == runtime.reader._canonical(list(wrapper._handler._memory.ids))
assert export['identifier_key'] not in metadata_raw
assert binding._PrivateBinding__snapshot['identifier_policy_binding'][1].hex() not in repr(prepared)
class UntrustedDestination:
    calls = 0
    def write(self, value):
        self.calls += 1
        raise AssertionError('private bytes escaped')
destination = UntrustedDestination()
try:
    prepared.write_to_handle(destination)
    raise AssertionError('unverified export must refuse')
except runtime.FullRuntimeRefused:
    pass
assert destination.calls == 0
import hmac, io, time
class Sink:
    def __init__(self): self.data, self.closed = bytearray(), False
    def write(self, data):
        count = min(37, len(data))
        self.data.extend(data[:count])
        return count
    def close(self): self.closed = True
sink = Sink()
candidate = runtime.inspect_manifest_policy_candidate(enabled=True, bundle_root=str(root), expected_manifest_sha256=pin)
assert candidate.status()['identifier_policy_sha256'] == policy_pin
assert candidate.status()['approval'] == 'UNVERIFIED'
bootstrap = entry_bootstrap
parent_stream = runtime._PrivateExportStream(sink)
try:
    prepared._send_parent_stream(enabled=True, stream=parent_stream,
        bootstrap=bootstrap, max_wire_bytes=20000000,
        deadline_monotonic=time.monotonic()+30)
    raise AssertionError('production authorization must refuse')
except runtime.FullRuntimeRefused:
    pass
assert not sink.data and not sink.closed
# Test-only provider checks synthetic pinned metadata and exact control handles;
# production has no provider argument and still refuses.
def synthetic_parent_provider(*, owner, stream, bootstrap: object):
    assert owner is wrapper and stream is parent_stream and bootstrap is globals()['bootstrap']
    assert candidate.status()['identifier_policy_sha256'] == hashlib.sha256(policy).hexdigest()
runtime._require_trusted_parent_export = synthetic_parent_provider
sent = prepared._send_parent_stream(enabled=True, stream=parent_stream,
    bootstrap=bootstrap, max_wire_bytes=20000000,
    deadline_monotonic=time.monotonic()+30)
assert sent == {'status':'SENT','authority':'none','receiver_authentication':'UNKNOWN'} and sink.closed
wire, seq, payloads, final = bytes(sink.data), 0, [], None
while wire:
    head, wire = wire[:16], wire[16:]
    magic, kind, number, length = struct.unpack('>4sB3xII', head)
    body, mac, wire = wire[:length], wire[length:length+32], wire[length+32:]
    assert magic == b'MEX1' and number == seq and len(body) == length
    assert hmac.compare_digest(mac, hmac.new(b'k'*32, b'wd.mind-export-frame.v1\0' + bytes.fromhex('e'*64) + head + body, hashlib.sha256).digest())
    seq += 1
    if kind == ord('F'):
        final = body
        assert not wire
    else:
        assert kind == (ord('M') if seq == 1 else ord('D'))
        payloads.append(body)
count, total, digest = struct.unpack('>IQ32s', final)
expected = metadata_raw + b''.join(value for _, value in blocks)
assert count == len(payloads)-1 and total == len(expected) and digest == hashlib.sha256(expected).digest()
assert b''.join(payloads) == expected
assert prepared.status()['state'] == 'DISCARDED' and prepared.status()['total_bytes'] == 0
response = wrapper.candidates({'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
    'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'})
assert response['status'] == 'CANDIDATES' and len(response['candidates']) == 1, repr(response)
assert response['candidates'][0]['source_event_sha256'] == sys.argv[3]
memory = wrapper._handler._memory
if sys.argv[4] == 'vectors':
    memory.vectors = b'\0' * len(memory.vectors)
else:
    policy_pin = '0'*64
try:
    wrapper.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256=policy_pin, max_total_bytes=10000000)
    raise AssertionError('altered vectors or policy pin accepted')
except runtime.FullRuntimeRefused:
    pass
assert wrapper.status()['state'] == 'HOLD'
assert 'tools.wd_mind_e5_rotating_client' not in sys.modules
print('synthetic_adapter_manifest_verified')
'''
    proc = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(bundle), pin, event_sha, fault],
                          cwd=bundle, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == 'synthetic_adapter_manifest_verified'


@pytest.mark.parametrize(('lane_name', 'expected_scope'), [
    ('codex-lead-1', 'own'), ('claude-rco-1', 'shared'), ('claude-rco-2', 'shared'),
])
def test_owner_child_role_runs_in_separate_synthetic_process(synthetic_bundle, lane_name, expected_scope):
    """Real role + pinned snapshot; model, parent trust and worker are fixtures."""
    import datetime
    import struct
    bundle, source = synthetic_bundle['bundle'], synthetic_bundle['source']
    rows = [json.loads(row) for row in source.read_bytes().splitlines()]
    for row in rows:
        row['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
    source.write_bytes(b''.join(canonical(row) + b'\n' for row in rows))
    event_sha = hashlib.sha256(canonical(rows[0])).hexdigest()
    policy = b'{"identifiers":["synthetic-private-marker-52"],"schema":"wd.mind-identifier-policy.v1"}'
    policy_sha = hashlib.sha256(policy).hexdigest()
    descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
                            'identifier_policy_sha256':policy_sha, 'approval_reference_sha256':'b'*64})
    (bundle / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    legacy_rel = 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    legacy_fixture = bundle.joinpath(*legacy_rel.split('/'))
    assert legacy_rel not in manifest['files'] and not legacy_fixture.exists()
    manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
    manifest_raw = canonical(manifest)
    manifest_path.write_bytes(manifest_raw)
    pin, nonce, channel = hashlib.sha256(manifest_raw).hexdigest(), b'n'*32, b'k'*32
    lane = lane_name.encode('ascii')
    mct = struct.pack('>4sI', b'MCT1', len(policy)) + bytes.fromhex(pin) + nonce + channel + bytes.fromhex(policy_sha) + policy
    control = b'MCO1' + bytes.fromhex(pin) + bytes.fromhex(policy_sha) + bytes.fromhex('b'*64) + bytes([len(lane)]) + lane + mct
    script = r'''
import hashlib, os, pathlib, struct, sys, types
root, pin = pathlib.Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / 'tools-bootstrap'))
from tools import wd_mind_runtime_child as child, wd_mind_full_runtime as runtime
from tools import wd_mind_e5_client as client_module, wd_mind_e5_lifecycle as lifecycle
owner = lifecycle._Owner(client_module.BULK_CONTRACT_SHA256)
owner.resources.process = types.SimpleNamespace(pid=1)
owner.resources.job = object()
owner._state = 'RUNNING'
binding_hash = hashlib.sha256((root / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
handshake = client_module._RESPONSE.pack(b'ME5R', 0, 0, 0, b'\0' * 32, b'\0' * 32)
owner._output.extend(client_module._LENGTH.pack(len(handshake)) + handshake)
client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=binding_hash,
                                             bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
vector = struct.pack('<384f', 1.0, *([0.0] * 383))
def encode(payload, **kwargs):
    check = kwargs.get('frame_check')
    if check is not None: check()
    count = len(payload['passages']) if payload['operation'] == 'refresh' else 1
    return {'nonce':payload['nonce'], 'request_sha256':payload['request_sha256'], 'vector_bytes':vector * count}
client.encode_supervised = encode
client.close = lambda **kwargs: {'state':'EXITED', 'owned':False}
factory = runtime._ManifestWorkerFactory(str(root), pin, {}, root / 'synthetic-lock', 1, 0,
                                         client_module.BULK_CONTRACT_SHA256)
def launch(self, *, owner_sink, call_deadline=None):
    owner_sink(owner)
    return {'status':'READY', 'client':client, 'owner':owner, 'lifecycle':owner.status(), 'authority':'none'}
runtime._ManifestWorkerFactory.__call__ = launch
runtime.create_manifest_worker_factory = lambda **kwargs: factory
runtime._require_trusted_parent_export = lambda **kwargs: None  # Synthetic fixture only.
child._require_verified_parent_spawn = lambda: None  # Synthetic fixture only.
os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = pin
raise SystemExit(child._fixed_child_main(['--mind-owner-child']))
'''
    proc = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(bundle), pin],
                          cwd=bundle, input=control, capture_output=True, timeout=30,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert proc.returncode == 0, 'synthetic owner process failed'
    assert proc.stdout.startswith(b'MEX1')
    candidate = json.loads(proc.stderr)
    assert candidate['nonce'] == nonce.hex() and candidate['lane'] == lane.decode() and candidate['scope'] == expected_scope
    assert candidate['status'] == 'CANDIDATES'
    assert candidate['candidates'][0]['source_event_sha256'] == event_sha
    excluded = {hashlib.sha256(canonical(row)).hexdigest() for row in rows[2:]}
    assert all(item['source_event_sha256'] not in excluded for item in candidate['candidates'])


@pytest.mark.parametrize(('lane', 'scope'), [
    ('codex-lead-1', 'own'), ('codex-tools-1', 'own'), ('fable-5', 'own'),
    ('claude-rco-1', 'shared'), ('claude-rco-2', 'shared'),
])
def test_startup_scope_is_fixed_by_recognized_lane(lane, scope):
    from tools import wd_bridge_mind_session as session, wd_mind_runtime_child as child
    assert child._startup_scope_for_lane(lane) == scope
    assert session._startup_scope_for_lane(lane) == scope


@pytest.mark.parametrize('lane', ['', 'claude-rco-3', 'CLAUDE-RCO-1', None, 1])
def test_startup_scope_rejects_unrecognized_lane(lane):
    from tools import wd_bridge_mind_session as session, wd_mind_runtime_child as child
    with pytest.raises(ValueError):
        child._startup_scope_for_lane(lane)
    with pytest.raises(ValueError):
        session._startup_scope_for_lane(lane)


@pytest.mark.skipif(os.name != 'nt', reason='Windows standard-handle control pipe')
def test_retained_parent_pair_wires_control_relay_and_two_synthetic_children(synthetic_bundle):
    """Exercise actual parent/pipe ownership; child roles and auth are fixtures."""
    import struct
    bundle = synthetic_bundle['bundle']
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    policy_sha = hashlib.sha256(policy).hexdigest()
    descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
                            'identifier_policy_sha256':policy_sha, 'approval_reference_sha256':'b'*64})
    (bundle / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    legacy_rel = 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    assert legacy_rel not in manifest['files'] and not bundle.joinpath(*legacy_rel.split('/')).exists()
    manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
    manifest_raw = canonical(manifest)
    manifest_path.write_bytes(manifest_raw)
    pin, nonce, key, lane = hashlib.sha256(manifest_raw).hexdigest(), b'n'*32, b'k'*32, b'codex-lead-1'
    mct = struct.pack('>4sI', b'MCT1', len(policy)) + bytes.fromhex(pin) + nonce + key + bytes.fromhex(policy_sha) + policy
    raw = b'MCP1' + bytes.fromhex(pin) + bytes.fromhex(policy_sha) + bytes.fromhex('b'*64) + bytes([len(lane)]) + lane + mct
    script = r'''
import io, os, pathlib, sys, time
root,pin = pathlib.Path(sys.argv[1]),sys.argv[2]
sys.path.insert(0, str(root / 'tools-bootstrap'))
from tools import wd_mind_runtime_child as child, wd_mind_full_runtime as runtime
owner_code = "import sys; c=sys.stdin.buffer.read(); assert c[:4]==b'MCO1'; sys.stderr.buffer.write(b'{}'); sys.stderr.buffer.close(); sys.stdout.buffer.write(b'synthetic-mex1'); sys.stdout.buffer.close()"
consumer_code = r"""
import ctypes,hashlib,json,sys
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetStdHandle.argtypes=[ctypes.c_ulong]; k.GetStdHandle.restype=ctypes.c_void_p
k.ReadFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]; k.ReadFile.restype=ctypes.c_int
h=k.GetStdHandle((-12)&0xffffffff); out=bytearray()
while True:
    b=ctypes.create_string_buffer(4096); n=ctypes.c_ulong()
    ok=k.ReadFile(h,b,4096,ctypes.byref(n),None)
    if not ok or not n.value: break
    out.extend(b.raw[:n.value])
assert out[:4]==b'MCC1' and b'MCR1' in out
assert sys.stdin.buffer.read()==b'synthetic-mex1'
fleet=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()
result={'schema':'wd.mind-flat-verified-session.v1','authority':'none','lane':'codex-lead-1',
 'fleet_sha256':fleet,'sender_authentication':'UNKNOWN','index_match':'UNKNOWN','same_user_aba':'UNKNOWN',
 'current_status_freshness':'UNKNOWN',
 'results':[{'text':'synthetic','source_event_sha256':'a'*64,'event_time_utc_floor':'2026-01-01T00:00:00Z',
 'source_agent':'codex-lead-1','event_age_seconds_upper_bound':1}]}
sys.stdout.write(json.dumps(result))
"""
child._require_verified_parent_spawn=lambda:None
child._verified_parent_child_command=lambda **kw: (sys.executable,'-I','-S','-B','-c',owner_code) if kw['role']=='owner' else (sys.executable,'-I','-S','-B','-c',consumer_code,str(root/'wd-fleet.json'))
os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH']=pin
raise SystemExit(child._fixed_child_main(['--parent-control']))
'''
    proc = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', script, str(bundle), pin], cwd=bundle,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, _ = proc.communicate(input=raw, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail('synthetic parent remains held after deadline; no process was killed')
    assert proc.returncode == 0, 'synthetic parent process failed'
    result = json.loads(stdout)
    assert result['lane'] == 'codex-lead-1' and result['results'][0]['source_event_sha256'] == 'a'*64


def test_single_manifest_owner_retains_failed_launch_owner(tmp_path, monkeypatch):
    """A post-reservation HOLD must stay reachable without an extra worker."""
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_e5_client
    fleet = canonical({'lanes': [{'agent': 'codex-lead-1'}],
                       'tools_supervisor': {'agent': 'codex-tools-1'}})
    snapshot = {'projection_sha256': 'b' * 64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a' * 64, (fleet,), {})
    factory = _synthetic_admitted_factory(monkeypatch, str(tmp_path), 'a' * 64, {}, tmp_path / 'lock', 5, 0,
                                          wd_mind_e5_client.BULK_CONTRACT_SHA256)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)

    class Owner:
        def status(self):
            return {'state': 'HOLD', 'owned': True}
        def shutdown(self, *, timeout):
            return {'state': 'HOLD', 'owned': True}

    held = Owner()
    calls = []
    def start(self, **kwargs):
        calls.append(True)
        kwargs['owner_sink'](held)
        return {'status': 'HOLD', 'client': None, 'owner': held,
                'lifecycle': held.status(), 'authority': 'none'}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', start)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert calls == []
    assert owner.start() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.start()
    assert calls == [True]


def _single_owner_synthetic(monkeypatch, tmp_path):
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_e5_client
    from tools import wd_mind_e5_snapshot_adapter as adapter_module
    fleet = canonical({'lanes': [{'agent': 'codex-lead-1'}],
                       'tools_supervisor': {'agent': 'codex-tools-1'}})
    snapshot = {'projection_sha256': 'b' * 64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a' * 64, (fleet,), {})
    factory = _synthetic_admitted_factory(monkeypatch, str(tmp_path), 'a' * 64, {}, tmp_path / 'lock', 5, 0,
                                          wd_mind_e5_client.BULK_CONTRACT_SHA256)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)

    class Owner:
        def status(self): return {'state': 'RUNNING', 'owned': True}
        def shutdown(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    class Client:
        def __init__(self, owner): self.owner = owner
        def close(self, *, timeout): return {'state': 'EXITED', 'owned': False, 'exit_code': 0}
    class Adapter:
        def passage(self, values): return b'synthetic'
    held = Owner()
    client = Client(held)
    calls = []
    def start(self, **kwargs):
        calls.append(True)
        kwargs['owner_sink'](held)
        return {'status': 'READY', 'client': client, 'owner': held,
                'lifecycle': held.status(), 'authority': 'none'}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', start)
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **kwargs: Adapter(), raising=False)
    return runtime, adapter_module, binding, factory, calls


def test_cache_preflight_refusal_precedes_worker_launch(monkeypatch, tmp_path):
    runtime, _, binding, factory, launches = _single_owner_synthetic(monkeypatch, tmp_path)
    class Cache:
        def preflight(self):
            raise ValueError('synthetic cache root refused')
    monkeypatch.setattr(runtime, '_load_pinned_vector_cache', lambda _: Cache())
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
        worker_factory=factory, shutdown_timeout=0, vector_cache_enabled=True)
    result = owner.start()
    assert result['state'] == 'HOLD'
    assert result['cache_status'] == 'PREFLIGHT_REFUSED'
    assert launches == []


def test_cache_write_failure_prevents_owner_ready(monkeypatch, tmp_path):
    runtime, _, binding, factory, launches = _single_owner_synthetic(monkeypatch, tmp_path)
    class Cache:
        def preflight(self): return 'MISSING'
    monkeypatch.setattr(runtime, '_load_pinned_vector_cache', lambda _: Cache())
    def build(handler):
        handler._memory = runtime._PrivateFlatMemory(binding, {}, b'', (), b'', b'', 'MISS_NOT_STORED')
        return {'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'}
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
        worker_factory=factory, shutdown_timeout=0, vector_cache_enabled=True)
    result = owner.start()
    assert result['state'] == 'HOLD' and result['cache_status'] == 'NOT_STORED'
    assert launches == [True]


def test_warm_cache_starts_query_worker_without_passage_reembedding(monkeypatch):
    from types import SimpleNamespace
    from tools import wd_mind_full_runtime as runtime
    rotator = SimpleNamespace(owner=None)
    calls = []
    class Adapter:
        encoder_binding_bytes = b'encoder'
        encoder_binding_sha256 = 'a' * 64
        def query(self, text):
            calls.append(('query', text))
            rotator.owner = object()
            return (1.0,) + (0.0,) * 383
        def passage(self, _texts):
            pytest.fail('warm cache must not re-embed a passage')
    memory = SimpleNamespace(manifest=b'm', ids=(), vectors=b'', encoder_binding=b'e', cache_status='HIT_STORED')
    monkeypatch.setattr(runtime, 'build_flat_memory', lambda **kwargs: memory)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_source_check', lambda self: None)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner',
                        lambda self: None if self._rotator.owner is not None else (_ for _ in ()).throw(ValueError()))
    handler = runtime._FlatRequestHandler(None, {}, 'codex-lead-1', (), rotator, Adapter(), None, 1, None,
                                          vector_cache=object())
    assert handler.build()['status'] == 'BUILT_IN_MEMORY'
    assert len(calls) == 1 and calls[0][0] == 'query'


@pytest.mark.parametrize('scenario', ['ready', 'held', 'interrupt', 'nonzero', 'client_swapped',
                                      'close_error', 'close_interrupt'])
def test_rotating_owner_retains_current_worker_and_closes_cooperatively(tmp_path, monkeypatch, scenario):
    """The optional route must retain the rotating owner through every outcome."""
    import types
    runtime, adapter_module, binding, factory, old_launches = _single_owner_synthetic(monkeypatch, tmp_path)
    checks, rotators = [], []
    monkeypatch.setattr(runtime._PrivateBinding, '_immutable_checkpoint', lambda self, frozen: checks.append('source'))
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: checks.append('policy'))
    monkeypatch.setattr(adapter_module, '_flat_bundle_bindings', lambda *args: (b'query-binding', 'c' * 64))

    class Rotator:
        def __init__(self, **kwargs):
            self.options, self.owner = kwargs, None
            self.state, self.closed = 'NEW', False
            rotators.append(self)
        def status(self):
            return {'state': self.state, 'owned': self.owner is not None, 'authority': 'none'}
        def close(self, *, timeout):
            self.closed = True
            if scenario == 'close_error': raise ValueError('synthetic close failure')
            if scenario == 'close_interrupt': raise KeyboardInterrupt()
            if self.state == 'HOLD':
                return {'state': 'HOLD', 'owned': True, 'authority': 'none'}
            if scenario == 'nonzero':
                return {'state': 'EXITED', 'owned': False, 'exit_code': 1, 'authority': 'none'}
            self.owner, self.state = None, 'EXITED'
            return {'state': 'EXITED', 'owned': False, 'exit_code': 0, 'authority': 'none'}

    module = types.ModuleType('tools.wd_mind_e5_rotating_client')
    module.RotatingGracefulE5Client = Rotator
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(adapter_module, '_load_pinned_rotating_module', lambda *args: module, raising=False)
    monkeypatch.setattr(adapter_module, 'create_rotating_flat_adapter', lambda **kwargs: object(), raising=False)

    def build(handler):
        rotator = handler._rotator
        assert rotator.options['source_check']() == 'b' * 64
        rotator.owner, rotator.state = object(), 'RUNNING'
        if scenario == 'held':
            rotator.state = 'HOLD'
            raise ValueError('synthetic hold')
        if scenario == 'interrupt':
            raise KeyboardInterrupt()
        return {'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'}

    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0, worker_rotation=True)
    assert old_launches == [] and rotators == []
    if scenario == 'interrupt':
        with pytest.raises(KeyboardInterrupt):
            owner.start()
    else:
        outcome = owner.start()
        assert outcome['state'] == ('HOLD' if scenario == 'held' else 'READY')
    assert checks == ['policy', 'source']
    assert old_launches == [] and len(rotators) == 1
    assert owner._client is rotators[0]
    if scenario == 'client_swapped':
        replacement = object()
        owner._client = replacement
        assert owner.status()['state'] == 'HOLD'
        assert owner.close()['state'] == 'HOLD'
        assert not rotators[0].closed and owner._client is replacement
        assert owner._rotation_anchor is rotators[0]
        return
    if scenario == 'close_interrupt':
        with pytest.raises(KeyboardInterrupt):
            owner.close()
        assert owner.status()['state'] == 'HOLD' and rotators[0].owner is not None
        return
    outcome = owner.close()
    assert rotators[0].closed
    if scenario in ('held', 'nonzero', 'close_error'):
        assert outcome['state'] == 'HOLD' and rotators[0].owner is not None
    else:
        assert outcome == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}


def test_rotating_owner_requires_explicit_boolean_opt_in(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    for invalid in (None, 1, 'true'):
        with pytest.raises(runtime.FullRuntimeRefused):
            runtime.create_single_manifest_owner(enabled=True, binding=binding,
                lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0, worker_rotation=invalid)


@pytest.mark.parametrize('changed', ['source', 'policy'])
def test_rotating_owner_refuses_changed_controls_before_worker_start(tmp_path, monkeypatch, changed):
    import types
    runtime, adapter_module, binding, factory, old_launches = _single_owner_synthetic(monkeypatch, tmp_path)
    calls = []

    def policy(self):
        calls.append('policy')
        if changed == 'policy': raise ValueError('changed')
    def source(self, frozen):
        calls.append('source')
        if changed == 'source': raise ValueError('changed')
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', policy)
    monkeypatch.setattr(runtime._PrivateBinding, '_immutable_checkpoint', source)
    monkeypatch.setattr(adapter_module, '_flat_bundle_bindings', lambda *args: (b'query-binding', 'c' * 64))

    class Rotator:
        def __init__(self, **kwargs): self.options, self.owner = kwargs, None
        def status(self): return {'state': 'NEW', 'owned': False, 'authority': 'none'}
        def close(self, *, timeout): return {'state': 'EXITED', 'owned': False, 'exit_code': 0}

    module = types.ModuleType('tools.wd_mind_e5_rotating_client')
    module.RotatingGracefulE5Client = Rotator
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(adapter_module, '_load_pinned_rotating_module', lambda *args: module, raising=False)
    monkeypatch.setattr(adapter_module, 'create_rotating_flat_adapter', lambda **kwargs: object(), raising=False)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda handler: handler._rotator.options['source_check']())
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0, worker_rotation=True)
    assert owner.start() == {'state': 'HOLD', 'owned': False, 'authority': 'none'}
    assert old_launches == []
    assert calls == (['policy'] if changed == 'policy' else ['policy', 'source'])
    assert owner.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}


def test_rotating_owner_refuses_unverified_loader_before_worker_start(tmp_path, monkeypatch):
    runtime, adapter, binding, factory, old_launches = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, '_flat_bundle_bindings', lambda *args: (b'query-binding', 'c' * 64))

    def reject(*args): raise ValueError('synthetic invalid module pin')
    monkeypatch.setattr(adapter, '_load_pinned_rotating_module', reject, raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0, worker_rotation=True)
    assert owner.start()['state'] == 'HOLD'
    assert owner._client is None and owner._rotation_anchor is None and old_launches == []


@pytest.mark.parametrize('failure', ['factory', 'snapshot'])
def test_single_owner_close_releases_proven_prelaunch_failure(tmp_path, monkeypatch, failure):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    if failure == 'factory':
        factory._manifest = '0' * 64
    else:
        monkeypatch.setattr(runtime, '_flat_snapshot', lambda *args: (_ for _ in ()).throw(ValueError()))
    assert owner.start()['state'] == 'HOLD'
    assert owner._launch_thread is None and owner._owner is None and calls == []
    assert owner.close() == {'state':'STOPPED', 'owned':False, 'authority':'none'}


def test_single_owner_close_preserves_uncertain_thread_start_failure(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    monkeypatch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError()))
    assert owner.start()['state'] == 'HOLD'
    assert owner._launch_thread is not None and calls == []
    assert owner.close() == {'state':'HOLD', 'owned':None, 'authority':'none'}


def test_single_owner_close_releases_prelaunch_cancellation(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        owner.start()
    assert owner._launch_thread is None and calls == []
    assert owner.close() == {'state':'STOPPED', 'owned':False, 'authority':'none'}


@pytest.mark.parametrize('operation', ['candidate', 'query'])
def test_single_owner_without_operator_anchor_cannot_return_candidates(tmp_path, monkeypatch, operation):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    request = {'schema':'wd.mind-pipe-request.v2','query_id':'startup_work_overview',
               'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
    def attempt():
        if operation == 'candidate':
            return owner.located_candidates(request)
        return owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    # Default-off refuses without touching a healthy owner.
    with pytest.raises(runtime.FullRuntimeRefused):
        attempt()
    assert owner.status() == {'state':'READY', 'owned':True, 'authority':'none'}
    # Synthetic setup only: expose the distinct missing-anchor check.
    from types import SimpleNamespace
    owner._snapshot['messages'] = {'end_cursor':SimpleNamespace(offset=100)}
    owner._prewarm_generation = owner._generation
    owner._retained = owner._handler._retained = object()
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    with pytest.raises(runtime.FullRuntimeRefused):
        attempt()
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert owner._retained is None and owner._handler._retained is None


def _refresh_generation_test_owner(monkeypatch, tmp_path):
    """Synthetic orchestration only; this fixture does not verify source pins."""
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    old_snapshot = owner._snapshot
    old_memory = runtime._PrivateFlatMemory(binding, old_snapshot, b'old', (), b'', b'encoder')
    owner._handler._memory = old_memory
    owner._handler._frozen_flat = (old_memory.manifest, old_memory.ids, old_memory.vectors, old_memory.encoder_binding)
    new_snapshot = {'projection_sha256':'c' * 64}
    new_binding = runtime._PrivateBinding(new_snapshot, tmp_path, 'a' * 64, binding._PrivateBinding__controls, {})
    new_memory = runtime._PrivateFlatMemory(new_binding, new_snapshot, b'new', (), b'', b'encoder')
    # Orchestration seam only. The separate real-bundle tests keep these checks.
    monkeypatch.setattr(runtime, '_verify_refresh_basis', lambda current_owner: None)
    monkeypatch.setattr(runtime, '_flat_check', lambda memory: None)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query',
                        lambda self: self._PrivateBinding__snapshot)
    return runtime, owner, (binding, old_snapshot, old_memory), (new_binding, new_snapshot, new_memory)


def _direct_query_test_owner(monkeypatch, tmp_path):
    """Synthetic orchestration; not evidence of an authenticated caller or live source."""
    from types import SimpleNamespace
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    owner._handler._memory = runtime._PrivateFlatMemory(binding, owner._snapshot, b'fake', (), b'', b'encoder')
    owner._handler._frozen_flat = (b'fake', (), b'', b'encoder')
    record_id = 'synthetic-id'
    doc_id = runtime.reader._sha(record_id.encode())
    ref, event_ref = 'a'*64, 'b'*64
    text = 'synthetic accepted message'
    record = {'record_id':record_id, 'time_utc':'2026-01-01T00:00:00Z',
              'meta':{'agent':'codex-lead-1'},
              'provenance':{'source_event_ref':event_ref}}
    monkeypatch.setattr(runtime, '_flat_check', lambda memory: {record_id:record})
    monkeypatch.setattr(runtime, '_local_generation_policy_pin', lambda owner: 'c'*64)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    monkeypatch.setattr(runtime.reader, 'decode_text', lambda value: value)
    source = {'ref_sha256':ref, 'doc_id_sha256':doc_id, 'ref_kind':'sha256_of_projected_metadata',
              'canonical_event':False, 'stored_text':text,
              'source_reference':{'sha256':event_ref, 'kind':'sha256_of_canonical_event_json',
                                  'verified':'MATCH_SELECTED_ROW', 'sender_authentication':'UNKNOWN'}}
    source_owner = SimpleNamespace(resolve=lambda **kwargs: {'results':[{
        'text':source['stored_text'], 'source_event_sha256':source['source_reference']['sha256']}]})
    retained = SimpleNamespace(_source_owner=source_owner, _records={record_id:record},
                               _controls=lambda:None, _prefix=lambda context, **kwargs:None)
    # Synthetic orchestration only; real generation prewarm has separate tests.
    owner._snapshot['messages'] = {'end_cursor':SimpleNamespace(offset=100)}
    owner._prewarm_generation = owner._generation
    owner._retained = owner._handler._retained = retained
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: retained)
    hit = dict(source, source_agent='codex-lead-1', source_time_utc=record['time_utc'],
               source_valid_until_utc=None, event_age_seconds_upper_bound=1)
    def handle(self, request, lane):
        assert lane == 'codex-lead-1' and request['scope'] == 'own'
        return {'schema':'wd.mind-flat-owner-response.v1', 'lane':lane, 'scope':'own',
                'authority':'none', 'expires_utc':None, 'expiry_scope':'source_validity_only',
                'snapshot_freshness':'UNKNOWN', 'result':{'schema':'wd.mind-flat-f32-query.v1',
                'authority':'none', 'status':'SEMANTIC_CANDIDATE', 'results':[hit]}}
    monkeypatch.setattr(runtime._FlatRequestHandler, '_handle_for_lane', handle)
    return runtime, owner, factory, record, source, hit


def test_single_owner_direct_query_rechecks_selected_source_and_keeps_unknown_freshness(tmp_path, monkeypatch):
    runtime, owner, _, _, _, _ = _direct_query_test_owner(monkeypatch, tmp_path)
    result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert result['schema'] == 'wd.mind-flat-verified-session.v1'
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['current_status_freshness'] == 'UNKNOWN' and len(result['results']) == 1
    assert result['results'][0]['source_event_sha256'] == 'b'*64
    assert result['results'][0]['event_age_seconds_upper_bound'] > 900
    assert result['results'][0]['text'] == 'synthetic accepted message'


@pytest.mark.parametrize('fault', ['source', 'policy', 'factory'])
def test_single_owner_direct_query_refuses_mutation_and_retains_hold(tmp_path, monkeypatch, fault):
    runtime, owner, factory, _, source, _ = _direct_query_test_owner(monkeypatch, tmp_path)
    if fault == 'source': source['source_reference'] = dict(source['source_reference'], sha256='e'*64)
    elif fault == 'policy': monkeypatch.setattr(runtime, '_local_generation_policy_pin', lambda owner: (_ for _ in ()).throw(ValueError()))
    else: factory._manifest = '0'*64
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}


@pytest.mark.parametrize('query', ['', ' '*2000 + 'x', '\ud800', 'x'*2000 + '\ud800',
                                   'x'*4001, 'a\x00b'])
def test_single_owner_direct_query_bad_request_does_not_close_owner(tmp_path, monkeypatch, query):
    runtime, owner, _, _, _, _ = _direct_query_test_owner(monkeypatch, tmp_path)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query=query)
    assert owner.status() == {'state':'READY', 'owned':True, 'authority':'none'}
    result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert result['results'][0]['source_event_sha256'] == 'b'*64


def test_single_owner_direct_query_accepts_over_2000_valid_characters(tmp_path, monkeypatch):
    _, owner, _, _, _, _ = _direct_query_test_owner(monkeypatch, tmp_path)
    result = owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='x'*2001)
    assert result['results'][0]['source_event_sha256'] == 'b'*64


def test_single_owner_direct_query_refuses_source_expiry_during_final_checks(tmp_path, monkeypatch):
    runtime, owner, _, record, _, _ = _direct_query_test_owner(monkeypatch, tmp_path)
    retained = owner._ensure_retained_locked()
    def expire(context, *, source_budget=None):
        record['provenance']['valid_until_utc'] = '2020-01-01T00:00:00Z'
    retained._prefix = expire
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='work')
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}


def _test_staged_refresh(runtime, owner, new, snapshot=None):
    handler = runtime._FlatRequestHandler(new[0], new[1], owner._lane, owner._lanes,
        owner._client, owner._adapter, owner._factory, owner._timeout, owner._factory._bulk, owner._watcher_lease)
    handler._memory = new[2]
    handler._frozen_flat = (new[2].manifest, new[2].ids, new[2].vectors, new[2].encoder_binding)
    return {'binding':new[0], 'snapshot':new[1] if snapshot is None else snapshot,
            'memory':new[2], 'handler':handler}


def _test_generation_view(owner):
    """Inspect all published members together under the same owner lock."""
    with owner._lock:
        return owner._binding, owner._snapshot, owner._handler._memory


def test_generation_refresh_is_default_off_and_does_not_stage(monkeypatch, tmp_path):
    runtime, owner, old, _ = _refresh_generation_test_owner(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(runtime, '_stage_refresh_generation', lambda *args: calls.append(True), raising=False)
    with pytest.raises(runtime.FullRuntimeRefused):
        owner._refresh_generation(enabled=False)
    assert calls == [] and _test_generation_view(owner) == old
    with pytest.raises(runtime.FullRuntimeRefused): owner._handler.refresh()


def test_generation_refresh_interrupted_handler_lock_releases_owner_lock(monkeypatch, tmp_path):
    runtime, owner, old, _ = _refresh_generation_test_owner(monkeypatch, tmp_path)
    old_handler = owner._handler
    old_view = old_handler.lane_view('codex-lead-1')
    original_lock = old_handler._lock

    class InterruptedLock:
        def acquire(self, *, blocking):
            assert blocking is False
            raise KeyboardInterrupt('synthetic lock interruption')

    old_handler._lock = InterruptedLock()
    try:
        with pytest.raises(KeyboardInterrupt):
            owner._refresh_generation(enabled=True)
    finally:
        old_handler._lock = original_lock
    assert owner._state == 'HOLD' and _test_generation_view(owner) == old
    assert old_handler._closed
    with pytest.raises(runtime.FullRuntimeRefused):
        old_view.handle({'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1',
                         'scope':'own', 'query':'synthetic unrelated target'})
    assert owner._lock.acquire(blocking=False)
    owner._lock.release()


def test_generation_refresh_publishes_binding_and_memory_together(monkeypatch, tmp_path):
    runtime, owner, old, new = _refresh_generation_test_owner(monkeypatch, tmp_path)
    observed = []
    def stage(current_owner):
        # Refresh holds the owner lock here. No candidate is yet published.
        observed.append((current_owner._binding, current_owner._snapshot, current_owner._handler._memory))
        return _test_staged_refresh(runtime, current_owner, new)
    monkeypatch.setattr(runtime, '_stage_refresh_generation', stage, raising=False)
    owner._refresh_generation(enabled=True)
    assert observed == [old]
    assert _test_generation_view(owner) == new
    assert owner._state == 'READY' and owner._handler._memory.binding is owner._binding


@pytest.mark.parametrize('fault', ['refused', 'cancel', 'generator', 'async', 'group', 'unknown', 'hold'])
def test_generation_refresh_refusal_locals_do_not_expose_snapshot_policy_or_key(monkeypatch, tmp_path, fault):
    import asyncio
    import traceback
    runtime, owner, old, new = _refresh_generation_test_owner(monkeypatch, tmp_path)
    policy_marker = b'SYNTHETIC_POLICY_LOCAL_MARKER_ONLY'
    key_marker = b'SYNTHETIC_KEY_LOCAL_MARKER_ONLY'
    old[1]['identifier_policy_binding'] = (policy_marker, key_marker, 'synthetic')
    new[1]['identifier_policy_binding'] = (policy_marker, key_marker, 'synthetic')
    monkeypatch.setattr(runtime, '_stage_refresh_generation',
                        lambda current_owner: _test_staged_refresh(runtime, current_owner, new), raising=False)
    def reject(current_owner, staged):
        if fault == 'cancel': raise KeyboardInterrupt('synthetic')
        if fault == 'generator': raise GeneratorExit('synthetic')
        if fault == 'async': raise asyncio.CancelledError('synthetic')
        if fault == 'group': raise BaseExceptionGroup('synthetic', [ValueError('discard'), GeneratorExit('discard')])
        if fault == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
        if fault == 'hold': raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
        raise ValueError('synthetic refusal')
    monkeypatch.setattr(runtime, '_validate_refresh_generation', reject)
    expected = {'cancel':KeyboardInterrupt, 'generator':GeneratorExit, 'async':asyncio.CancelledError,
                'group':BaseExceptionGroup, 'unknown':runtime.FullRuntimeCancellationUnknown}.get(
                    fault, runtime.FullRuntimeRefused)
    with pytest.raises(expected) as caught: owner._refresh_generation(enabled=True)
    trace = traceback.TracebackException.from_exception(caught.value, capture_locals=True)
    frame = next(item for item in trace.stack if item.name == '_refresh_generation')
    shown = ' '.join((frame.locals or {}).values())
    assert policy_marker.decode() not in shown and key_marker.decode() not in shown
    assert _test_generation_view(owner) == old and owner._state == 'HOLD'


@pytest.mark.parametrize('fault', ['refused', 'ordinary_group', 'cancel', 'generator', 'async',
                                   'group', 'oversized', 'unknown', 'hold'])
def test_generation_delivery_refusal_locals_do_not_expose_snapshot_policy_or_key(monkeypatch, tmp_path, fault):
    import asyncio
    import traceback
    runtime, owner, old, _ = _refresh_generation_test_owner(monkeypatch, tmp_path)
    policy_marker = b'SYNTHETIC_POLICY_LOCAL_MARKER_ONLY'
    key_marker = b'SYNTHETIC_KEY_LOCAL_MARKER_ONLY'
    old[1]['identifier_policy_binding'] = (policy_marker, key_marker, 'synthetic')
    def reject(*args):
        if fault == 'cancel': raise KeyboardInterrupt('synthetic')
        if fault == 'generator': raise GeneratorExit('synthetic')
        if fault == 'async': raise asyncio.CancelledError('synthetic')
        if fault == 'group': raise BaseExceptionGroup('synthetic', [ValueError('discard'), GeneratorExit('discard')])
        if fault == 'oversized':
            raise BaseExceptionGroup('synthetic', [ValueError('discard') for _ in range(1024)] + [GeneratorExit()])
        if fault == 'ordinary_group': raise ExceptionGroup('synthetic', [ValueError('discard')])
        if fault == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
        if fault == 'hold': raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
        raise runtime._GenerationInputRefused('mind_full_runtime_refused')
    monkeypatch.setattr(runtime, '_prepare_generation_delivery', reject)
    expected = {'cancel':KeyboardInterrupt, 'generator':GeneratorExit, 'async':asyncio.CancelledError,
                'group':BaseExceptionGroup, 'oversized':runtime.FullRuntimeCancellationUnknown,
                'unknown':runtime.FullRuntimeCancellationUnknown}.get(fault, runtime.FullRuntimeRefused)
    with pytest.raises(expected) as caught:
        owner._prepare_consistent_delivery(enabled=True, max_total_bytes=64 * 1024 * 1024)
    trace = traceback.TracebackException.from_exception(caught.value, capture_locals=True)
    frame = next(item for item in trace.stack if item.name == '_prepare_consistent_delivery')
    shown = ' '.join((frame.locals or {}).values())
    assert policy_marker.decode() not in shown and key_marker.decode() not in shown
    assert owner._state == ('READY' if fault == 'refused' else 'HOLD')


def test_generation_refresh_refuses_policy_bytes_with_valid_hmac_but_wrong_independent_pin(monkeypatch, tmp_path):
    import hmac
    from tools import wd_mind_full_runtime as runtime
    real_basis = runtime._verify_refresh_basis
    _, owner, old, _ = _refresh_generation_test_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime, '_verify_refresh_basis', real_basis)
    approved = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    other = b'{"identifiers": [],"schema":"wd.mind-identifier-policy.v2"}'
    key = b's' * 32
    owner._binding._PrivateBinding__policy_pin = hashlib.sha256(approved).hexdigest()
    owner._snapshot['identifier_policy_binding'] = (other, key, hmac.new(key, other, hashlib.sha256).hexdigest())
    with pytest.raises(runtime.FullRuntimeRefused): owner._refresh_generation(enabled=True)
    assert _test_generation_view(owner) == old and owner._state == 'HOLD'


@pytest.mark.parametrize('fault', ['source_changed', 'policy_pin_changed', 'worker_changed',
                                   'adapter_changed', 'lease_changed', 'failed_handler',
                                   'failure', 'cancel', 'group', 'hold'])
def test_generation_refresh_never_publishes_partial_candidate(monkeypatch, tmp_path, fault):
    runtime, owner, old, new = _refresh_generation_test_owner(monkeypatch, tmp_path)
    def stage(current_owner):
        assert (current_owner._binding, current_owner._snapshot, current_owner._handler._memory) == old
        if fault == 'cancel': raise KeyboardInterrupt('synthetic')
        if fault == 'group': raise BaseExceptionGroup('synthetic', [ValueError('discard'), GeneratorExit('discard')])
        if fault == 'hold': raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
        if fault == 'source_changed':
            # The candidate itself is inconsistent; verification must reject it.
            return _test_staged_refresh(runtime, current_owner, new, snapshot=old[1])
        if fault == 'policy_pin_changed':
            new[0]._PrivateBinding__policy_pin = 'f' * 64
            return _test_staged_refresh(runtime, current_owner, new)
        if fault in ('worker_changed', 'adapter_changed', 'lease_changed', 'failed_handler'):
            staged = _test_staged_refresh(runtime, current_owner, new)
            handler = staged['handler']
            if fault == 'worker_changed': handler._rotator = object()
            elif fault == 'adapter_changed': handler._adapter = object()
            elif fault == 'lease_changed': handler._watcher_lease = None
            else: handler._failed = True
            return staged
        raise ValueError('synthetic staging failure')
    monkeypatch.setattr(runtime, '_stage_refresh_generation', stage, raising=False)
    if fault == 'cancel': expected = KeyboardInterrupt
    elif fault == 'group': expected = BaseExceptionGroup
    else: expected = runtime.FullRuntimeRefused
    with pytest.raises(expected): owner._refresh_generation(enabled=True)
    assert _test_generation_view(owner) == old
    assert owner._state == 'HOLD'


def test_generation_delivery_rejects_export_from_previous_generation(monkeypatch, tmp_path):
    runtime, owner, old, new = _refresh_generation_test_owner(monkeypatch, tmp_path)
    old_export = runtime._PrivateConsumerExport(b'synthetic', (), 0, owner, old[2])
    monkeypatch.setattr(runtime, '_stage_refresh_generation',
                        lambda current_owner: _test_staged_refresh(runtime, current_owner, new), raising=False)
    owner._refresh_generation(enabled=True)
    # This is an in-process generation check, not a parent-channel or wire test.
    with pytest.raises(runtime.FullRuntimeRefused):
        owner._check_generation_export(enabled=True, export=old_export)
    assert _test_generation_view(owner) == new


def test_generation_delivery_never_mixes_candidates_and_export_during_refresh(monkeypatch, tmp_path):
    import threading
    runtime, owner, old, new = _refresh_generation_test_owner(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def stage(current_owner):
        entered.set()
        if not release.wait(10): raise ValueError('synthetic timeout')
        return _test_staged_refresh(runtime, current_owner, new)
    def prepare(current_owner, request, nonce, policy_pin, max_total_bytes):
        return {'candidate_generation':current_owner._snapshot['projection_sha256'],
                'export_generation':current_owner._handler._memory.snapshot['projection_sha256']}
    monkeypatch.setattr(runtime, '_stage_refresh_generation', stage, raising=False)
    monkeypatch.setattr(runtime, '_prepare_generation_delivery', prepare, raising=False)
    def refresh():
        try: owner._refresh_generation(enabled=True)
        except BaseException as exc: errors.append(type(exc).__name__)
    thread = threading.Thread(target=refresh)
    thread.start()
    try:
        assert entered.wait(2), f'refresh did not stage: {errors}'
        try:
            during = owner._prepare_consistent_delivery(enabled=True, max_total_bytes=64 * 1024 * 1024)
            assert during['candidate_generation'] == during['export_generation'] == old[1]['projection_sha256']
        except runtime.FullRuntimeRefused:
            pass  # BUSY/refusal is also safe; no mixed output is returned.
    finally:
        release.set(); thread.join(10)
    assert not thread.is_alive() and errors == []
    after = owner._prepare_consistent_delivery(enabled=True, max_total_bytes=64 * 1024 * 1024)
    assert after['candidate_generation'] == after['export_generation'] == new[1]['projection_sha256']


@pytest.mark.parametrize('mode', ['timeout', 'cancel', 'group', 'unknown',
                                  'group_unknown', 'group_oversized', 'group_no_cancel'])
def test_single_owner_retains_its_live_source_verifier_on_close_and_retry(tmp_path, monkeypatch, mode):
    import threading
    runtime, adapter_module, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    release = threading.Event()
    thread = threading.Thread(target=release.wait)
    class SyntheticWatcher:
        def alive(self): return thread.is_alive()
    watcher = SyntheticWatcher()
    class Adapter:
        encoder_binding_bytes, encoder_binding_sha256 = b'synthetic', 'a' * 64
        def passage(self, texts): return b'synthetic'
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **kwargs: Adapter())
    def build(**kwargs):
        kwargs['_watcher_lease']._capture(watcher)
        if mode == 'cancel': raise KeyboardInterrupt()
        if mode == 'group':
            raise BaseExceptionGroup('synthetic-secret-marker',
                                     [ValueError('synthetic-secret-marker'), GeneratorExit('discard')])
        if mode == 'group_unknown':
            raise BaseExceptionGroup('synthetic-secret-marker',
                                     [ValueError('discard'), runtime.FullRuntimeCancellationUnknown()])
        if mode == 'group_oversized':
            raise BaseExceptionGroup('synthetic-secret-marker',
                                     [ValueError('discard') for _ in range(1024)] + [GeneratorExit('discard')])
        if mode == 'group_no_cancel': raise ExceptionGroup('synthetic-secret-marker', [ValueError('discard')])
        if mode == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
        raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
    monkeypatch.setattr(runtime, 'build_flat_memory', build)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    thread.start()
    try:
        if mode == 'cancel':
            with pytest.raises(KeyboardInterrupt): owner.start()
        elif mode in ('group', 'group_unknown'):
            import traceback
            with pytest.raises(BaseExceptionGroup) as caught: owner.start()
            expected = GeneratorExit if mode == 'group' else runtime.FullRuntimeCancellationUnknown
            assert len(caught.value.exceptions) == 1 and type(caught.value.exceptions[0]) is expected
            assert 'synthetic-secret-marker' not in ''.join(traceback.format_exception(caught.value))
        elif mode in ('unknown', 'group_oversized'):
            with pytest.raises(runtime.FullRuntimeCancellationUnknown) as caught: owner.start()
            assert caught.value.args == ('mind_full_runtime_cancellation_unknown',)
        else:
            assert owner.start()['state'] == 'HOLD'
        assert owner.close()['state'] == 'HOLD'
        assert owner.close()['state'] == 'HOLD'
        release.set(); thread.join(5)
        assert not thread.is_alive()
        # A close request seals this owner's lease even while it waits for the
        # first verifier; no later builder may attach a new verifier to it.
        with pytest.raises(ValueError): owner._watcher_lease._capture(SyntheticWatcher())
        assert owner.close()['state'] == 'STOPPED'
    finally:
        release.set(); thread.join(5)


def test_single_owner_does_not_adopt_another_builders_source_verifier(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    release = threading.Event()
    thread = threading.Thread(target=release.wait)
    class SyntheticWatcher:
        def alive(self): return thread.is_alive()
    foreign = SyntheticWatcher()
    def foreign_hold(handler):
        assert foreign.alive()
        raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', foreign_hold)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    thread.start()
    try:
        assert owner.start()['state'] == 'HOLD'
        assert owner.close()['state'] == 'STOPPED'
        assert foreign.alive()
    finally:
        release.set(); thread.join(5)


@pytest.mark.parametrize('mode', ['group', 'unknown', 'oversized'])
def test_single_owner_close_preserves_grouped_verifier_cancellation(tmp_path, monkeypatch, mode):
    import traceback
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    state = {'raise':False}
    class SyntheticWatcher:
        def alive(self):
            if not state['raise']: return False
            if mode == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
            if mode == 'oversized':
                raise BaseExceptionGroup('synthetic-secret-marker',
                    [ValueError('discard') for _ in range(1024)] + [GeneratorExit('discard')])
            raise BaseExceptionGroup('synthetic-secret-marker',
                                     [ValueError('discard'), GeneratorExit('discard')])
    watcher = SyntheticWatcher()
    def build(handler):
        handler._watcher_lease._capture(watcher)
        raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'HOLD'
    state['raise'] = True
    expected = BaseExceptionGroup if mode == 'group' else runtime.FullRuntimeCancellationUnknown
    with pytest.raises(expected) as caught: owner.close()
    assert owner._state == 'HOLD'
    assert 'synthetic-secret-marker' not in ''.join(traceback.format_exception(caught.value))
    if mode == 'group':
        assert len(caught.value.exceptions) == 1 and type(caught.value.exceptions[0]) is GeneratorExit
    else:
        assert caught.value.args == ('mind_full_runtime_cancellation_unknown',)
    state['raise'] = False
    assert owner.close()['state'] == 'STOPPED'


@pytest.mark.parametrize(('verifier', 'expected_owned'), [('alive', True), ('unknown', None)])
def test_single_owner_close_reports_verifier_ownership_when_worker_exited(tmp_path, monkeypatch, verifier, expected_owned):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    class SyntheticWatcher:
        def alive(self):
            if verifier == 'unknown': raise ValueError('synthetic uncertainty')
            return True
    def build(handler):
        handler._watcher_lease._capture(SyntheticWatcher())
        raise runtime.FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'HOLD'
    monkeypatch.setattr(owner._owner, 'status', lambda: {'state':'EXITED', 'owned':False})
    assert owner.status() == {'state':'HOLD', 'owned':expected_owned, 'authority':'none'}
    assert owner.close() == {'state':'HOLD', 'owned':expected_owned, 'authority':'none'}


@pytest.mark.parametrize('mode', ['group', 'unknown'])
def test_single_owner_worker_close_cancellation_preserves_hold(tmp_path, monkeypatch, mode):
    import traceback
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    def cancelled(*, timeout):
        if mode == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
        raise BaseExceptionGroup('synthetic-secret-marker', [ValueError('discard'), GeneratorExit('discard')])
    monkeypatch.setattr(owner._client, 'close', cancelled)
    expected = BaseExceptionGroup if mode == 'group' else runtime.FullRuntimeCancellationUnknown
    with pytest.raises(expected) as caught: owner.close()
    assert owner._state == 'HOLD'
    assert 'synthetic-secret-marker' not in ''.join(traceback.format_exception(caught.value))
    if mode == 'group':
        assert len(caught.value.exceptions) == 1 and type(caught.value.exceptions[0]) is GeneratorExit
    else:
        assert caught.value.args == ('mind_full_runtime_cancellation_unknown',)


@pytest.mark.parametrize('mode', ['group', 'unknown', 'oversized', 'ordinary_group'])
def test_single_owner_worker_status_cancellation_preserves_hold(tmp_path, monkeypatch, mode):
    import traceback
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    def cancelled():
        if mode == 'unknown': raise runtime.FullRuntimeCancellationUnknown()
        if mode == 'oversized':
            raise BaseExceptionGroup('synthetic-secret-marker',
                                     [ValueError('discard') for _ in range(1024)] + [GeneratorExit('discard')])
        if mode == 'ordinary_group': raise ExceptionGroup('synthetic-secret-marker', [ValueError('discard')])
        raise BaseExceptionGroup('synthetic-secret-marker', [ValueError('discard'), GeneratorExit('discard')])
    monkeypatch.setattr(owner._owner, 'status', cancelled)
    if mode == 'ordinary_group':
        assert owner.status() == {'state':'HOLD', 'owned':None, 'authority':'none'}
        return
    expected = BaseExceptionGroup if mode == 'group' else runtime.FullRuntimeCancellationUnknown
    with pytest.raises(expected) as caught: owner.status()
    assert owner._state == 'HOLD'
    assert 'synthetic-secret-marker' not in ''.join(traceback.format_exception(caught.value))
    if mode == 'group':
        assert len(caught.value.exceptions) == 1 and type(caught.value.exceptions[0]) is GeneratorExit
    else:
        assert caught.value.args == ('mind_full_runtime_cancellation_unknown',)


def test_foreign_global_verifier_reservation_is_not_captured_by_new_owner():
    import threading
    from tools import wd_mind_content_snapshot_package as package, wd_mind_full_runtime as runtime
    release = threading.Event()
    foreign = package._reserve_watcher()
    foreign.thread = threading.Thread(target=release.wait)
    lease = runtime._OwnedSnapshotWatcher()
    foreign.thread.start()
    try:
        with pytest.raises(package.SnapshotPackageHold):
            runtime._reserve_owned_snapshot_watcher(package, lease)
        assert lease._watcher is None and not lease._held()
        assert package._WATCHER_REGISTRY['owner'] is foreign
    finally:
        release.set(); foreign.thread.join(5); package._release_watcher(foreign)


def test_single_manifest_owner_ready_and_close_are_explicit(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    expected = {'schema': 'synthetic-refs-only', 'authority': 'none', 'results': []}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', lambda **kwargs: expected)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_candidate_delivery', lambda self: None)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    request = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
               'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    assert calls == []
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    assert owner.candidates(request) == expected
    assert owner.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    with pytest.raises(runtime.FullRuntimeRefused):
        owner.candidates(request)
    assert calls == [True]


def test_single_manifest_owner_rechecks_source_before_spawn(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    def changed(*args): raise ValueError('synthetic source changed')
    monkeypatch.setattr(runtime, '_flat_snapshot', changed)
    assert owner.start() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert calls == []


@pytest.mark.parametrize('fault', ['bundle', 'manifest', 'bulk', 'location'])
def test_single_manifest_owner_rejects_factory_retarget_before_spawn(tmp_path, monkeypatch, fault):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    if fault == 'bundle': factory._bundle = str(tmp_path / 'foreign')
    elif fault == 'manifest': factory._manifest = '0' * 64
    elif fault == 'bulk': factory._bulk = '0' * 64
    else: factory._location['runtime_revision'] = 'changed'
    assert owner.start() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert calls == []


def test_single_manifest_owner_keeps_owner_if_factory_retargets_during_launch(tmp_path, monkeypatch):
    runtime, _, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    original = runtime._ManifestWorkerFactory.__call__
    def retarget(self, **kwargs):
        result = original(self, **kwargs)
        self._manifest = '0' * 64
        return result
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', retarget)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner._owner is not None and calls == [True]


@pytest.mark.parametrize('phase', ['before', 'during'])
def test_single_manifest_owner_holds_and_never_returns_retargeted_candidates(tmp_path, monkeypatch, phase):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    calls = []
    def candidates(**kwargs):
        calls.append(True)
        if phase == 'during': factory._manifest = '0' * 64
        return {'synthetic': 'must not escape'}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', candidates)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    request = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
               'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    if phase == 'before': factory._manifest = '0' * 64
    with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'):
        owner.candidates(request)
    assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert calls == ([] if phase == 'before' else [True])


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
@pytest.mark.parametrize('phase', ['before', 'after', 'provider'])
def test_candidate_factory_or_provider_failure_drops_retained_state(tmp_path, monkeypatch, located, phase):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: self._retained)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    retained = object()
    owner._retained = owner._handler._retained = retained
    request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id':'startup_work_overview', 'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    def candidate(**kwargs):
        if phase in ('after', 'provider'):
            factory._manifest = '0'*64
        if phase == 'provider':
            owner._check_factory_or_hold()
        return {'synthetic':'not returned'}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', candidate)
    if phase == 'before':
        factory._manifest = '0'*64
    with pytest.raises(runtime.FullRuntimeRefused):
        (owner.located_candidates if located else owner.candidates)(request)
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert owner._retained is None and owner._handler._retained is None


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
def test_external_policy_invalidation_drops_retained_and_fences_owner(tmp_path, monkeypatch, located):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    retained = object()
    owner._retained = owner._handler._retained = retained
    assert owner.invalidate_external_policy() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert owner._retained is None and owner._handler._retained is None
    request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id':'startup_work_overview', 'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        (owner.located_candidates if located else owner.candidates)(request)
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
def test_malformed_candidate_request_preserves_retained_state(tmp_path, monkeypatch, located):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    retained = object()
    owner._retained = owner._handler._retained = retained
    request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id':'startup_work_overview', 'nonce':'bad', 'lane':'codex-lead-1', 'scope':'own'}
    with pytest.raises(runtime.FullRuntimeRefused):
        (owner.located_candidates if located else owner.candidates)(request)
    assert owner.status() == {'state':'READY', 'owned':True, 'authority':'none'}
    assert owner._retained is retained and owner._handler._retained is retained


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
@pytest.mark.parametrize('changed_value', ['shared', None], ids=['valid-string', 'wrong-type'])
def test_changed_caller_request_refuses_without_dropping_retained_state(tmp_path, monkeypatch, located, changed_value):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: self._retained)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_candidate_delivery', lambda self: None)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    retained = object()
    owner._retained = owner._handler._retained = retained
    request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id':'startup_work_overview', 'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    ready, changed = threading.Event(), threading.Event()
    def mutate():
        if ready.wait(5):
            request['scope'] = changed_value
            changed.set()
    worker = threading.Thread(target=mutate)
    worker.start()
    first = [True]
    def candidate(**kwargs):
        if first[0]:
            first[0] = False
            ready.set()
            assert changed.wait(5)
        return {'synthetic':'ok'}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', candidate)
    try:
        with pytest.raises(runtime.FullRuntimeRefused):
            (owner.located_candidates if located else owner.candidates)(request)
    finally:
        ready.set()
        worker.join(5)
    assert not worker.is_alive()
    assert owner.status() == {'state':'READY', 'owned':True, 'authority':'none'}
    assert owner._retained is retained and owner._handler._retained is retained
    request['scope'] = 'own'
    assert (owner.located_candidates if located else owner.candidates)(request) == {'synthetic':'ok'}


@pytest.mark.parametrize('located', [False, True], ids=['v1', 'v2'])
def test_changed_request_cannot_hide_simultaneous_factory_failure(tmp_path, monkeypatch, located):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'})
    monkeypatch.setattr(runtime._SingleManifestOwner, '_ensure_retained_locked', lambda self: self._retained)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda self: None)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    retained = object()
    owner._retained = owner._handler._retained = retained
    request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
               'query_id':'startup_work_overview', 'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'}
    def candidate(**kwargs):
        request['scope'] = 'shared'
        factory._manifest = '0'*64
        return {'synthetic':'must not escape'}
    monkeypatch.setattr(runtime, 'map_pipe_candidates', candidate)
    with pytest.raises(runtime.FullRuntimeRefused):
        (owner.located_candidates if located else owner.candidates)(request)
    assert owner.status() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert owner._retained is None and owner._handler._retained is None


def test_single_manifest_owner_never_reports_ready_when_owner_status_raises(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state': 'READY', 'owned': True, 'authority': 'none'}
    held = owner._owner
    def failed_status(): raise ValueError('synthetic private owner detail')
    monkeypatch.setattr(held, 'status', failed_status)
    assert owner.status() == {'state': 'HOLD', 'owned': None, 'authority': 'none'}
    assert owner._owner is held


@pytest.mark.parametrize('signal_name', ['keyboard', 'cancelled'])
def test_single_manifest_owner_retains_worker_when_adapter_is_cancelled(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, adapter_module, binding, factory, calls = _single_owner_synthetic(monkeypatch, tmp_path)
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    def interrupt(**kwargs): raise signal()
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', interrupt, raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    with pytest.raises(signal):
        owner.start()
    assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    # A signalled launch thread can still be completing its finally block.
    # STOPPED is valid only after that owned thread is proven to have exited.
    owner._launch_thread.join(timeout=5)
    assert not owner._launch_thread.is_alive()
    assert owner.close() == {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
    assert calls == [True]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def synthetic_bundle(tmp_path):
    bundle = tmp_path / 'bundle'
    bootstrap = bundle / 'tools-bootstrap'
    shared = tmp_path / 'runtime' / 'shared'
    shared.mkdir(parents=True)
    lane_names = ('claude-rco-1', 'claude-rco-2', 'codex-lead-1', 'fable-5')
    ids = {name: f'00000000-0000-4000-8000-{index:012d}'
           for index, name in enumerate((*lane_names, 'codex-tools-1'), 1)}
    fleet = {'schema_version': 2, 'runtime_root': str(shared.parent),
             'lanes': [{'agent': n, 'agent_uuid': ids[n]} for n in lane_names],
             'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': ids['codex-tools-1']}}
    policy = {'schema': 'wd.mind-content-policy.v3', 'lanes': ['codex-lead-1', 'codex-tools-1', 'fable-5'],
              'types': ['message', 'done', 'decision'], 'task_scope': 'all_tasks', 'removed_source_refs': [],
              'max_message_chars': 4096, 'max_chunk_chars': 300}
    files = {}
    def put(relative, data):
        path = bundle.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[relative] = hashlib.sha256(data).hexdigest()
    put('wd-fleet.json', canonical(fleet))
    put('wd-mind-message-policy.json', canonical(policy))
    code_paths = set(reader._REQUIRED_CODE) | {'tools/wd_mind_content_query_readonly.py',
        'tools/wd_mind_startup_content.py', 'tools/wd_mind_full_runtime.py',
        'tools/wd_mind_content_snapshot_package.py', 'tools/wd_mind_e5_client.py',
        'tools/wd_mind_e5_lifecycle.py',
        'tools/wd_mind_e5_snapshot_adapter.py', 'tools/wd_mind_e5_worker.py',
        'tools/wd_mind_runtime_manifest.py', 'tools/wd_mind_interpreter_identity.py',
        'tools/wd_mind_runtime_child.py', 'tools/wd_mind_runtime_process.py',
        'tools/wd_mind_runtime_bootstrap.py', 'tools/wd_mind_runtime_mutex.py'}
    for relative in sorted(code_paths):
        data = (ROOT / relative).read_bytes()
        if relative in reader._REQUIRED_CODE and hashlib.sha256(data).hexdigest() != reader._REQUIRED_CODE[relative]:
            # Windows checkout may translate the committed, hash-pinned LF bytes.
            candidate = data.replace(b'\r\n', b'\n')
            if hashlib.sha256(candidate).hexdigest() == reader._REQUIRED_CODE[relative]:
                data = candidate
        put('tools-bootstrap/' + relative, data)
    model_root, interpreter_root = tmp_path / 'model-root', tmp_path / 'interpreter-root'
    def asset(root, relative, data):
        path = root.joinpath(*relative.split('/')); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return {'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    weight = asset(model_root, 'model/model.safetensors', b'synthetic-model-weights')
    runtime_manifest = canonical({'schema':'wd.q4-runtime-manifest.v1', 'generation':'a'*40,
                                  'revision':'b'*40, 'files':[weight]})
    interpreter_files = [dict(asset(interpreter_root, relative, data), role=role) for relative, data, role in (
        ('python.exe', b'synthetic-python', 'interpreter'), ('python.dll', b'synthetic-dll', 'dll'),
        ('lib/os.py', b'synthetic-stdlib', 'stdlib'))]
    interpreter_identity = canonical({'schema':'wd.q4-interpreter-identity.v1', 'files':interpreter_files})
    put('wd-mind-runtime-manifest.json', runtime_manifest)
    put('wd-mind-interpreter-identity.json', interpreter_identity)
    e5_policy = {'truncate_chars': 2000, 'normalize': True, 'dtype': 'float32', 'dim': 384}
    runtime_binding = {'schema': 'wd.mind-memory-encoder-binding.v1',
        'encoder': {'model': 'intfloat/multilingual-e5-small', 'revision': 'b' * 40, 'weights_sha256': weight['sha256']},
        'code_sha256': files['tools-bootstrap/tools/wd_mind_e5_worker.py'],
        'passage_policy': dict(e5_policy, prefix='passage: '), 'query_policy': dict(e5_policy, prefix='query: ')}
    put('wd-mind-e5-encoder-binding.json', canonical(runtime_binding))
    location = {'schema':'wd.mind-runtime-location.v1', 'source_commit':'c'*40,
        'runtime_root':str(model_root), 'interpreter_root':str(interpreter_root),
        'runtime_manifest_sha256':files['wd-mind-runtime-manifest.json'],
        'interpreter_identity_sha256':files['wd-mind-interpreter-identity.json'],
        'encoder_binding_sha256':files['wd-mind-e5-encoder-binding.json']}
    put('wd-mind-runtime-location.json', canonical(location))
    manifest = canonical({'schema_version': 1, 'source_commit':'c'*40, 'files': files})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    def row(message, agent='codex-lead-1', recipient='codex-tools-1'):
        return {'agent': agent, 'agent_uuid': ids[agent], 'to': recipient,
                'type': 'message', 'status': 'in_progress', 'task_id': 'synthetic-task',
                'ts_utc': '2026-01-01T00:00:00.000000Z', 'message': message}
    events = [row('ordinary synthetic work outcome'), row('password=syntheticfixturevalue'),
              row('synthetic rco authored', agent='claude-rco-1'),
              row('synthetic rco addressed', recipient='claude-rco-2')]
    wrong_identity = row('synthetic wrong identity')
    wrong_identity['agent_uuid'] = ids['fable-5']
    events.append(wrong_identity)
    source = shared / 'events.jsonl'
    source.write_bytes(b''.join(canonical(event) + b'\n' for event in events))
    return {'bundle': bundle, 'source': source, 'digest': hashlib.sha256(manifest).hexdigest(),
            'model_root':model_root, 'interpreter_root':interpreter_root}


def invoke(case, scenario='normal'):
    if scenario.startswith(('package_direct_', 'refresh_real_delivery', 'refresh_real_worker_')):
        # Synthetic operator pins: the approval descriptor is part of the
        # test's independently pinned deployment manifest, never live data.
        policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
        descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
                                'identifier_policy_sha256':hashlib.sha256(policy).hexdigest(),
                                'approval_reference_sha256':'b'*64})
        bundle = case['bundle']
        (bundle / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
        manifest_path = bundle / 'deployment-manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
        raw = canonical(manifest)
        manifest_path.write_bytes(raw)
        case = dict(case, digest=hashlib.sha256(raw).hexdigest())
    code = r'''
import hashlib, io, json, os, pathlib, struct, sys
sys.path.insert(0, sys.argv[1] + '/tools-bootstrap')
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_absent_generation_prefix_readonly as prefix
mode = sys.argv[4]
if mode.startswith(('package', 'handler')):
    path = pathlib.Path(sys.argv[3])
    row = json.loads(path.read_bytes().splitlines()[0])
    with path.open('ab') as output:
        for number in range(40 if mode.startswith('package_owner_build_') else
                            25 if mode in ('handler_fast_multibatch','handler_fast_rebind',
                                           'package_immutable_multibatch') else 4):
            row['message'] = 'synthetic additional working item ' + str(number)
            if mode.startswith('handler') and not mode.startswith('handler_historical'):
                import datetime
                minutes = -2 if mode == 'handler_future_time' else (20 if mode == 'handler_stale_time' else 2)
                row['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
            output.write(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        fleet = json.loads((pathlib.Path(sys.argv[1]) / 'wd-fleet.json').read_bytes())
        fable = next(entry for entry in fleet['lanes'] if entry['agent'] == 'fable-5')
        row.update(agent='fable-5', agent_uuid=fable['agent_uuid'], message='synthetic external note')
        output.write(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
if mode in ('rewrite', 'rotation'):
    observe = prefix.observe_prefix
    def changed(**kwargs):
        result = observe(**kwargs)
        path = pathlib.Path(sys.argv[3])
        data = path.read_bytes()
        if mode == 'rewrite': path.write_bytes(data.replace(b'ordinary', b'Ordinary'))
        else:
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(data)
            os.replace(replacement, path)
        return result
    prefix.observe_prefix = changed
if mode in ('refresh_real_delivery', 'refresh_real_delivery_error_adapter'):
    import datetime
    path = pathlib.Path(sys.argv[3]); rows = path.read_bytes().splitlines()
    event = json.loads(rows[0])
    event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()
    rows[0] = json.dumps(event, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(b'\n'.join(rows) + b'\n')
if mode == 'policy_during':
    original = reader.rederive_complete_message_projection
    def changed_projection(**kwargs):
        result = original(**kwargs)
        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
        return result
    reader.rederive_complete_message_projection = changed_projection
try:
    if mode.startswith('factory'):
        from tools import wd_mind_runtime_child as child, wd_mind_e5_client as worker_client
        synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-audit'
        (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
        child._AUDIT_ROOT = synthetic_audit  # Synthetic fixture; never change the host audit policy.
        runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
        calls = []
        def fake_start(**kwargs):
            calls.append(kwargs)
            return {'status':'HOLD','owner':'synthetic-owner','client':None}
        child.start_e5_worker_graceful = fake_start
        try:
            if mode == 'factory_missing_audit': (synthetic_audit / 'wd-mind-runtime').rmdir()
            if mode == 'factory_bad_origin': child.__file__ = str(pathlib.Path(sys.argv[1]) / 'wrong-child.py')
            factory = runtime.create_manifest_worker_factory(enabled=(mode != 'factory_disabled'),
                bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
                worker_timeout_seconds=0 if mode == 'factory_bad_timeout' else 5,
                lock_timeout_seconds=-1 if mode == 'factory_bad_lock_timeout' else 1,
                bulk_contract_sha256=('0'*64 if mode == 'factory_bad_bulk' else worker_client.BULK_CONTRACT_SHA256))
            if mode == 'factory_asset_mutation':
                location = json.loads((pathlib.Path(sys.argv[1]) / 'wd-mind-runtime-location.json').read_bytes())
                (pathlib.Path(location['runtime_root']) / 'model/model.safetensors').write_bytes(b'changed')
            if mode == 'factory_manifest_mutation':
                (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            if mode == 'factory_audit_removed': (synthetic_audit / 'wd-mind-runtime').rmdir()
            if mode == 'factory_lock_changed': runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'other.lock'
            output = factory()
            item = calls[0] if calls else {}
            args = item.get('tuple_args', {})
            print(json.dumps({'result':'ok','calls':len(calls),'status':output.get('status'),
                'same_owner':output.get('owner') == 'synthetic-owner',
                'worker_only':item.get('worker_only') is True,'scratch_none':item.get('scratch_root') is None,
                'lock_exact':args.get('lock-file') == str(synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'),
                'args_exact':set(args) == {'runtime-root','runtime-manifest','runtime-manifest-sha256',
                                           'runtime-generation','runtime-revision','lock-file','lock-timeout'}}))
        except Exception:
            print(json.dumps({'result':'refused','calls':len(calls)}))
        sys.exit(0)
    if mode == 'location':
        resolved = runtime.resolve_manifest_runtime_location(enabled=True, bundle_root=sys.argv[1],
                                                             expected_manifest_sha256=sys.argv[2])
        print(json.dumps({'result':'ok', 'generation':resolved['runtime_generation'],
                          'revision':resolved['runtime_revision'],
                          'interpreter_name':pathlib.Path(resolved['interpreter']).name,
                          'verified':resolved['runtime_status'] == resolved['interpreter_status'] == 'VERIFIED'}))
        sys.exit(0)
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    binding = runtime.assemble_full_runtime(enabled=(mode != 'disabled'), bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
        identifier_policy_bytes=policy, expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
    if mode.startswith('refresh_real_'):
        # Synthetic bundle and worker; the source, policy and manifest checks
        # use their real implementations and real temporary fixture bytes.
        if not hasattr(runtime._SingleManifestOwner, '_refresh_generation'):
            print(json.dumps({'result':'missing_refresh'})); sys.exit(0)
        from tools import wd_mind_e5_client as client_module, wd_mind_e5_snapshot_adapter as adapter_module
        root = pathlib.Path(sys.argv[1])
        runtime_binding = json.loads((root / 'wd-mind-e5-encoder-binding.json').read_bytes())
        encoder_binding = json.dumps({'schema':'wd.mind-readonly-encoder-binding.v1',
            'encoder':runtime_binding['encoder'], 'code_sha256':runtime_binding['code_sha256'],
            'query_policy':{'prefix':'query: ','truncate_chars':2000,'normalize':True,'dtype':'float32','dim':384}},
            sort_keys=True,separators=(',', ':')).encode()
        class SyntheticOwner:
            def status(self): return {'state':'RUNNING','owned':True}
            def shutdown(self, *, timeout): return {'state':'EXITED','owned':False}
        class SyntheticClient:
            def __init__(self, owner): self.owner=owner
            def close(self, *, timeout): return {'state':'EXITED','owned':False}
        class SyntheticAdapter:
            encoder_binding_bytes = encoder_binding
            encoder_binding_sha256 = hashlib.sha256(encoder_binding).hexdigest()
            def passage(self, values): return struct.pack('<384f', 1.0, *([0.0]*383)) * len(values)
            def query(self, text): return (1.0,) + (0.0,) * 383
        worker = SyntheticOwner(); fake_client = SyntheticClient(worker)
        def fake_factory(self, *, owner_sink, call_deadline=None):
            owner_sink(worker)
            return {'status':'READY','owner':worker,'client':fake_client}
        runtime._ManifestWorkerFactory.__call__ = fake_factory
        adapter_module.create_single_flat_adapter = lambda **kwargs: SyntheticAdapter()
        factory = runtime._ManifestWorkerFactory(str(root), sys.argv[2], {}, root / 'synthetic.lock', 5, 0,
                                                  client_module.BULK_CONTRACT_SHA256)
        anchor = (runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=str(root),
                    expected_manifest_sha256=sys.argv[2], operator_policy_sha256=hashlib.sha256(policy).hexdigest(),
                    operator_approval_reference_sha256='b'*64)
                  if mode.startswith(('refresh_real_delivery', 'refresh_real_worker_')) else None)
        owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
                                                     worker_factory=factory, shutdown_timeout=0, policy_anchor=anchor)
        if owner.start()['state'] != 'READY': raise ValueError('synthetic owner not ready')
        old = (owner._binding, owner._snapshot, owner._handler._memory)
        if mode.startswith('refresh_real_delivery_error_'):
            import traceback
            fault = mode.removeprefix('refresh_real_delivery_error_')
            nonce = 'd' * 64
            request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                       'nonce':nonce,'lane':'codex-lead-1','scope':'own'}
            limit = 64 * 1024 * 1024
            caller_pin = hashlib.sha256(policy).hexdigest()
            if fault == 'source':
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            elif fault == 'manifest': (root / 'deployment-manifest.json').write_bytes(b'{}')
            elif fault == 'adapter':
                def fail_query(text): raise ValueError('synthetic-secret-marker')
                owner._adapter.query = fail_query
            elif fault == 'bad_request': request['schema'] = 'wd.synthetic-invalid.v1'
            elif fault == 'size': limit = 1
            elif fault == 'wrong_policy_pin': caller_pin = '0' * 64
            elif fault == 'internal_pin': owner._binding._PrivateBinding__policy_pin = '0' * 64
            elif fault == 'internal_policy':
                _, key, tag = owner._binding._PrivateBinding__snapshot['identifier_policy_binding']
                owner._binding._PrivateBinding__snapshot['identifier_policy_binding'] = (b'{}', key, tag)
            try:
                owner._prepare_consistent_delivery(enabled=True, request=request, expected_nonce=nonce,
                    expected_identifier_policy_sha256=caller_pin, max_total_bytes=limit)
                print(json.dumps({'result':'unexpected_success'})); sys.exit(0)
            except runtime.FullRuntimeRefused as exc:
                trace = traceback.TracebackException.from_exception(exc, capture_locals=True)
                frame = next(item for item in trace.stack if item.name == '_prepare_consistent_delivery')
                shown = ' '.join((frame.locals or {}).values())
                private_policy, private_key, _ = owner._snapshot['identifier_policy_binding']
                clean = repr(private_policy) not in shown and repr(private_key) not in shown
                print(json.dumps({'result':'refused','state':owner._state,'clean':clean})); sys.exit(0)
        if mode.startswith('refresh_real_worker_'):
            import traceback
            calls = {'count':0}
            def worker_status():
                calls['count'] += 1
                if calls['count'] == 1 and mode != 'refresh_real_worker_exit_before':
                    return {'state':'RUNNING','owned':True}
                if mode == 'refresh_real_worker_cancel': raise KeyboardInterrupt('synthetic')
                if mode == 'refresh_real_worker_group':
                    raise BaseExceptionGroup('synthetic', [ValueError('discard'), GeneratorExit('discard')])
                if mode == 'refresh_real_worker_unknown': raise runtime.FullRuntimeCancellationUnknown()
                return {'state':'EXITED','owned':False}
            worker.status = worker_status
            nonce = 'd' * 64
            request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                       'nonce':nonce,'lane':'codex-lead-1','scope':'own'}
            try:
                owner._prepare_consistent_delivery(enabled=True, request=request, expected_nonce=nonce,
                    expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(), max_total_bytes=64*1024*1024)
                print(json.dumps({'result':'unexpected_success'})); sys.exit(0)
            except (runtime.FullRuntimeRefused, KeyboardInterrupt, BaseExceptionGroup,
                    runtime.FullRuntimeCancellationUnknown) as exc:
                signal = type(exc).__name__
                trace = traceback.TracebackException.from_exception(exc, capture_locals=True)
                frame = next(item for item in trace.stack if item.name == '_prepare_consistent_delivery')
                shown = ' '.join((frame.locals or {}).values())
                private_policy, private_key, _ = owner._snapshot['identifier_policy_binding']
                clean = repr(private_policy) not in shown and repr(private_key) not in shown
                print(json.dumps({'result':'refused','signal':signal,'hold':owner._state == 'HOLD',
                                  'clean':clean, 'calls':calls['count']})); sys.exit(0)
        if mode == 'refresh_real_delivery':
            nonce = 'd' * 64
            request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                       'nonce':nonce,'lane':'codex-lead-1','scope':'own'}
            delivery = owner._prepare_consistent_delivery(enabled=True, request=request, expected_nonce=nonce,
                expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(), max_total_bytes=64*1024*1024)
            exported = delivery['export']
            same = (delivery['generation_sha256'] == owner._snapshot['projection_sha256'] and
                    exported._PrivateConsumerExport__memory is owner._handler._memory and
                    owner._check_generation_export(enabled=True, export=exported) is True)
            refs = delivery['candidates']['status'] == 'CANDIDATES' and bool(delivery['candidates']['candidates'])
            try:
                owner._prepare_consistent_delivery(enabled=True, request=request, expected_nonce=nonce,
                    expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(), max_total_bytes=1)
                cap_refused = False
            except runtime.FullRuntimeRefused: cap_refused = True
            path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
            event['message'] = 'synthetic new build-lane work'
            with path.open('ab') as output:
                output.write(json.dumps(event, sort_keys=True, separators=(',', ':')).encode() + b'\n')
            owner._refresh_generation(enabled=True)
            try: owner._check_generation_export(enabled=True, export=exported); stale_refused = False
            except runtime.FullRuntimeRefused: stale_refused = True
            print(json.dumps({'result':'ok','same':same,'refs':refs,'cap_refused':cap_refused,
                              'stale_refused':stale_refused,'ready':owner._state == 'READY'})); sys.exit(0)
        if mode == 'refresh_real_appended':
            path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
            event['message'] = 'synthetic new build-lane work'
            with path.open('ab') as output:
                output.write(json.dumps(event, sort_keys=True, separators=(',', ':')).encode() + b'\n')
            owner._refresh_generation(enabled=True)
            projection = json.loads(owner._snapshot['projection_bytes'])
            records = projection['messages']['records']
            found = [item for item in records if reader.decode_text(item['stored_text']) == event['message']]
            print(json.dumps({'result':'ok','new_binding':owner._binding is not old[0],
                'new_memory':owner._handler._memory is not old[2], 'hold':owner._state != 'READY',
                'source_ref':bool(found and found[0]['provenance']['source_event_ref'])})); sys.exit(0)
        if mode == 'refresh_real_policy_anchor_changed':
            owner._binding._PrivateBinding__policy_pin = '0' * 64
        if mode in ('refresh_real_stage_source_changed', 'refresh_real_stage_policy_changed'):
            original_stage = runtime._stage_refresh_generation
            def mutate_after_stage(current_owner):
                staged = original_stage(current_owner)
                if mode == 'refresh_real_stage_source_changed':
                    path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                else:
                    (root / 'wd-mind-message-policy.json').write_bytes(b'{}')
                return staged
            runtime._stage_refresh_generation = mutate_after_stage
        if mode == 'refresh_real_source_changed':
            path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
        elif mode == 'refresh_real_policy_changed':
            (root / 'wd-mind-message-policy.json').write_bytes(b'{}')
        try:
            owner._refresh_generation(enabled=True)
        except runtime.FullRuntimeRefused:
            print(json.dumps({'result':'refused', 'unchanged':(owner._binding,owner._snapshot,owner._handler._memory)==old,
                              'hold':owner._state == 'HOLD'})); sys.exit(0)
        print(json.dumps({'result':'unexpected_success'})); sys.exit(0)
    if mode.startswith(('package', 'handler')):
        def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
        def sha(raw): return hashlib.sha256(raw).hexdigest()
        runtime_binding_raw = (pathlib.Path(sys.argv[1]) / 'wd-mind-e5-encoder-binding.json').read_bytes()
        runtime_binding = json.loads(runtime_binding_raw)
        identity = runtime_binding['encoder']
        encoder_binding = canonical({'schema':'wd.mind-readonly-encoder-binding.v1','encoder':identity,
            'code_sha256':(runtime_binding['code_sha256'] if mode.startswith('handler') or mode.startswith('package_direct_') else '2'*64), 'query_policy':{'prefix':'query: ', 'truncate_chars':2000,
            'normalize':True, 'dtype':'float32', 'dim':384}})
        row = struct.pack('<384f', 1.0, *([0.0]*383))
        def mutate(stage):
            if mode == 'package_source_' + stage:
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'package_stale_history_source_mutation' and stage == 'query':
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'package_immutable_source_' + stage:
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'package_immutable_generation_' + stage:
                pathlib.Path(sys.argv[3]).with_name('events.generation.json').write_bytes(b'{}')
            if mode == 'package_immutable_provider_' + stage:
                raise RuntimeError('synthetic provider failure')
            if mode == 'package_immutable_helper_' + stage:
                (pathlib.Path(sys.argv[1]) / 'tools-bootstrap/tools/wd_mind_content_snapshot_package.py').write_bytes(b'changed')
            if mode == 'package_immutable_bundle_' + stage:
                (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            if mode == 'package_bundle_' + stage:
                (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
            if mode == 'package_provider_' + stage: raise RuntimeError('synthetic provider failure')
            if mode == 'package_memory_repin_' + stage:
                opposite = struct.pack('<384f', -1.0, *([0.0]*383))
                product.vectors = opposite * len(product.ids)
                changed = json.loads(product.manifest)
                changed.update(runtime._flat_hashes(product.ids, product.vectors))
                product.manifest = canonical(changed)
        def passage(texts):
            mutate('passage')
            if mode == 'package_general':
                other = struct.pack('<384f', 0.0, 1.0, *([0.0]*382))
                return b''.join(row if 'synthetic external note' in text else other for text in texts)
            return row * len(texts)
        if mode.startswith('package_owner_build_'):
            import threading
            from types import SimpleNamespace
            from tools import wd_mind_content_snapshot_package as package
            default_join = package._WATCH_JOIN_TIMEOUT_SECONDS
            checks, batch_checks = {'main':0}, []
            original_verify = reader._verified_complete
            entered, release = threading.Event(), threading.Event()
            def counted(*args, **kwargs):
                if threading.current_thread().name == 'mind-source-verifier' and mode == 'package_owner_build_hold':
                    entered.set()
                    if not release.wait(10): raise RuntimeError('test watcher deadline')
                if threading.current_thread() is threading.main_thread(): checks['main'] += 1
                return original_verify(*args, **kwargs)
            reader._verified_complete = counted
            if mode == 'package_owner_build_hold':
                package._WATCH_INTERVAL_SECONDS, package._WATCH_JOIN_TIMEOUT_SECONDS = .001, .01
            class Adapter:
                encoder_binding_bytes = encoder_binding
                encoder_binding_sha256 = sha(encoder_binding)
                def passage(self, texts):
                    batch_checks.append(checks['main'])
                    path = pathlib.Path(sys.argv[3])
                    if len(batch_checks) == 1 and mode == 'package_owner_build_mutation':
                        path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if len(batch_checks) == 1 and mode == 'package_owner_build_control':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                    if len(batch_checks) == 1 and mode == 'package_owner_build_append':
                        with path.open('ab') as out: out.write(b'{"synthetic":"append"}\n')
                    if mode == 'package_owner_build_hold' and not entered.wait(5):
                        raise RuntimeError('test watcher did not enter')
                    return row * len(texts)
            worker = SimpleNamespace(status=lambda: {'state':'RUNNING','owned':True})
            lease = runtime._OwnedSnapshotWatcher()
            handler = runtime._FlatRequestHandler(binding, binding.snapshot_for_query(), 'codex-lead-1',
                {'codex-lead-1'}, SimpleNamespace(owner=worker), Adapter(), None, 1, None, lease)
            try:
                handler.build()
                baseline = runtime.build_flat_memory(enabled=True, binding=binding,
                    passage_encoder=lambda texts: row * len(texts), encoder_binding_bytes=encoder_binding,
                    encoder_binding_sha256=sha(encoder_binding))
                result = {'result':'ok', 'records':len(handler._memory.ids), 'batch_checks':batch_checks,
                          'same_vectors':handler._memory.ids == baseline.ids and handler._memory.vectors == baseline.vectors,
                          'held':lease._held(), 'default_join':default_join}
            except Exception:
                result = {'result':'refused', 'no_partial':handler._memory is None,
                          'held':lease._held(), 'default_join':default_join}
            finally:
                release.set()
                watcher = lease._current()
                if watcher is not None and watcher.thread is not None:
                    watcher.thread.join(10)
            result['released'] = lease._held() is False
            print(json.dumps(result)); sys.exit(0)
        if mode.startswith('handler'):
            import threading
            from tools import wd_mind_e5_client, wd_mind_e5_rotating_client
            bulk = wd_mind_e5_rotating_client.BULK_CONTRACT_SHA256
            busy_entered, busy_release = threading.Event(), threading.Event()
            class FakeOwner:
                def __init__(self, number): self.owned, self.state, self._bulk_contract_sha256, self.number = True, 'RUNNING', bulk, number
                def status(self): return {'state':self.state, 'owned':self.owned, 'exit_code':None}
                def shutdown(self, timeout=0):
                    if (mode in ('handler_hold', 'handler_build_hold', 'handler_fast_hold') or
                            (mode in ('handler_refresh_hold', 'handler_auto_hold') and self.number == 1) or
                            (mode == 'handler_refresh_failure_hold' and self.number == 2)): self.state = 'HOLD'
                    else: self.state, self.owned = 'EXITED', False
                    return {'state':self.state, 'owned':self.owned, 'exit_code':0 if not self.owned else None}
            starts = []
            audit = {'provider_calls':0,'changed':False,'original_rows':None,'foreign_calls':0}
            def factory():
                owner = FakeOwner(len(starts) + 1)
                starts.append(owner)
                client = object.__new__(wd_mind_e5_client.GracefulE5WorkerClient)
                client.owner, client._binding_sha256, client._closed, client._next_id = owner, sha(runtime_binding_raw), False, 1
                client._used_nonces = set()
                client._bulk_contract_sha256 = bulk
                def supervised(transport):
                    if mode in ('handler_fast_mid_hash','handler_fast_rebind'):
                        audit['provider_calls'] += 1
                    if mode == 'handler_fast_rebind':
                        fence = handler._rotator._build_source.__self__
                        if audit['original_rows'] is None:
                            audit['original_rows'] = fence.rows
                            fence.rows = fence.rows[:16] + tuple((key,'passage: foreign synthetic material')
                                                                 for key,text in fence.rows[16:])
                            fence.rows_sha = reader._sha(reader._canonical(fence.rows))
                        if any(text == 'passage: foreign synthetic material' for text in transport['passages']):
                            audit['foreign_calls'] += 1
                            if fence.position == len(fence.chunks)-1:
                                fence.rows = audit['original_rows']
                                fence.rows_sha = reader._sha(reader._canonical(fence.rows))
                                fence.batch = tuple(text for key,text in fence.rows[fence.consumed:fence.consumed+16])
                                fence.batch_sha = reader._sha(reader._canonical(fence.batch))
                                fence.chunks = tuple(fence.batch[i:i+4] for i in range(0,len(fence.batch),4))
                    if mode == 'handler_auto_busy' and owner.number == 2 and transport['operation'] == 'refresh':
                        busy_entered.set()
                        if not busy_release.wait(15): raise RuntimeError('synthetic deadline')
                    if ((mode == 'handler_build_hold' or (mode in ('handler_refresh_failure', 'handler_refresh_failure_hold') and owner.number == 2))
                            and transport['operation'] == 'refresh'):
                        raise RuntimeError('synthetic failure')
                    if mode == 'handler_refresh_mutation' and owner.number == 2 and transport['operation'] == 'refresh':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_historical_source_mutation' and transport['operation'] == 'query':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_historical_provider_error' and transport['operation'] == 'query':
                        raise ValueError('synthetic provider error')
                    if mode == 'handler_historical_bundle_mutation' and transport['operation'] == 'query':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                    if mode == 'handler_fast_source_mutation' and transport['operation'] == 'refresh':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_fast_bundle_mutation' and transport['operation'] == 'refresh':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                    if mode == 'handler_fast_snapshot_mutation' and transport['operation'] == 'refresh':
                        handler._snapshot['projection_bytes'] = b'changed'
                    if mode == 'handler_fast_request_mutation' and transport['operation'] == 'refresh':
                        transport['passages'][0] = 'passage: modified synthetic text'
                    if mode in ('handler_fast_provider_error','handler_fast_hold') and transport['operation'] == 'refresh':
                        raise ValueError('synthetic failure')
                    if mode == 'handler_source_mutation':
                        path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    if mode == 'handler_request_mutation' and transport['operation'] == 'query':
                        request['query'] = 'changed synthetic query'
                    if mode == 'handler_mapper_mutation' and transport['operation'] == 'query':
                        wire['scope'] = 'shared'
                    if mode == 'handler_owner_hold' and transport['operation'] == 'query': owner.state = 'HOLD'
                    if mode == 'handler_binding_mutation' and transport['operation'] == 'query':
                        (pathlib.Path(sys.argv[1]) / 'wd-mind-e5-encoder-binding.json').write_bytes(b'{}')
                    count = len(transport['passages']) if transport['operation'] == 'refresh' else 1
                    return {'nonce':transport['nonce'], 'request_sha256':transport['request_sha256'], 'vector_bytes':row * count}
                client.encode_supervised = supervised
                client.close = lambda timeout=0: owner.shutdown(timeout=timeout)
                return {'status':'READY', 'owner':None if mode == 'handler_missing_factory_owner' else owner,
                        'client':client, 'bulk_contract_sha256':bulk}
            bound_lane = 'claude-rco-1' if mode in ('handler_rco', 'handler_rco_shared', 'handler_abstain_hold') else 'codex-lead-1'
            if mode in ('handler_coordinator','handler_coordinator_fast'):
                from tools import wd_mind_runtime_process as process
                class SyntheticService:
                    def __init__(self, callback): self.callback = callback
                    def start(self): return {'state':'STARTING','owned':True}
                    def status(self): return {'state':'RECEIVING','owned':True}
                    def stop(self, *, timeout=0): return {'state':'STOPPED','owned':False}
                if mode == 'handler_coordinator_fast':
                    from tools import wd_mind_runtime_child as child
                    synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-coordinator-audit'
                    (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
                    child._AUDIT_ROOT = synthetic_audit
                    runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
                    original_factory = factory
                    pinned_factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=sys.argv[1],
                        expected_manifest_sha256=sys.argv[2], worker_timeout_seconds=5, lock_timeout_seconds=1,
                        bulk_contract_sha256=bulk)
                    runtime._ManifestWorkerFactory.__call__ = lambda self: original_factory()
                    factory = pinned_factory
                runtime.create_manifest_worker_factory = lambda **kwargs: factory
                process.create_candidate_service = lambda **kwargs: SyntheticService(kwargs['handler'])
                coordinator = runtime.create_manifest_coordinator(enabled=True, bundle_root=sys.argv[1],
                    expected_manifest_sha256=sys.argv[2], lane='codex-lead-1', worker_timeout_seconds=5,
                    lock_timeout_seconds=1, shutdown_timeout=1, refresh_interval_seconds=3600,
                    bulk_contract_sha256=bulk, fast_build=(mode == 'handler_coordinator_fast'))
                coordinator.start()
                wire = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                        'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                candidates = coordinator._service.callback(wire)
                ok = bool(candidates) and all(set(item) == {'ref_sha256','doc_id_sha256','source_event_sha256'}
                                              for item in candidates)
                status = coordinator.status()['state']
                closed = coordinator.stop(timeout=1)['state']
                print(json.dumps({'result':'ok','status':status,'safe':ok,'closed':closed,'starts':len(starts)}))
                sys.exit(0)
            if (mode.startswith('handler_historical') and mode != 'handler_historical_untrusted') or (mode.startswith('handler_fast') and mode != 'handler_fast_untrusted'):
                from tools import wd_mind_runtime_child as child
                synthetic_audit = pathlib.Path(sys.argv[1]).parent / 'synthetic-history-audit'
                (synthetic_audit / 'wd-mind-runtime').mkdir(parents=True)
                child._AUDIT_ROOT = synthetic_audit
                runtime._fixed_worker_audit_lock = lambda: synthetic_audit / 'wd-mind-runtime' / 'q4-reader.lock'
                original_factory = factory
                pinned_factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=sys.argv[1],
                    expected_manifest_sha256=sys.argv[2], worker_timeout_seconds=5, lock_timeout_seconds=1,
                    bulk_contract_sha256=bulk)
                runtime._ManifestWorkerFactory.__call__ = lambda self: original_factory()
                factory = pinned_factory
            handler = runtime.create_flat_request_handler(enabled=True, binding=binding, lane=bound_lane,
                worker_factory=factory, shutdown_timeout=1, bulk_contract_sha256=bulk)
            if mode.startswith('handler_fast'):
                if mode == 'handler_fast_mid_hash':
                    import inspect
                    actual_open = runtime.bridge._open_log
                    class RaceRead:
                        def __init__(self, stream): self.stream=stream
                        def __getattr__(self, name): return getattr(self.stream,name)
                        def __enter__(self): return self
                        def __exit__(self,*args): return self.stream.__exit__(*args)
                        def read(self,size=-1):
                            data=self.stream.read(size)
                            if size > 1 and not audit['changed']:
                                frame=inspect.currentframe()
                                while frame and frame.f_code.co_name != '_check_source': frame=frame.f_back
                                if frame:
                                    audit['changed']=True
                                    path=pathlib.Path(sys.argv[3]); content=path.read_bytes()
                                    with path.open('r+b') as writer:
                                        writer.seek(content.index(b'ordinary'));writer.write(b'O')
                            return data
                    def race_open(path):
                        stream=actual_open(path)
                        return RaceRead(stream) if pathlib.Path(path)==pathlib.Path(sys.argv[3]) else stream
                    runtime.bridge._open_log=race_open
                if mode == 'handler_fast_postvalidation':
                    def refused_source(self): raise ValueError('synthetic final validation failure')
                    runtime._FlatRequestHandler._source_check = refused_source
                try:
                    result = runtime.build_fast_fenced_owner(enabled=(mode != 'handler_fast_disabled'),handler=handler)
                    inspection = handler.owner_status()
                    closed = handler.close()
                    print(json.dumps({'result':'ok','build':result['status'],'owner':inspection['state'],
                                      'closed':closed['state'],'starts':len(starts)}))
                except Exception:
                    state = handler.owner_status()
                    no_partial = handler._memory is None
                    failed = handler._failed
                    closed = handler.close()
                    if mode in ('handler_fast_hold','handler_fast_postvalidation'):
                        print(json.dumps({'result':'ok','owner':state['state'],'owned':state['owned'],
                                          'close':closed['state'],'no_partial':no_partial,'failed':failed}))
                    elif mode in ('handler_fast_mid_hash','handler_fast_rebind'):
                        print(json.dumps({'result':'refused','starts':len(starts),'provider_calls':audit['provider_calls'],
                                          'changed':audit['changed'],'foreign_calls':audit['foreign_calls'],
                                          'no_partial':no_partial}))
                    else:
                        print(json.dumps({'result':'refused'}))
                sys.exit(0)
            if mode == 'handler_build_hold':
                try: handler.build()
                except Exception:
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'state':close['state'], 'owned':close['owned'],
                                      'inspection':handler.owner_status()}))
                    sys.exit(0)
                raise ValueError('synthetic build should refuse')
            if mode == 'handler_missing_factory_owner':
                try: handler.build()
                except Exception:
                    inspection = handler.owner_status()
                    retained_owner = handler._rotator._client.owner
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'inspection':inspection,
                                      'retained_owned':retained_owner.owned, 'close':close}))
                    retained_owner.shutdown(); sys.exit(0)
                raise ValueError('malformed factory result accepted')
            handler.build()
            if mode.startswith('handler_historical'):
                try:
                    result = runtime.query_historical_flat_memory(enabled=(mode != 'handler_historical_disabled'),
                        handler=handler,lane='codex-lead-1',own_lane_only=True,query='synteettinen muistihaku')
                    closed = handler.close()
                    print(json.dumps({'result':'ok','status':result['status'],'scope':result['scope'],
                        'old':any(hit['age_seconds'] > 899 for hit in result['results']),
                        'safe':all('stored_text' not in hit and 'display_text' in hit and
                                   'synthetic rco' not in hit['display_text'] and 'password=' not in hit['display_text']
                                   for hit in result['results']), 'closed':closed['state']}))
                except Exception:
                    handler.close()
                    print(json.dumps({'result':'refused'}))
                sys.exit(0)
            if mode.startswith('handler_mapper'):
                target = 'claude-rco-1' if mode in ('handler_mapper_rco_own','handler_mapper_rco_shared') else 'codex-lead-1'
                wire = {'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
                        'nonce':'d'*64, 'lane':target, 'scope':'shared' if mode == 'handler_mapper_rco_shared' else 'own'}
                if mode == 'handler_mapper_bad_schema': wire['schema'] = 'wd.bad.v1'
                if mode == 'handler_mapper_bad_nonce': wire['nonce'] = 'D'*64
                if mode == 'handler_mapper_bad_query': wire['query_id'] = 'free_query'
                if mode == 'handler_mapper_bad_lane': wire['lane'] = 'unknown'
                if mode == 'handler_mapper_bad_scope': wire['scope'] = 'bad'
                if mode == 'handler_mapper_extra': wire['text'] = 'not allowed'
                if mode == 'handler_mapper_hold': starts[-1].state = 'HOLD'
                expected = None
                if mode in ('handler_mapper', 'handler_mapper_rco_own', 'handler_mapper_rco_shared'):
                    direct = handler.lane_view(target).handle({'schema':'wd.mind-flat-owner-request.v1',
                        'lane':target,'scope':wire['scope'],'query':'current task active work next step recent status'})
                    expected = [{'ref_sha256':hit['ref_sha256'],'doc_id_sha256':hit['doc_id_sha256'],
                                 'source_event_sha256':hit['source_reference']['sha256']}
                                for hit in direct['result']['results']]
                mapped = runtime.map_pipe_candidates(enabled=(mode != 'handler_mapper_disabled'), handler=handler, request=wire)
                safe = (set(mapped) == {'schema','nonce','lane','scope','status','candidates'} and
                        all(set(item) == {'ref_sha256','doc_id_sha256','source_event_sha256'} for item in mapped['candidates']))
                closed = handler.close()
                print(json.dumps({'result':'ok','status':mapped['status'],'safe':safe,
                                  'matches':mapped['candidates'] == expected,'nonce':mapped['nonce'] == 'd'*64,
                                  'lane':mapped['lane'],'scope':mapped['scope'],'closed':closed['state']})); sys.exit(0)
            if mode.startswith('handler_auto'):
                import time, datetime
                if mode == 'handler_auto_disabled':
                    try: runtime.create_flat_auto_refresh(handler=handler, interval_seconds=0.05)
                    except Exception:
                        close = handler.close()
                        print(json.dumps({'result':'ok','closed':close['state'],'starts':len(starts)})); sys.exit(0)
                    raise ValueError('default must be disabled')
                if mode == 'handler_auto_duplicate':
                    control = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                    try: runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05); duplicate = False
                    except Exception: duplicate = True
                    busy = handler.close()['state']
                    first = control.stop(timeout=0)
                    other = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                    second = other.stop(timeout=0)
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'duplicate_rejected':duplicate, 'close_while_reserved':busy,
                                      'first':first['state'], 'second':second['state'], 'closed':close['state']})); sys.exit(0)
                path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
                event['message'] = 'refreshed synthetic clue marker'
                event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00','Z')
                with path.open('ab') as output: output.write(canonical(event)+b'\n')
                control = runtime.create_flat_auto_refresh(enabled=True, handler=handler, interval_seconds=0.05)
                control.start()
                if mode == 'handler_auto_busy':
                    if not busy_entered.wait(15): raise ValueError('test did not enter provider')
                    first = control.stop(timeout=0)
                    busy_release.set()
                    second = control.stop(timeout=15)
                    close = handler.close()
                    print(json.dumps({'result':'ok','first':first['state'],'second':second['state'],
                                      'starts':len(starts),'closed':close['state']})); sys.exit(0)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    state = control.status()
                    if state['last_result'] == 'REFRESHED' or state['state'] == 'REFUSED': break
                    time.sleep(0.05)
                state = control.stop(timeout=15)
                inspection = handler.owner_status()
                if mode == 'handler_auto_hold':
                    print(json.dumps({'result':'ok','state':state['state'],'owner':inspection,
                                      'starts':len(starts)})); sys.exit(0)
                response = handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                           'query':'refreshed clue','scope':'own'})
                found = any('refreshed synthetic clue marker' in hit['stored_text'] for hit in response['result']['results'])
                close = handler.close()
                print(json.dumps({'result':'ok','state':state['state'],'found':found,
                                  'starts':len(starts),'closed':close['state']})); sys.exit(0)
            if mode.startswith('handler_refresh'):
                before = handler._memory
                if mode == 'handler_refresh_concurrent':
                    import threading
                    seen = []
                    handler._lock.acquire()
                    def concurrent():
                        try: handler.refresh(); seen.append('accepted')
                        except Exception: seen.append('refused')
                    thread = threading.Thread(target=concurrent); thread.start(); thread.join(5)
                    handler._lock.release()
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'concurrent':seen, 'starts':len(starts), 'closed':close['state']}))
                    sys.exit(0)
                if mode != 'handler_refresh_unchanged':
                    import datetime
                    path = pathlib.Path(sys.argv[3]); event = json.loads(path.read_bytes().splitlines()[0])
                    event['message'] = 'refreshed synthetic clue marker'
                    event['ts_utc'] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
                    with path.open('ab') as output: output.write(canonical(event) + b'\n')
                try:
                    refreshed = handler.refresh()
                    if mode in ('handler_refresh_hold', 'handler_refresh_failure', 'handler_refresh_failure_hold', 'handler_refresh_mutation'):
                        raise ValueError('refresh should fail')
                    if mode == 'handler_refresh_unchanged':
                        close = handler.close()
                        print(json.dumps({'result':'ok', 'refresh':refreshed['status'],
                                          'starts':len(starts), 'closed':close['state']})); sys.exit(0)
                    response = handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                               'query':'refreshed clue','scope':'own'})
                    found = any('refreshed synthetic clue marker' in hit['stored_text']
                                for hit in response['result']['results'])
                    close = handler.close()
                    print(json.dumps({'result':'ok', 'refresh':refreshed['status'], 'found':found,
                                      'starts':len(starts), 'closed':close['state']})); sys.exit(0)
                except Exception:
                    if mode not in ('handler_refresh_hold', 'handler_refresh_failure', 'handler_refresh_failure_hold', 'handler_refresh_mutation'):
                        raise
                    try:
                        handler.handle({'schema':'wd.mind-flat-owner-request.v1','lane':'codex-lead-1',
                                        'query':'refreshed clue','scope':'own'})
                        refused = False
                    except Exception: refused = True
                    inspection = handler.owner_status()
                    print(json.dumps({'result':'ok','refused':refused,'retained':handler._memory is before,
                                      'starts':len(starts),'inspection':inspection})); sys.exit(0)
            if mode == 'handler_five':
                views = {lane:handler.lane_view(lane) for lane in
                         ('codex-lead-1','codex-tools-1','fable-5','claude-rco-1','claude-rco-2')}
                states = {}
                for target, view in views.items():
                    observed = view.handle({'schema':'wd.mind-flat-owner-request.v1', 'lane':target,
                                            'query':'yhteyskatko uudelleenyritys', 'scope':'own'})
                    states[target] = observed['result']['status']
                try:
                    views['codex-lead-1'].handle({'schema':'wd.mind-flat-owner-request.v1', 'lane':'fable-5',
                                                   'query':'yhteyskatko uudelleenyritys', 'scope':'own'})
                    forged = False
                except Exception: forged = True
                close = handler.close()
                print(json.dumps({'result':'ok', 'states':states, 'forged_rejected':forged,
                                  'closed':close['state'], 'starts':len(starts)}))
                sys.exit(0)
            if mode == 'handler_source_after_build':
                path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
            if mode == 'handler_abstain_hold': starts[-1].state = 'HOLD'
            request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':bound_lane,
                       'query':'yhteyskatko uudelleenyritys', 'scope':'shared' if mode == 'handler_rco_shared' else 'own'}
            if mode == 'handler_wrong_lane': request['lane'] = 'fable-5'
            if mode == 'handler_extra': request['extra'] = True
            if mode == 'handler_wrong_schema': request['schema'] = 'wd.synthetic-wrong.v1'
            if mode == 'handler_wrong_scope': request['scope'] = 'unknown'
            if mode == 'handler_time_injection': request['as_of_utc'] = '2026-01-01T00:02:00Z'
            if mode == 'handler_input_race':
                import inspect
                source_lines, first = inspect.getsourcelines(runtime._FlatRequestHandler._handle_for_lane)
                freeze_line = first + next(i for i, line in enumerate(source_lines) if 'frozen = dict(request)' in line)
                def trace(frame, event, arg):
                    if (event == 'line' and frame.f_code is runtime._FlatRequestHandler._handle_for_lane.__code__
                            and frame.f_lineno == freeze_line):
                        request['scope'] = 'INVALID'
                        sys.settrace(None)
                    return trace
                sys.settrace(trace)
            response = handler.handle(request)
            close = handler.close()
            closed_rejected = False
            try: handler.handle(request)
            except Exception: closed_rejected = True
            print(json.dumps({'result':'ok', 'status':response['result']['status'],
                              'expiry':isinstance(response['expires_utc'], str),
                              'lane':response['lane'], 'owned':close['owned'], 'state':close['state'],
                              'scope':response['scope'], 'closed_rejected':closed_rejected,
                              'expiry_scope':response['expiry_scope'], 'snapshot_freshness':response['snapshot_freshness'],
                              'agents':[hit['source_agent'] for hit in response['result']['results']],
                              'latest':response['result']['own_latest_task']}))
            sys.exit(0)
        options = {'source_verification':'verified_immutable_snapshot_v1'} if mode.startswith('package_immutable') else {}
        if mode == 'package_immutable_multibatch':
            import threading
            full_checks = {'main':0}
            original_verify = reader._verified_complete
            def counted(*args, **kwargs):
                if threading.current_thread() is threading.main_thread(): full_checks['main'] += 1
                return original_verify(*args, **kwargs)
            reader._verified_complete = counted
        if mode == 'package_immutable_invalid_optin': options['source_verification'] = 'unverified'
        if mode == 'package_immutable_bad_timeout': options['watcher_join_timeout_seconds'] = 601
        if mode == 'package_immutable_forged_hold':
            from tools import wd_mind_content_snapshot_package as package
            def passage(texts): raise package.SnapshotPackageHold('synthetic forged hold')
        if mode == 'package_immutable_reserve_cancel':
            import inspect
            from tools import wd_mind_content_snapshot_package as package
            lease = runtime._OwnedSnapshotWatcher()
            options['_watcher_lease'] = lease
            lines, first = inspect.getsourcelines(runtime._reserve_owned_snapshot_watcher)
            target = first + next(i for i, line in enumerate(lines) if line.strip() == 'return watcher')
            def interrupt(frame, event, arg):
                if (frame.f_code is runtime._reserve_owned_snapshot_watcher.__code__
                        and event == 'line' and frame.f_lineno == target):
                    sys.settrace(None)
                    raise KeyboardInterrupt('synthetic cancellation')
                return interrupt
            sys.settrace(interrupt)
            try:
                runtime.build_flat_memory(enabled=True, binding=binding, passage_encoder=passage,
                    encoder_binding_bytes=encoder_binding, encoder_binding_sha256=sha(encoder_binding), **options)
            except KeyboardInterrupt:
                sys.settrace(None)
                print(json.dumps({'result':'cancelled', 'retained':lease._watcher is not None,
                    'held':lease._held(), 'registry_idle':package.snapshot_build_watcher_status()['state'] == 'idle'}))
                sys.exit(0)
            finally:
                sys.settrace(None)
            raise AssertionError('synthetic interruption did not fire')
        if mode == 'package_immutable_source_before':
            path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
        if mode == 'package_immutable_generation_before':
            pathlib.Path(sys.argv[3]).with_name('events.generation.json').write_bytes(b'{}')
        if mode in ('package_immutable_background', 'package_immutable_after_join'):
            from tools import wd_mind_content_snapshot_package as package
            audit = {'detected':False, 'joined':False}
            if mode == 'package_immutable_background':
                package._WATCH_INTERVAL_SECONDS = .001
                def passage(texts):
                    path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    owner = package._WATCHER_REGISTRY['owner']
                    audit['detected'] = owner.failed.wait(10)
                    return row * len(texts)
            else:
                package._WATCH_INTERVAL_SECONDS = 60
                original_stop = package._SourceWatcher.stop
                def changed_after_join(self, timeout=None):
                    original_stop(self, timeout)
                    audit['joined'] = True
                    path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                package._SourceWatcher.stop = changed_after_join
        if mode in ('package_immutable_hold', 'package_immutable_cancel_hold',
                    'package_immutable_generator_hold', 'package_immutable_async_hold',
                    'package_immutable_group_hold', 'package_immutable_oversized_group_hold',
                    'package_immutable_owned_hold', 'package_immutable_owned_cancel_hold'):
            import asyncio, threading
            from tools import wd_mind_content_snapshot_package as package
            entered, release = threading.Event(), threading.Event()
            original = reader._verified_complete
            def blocked(*args, **kwargs):
                if threading.current_thread().name == 'mind-source-verifier':
                    entered.set(); release.wait(10)
                return original(*args, **kwargs)
            reader._verified_complete = blocked
            package._WATCH_INTERVAL_SECONDS = .001
            options['watcher_join_timeout_seconds'] = .01
            lease = None
            if mode.startswith('package_immutable_owned'):
                lease = runtime._OwnedSnapshotWatcher()
                options['_watcher_lease'] = lease
            def passage(texts):
                assert entered.wait(10)
                if mode in ('package_immutable_cancel_hold', 'package_immutable_owned_cancel_hold'):
                    raise KeyboardInterrupt('synthetic cancellation')
                if mode == 'package_immutable_generator_hold': raise GeneratorExit('synthetic cancellation')
                if mode == 'package_immutable_async_hold': raise asyncio.CancelledError('synthetic cancellation')
                if mode == 'package_immutable_group_hold':
                    raise BaseExceptionGroup('synthetic', [ValueError('discard'), GeneratorExit('discard')])
                if mode == 'package_immutable_oversized_group_hold':
                    raise BaseExceptionGroup('synthetic-secret-marker',
                        [ValueError('synthetic-secret-marker') for _ in range(1024)] + [GeneratorExit('discard')])
                return row * len(texts)
            try:
                runtime.build_flat_memory(enabled=True, binding=binding, passage_encoder=passage,
                    encoder_binding_bytes=encoder_binding, encoder_binding_sha256=sha(encoder_binding), **options)
                raise AssertionError('must not publish')
            except (runtime.FullRuntimeSnapshotHold, KeyboardInterrupt, GeneratorExit, asyncio.CancelledError,
                    BaseExceptionGroup, getattr(runtime, 'FullRuntimeCancellationUnknown', GeneratorExit)) as exc:
                signal = type(exc).__name__
                clean = (not isinstance(exc, BaseExceptionGroup) or
                         (len(exc.exceptions) == 1 and type(exc.exceptions[0]) is GeneratorExit
                          and not exc.exceptions[0].args))
                if isinstance(exc, getattr(runtime, 'FullRuntimeCancellationUnknown', GeneratorExit)):
                    import traceback
                    clean = (exc.args == ('mind_full_runtime_cancellation_unknown',) and
                             'synthetic-secret-marker' not in ''.join(traceback.format_exception(exc)))
                held = package.snapshot_build_watcher_status()['state'] != 'idle'
                captured = (lease is None or
                            (lease._watcher is package._WATCHER_REGISTRY['owner'] and lease._held()))
                try:
                    runtime.build_flat_memory(enabled=True, binding=binding, passage_encoder=passage,
                        encoder_binding_bytes=encoder_binding, encoder_binding_sha256=sha(encoder_binding), **options)
                    retry_held = False
                except runtime.FullRuntimeSnapshotHold:
                    retry_held = True
                finally:
                    release.set()
                    owner = package._WATCHER_REGISTRY['owner']
                    if owner is not None and owner.thread is not None: owner.thread.join(10)
                print(json.dumps({'result':'hold','signal':signal,'held':held,'retry_held':retry_held,
                                  'released':package.snapshot_build_watcher_status()['state'] == 'idle','clean':clean,
                                  'captured':captured}))
                sys.exit(0)
        try:
            product = runtime.build_flat_memory(enabled=True, binding=(object() if mode == 'package_fake_binding' else binding),
                passage_encoder=passage, encoder_binding_bytes=encoder_binding, encoder_binding_sha256=sha(encoder_binding), **options)
        except Exception:
            if mode in ('package_immutable_background', 'package_immutable_after_join'):
                print(json.dumps({'result':'refused', **audit})); sys.exit(0)
            raise
        if mode == 'package_immutable_multibatch':
            print(json.dumps({'result':'ok', 'records':len(product.ids), 'full_checks':full_checks['main']})); sys.exit(0)
        def encode_query(text):
            mutate('query')
            if mode == 'package_orthogonal': return (0.0, 1.0) + (0.0,) * 382
            return (1.0,) + (0.0,) * 383
        if mode == 'package_direct_narrow_probe':
            from tools import wd_mind_content_snapshot_package as package
            source_owner = package.create_consumer_flat_source_owner(enabled=True, bundle_root=sys.argv[1],
                expected_manifest_sha256=sys.argv[2], snapshot=product.snapshot,
                flat_manifest_bytes=product.manifest, flat_manifest_sha256=sha(product.manifest),
                ids=product.ids, vectors=product.vectors, encoder_binding_bytes=product.encoder_binding)
            projection = json.loads(product.snapshot['projection_bytes'].decode('utf-8'))
            record = next(item for item in projection['messages']['records'] if item['meta']['agent']=='codex-lead-1')
            proposal = {'ref_sha256':sha(record['provenance']['event_ref'].encode()),
                'doc_id_sha256':sha(record['record_id'].encode()),
                'source_event_sha256':record['provenance']['source_event_ref']}
            resolved = source_owner.resolve(proposals=[proposal], lane='codex-lead-1', scope='own')
            print(json.dumps({'result':'ok', 'resolved':len(resolved['results']),
                'ref_match':resolved['results'][0]['source_event_sha256'] == proposal['source_event_sha256']})); sys.exit(0)
        if mode in ('package_direct_owner_source_recheck', 'package_direct_narrow_append',
                    'package_direct_narrow_policy', 'package_direct_narrow_equivalence',
                    'package_direct_narrow_candidates', 'package_direct_narrow_wrong_pin',
                    'package_direct_narrow_generation', 'package_direct_narrow_foreign_pid',
                    'package_direct_narrow_candidate_mutation', 'package_direct_narrow_candidates_first',
                    'package_direct_narrow_approval_before', 'package_direct_narrow_approval_during',
                    'package_direct_narrow_approval_direct_before', 'package_direct_narrow_approval_direct_during',
                    'package_direct_candidate_race_source_v1', 'package_direct_candidate_race_source_v2',
                    'package_direct_candidate_race_caller_v1', 'package_direct_candidate_race_caller_v2'):
            import threading
            from types import SimpleNamespace
            lanes = {'codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2'}
            handler = runtime._FlatRequestHandler(binding, product.snapshot, 'codex-lead-1', lanes,
                None, SimpleNamespace(query=encode_query), None, 1, None)
            handler._memory = product
            handler._frozen_flat = (product.manifest, product.ids, product.vectors, product.encoder_binding)
            runtime._FlatRequestHandler._running_owner = lambda self: None
            runtime._SingleManifestOwner._check_factory = lambda self: None
            owner = object.__new__(runtime._SingleManifestOwner)
            owner._generation = (binding, product.snapshot, handler)
            owner._lanes, owner._state, owner._lock = frozenset(lanes), 'READY', threading.Lock()
            owner._lane, owner._client, owner._adapter = 'codex-lead-1', handler._rotator, handler._adapter
            owner._factory, owner._timeout, owner._watcher_lease = SimpleNamespace(_bulk=None), 1, None
            handler._factory = owner._factory
            owner._retained = None
            anchor = runtime.create_operator_pinned_policy_anchor(enabled=True, bundle_root=sys.argv[1],
                expected_manifest_sha256=sys.argv[2], operator_policy_sha256=hashlib.sha256(policy).hexdigest(),
                operator_approval_reference_sha256='b'*64)
            owner._policy_pins, owner._state = None, 'NEW'
            owner._bind_policy_anchor(anchor)
            owner._state = 'READY'
            first = (None if mode == 'package_direct_narrow_candidates_first' else
                     owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                       query='yhteyskatko uudelleenyritys'))
            path = pathlib.Path(sys.argv[3]); original = path.read_bytes()
            if mode in ('package_direct_candidate_race_source_v1', 'package_direct_candidate_race_source_v2',
                        'package_direct_candidate_race_caller_v1', 'package_direct_candidate_race_caller_v2'):
                located = mode.endswith('_v2')
                source_changed = '_race_source_' in mode
                request = {'schema':'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1',
                           'query_id':'startup_work_overview','nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                mapper = runtime.map_pipe_candidates
                retained_before = owner._retained
                def mutate_after_mapper(**kwargs):
                    mapped = mapper(**kwargs)
                    if source_changed:
                        changed = original.replace(b'ordinary', b'Ordinary')
                        assert changed != original and len(changed) == len(original)
                        path.write_bytes(changed)
                    request['scope'] = 'shared'
                    return mapped
                runtime.map_pipe_candidates = mutate_after_mapper
                try:
                    (owner.located_candidates if located else owner.candidates)(request)
                except runtime.FullRuntimeRefused:
                    refused = True
                else:
                    refused = False
                if not source_changed:
                    preserved = owner._state == 'READY' and owner._retained is retained_before
                    runtime.map_pipe_candidates = mapper
                    request['scope'] = 'own'
                    next_reply = (owner.located_candidates if located else owner.candidates)(request)
                    print(json.dumps({'result':'ok','refused':refused,'preserved':preserved,
                        'next_status':next_reply['status']})); sys.exit(0)
                print(json.dumps({'result':'ok','refused':refused,'state':owner._state,
                    'retained_cleared':owner._retained is None and handler._retained is None})); sys.exit(0)
            if mode in ('package_direct_narrow_approval_before', 'package_direct_narrow_approval_during',
                        'package_direct_narrow_approval_direct_before', 'package_direct_narrow_approval_direct_during'):
                descriptor_path = pathlib.Path(sys.argv[1]) / 'wd-mind-identifier-policy-approval.json'
                if mode.endswith('before'):
                    descriptor_path.write_bytes(b'{}')
                else:
                    def alter_descriptor_during_query(text):
                        descriptor_path.write_bytes(b'{}')
                        return encode_query(text)
                    handler._adapter.query = alter_descriptor_during_query
                request = {'schema':'wd.mind-pipe-request.v2','query_id':'startup_work_overview',
                           'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                try:
                    if '_approval_direct_' in mode:
                        owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                          query='yhteyskatko uudelleenyritys')
                    else:
                        owner.located_candidates(request)
                except runtime.FullRuntimeRefused:
                    refused = True
                else:
                    refused = False
                print(json.dumps({'result':'ok','refused':refused,'state':owner._state,
                    'retained_cleared':owner._retained is None and handler._retained is None})); sys.exit(0)
            if mode == 'package_direct_narrow_candidate_mutation':
                changed = original.replace(b'ordinary', b'Ordinary')
                assert changed != original and len(changed) == len(original)
                path.write_bytes(changed)
                request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                           'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                try:
                    owner.candidates(request)
                except runtime.FullRuntimeRefused:
                    refused = True
                else:
                    refused = False
                print(json.dumps({'result':'ok','refused':refused,'state':owner._state,
                    'retained_cleared':owner._retained is None and handler._retained is None})); sys.exit(0)
            if mode in ('package_direct_narrow_candidates', 'package_direct_narrow_candidates_first'):
                if mode == 'package_direct_narrow_candidates':
                    runtime.reader._verified_complete = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('full_rederive'))
                request = {'schema':'wd.mind-pipe-request.v1','query_id':'startup_work_overview',
                           'nonce':'d'*64,'lane':'codex-lead-1','scope':'own'}
                legacy = owner.candidates(request)
                retained_before = owner._retained
                try:
                    owner.candidates(dict(request, nonce='bad'))
                except runtime.FullRuntimeRefused:
                    invalid_preserved = owner._state == 'READY' and owner._retained is retained_before
                else:
                    invalid_preserved = False
                runtime.reader._verified_complete = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('full_rederive'))
                request['schema'] = 'wd.mind-pipe-request.v2'
                located = owner.located_candidates(request)
                projection = json.loads(product.snapshot['projection_bytes'])['messages']['records']
                by_doc = {sha(row['record_id'].encode()):row for row in projection}
                locations_ok = all(sha(canonical(json.loads(original[row['source_offset']:
                        row['source_offset'] + row['source_length']]))) == row['source_event_sha256']
                    and by_doc[row['doc_id_sha256']]['provenance']['chunk_index'] == row['chunk_index']
                    for row in located['candidates'])
                print(json.dumps({'result':'ok','v1':legacy['schema'],'v2':located['schema'],
                    'count':len(located['candidates']), 'locations_ok':locations_ok,
                    'invalid_preserved':invalid_preserved,
                    'same_refs':[row['source_event_sha256'] for row in legacy['candidates']] ==
                                [row['source_event_sha256'] for row in located['candidates']]})); sys.exit(0)
            if mode == 'package_direct_narrow_equivalence':
                import datetime
                full = runtime.query_flat_memory(enabled=True, memory=product, lane='codex-lead-1',
                    own_lane_only=True, query='yhteyskatko uudelleenyritys',
                    as_of_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    query_encoder=encode_query, include_historical=True)
                print(json.dumps({'result':'ok', 'same_refs':
                    [item['source_event_sha256'] for item in first['results']] ==
                    [item['source_reference']['sha256'] for item in full['results']]})); sys.exit(0)
            if mode == 'package_direct_narrow_append':
                extra = json.loads(original.splitlines()[0]); extra['message'] = 'synthetic later append'
                with path.open('ab') as target:
                    target.write(json.dumps(extra, sort_keys=True, separators=(',', ':')).encode() + b'\n')
                runtime.reader._verified_complete = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('full_rederive'))
                second = owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                           query='yhteyskatko uudelleenyritys')
                print(json.dumps({'result':'ok','first_hits':len(first['results']),
                    'same_refs':[item['source_event_sha256'] for item in first['results']] ==
                                [item['source_event_sha256'] for item in second['results']]})); sys.exit(0)
            if mode in ('package_direct_narrow_policy', 'package_direct_narrow_wrong_pin',
                        'package_direct_narrow_generation', 'package_direct_narrow_foreign_pid'):
                if mode == 'package_direct_narrow_policy':
                    (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
                elif mode == 'package_direct_narrow_wrong_pin':
                    binding._PrivateBinding__manifest = '0'*64
                elif mode == 'package_direct_narrow_generation':
                    path.with_name('events.generation.json').write_bytes(b'{}')
                else:
                    actual_pid = os.getpid()
                    runtime.os.getpid = lambda: actual_pid + 1
                try:
                    owner.query_local(enabled=True, lane='codex-lead-1', scope='own', query='yhteyskatko uudelleenyritys')
                except runtime.FullRuntimeRefused:
                    refused = True
                else:
                    refused = False
                print(json.dumps({'result':'ok','refused':refused,'state':owner._state,
                    'retained_cleared':owner._retained is None and handler._retained is None})); sys.exit(0)
            changed = original.replace(b'ordinary', b'Ordinary')
            assert changed != original and len(changed) == len(original)
            path.write_bytes(changed)
            try:
                owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                  query='yhteyskatko uudelleenyritys')
            except runtime.FullRuntimeRefused:
                refused = True
            else:
                refused = False
            print(json.dumps({'result':'ok', 'first_hits':len(first['results']),
                              'source_mutation_refused':refused, 'state':owner._state,
                              'retained_cleared':owner._retained is None and handler._retained is None})); sys.exit(0)
        if mode.startswith('package_historical'):
            if mode == 'package_historical_orthogonal':
                encode_query = lambda text: (0.0,1.0) + (0.0,)*382
            if mode == 'package_historical_mutation':
                def encode_query(text):
                    path = pathlib.Path(sys.argv[3]); path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
                    return (1.0,) + (0.0,)*383
            if mode == 'package_historical_provider_error':
                def encode_query(text): raise ValueError('synthetic provider error')
            result = runtime._query_historical_flat_memory(enabled=(mode != 'package_historical_disabled'),
                memory=product, lane='codex-lead-1', own_lane_only=True,
                query='yhteyskatko uudelleenyritys', query_encoder=encode_query)
            import datetime
            ages_ok = all(hit['age_seconds'] > 899 and hit['source_time_utc'].startswith('2026-01-01')
                          for hit in result['results'])
            startup = runtime.query_flat_memory(enabled=True,memory=product,lane='codex-lead-1',own_lane_only=True,
                query='yhteyskatko uudelleenyritys',as_of_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                query_encoder=encode_query)
            print(json.dumps({'result':'ok','status':result['status'],'scope':result['scope'],
                'age_ok':ages_ok,'hits':len(result['results']),'startup':startup['status'],
                'authority':result['authority'],'freshness':result['snapshot_freshness'],
                'latest':result['own_latest_task'],'refs':all(h['source_reference']['verified']=='MATCH_WITHIN_PREFIX'
                                                            for h in result['results']),
                'safe':all('password=' not in h['display_text'] and 'synthetic rco' not in h['display_text']
                           and 'stored_text' not in h for h in result['results'])}));sys.exit(0)
        lane = 'claude-rco-1' if mode == 'package_rco' else ('codex-tools-1' if mode == 'package_other' else 'codex-lead-1')
        result = runtime.query_flat_memory(enabled=True, memory=product, lane=lane,
            own_lane_only=mode != 'package_general', query='yhteyskatko uudelleenyritys',
            as_of_utc=('2025-12-31T23:00:00Z' if mode == 'package_future_history' else
                       '2026-01-01T00:17:00Z' if mode == 'package_stale' or mode.startswith('package_stale_history')
                       else '2026-01-01T00:02:00Z'),
            query_encoder=encode_query, **({'include_historical': True} if mode.startswith('package_stale_history')
                                           or mode == 'package_future_history' else {}))
        projection, _, _ = reader._verified_complete(binding.snapshot_for_query())
        records = {sha(record['record_id'].encode()): record for record in projection['messages']['records']}
        references_match = all(hit['doc_id_sha256'] in records and
            hit['source_reference']['sha256'] == records[hit['doc_id_sha256']]['provenance']['source_event_ref'] and
            hit['stored_text'] == records[hit['doc_id_sha256']]['stored_text'] and
            hit['source_agent'] == records[hit['doc_id_sha256']]['meta']['agent'] and
            hit['source_time_utc'] == records[hit['doc_id_sha256']]['time_utc'] for hit in result['results'])
        print(json.dumps({'result':'ok', 'status':result['status'], 'relevance':result.get('relevance'),
                          'hits':len(result['results']), 'agents':[hit['source_agent'] for hit in result['results']],
                          'refs':references_match and all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX'
                                                           for hit in result['results']),
                          'latest':result['own_latest_task'], 'index_match':result['index_match']}))
        sys.exit(0)
    if mode == 'policy_after':
        (pathlib.Path(sys.argv[1]) / 'wd-mind-message-policy.json').write_bytes(b'{}')
    if mode == 'source_after':
        path = pathlib.Path(sys.argv[3])
        path.write_bytes(path.read_bytes().replace(b'ordinary', b'Ordinary'))
    snapshot = binding.snapshot_for_query()
    if mode == 'ephemeral':
        other = runtime.assemble_full_runtime(enabled=True, bundle_root=sys.argv[1], expected_manifest_sha256=sys.argv[2],
            identifier_policy_bytes=policy, expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
        print(json.dumps({'result':'ok', 'fresh': snapshot['identifier_policy_binding'][2] !=
                          other.snapshot_for_query()['identifier_policy_binding'][2]}))
        sys.exit(0)
    projection, _, report = reader._verified_complete(snapshot)
    print(json.dumps({'result':'ok','records':len(projection['messages']['records']),
                      'verified':report['status'], 'authority':binding.authority,
                      'safe_repr': repr(binding) == '<PrivateMindFullRuntimeBinding>'}))
except Exception:
    print(json.dumps({'result':'refused'}))
'''
    proc = subprocess.run([sys.executable, '-B', '-', str(case['bundle']), case['digest'],
                           str(case['source']), scenario], cwd=case['bundle'], input=code, capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_synthetic_manifest_bound_runtime_accepts_screened_records(synthetic_bundle):
    result = invoke(synthetic_bundle)
    assert result == {'result': 'ok', 'records': 1, 'verified': 'MATCH', 'authority': 'none', 'safe_repr': True}


def test_manifest_runtime_location_verifies_both_synthetic_trees(synthetic_bundle):
    assert invoke(synthetic_bundle, 'location') == {'result':'ok', 'generation':'a'*40,
        'revision':'b'*40, 'interpreter_name':'python.exe', 'verified':True}


def test_manifest_worker_factory_uses_exact_bindings_and_preserves_hold(synthetic_bundle):
    result = invoke(synthetic_bundle, 'factory')
    assert result == {'result':'ok','calls':1,'status':'HOLD','same_owner':True,
                      'worker_only':True,'scratch_none':True,'lock_exact':True,'args_exact':True}


@pytest.mark.parametrize('scenario', ['factory_disabled', 'factory_missing_audit',
    'factory_asset_mutation', 'factory_manifest_mutation', 'factory_bad_origin', 'factory_bad_timeout',
    'factory_bad_lock_timeout', 'factory_bad_bulk', 'factory_audit_removed', 'factory_lock_changed'])
def test_manifest_worker_factory_refuses_before_spawn(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {'result':'refused','calls':0}


@pytest.mark.parametrize('fault', ['manifest', 'fleet', 'runtime_descriptor', 'identity_descriptor',
    'binding_descriptor', 'location_descriptor', 'policy_descriptor', 'model_asset', 'interpreter_asset',
    'missing_model_root', 'missing_interpreter_root'])
def test_manifest_runtime_location_refuses_mutation_or_missing_roots(synthetic_bundle, fault):
    case = synthetic_bundle
    if fault == 'manifest': case['digest'] = '0'*64
    if fault == 'fleet': (case['bundle'] / 'wd-fleet.json').write_bytes(b'{}')
    descriptor_files = {'runtime_descriptor':'wd-mind-runtime-manifest.json',
        'identity_descriptor':'wd-mind-interpreter-identity.json', 'binding_descriptor':'wd-mind-e5-encoder-binding.json',
        'location_descriptor':'wd-mind-runtime-location.json', 'policy_descriptor':'wd-mind-message-policy.json'}
    if fault in descriptor_files: (case['bundle'] / descriptor_files[fault]).write_bytes(b'{}')
    if fault == 'model_asset': (case['model_root'] / 'model/model.safetensors').write_bytes(b'changed')
    if fault == 'interpreter_asset': (case['interpreter_root'] / 'python.exe').write_bytes(b'changed')
    if fault == 'missing_model_root':
        (case['model_root'] / 'model/model.safetensors').unlink()
        (case['model_root'] / 'model').rmdir(); case['model_root'].rmdir()
    if fault == 'missing_interpreter_root':
        for relative in ('python.exe', 'python.dll', 'lib/os.py'):
            (case['interpreter_root'] / relative).unlink()
        (case['interpreter_root'] / 'lib').rmdir(); case['interpreter_root'].rmdir()
    assert invoke(case, 'location') == {'result':'refused'}


@pytest.mark.parametrize('fault', ['source_commit', 'runtime_cross_pin', 'identity_cross_pin',
    'binding_cross_pin', 'relative_root', 'overlapping_roots', 'wrong_model_revision',
    'duplicate_interpreter', 'wrong_worker_code_pin'])
def test_rehashed_descriptor_conflicts_still_refuse(synthetic_bundle, fault):
    case = synthetic_bundle
    bundle = case['bundle']
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    def repin(name, value):
        raw = canonical(value); (bundle / name).write_bytes(raw)
        manifest['files'][name] = hashlib.sha256(raw).hexdigest()
    location = json.loads((bundle / 'wd-mind-runtime-location.json').read_bytes())
    binding = json.loads((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes())
    if fault == 'source_commit': location['source_commit'] = 'd'*40
    if fault == 'runtime_cross_pin': location['runtime_manifest_sha256'] = '0'*64
    if fault == 'identity_cross_pin': location['interpreter_identity_sha256'] = '0'*64
    if fault == 'binding_cross_pin': location['encoder_binding_sha256'] = '0'*64
    if fault == 'relative_root': location['runtime_root'] = 'relative-root'
    if fault == 'overlapping_roots': location['runtime_root'] = location['interpreter_root']
    if fault == 'wrong_model_revision':
        binding['encoder']['revision'] = 'd'*40
        repin('wd-mind-e5-encoder-binding.json', binding)
        location['encoder_binding_sha256'] = manifest['files']['wd-mind-e5-encoder-binding.json']
    if fault == 'wrong_worker_code_pin':
        binding['code_sha256'] = '0'*64
        repin('wd-mind-e5-encoder-binding.json', binding)
        location['encoder_binding_sha256'] = manifest['files']['wd-mind-e5-encoder-binding.json']
    if fault == 'duplicate_interpreter':
        identity = json.loads((bundle / 'wd-mind-interpreter-identity.json').read_bytes())
        identity['files'].append(dict(identity['files'][0]))
        repin('wd-mind-interpreter-identity.json', identity)
        location['interpreter_identity_sha256'] = manifest['files']['wd-mind-interpreter-identity.json']
    repin('wd-mind-runtime-location.json', location)
    raw = canonical(manifest); manifest_path.write_bytes(raw); case['digest'] = hashlib.sha256(raw).hexdigest()
    assert invoke(case, 'location') == {'result':'refused'}


@pytest.mark.parametrize('fault', ['disabled', 'manifest', 'fleet', 'policy', 'code', 'sidecar', 'rewrite', 'rotation',
                                   'policy_during', 'policy_after', 'source_after'])
def test_synthetic_manifest_runtime_refuses_mutation_or_rotation(synthetic_bundle, fault):
    case = synthetic_bundle
    if fault == 'manifest': case['digest'] = '0' * 64
    if fault == 'fleet': (case['bundle'] / 'wd-fleet.json').write_bytes(b'{}')
    if fault == 'policy': (case['bundle'] / 'wd-mind-message-policy.json').write_bytes(b'{}')
    if fault == 'code':
        with (case['bundle'] / 'tools-bootstrap' / 'tools' / 'wd_mind_content_query_readonly.py').open('ab') as stream:
            stream.write(b'\n# synthetic tampering\n')
    if fault == 'sidecar': case['source'].with_name('events.generation.json').write_bytes(b'{}')
    assert invoke(case, fault) == {'result': 'refused'}


def test_each_private_binding_gets_a_fresh_ephemeral_hmac_key(synthetic_bundle):
    assert invoke(synthetic_bundle, 'ephemeral') == {'result': 'ok', 'fresh': True}


def test_synthetic_private_binding_build_package_and_query(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package')
    assert result['result'] == 'ok' and result['status'] == 'SEMANTIC_CANDIDATE'
    assert result['relevance'] == 'UNVERIFIED' and result['hits'] > 0
    assert set(result['agents']) == {'codex-lead-1'} and result['refs']
    assert result['latest'] == 'UNKNOWN' and result['index_match'] == 'NOT_APPLICABLE'


@pytest.mark.parametrize('fault', ['source_changed', 'policy_changed', 'policy_anchor_changed',
                                   'stage_source_changed', 'stage_policy_changed'])
def test_generation_refresh_rechecks_real_synthetic_source_and_policy(synthetic_bundle, fault):
    # Unlike the orchestration-double tests, these cases use actual pinned
    # reader, source and manifest checks against a synthetic temporary bundle.
    assert invoke(synthetic_bundle, 'refresh_real_' + fault) == {
        'result':'refused', 'unchanged':True, 'hold':True}


def test_generation_refresh_rederives_appended_synthetic_source(synthetic_bundle):
    assert invoke(synthetic_bundle, 'refresh_real_appended') == {
        'result':'ok', 'new_binding':True, 'new_memory':True, 'hold':False, 'source_ref':True}


def test_generation_delivery_real_synthetic_bundle_matches_export_and_refuses_stale(synthetic_bundle):
    assert invoke(synthetic_bundle, 'refresh_real_delivery') == {
        'result':'ok', 'same':True, 'refs':True, 'cap_refused':True, 'stale_refused':True, 'ready':True}


@pytest.mark.parametrize(('fault','signal'), [('exit_before','FullRuntimeRefused'),
    ('exit_during','FullRuntimeRefused'), ('cancel','KeyboardInterrupt'),
    ('group','BaseExceptionGroup'), ('unknown','FullRuntimeCancellationUnknown')])
def test_generation_delivery_real_worker_failure_preserves_hold_and_private_locals(synthetic_bundle, fault, signal):
    result = invoke(synthetic_bundle, 'refresh_real_worker_' + fault)
    assert result['result'] == 'refused' and result['signal'] == signal
    assert result['hold'] is True and result['clean'] is True
    assert result['calls'] >= 1


@pytest.mark.parametrize(('fault','state'), [('source','HOLD'), ('manifest','HOLD'), ('adapter','HOLD'),
    ('internal_pin','HOLD'), ('internal_policy','HOLD'), ('wrong_policy_pin','READY'),
    ('bad_request','READY'), ('size','READY')])
def test_generation_delivery_integrity_failure_differs_from_user_refusal(synthetic_bundle, fault, state):
    assert invoke(synthetic_bundle, 'refresh_real_delivery_error_' + fault) == {
        'result':'refused', 'state':state, 'clean':True}


@pytest.mark.parametrize('scenario', ['package_owner_build_multibatch', 'package_owner_build_append'])
def test_owner_build_uses_immutable_prefix_with_bounded_full_checks(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['result'] == 'ok' and result['records'] > 32
    assert len(result['batch_checks']) >= 3 and result['same_vectors'] is True
    assert len(set(result['batch_checks'])) == 1
    assert result['default_join'] == 600.0 and result['held'] is False and result['released'] is True


@pytest.mark.parametrize('scenario', ['package_owner_build_mutation', 'package_owner_build_control', 'package_owner_build_hold'])
def test_owner_build_refuses_mutation_and_retains_watcher_on_hold(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['result'] == 'refused' and result['no_partial'] is True
    assert result['held'] is (scenario == 'package_owner_build_hold')
    assert result['default_join'] == 600.0 and result['released'] is True


def test_immutable_snapshot_build_returns_verified_synthetic_results(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_immutable')
    assert result['result'] == 'ok' and result['status'] == 'SEMANTIC_CANDIDATE'
    assert result['hits'] > 0 and result['refs']


def test_immutable_snapshot_multiple_batches_do_not_repeat_full_source_check(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_immutable_multibatch')
    assert result['result'] == 'ok' and result['records'] > 16
    assert result['full_checks'] <= 6


@pytest.mark.parametrize('field', ['source_path', 'generation_path'])
def test_immutable_checkpoint_rejects_reader_source_path_mismatch(monkeypatch, tmp_path, field):
    from tools import wd_mind_full_runtime as runtime
    source, sidecar = tmp_path / 'events.jsonl', tmp_path / 'events.generation.json'
    cursor = runtime.bridge.BridgeCursor(1, 'synthetic-identity', None)
    frozen = {'messages':{}, 'projection_bytes':b'{}', 'projection_sha256':'a' * 64}
    controls = ('synthetic',)
    args = {'enabled':True, 'bundle_root':str(tmp_path), 'expected_manifest_sha256':'b' * 64,
            'source_path':str(source), 'generation_path':str(sidecar), 'cursor':cursor,
            'expected_prefix_sha256':'c' * 64}
    context = {'decision':{'source_prefix':{'sha256':'c' * 64}},
               'messages':{'source_path':str(source), 'generation_path':str(sidecar), 'end_cursor':cursor}}
    context['messages'][field] = str(tmp_path / 'different')
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *args: controls)
    monkeypatch.setattr(runtime.prefix, '_deployment_source', lambda *args: (source, sidecar, 'd' * 64))
    monkeypatch.setattr(runtime.reader, '_inputs', lambda *args, **kwargs: context)
    binding = runtime._PrivateBinding(frozen, tmp_path, 'b' * 64, controls, args)
    with pytest.raises(ValueError, match='anchor_changed'):
        binding._immutable_checkpoint(frozen)


@pytest.mark.parametrize('fault', ['source_before', 'generation_before', 'source_passage',
                                   'generation_passage', 'provider_passage', 'bundle_passage',
                                   'helper_passage', 'invalid_optin', 'bad_timeout', 'forged_hold'])
def test_immutable_snapshot_refuses_synthetic_source_or_provider_fault(synthetic_bundle, fault):
    assert invoke(synthetic_bundle, 'package_immutable_' + fault) == {'result': 'refused'}


def test_immutable_snapshot_background_failure_refuses(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_immutable_background')
    assert result == {'result':'refused', 'detected':True, 'joined':False}


def test_immutable_snapshot_final_verification_follows_verifier_join(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_immutable_after_join')
    assert result == {'result':'refused', 'detected':False, 'joined':True}


def test_owned_snapshot_reservation_interrupt_keeps_identity_and_releases_idle_registry(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_immutable_reserve_cancel') == {
        'result':'cancelled', 'retained':True, 'held':False, 'registry_idle':True}


@pytest.mark.parametrize(('scenario', 'signal'), [('package_immutable_hold', 'FullRuntimeSnapshotHold'),
                                                  ('package_immutable_cancel_hold', 'KeyboardInterrupt'),
                                                  ('package_immutable_generator_hold', 'GeneratorExit'),
                                                  ('package_immutable_async_hold', 'CancelledError'),
                                                  ('package_immutable_group_hold', 'BaseExceptionGroup'),
                                                  ('package_immutable_oversized_group_hold', 'FullRuntimeCancellationUnknown'),
                                                  ('package_immutable_owned_hold', 'FullRuntimeSnapshotHold'),
                                                  ('package_immutable_owned_cancel_hold', 'KeyboardInterrupt')])
def test_immutable_snapshot_hold_retains_verifier_until_exit(synthetic_bundle, scenario, signal):
    assert invoke(synthetic_bundle, scenario) == {'result':'hold', 'signal':signal, 'held':True,
                                                   'retry_held':True, 'released':True, 'clean':True, 'captured':True}


@pytest.mark.parametrize('scenario', ['package_rco', 'package_other'])
def test_own_lane_query_does_not_claim_another_lanes_work(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['status'] == 'ABSTAIN' and result['hits'] == 0 and result['latest'] == 'UNKNOWN'


def test_general_query_reports_own_task_unknown_for_another_lanes_hit(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_general')
    assert result['status'] == 'SEMANTIC_CANDIDATE' and result['agents'] == ['fable-5']
    assert result['latest'] == 'UNKNOWN' and result['refs']












































@pytest.mark.parametrize('scenario', ['package_orthogonal', 'package_stale'])
def test_flat_query_abstains_on_orthogonal_or_stale_hits(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result['status'] == 'ABSTAIN' and result['hits'] == 0


def test_flat_query_explicit_history_retains_verified_older_hits(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_stale_history')
    assert result['status'] == 'SEMANTIC_CANDIDATE'
    assert result['hits'] > 0 and result['refs'] is True


def test_historical_query_excludes_future_and_rechecks_source(synthetic_bundle):
    future = invoke(synthetic_bundle, 'package_future_history')
    assert future['status'] == 'ABSTAIN' and future['hits'] == 0
    assert invoke(synthetic_bundle, 'package_stale_history_source_mutation') == {'result': 'refused'}


def test_single_owner_direct_query_rechecks_real_synthetic_source_between_calls(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_direct_owner_source_recheck')
    assert result['result'] == 'ok' and result['first_hits'] > 0
    assert result['source_mutation_refused'] is True and result['state'] == 'HOLD'
    assert result['retained_cleared'] is True


def test_flat_source_owner_accepts_synthetic_runtime_memory(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_direct_narrow_probe') == {'result':'ok','resolved':1,'ref_match':True}


def test_single_owner_narrow_query_allows_append_without_full_rederive(synthetic_bundle):
    result = invoke(synthetic_bundle, 'package_direct_narrow_append')
    assert result['result'] == 'ok' and result['first_hits'] > 0 and result['same_refs'] is True


def test_single_owner_narrow_query_agrees_with_full_ranking(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_direct_narrow_equivalence') == {'result':'ok','same_refs':True}


def test_single_owner_narrow_query_refuses_changed_policy(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_direct_narrow_policy') == {
        'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


def test_single_owner_narrow_query_refuses_changed_binding_manifest_pin(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_direct_narrow_wrong_pin') == {
        'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


@pytest.mark.parametrize('scenario', ['package_direct_narrow_approval_before', 'package_direct_narrow_approval_during',
                                     'package_direct_narrow_approval_direct_before', 'package_direct_narrow_approval_direct_during'])
def test_located_candidates_refuse_changed_real_approval_descriptor(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {
        'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


@pytest.mark.parametrize('scenario', ['package_direct_narrow_generation', 'package_direct_narrow_foreign_pid'])
def test_single_owner_narrow_query_refuses_changed_generation_or_process(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


@pytest.mark.parametrize('scenario', ['package_direct_narrow_candidates', 'package_direct_narrow_candidates_first'])
def test_single_owner_v1_and_located_v2_candidates_share_narrow_source(synthetic_bundle, scenario):
    result = invoke(synthetic_bundle, scenario)
    assert result == {'result':'ok','v1':'wd.mind-pipe-candidates.v1','v2':'wd.mind-pipe-candidates.v2',
                      'count':3,'locations_ok':True,'same_refs':True,'invalid_preserved':True}


def test_single_owner_candidate_source_mutation_drops_retained_state(synthetic_bundle):
    assert invoke(synthetic_bundle, 'package_direct_narrow_candidate_mutation') == {
        'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


@pytest.mark.parametrize('scenario', ['package_direct_candidate_race_source_v1', 'package_direct_candidate_race_source_v2'])
def test_changed_candidate_request_cannot_hide_real_source_mutation(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {
        'result':'ok','refused':True,'state':'HOLD','retained_cleared':True}


@pytest.mark.parametrize('scenario', ['package_direct_candidate_race_caller_v1', 'package_direct_candidate_race_caller_v2'])
def test_changed_candidate_request_preserves_real_source_owner(synthetic_bundle, scenario):
    assert invoke(synthetic_bundle, scenario) == {
        'result':'ok','refused':True,'preserved':True,'next_status':'CANDIDATES'}


def test_owner_and_mapper_keep_historical_candidates_with_unknown_freshness(monkeypatch):
    from types import SimpleNamespace
    import threading
    from tools import wd_mind_full_runtime as runtime
    handler = object.__new__(runtime._FlatRequestHandler)
    handler._lock, handler._closed, handler._failed = threading.Lock(), False, False
    handler._memory, handler._lanes = object(), frozenset({'codex-lead-1'})
    handler._retained = None
    handler._adapter = SimpleNamespace(query=lambda _: None)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_running_owner', lambda self: None)
    monkeypatch.setattr(runtime._FlatRequestHandler, '_source_check', lambda self: None)
    calls = []
    source_expiry = [None]
    def query(**kwargs):
        calls.append(kwargs)
        return {'schema':'wd.mind-flat-f32-query.v1', 'status':'SEMANTIC_CANDIDATE',
                'authority':'none', 'relevance':'UNVERIFIED', 'index_match':'NOT_APPLICABLE',
                'own_latest_task':'UNKNOWN', 'results':[{'ref_sha256':'a'*64, 'doc_id_sha256':'b'*64,
                    'source_reference':{'sha256':'c'*64, 'kind':'sha256_of_canonical_event_json',
                                        'verified':'MATCH_WITHIN_PREFIX', 'sender_authentication':'UNKNOWN'},
                    'source_time_utc':'2026-01-01T00:00:00Z', 'source_valid_until_utc':source_expiry[0],
                    'event_age_seconds_upper_bound':1000000}]}
    monkeypatch.setattr(runtime, 'query_flat_memory', query)
    request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1', 'query':'work', 'scope':'own'}
    answer = handler._handle_for_lane(request, 'codex-lead-1')
    assert calls[-1]['include_historical'] is True
    assert answer['snapshot_freshness'] == 'UNKNOWN'
    assert answer['expiry_scope'] == 'source_validity_only' and answer['expires_utc'] is None
    pipe = runtime.map_pipe_candidates(enabled=True, handler=handler, request={
        'schema':'wd.mind-pipe-request.v1', 'query_id':'startup_work_overview',
        'nonce':'d'*64, 'lane':'codex-lead-1', 'scope':'own'})
    assert pipe['status'] == 'CANDIDATES' and pipe['candidates'][0]['source_event_sha256'] == 'c'*64
    source_expiry[0] = '2020-01-01T00:00:00Z'
    expired = handler._handle_for_lane(request, 'codex-lead-1')
    assert expired['result']['status'] == 'ABSTAIN' and expired['result']['results'] == []
    source_expiry[0] = '2999-01-01T00:00:00Z'
    valid = handler._handle_for_lane(request, 'codex-lead-1')
    assert valid['result']['status'] == 'SEMANTIC_CANDIDATE' and valid['expires_utc'] is not None


@pytest.mark.parametrize('fault', ['package_fake_binding', 'package_source_passage', 'package_bundle_passage',
    'package_provider_passage', 'package_source_query', 'package_bundle_query', 'package_provider_query',
    'package_memory_repin_query'])
def test_synthetic_in_memory_package_refuses_provider_or_binding_changes(synthetic_bundle, fault):
    assert invoke(synthetic_bundle, fault) == {'result': 'refused'}


@pytest.mark.parametrize('fault', ['missing_removals', 'missing_lane', 'wrong_scope', 'unknown_type'])
def test_manifest_pinned_invalid_content_policy_is_not_accepted(synthetic_bundle, fault):
    case = synthetic_bundle
    path = case['bundle'] / 'wd-mind-message-policy.json'
    policy = json.loads(path.read_bytes())
    if fault == 'missing_removals': policy.pop('removed_source_refs')
    if fault == 'missing_lane': policy['lanes'].pop()
    if fault == 'wrong_scope': policy['task_scope'] = 'selected'
    if fault == 'unknown_type': policy['types'].append('synthetic-unknown')
    raw = canonical(policy)
    path.write_bytes(raw)
    manifest_path = case['bundle'] / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['wd-mind-message-policy.json'] = hashlib.sha256(raw).hexdigest()
    manifest_raw = canonical(manifest)
    manifest_path.write_bytes(manifest_raw)
    case['digest'] = hashlib.sha256(manifest_raw).hexdigest()
    assert invoke(case) == {'result': 'refused'}















def test_deferred_owner_preflight_refuses_before_access(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    def forbidden(*args, **kwargs):
        raise AssertionError('deferred preflight must not access resources')
    monkeypatch.setattr(runtime, '_bundle_inputs', forbidden)
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', forbidden)
    monkeypatch.setattr(runtime, '_worker_factory_preflight', forbidden)
    with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'):
        runtime._owner_preflight(bundle_root=str(Path.cwd()), manifest_sha256='a'*64)














































def test_single_owner_interrupt_during_spawn_preserves_owned_hold(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Held:
        def status(self): return {'state': 'RUNNING', 'owned': True}
        def shutdown(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    held = Held()
    def launch(self, *, owner_sink, call_deadline=None):
        owner_sink(held)
        entered.set()
        assert release.wait(5)
        return {'status': 'HOLD', 'client': None, 'owner': held}
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt, raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt):
            owner.start()
        assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
        assert owner._owner is held
        assert owner.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    finally:
        release.set()
        if getattr(owner, '_launch_thread', None) is not None:
            owner._launch_thread.join(timeout=2)



@pytest.mark.parametrize('entrypoint', ['handler', 'refresh', 'fast', 'coordinator', 'entrypoint'])
def test_deferred_legacy_rotator_routes_refuse_without_starting_a_worker(monkeypatch, entrypoint):
    from tools import wd_mind_full_runtime as runtime
    if entrypoint == 'entrypoint':
        import io
        output = io.StringIO()
        assert runtime._owner_entrypoint([], io.StringIO(), output) != 0
        assert 'READY' not in output.getvalue()
        return
    if entrypoint == 'handler': call = lambda: runtime.create_flat_request_handler(enabled=True)
    elif entrypoint == 'fast': call = lambda: runtime.build_fast_fenced_owner(enabled=True)
    elif entrypoint == 'coordinator': call = lambda: runtime.create_manifest_coordinator(enabled=True)
    else:
        handler = object.__new__(runtime._FlatRequestHandler)
        call = handler.refresh
    with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'):
        call()


def test_single_owner_package_has_static_closure_with_rotator():
    from test_bridge_code_package_closure import _closure, _packaged_python, _definition
    rotator = 'tools/wd_mind_e5_rotating_client.py'
    entries = _packaged_python()
    assert rotator in entries
    assert 'mind_guardian' not in _definition()['python_entrypoints']
    gaps, unresolved = _closure(entries)
    assert not gaps, gaps
    assert not unresolved, unresolved


def test_single_owner_retains_both_owners_on_mismatched_launch_result(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
        def shutdown(self, *, timeout): return {'state':'HOLD','owned':True}
    sink_owner, returned_owner = Owner(), Owner()
    class Client:
        owner = returned_owner
    def launch(self, *, owner_sink, call_deadline=None):
        owner_sink(sink_owner)
        return {'status':'READY', 'client':Client(), 'owner':returned_owner}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner._owner is sink_owner and owner._launch['owner'] is returned_owner
    assert owner._client is None


def test_single_owner_retains_conflicting_sink_owners(tmp_path, monkeypatch):
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
    first, second = Owner(), Owner()
    def launch(self, *, owner_sink, call_deadline=None):
        owner_sink(first)
        owner_sink(second)
        pytest.fail('duplicate owner must refuse')
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start() == {'state':'HOLD','owned':None,'authority':'none'}
    assert owner._owner is first and owner._owner_conflicts == [second]
    assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}


def test_cancelled_single_owner_retains_later_conflicting_factory_owner(tmp_path, monkeypatch):
    import threading
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Owner:
        def status(self): return {'state':'RUNNING','owned':True}
        def shutdown(self, *, timeout): return {'state':'EXITED','owned':False}
    first, second = Owner(), Owner()
    def launch(self, *, owner_sink, call_deadline=None):
        owner_sink(first)
        entered.set()
        assert release.wait(5)
        return {'status':'READY', 'owner':second, 'client':None}
    def interrupt(self):
        assert entered.wait(2)
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_wait_for_launch', interrupt)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    try:
        with pytest.raises(KeyboardInterrupt): owner.start()
        release.set()
        owner._launch_thread.join(timeout=5)
        assert not owner._launch_thread.is_alive()
        assert owner._owner is first and owner._launch['owner'] is second
        assert second in owner._owner_conflicts
        assert owner.close() == {'state':'HOLD','owned':None,'authority':'none'}
    finally:
        release.set()
        if owner._launch_thread is not None: owner._launch_thread.join(timeout=5)


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled'])
def test_export_cancellation_never_exposes_inner_secret_traceback(tmp_path, monkeypatch, signal_name):
    import asyncio, traceback
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    signal = KeyboardInterrupt if signal_name == 'keyboard' else asyncio.CancelledError
    fake_key = b'synthetic-policy-key-32-bytes!!!'
    def interrupted(memory):
        inner_private = fake_key
        try:
            raise ValueError(fake_key)
        except ValueError:
            raise signal(fake_key)
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(signal) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    trace = traceback.TracebackException.from_exception(captured.value, capture_locals=True)
    owned_frames = [frame for frame in trace.stack if frame.name != 'test_export_cancellation_never_exposes_inner_secret_traceback']
    assert owned_frames
    assert captured.value.__context__ is None and captured.value.args == ()
    assert all(repr(fake_key) not in value for frame in owned_frames for value in (frame.locals or {}).values())
    assert owner.status()['state'] == 'HOLD'


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_export_cancellation_subclass_constructor_never_runs_again(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    calls = []
    class UntrustedSignal(base):
        def __init__(self):
            calls.append(True)
            if len(calls) > 1: raise RuntimeError('synthetic private failure')
    event = UntrustedSignal()
    def interrupted(memory): raise event
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(base) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    assert type(captured.value) is base and calls == [True]
    assert captured.value.args == () and captured.value.__context__ is None
    assert owner.status()['state'] == 'HOLD'


@pytest.mark.parametrize('signal_name', ['keyboard','cancelled','generator'])
def test_export_cancellation_never_touches_untrusted_signal_attributes(tmp_path, monkeypatch, signal_name):
    import asyncio
    runtime, _, binding, factory, _ = _single_owner_synthetic(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', lambda self: {
        'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'})
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0)
    assert owner.start()['state'] == 'READY'
    base = {'keyboard':KeyboardInterrupt, 'cancelled':asyncio.CancelledError, 'generator':GeneratorExit}[signal_name]
    class UntrustedSignal(base):
        def __setattr__(self, name, value):
            if name in ('__traceback__', '__context__', '__cause__'):
                raise RuntimeError('synthetic private failure')
            return super().__setattr__(name, value)
    event = UntrustedSignal()
    def interrupted(memory): raise event
    monkeypatch.setattr(runtime, '_flat_check', interrupted)
    with pytest.raises(base) as captured:
        owner.prepare_consumer_export(enabled=True, expected_nonce='e'*64, expected_identifier_policy_sha256='a'*64, max_total_bytes=1000)
    assert type(captured.value) is base
    assert captured.value.args == () and captured.value.__context__ is None
    assert owner.status()['state'] == 'HOLD'
