"""Synthetic immutable builder/cache integration; no live inputs or model.

Actual cache codec, full reader and snapshot watcher run. Windows ACL/handle
operations and deployment binding are replaced only at explicit test seams;
this does not establish production ACL, bundle pin, or process isolation.
"""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_vector_cache as cache
from tools import wd_mind_content_snapshot_package as package
import test_wd_mind_content_query_readonly as fixtures


reader = runtime.reader
VECTOR = struct.pack('<384f', 1.0, *([0.0] * 383))


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    assert '.codex-audit' in str(tmp_path).lower()
    source_dir = tmp_path / 'synthetic-source'
    source_dir.mkdir()
    cache_root = tmp_path / 'synthetic-cache'
    cache_root.mkdir()
    cache_path = cache_root / 'vectors.bin'
    removed = fixtures._row('synthetic removed build record')
    rows = [fixtures._row('synthetic allowed alpha work'),
            fixtures._row('synthetic allowed beta work'),
            fixtures._row('password=syntheticfixturevalue'),
            fixtures._row('synthetic private review', agent='claude-rco-1',
                          agent_uuid=fixtures.ROSTER['claude-rco-1']), removed]
    case = fixtures._all_tasks_case(reader, source_dir, rows)
    policy = json.loads(case['messages']['policy_bytes'])
    policy['removed_source_refs'] = [fixtures._sha(fixtures._raw(removed))]
    case['messages']['policy_bytes'] = fixtures._raw(policy)
    case['messages']['policy_sha256'] = fixtures._sha(case['messages']['policy_bytes'])
    fixtures._edit_decision(case, message_policy_sha256=case['messages']['policy_sha256'])
    source = Path(case['messages']['source_path'])

    def snapshot():
        raw = source.read_bytes()
        old = case['messages']['end_cursor']
        cursor = type(old)(len(raw), old.file_identity, old.generation)
        case['messages']['end_cursor'] = cursor
        digest = fixtures._sha(raw)
        fixtures._edit_decision(case, source_prefix={'offset': cursor.offset,
                                'sha256': digest, 'generation': cursor.generation})
        projection = reader.rederive_complete_message_projection(**case)
        frozen = fixtures._snapshot(case, projection)
        args = {'cursor': cursor, 'expected_prefix_sha256': digest}
        controls = (b'synthetic-fleet', case['roster_bytes'], case['messages']['policy_bytes'])
        binding = runtime._PrivateBinding(frozen, tmp_path / 'synthetic-bundle', 'a'*64,
                                          controls, args, 'b'*64)
        return binding, projection

    # No real deployment manifest is read: emulate only bundle binding while
    # retaining real source re-derivation and watcher integrity checks.
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query',
                        lambda self: self._PrivateBinding__snapshot)
    monkeypatch.setattr(runtime._PrivateBinding, '_snapshot_support', lambda self: package)
    monkeypatch.setattr(runtime._PrivateBinding, '_immutable_checkpoint',
                        lambda self, frozen: reader._verified_complete(frozen))
    monkeypatch.setattr(runtime, '_cache_content_pins', lambda binding: {'synthetic-code': 'd'*64})

    def checked_path(path):
        assert Path(path) == cache_path
        return cache_path
    def checked_directory(path):
        assert Path(path) == cache_root
        return nullcontext(SimpleNamespace(native=None))
    def checked_lock(directory, path):
        assert Path(path) == cache_path
        return nullcontext()
    def synthetic_fd(native, path, disposition, access, sharing, flags):
        path = Path(path)
        assert path.parent == cache_root
        assert path.name == 'vectors.bin' or (path.name.startswith('vectors.bin.') and path.name.endswith('.tmp'))
        if disposition == 1:
            flags |= os.O_CREAT | os.O_EXCL
        return os.open(path, flags | getattr(os, 'O_BINARY', 0), 0o600)
    monkeypatch.setattr(cache, '_plain_path', checked_path)
    monkeypatch.setattr(cache, '_Directory', checked_directory)
    monkeypatch.setattr(cache, '_WriterLock', checked_lock)
    monkeypatch.setattr(cache, '_fd', synthetic_fd)
    checks = []
    adapter = runtime._PrivateVectorCache(cache, cache_path, lambda: checks.append('synthetic-pin'))
    encoder = fixtures._raw({'schema':'wd.mind-readonly-encoder-binding.v1',
        'encoder':{'model':'intfloat/multilingual-e5-small', 'revision':'synthetic-revision', 'weights_sha256':'e'*64},
        'code_sha256':'f'*64, 'query_policy':{'prefix':'query: ', 'truncate_chars':2000,
        'normalize':True, 'dtype':'float32', 'dim':384}})
    passages = []
    def encode(values):
        passages.extend(values)
        return VECTOR * len(values)
    def build(binding):
        return runtime._build_flat_memory_immutable(binding, encode, encoder, fixtures._sha(encoder),
                                                     5, None, vector_cache=adapter)
    return SimpleNamespace(case=case, source=source, cache_path=cache_path, snapshot=snapshot,
                           passages=passages, checks=checks, build=build)


def test_cold_warm_and_append_use_only_screened_vectors(synthetic):
    cold_binding, cold_projection = synthetic.snapshot()
    cold = synthetic.build(cold_binding)
    assert cold.cache_status == 'MISS_STORED'
    assert set(synthetic.passages) == {'passage: synthetic allowed alpha work', 'passage: synthetic allowed beta work'}
    assert len(cold_projection['messages']['records']) == 2
    cache_bytes = synthetic.cache_path.read_bytes()
    assert all(text not in cache_bytes for text in
               (b'password=', b'synthetic allowed', b'synthetic private', b'synthetic removed'))
    synthetic.passages.clear()
    warm_binding, _ = synthetic.snapshot()
    warm = synthetic.build(warm_binding)
    assert warm_binding is not cold_binding and warm.cache_status == 'HIT_STORED'
    assert synthetic.passages == [] and warm.ids == cold.ids and warm.vectors == cold.vectors
    new_row = fixtures._row('synthetic allowed appended work')
    with synthetic.source.open('ab') as stream:
        stream.write(fixtures._raw(new_row) + b'\n')
    new_binding, new_projection = synthetic.snapshot()
    newer = synthetic.build(new_binding)
    assert newer.cache_status == 'HIT_STORED'
    assert synthetic.passages == ['passage: synthetic allowed appended work']
    assert len(new_projection['messages']['records']) == 3 and len(newer.ids) == 3
    assert synthetic.checks


@pytest.mark.parametrize('fault', ['missing_cache', 'changed_cache', 'changed_prefix'])
def test_invalid_cache_or_changed_prefix_reencodes_entire_screened_set(synthetic, fault):
    binding, _ = synthetic.snapshot()
    assert synthetic.build(binding).cache_status == 'MISS_STORED'
    synthetic.passages.clear()
    if fault == 'missing_cache':
        synthetic.cache_path.unlink()
    elif fault == 'changed_cache':
        raw = bytearray(synthetic.cache_path.read_bytes())
        raw[-1] ^= 1
        synthetic.cache_path.write_bytes(raw)
    else:
        raw = synthetic.source.read_bytes()
        assert b'alpha work' in raw
        synthetic.source.write_bytes(raw.replace(b'alpha work', b'gamma work', 1))
    changed_binding, projection = synthetic.snapshot()
    rebuilt = synthetic.build(changed_binding)
    assert rebuilt.cache_status == 'MISS_STORED'
    assert len(projection['messages']['records']) == 2 and len(synthetic.passages) == 2
    assert all('password' not in text and 'private review' not in text and 'removed' not in text
               for text in synthetic.passages)
