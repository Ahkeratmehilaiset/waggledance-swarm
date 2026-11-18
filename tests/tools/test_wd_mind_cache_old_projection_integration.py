"""Synthetic integration of old-prefix cache verification after source append.

The only log, roster, policy and records used here are test fixtures. No bridge,
model, installed bundle or persisted content cache is used.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace

import pytest

from tools import wd_mind_full_runtime as runtime
import test_wd_mind_content_query_readonly as fixtures


reader = runtime.reader


@pytest.fixture(autouse=True)
def exact_pinned_synthetic_code(monkeypatch):
    """Test pinned LF bytes and real origin checks in an isolated audit tree."""
    import tools
    import waggledance.core

    checkout = Path(__file__).resolve().parents[2]
    audit = checkout / '.codex-audit'
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mind-old-projection-', dir=audit) as directory:
        root = Path(directory)
        for relative, expected in reader._REQUIRED_CODE.items():
            raw = (checkout / relative).read_bytes()
            normalized = raw.replace(b'\r\n', b'\n')
            data = raw if hashlib.sha256(raw).hexdigest() == expected else normalized
            assert hashlib.sha256(data).hexdigest() == expected, relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == expected

        # The test retains the actual loaded reader's logic; only its root is
        # redirected to an exact copy of its own bytes and pinned dependencies.
        source = Path(reader.__file__).resolve()
        query_copy = root / 'tools' / source.name
        query_copy.write_bytes(source.read_bytes())
        monkeypatch.setattr(reader, '__file__', str(query_copy))

        def load_exact(name, relative, package, attribute):
            path = root / relative
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, name, module)
            spec.loader.exec_module(module)
            monkeypatch.setattr(package, attribute, module, raising=False)

        load_exact('tools.bridge_compact_view', 'tools/bridge_compact_view.py', tools, 'bridge_compact_view')
        load_exact('waggledance.core.bridge_log_reader', 'waggledance/core/bridge_log_reader.py',
                   waggledance.core, 'bridge_log_reader')
        load_exact('tools.wd_mind_content_prefix_readonly', 'tools/wd_mind_content_prefix_readonly.py',
                   tools, 'wd_mind_content_prefix_readonly')
        load_exact('tools.wd_mind_absent_generation_prefix_readonly',
                   'tools/wd_mind_absent_generation_prefix_readonly.py', tools,
                   'wd_mind_absent_generation_prefix_readonly')
        yield


def test_synthetic_code_tree_rejects_tampered_pinned_bytes():
    path = Path(reader.__file__).parent / 'wd_mind_content_prefix_readonly.py'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='dependency_changed'):
        reader._check_code()


def test_synthetic_code_tree_rejects_wrong_loaded_module_origin(monkeypatch):
    module = sys.modules['tools.wd_mind_content_prefix_readonly']
    monkeypatch.setattr(module, '__file__', str(Path(reader.__file__)))
    with pytest.raises(ValueError, match='dependency_origin_mismatch'):
        reader._pinned_dependency('tools.wd_mind_content_prefix_readonly',
                                  'tools/wd_mind_content_prefix_readonly.py',
                                  ('read_prefix_rows',))


def _synthetic_appended_snapshot(tmp_path):
    # More than 256 accepted records catches any accidental projection cap.
    keep = [fixtures._row(f'synthetic allowed build observation {i} complete') for i in range(260)]
    secret = fixtures._row('password=syntheticfixturevalue')
    rco = fixtures._row('synthetic reviewer only', agent='claude-rco-1',
                        agent_uuid=fixtures.ROSTER['claude-rco-1'])
    addressed = fixtures._row('synthetic review addressed', to='claude-rco-2')
    removed = fixtures._row('synthetic removed source observation')
    old_rows = keep + [secret, rco, addressed, removed]
    case = fixtures._all_tasks_case(reader, tmp_path, old_rows)
    policy = json.loads(case['messages']['policy_bytes'])
    policy['removed_source_refs'] = [fixtures._sha(fixtures._raw(removed))]
    policy_bytes = fixtures._raw(policy)
    case['messages']['policy_bytes'] = policy_bytes
    case['messages']['policy_sha256'] = fixtures._sha(policy_bytes)
    fixtures._edit_decision(case, message_policy_sha256=fixtures._sha(policy_bytes))
    old_projection = reader.rederive_complete_message_projection(**case)
    path = Path(case['messages']['source_path'])
    old_bytes = path.read_bytes()
    old_cursor = case['messages']['end_cursor']
    old_sha = fixtures._sha(old_bytes)
    appended = fixtures._row('synthetic newer build observation after append')
    appended_bytes = fixtures._raw(appended) + b'\n'
    with path.open('ab') as stream:
        stream.write(appended_bytes)
    new_cursor = type(old_cursor)(len(old_bytes) + len(appended_bytes),
                                  old_cursor.file_identity, old_cursor.generation)
    case['messages']['end_cursor'] = new_cursor
    fixtures._edit_decision(case, source_prefix={'offset': new_cursor.offset,
        'sha256': fixtures._sha(old_bytes + appended_bytes), 'generation': new_cursor.generation})
    current = reader.rederive_complete_message_projection(**case)
    snapshot = fixtures._snapshot(case, current)
    return snapshot, old_projection, current, old_cursor.offset, old_sha, keep, appended, path


def _cache_fixture(snapshot, old, current, offset, digest):
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    records = old['messages']['records']
    rows = tuple(sorted((*runtime._cache_record_keys(record), vector) for record in records))
    binding = {'prefix_offset': offset, 'prefix_sha256': digest,
               'projection_sha256': runtime._cache_projection_sha256(old)}
    by_id = {record['record_id']: record for record in current['messages']['records']}
    return SimpleNamespace(binding=binding, rows=rows), by_id, vector


def test_real_appended_prefix_rescreens_all_old_records_and_checks_cache(tmp_path):
    snapshot, old, current, offset, digest, keep, appended, _ = _synthetic_appended_snapshot(tmp_path)
    rederived = runtime._cache_old_projection(snapshot, offset, digest)
    assert rederived == old
    old_refs = {record['provenance']['source_event_ref'] for record in rederived['messages']['records']}
    expected = {fixtures._sha(fixtures._raw(row)) for row in keep}
    assert len(old_refs) == 260 and old_refs == expected
    assert fixtures._sha(fixtures._raw(appended)) not in old_refs
    assert len(current['messages']['records']) == 261
    cache, by_id, vector = _cache_fixture(snapshot, old, current, offset, digest)
    reusable = runtime._cache_verified_reusable(snapshot, current, by_id, cache)
    assert set(reusable) == {record['record_id'] for record in old['messages']['records']}
    assert set(reusable.values()) == {vector}


@pytest.mark.parametrize('fault', ['prefix_digest', 'prefix_bytes', 'projection_digest',
                                   'missing_record', 'extra_record', 'changed_policy'])
def test_old_prefix_cache_fails_closed_on_mismatch(tmp_path, fault):
    snapshot, old, current, offset, digest, _, _, path = _synthetic_appended_snapshot(tmp_path)
    cache, by_id, _ = _cache_fixture(snapshot, old, current, offset, digest)
    if fault == 'prefix_digest':
        cache.binding['prefix_sha256'] = '0' * 64
    elif fault == 'prefix_bytes':
        with path.open('r+b') as stream:
            stream.seek(1)
            original = stream.read(1)
            stream.seek(1)
            stream.write(b'X' if original != b'X' else b'Y')
    elif fault == 'projection_digest':
        cache.binding['projection_sha256'] = '0' * 64
    elif fault == 'missing_record':
        cache.rows = cache.rows[:-1]
    elif fault == 'extra_record':
        cache.rows = cache.rows + (cache.rows[-1],)
    else:
        # Change an externally pinned policy without changing its expected hash.
        snapshot = copy.deepcopy(snapshot)
        snapshot['messages']['policy_bytes'] += b' '
    with pytest.raises((ValueError, reader.ContentQueryRefused)):
        runtime._cache_verified_reusable(snapshot, current, by_id, cache)
