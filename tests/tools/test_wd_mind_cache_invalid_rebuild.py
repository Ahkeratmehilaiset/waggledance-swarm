"""Synthetic cache corruption must rebuild; unsafe cache access must refuse."""

import hashlib
import json
import struct

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_vector_cache as cache
from test_wd_mind_cache_builder_integration import _synthetic_case


_REAL_CACHE_CONTENT_PINS = runtime._cache_content_pins


@pytest.fixture
def case(tmp_path, monkeypatch):
    # The containing pytest basetemp is placed under the task's audit root.
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    return _synthetic_case(audit, monkeypatch)


def _change_header(raw, change):
    start = len(cache._MAGIC) + 4
    length = struct.unpack('<I', raw[len(cache._MAGIC):start])[0]
    header = json.loads(raw[start:start + length])
    change(header)
    encoded = cache._canonical(header)
    return cache._MAGIC + struct.pack('<I', len(encoded)) + encoded + raw[start + length:]


@pytest.mark.parametrize('fault', ['short_header', 'invalid_json', 'duplicate_key', 'unknown_field',
                                   'short_row', 'extra_byte', 'oversize_count', 'invalid_vector'])
def test_corrupt_cache_rebuilds_screened_rows(case, fault):
    binding, _ = case.snapshot()
    assert case.build(binding).cache_status == 'MISS_STORED'
    original = case.cache_path.read_bytes()
    if fault == 'short_header':
        corrupt = cache._MAGIC[:2]
    elif fault == 'invalid_json':
        corrupt = cache._MAGIC + struct.pack('<I', 1) + b'{'
    elif fault == 'duplicate_key':
        start = len(cache._MAGIC) + 4
        length = struct.unpack('<I', original[len(cache._MAGIC):start])[0]
        encoded = b'{"schema":"duplicate",' + original[start + 1:start + length]
        corrupt = cache._MAGIC + struct.pack('<I', len(encoded)) + encoded + original[start + length:]
    elif fault == 'unknown_field':
        corrupt = _change_header(original, lambda header: header.update(unexpected=True))
    elif fault == 'short_row':
        corrupt = original[:-1]
    elif fault == 'extra_byte':
        corrupt = original + b'x'
    elif fault == 'oversize_count':
        corrupt = _change_header(original, lambda header: header.update(count=250001))
    else:
        start = len(cache._MAGIC) + 4
        length = struct.unpack('<I', original[len(cache._MAGIC):start])[0]
        old_header = json.loads(original[start:start + length])
        rows = (('a' * 64, 'b' * 64, bytes(1536)),)
        encoded = cache._canonical(cache._header(old_header['binding'], rows))
        corrupt = cache._MAGIC + struct.pack('<I', len(encoded)) + encoded + b''.join(cache._encoded(rows))
    case.cache_path.write_bytes(corrupt)
    case.passages.clear()
    binding, _ = case.snapshot()
    rebuilt = case.build(binding)
    assert rebuilt.cache_status == 'MISS_STORED'
    assert len(case.passages) == 2
    assert case.cache_path.read_bytes() != corrupt
    case.passages.clear()
    binding, _ = case.snapshot()
    assert case.build(binding).cache_status == 'HIT_STORED'
    assert case.passages == []


@pytest.mark.parametrize('reason', ['acl_not_user_only', 'owner_not_trusted',
                                    'reparse_or_type', 'hardlink', 'writer_busy', 'open'])
def test_security_refusals_do_not_turn_into_rebuild(case, monkeypatch, reason):
    binding, _ = case.snapshot()
    def refuse(*_args, **_kwargs):
        raise cache.CacheRefused(reason)
    monkeypatch.setattr(cache, 'load_cache', refuse)
    with pytest.raises(ValueError, match='snapshot_build_refused'):
        case.build(binding)
    assert case.passages == []
    assert not case.cache_path.exists()


def test_failed_prefix_verification_is_not_a_recoverable_cache_miss(case):
    binding, _ = case.snapshot()
    case.build(binding)
    raw = case.cache_path.read_bytes()
    start = len(cache._MAGIC) + 4
    length = struct.unpack('<I', raw[len(cache._MAGIC):start])[0]
    expected_binding = json.loads(raw[start:start + length])['binding']
    calls = []
    def fail(*_args):
        calls.append(True)
        raise OSError('synthetic source verification failure')
    with pytest.raises(cache.CacheRefused) as info:
        case.adapter.load(expected_binding, fail)
    assert calls == [True]
    assert not isinstance(info.value, cache.CacheMiss)


def test_content_os_read_failure_is_a_hard_refusal(case, monkeypatch):
    binding, _ = case.snapshot()
    case.build(binding)
    raw = case.cache_path.read_bytes()
    start = len(cache._MAGIC) + 4
    length = struct.unpack('<I', raw[len(cache._MAGIC):start])[0]
    expected_binding = json.loads(raw[start:start + length])['binding']
    original_fdopen = cache.os.fdopen

    class Unreadable:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            raise OSError('synthetic content read failure')

    with monkeypatch.context() as patch:
        patch.setattr(cache.os, 'fdopen', lambda fd, mode: Unreadable(original_fdopen(fd, mode)))
        with pytest.raises(cache.CacheRefused, match='read') as info:
            case.adapter.load(expected_binding, lambda *_: True)
    assert not isinstance(info.value, cache.CacheMiss)
    assert case.cache_path.read_bytes() == raw


def test_mismatched_file_size_is_rejected_before_reading_rows(case, monkeypatch):
    binding, _ = case.snapshot()
    case.build(binding)
    raw = case.cache_path.read_bytes()
    start = len(cache._MAGIC) + 4
    length = struct.unpack('<I', raw[len(cache._MAGIC):start])[0]
    expected_binding = json.loads(raw[start:start + length])['binding']
    case.cache_path.write_bytes(raw + b'extra')
    reads, verifications = [], []
    original_fdopen = cache.os.fdopen

    class Tracked:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            reads.append(size)
            return self.stream.read(size)
        def fileno(self):
            return self.stream.fileno()

    with monkeypatch.context() as patch:
        patch.setattr(cache.os, 'fdopen', lambda fd, mode: Tracked(original_fdopen(fd, mode)))
        with pytest.raises(cache.CacheMiss, match='truncated'):
            case.adapter.load(expected_binding, lambda *_: verifications.append(True))
    assert reads == [len(cache._MAGIC) + 4, length]
    assert verifications == []


def test_worker_environment_and_lifecycle_are_cache_inputs():
    assert 'tools-bootstrap/tools/wd_mind_runtime_child.py' in runtime._CACHE_CONTENT_PATHS
    assert 'tools-bootstrap/tools/wd_mind_e5_lifecycle.py' in runtime._CACHE_CONTENT_PATHS
    assert cache._MAX_ROWS == 500000


def test_cache_rows_allow_more_than_65536_screened_vectors():
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    rows = tuple((f'{number:064x}', 'b' * 64, vector) for number in range(65537))
    assert cache._rows(rows) is rows


def test_cache_rows_reject_more_than_500000_screened_vectors():
    # Length must be rejected before inspecting or decoding any row.
    rows = (None,) * 500001
    with pytest.raises(cache.CacheRefused, match='rows'):
        cache._rows(rows)


@pytest.mark.parametrize('changed_path', [
    'tools-bootstrap/tools/wd_mind_runtime_child.py',
    'tools-bootstrap/tools/wd_mind_e5_lifecycle.py',
])
def test_changed_worker_pin_rebuilds_previously_bound_cache(case, monkeypatch, changed_path):
    # Use real pinned bundle reads rather than the cache builder fixture's
    # synthetic constant-content-pins seam. No production code is executed
    # from this synthetic bundle.
    monkeypatch.setattr(runtime, '_cache_content_pins', _REAL_CACHE_CONTENT_PINS)
    first, _ = case.snapshot()
    bundle = first._PrivateBinding__bundle
    bundle.mkdir()
    pins = {}
    for relative in runtime._CACHE_CONTENT_PATHS:
        path = bundle.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == 'wd-mind-runtime-manifest.json':
            value = {'schema': 'wd.q4-runtime-manifest.v1', 'generation': 'a' * 40,
                     'revision': 'b' * 40, 'files': [{'path': 'model/tokenizer.json',
                        'size': 1, 'sha256': '1' * 64}]}
            data = json.dumps(value).encode('utf-8')
        elif relative == 'wd-mind-interpreter-identity.json':
            value = {'schema': 'wd.q4-interpreter-identity.v1', 'files': [
                     {'path': 'python.exe', 'size': 1, 'sha256': '2' * 64, 'role': 'interpreter'}]}
            data = json.dumps(value).encode('utf-8')
        else:
            data = ('synthetic ' + relative).encode('utf-8')
        path.write_bytes(data)
        pins[relative] = hashlib.sha256(data).hexdigest()

    def publish_manifest(binding):
        data = json.dumps({'files': pins}, sort_keys=True).encode('utf-8')
        (bundle / 'deployment-manifest.json').write_bytes(data)
        binding._PrivateBinding__manifest = hashlib.sha256(data).hexdigest()

    publish_manifest(first)
    assert case.build(first).cache_status == 'MISS_STORED'
    assert len(case.passages) == 2
    case.passages.clear()

    # A new manifest with a non-cache-related pin must reuse the old cache.
    pins['synthetic-unrelated-file'] = 'f' * 64
    unrelated, _ = case.snapshot()
    publish_manifest(unrelated)
    assert case.build(unrelated).cache_status == 'HIT_STORED'
    assert case.passages == []

    # Rebind one real vector-affecting code path to new bytes: old vectors
    # must not be reused, even though source, policy and model did not change.
    changed = bundle.joinpath(*changed_path.split('/'))
    new_bytes = changed.read_bytes() + b'\nsynthetic change'
    changed.write_bytes(new_bytes)
    pins[changed_path] = hashlib.sha256(new_bytes).hexdigest()
    rebound, _ = case.snapshot()
    publish_manifest(rebound)
    assert case.build(rebound).cache_status == 'MISS_STORED'
    assert len(case.passages) == 2

    case.passages.clear()
    warm, _ = case.snapshot()
    publish_manifest(warm)
    assert case.build(warm).cache_status == 'HIT_STORED'
    assert case.passages == []
