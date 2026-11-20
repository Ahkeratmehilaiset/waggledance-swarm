"""Synthetic cross-bundle cache identity: real content pin list, no live data.

The reused fixture runs each bundle in a separate process and replaces only
private cache ACL/OS handle operations. It does not prove production ACLs.
"""
import ast
import hashlib
import json

import test_wd_mind_pinned_cache_integration as pinned


RUNTIME = 'tools-bootstrap/tools/wd_mind_full_runtime.py'


def _audit_root(tmp_path):
    root = tmp_path / '.codex-audit'
    root.mkdir()
    return root


def _cache(root):
    path = root / 'synthetic-private-cache'
    path.mkdir()
    return path


def test_pinned_cache_survives_comment_only_runtime_change(tmp_path):
    root = _audit_root(tmp_path)
    base, base_pin, other, _ = pinned._synthetic_pair(root, 'unrelated')
    # Remove the fixture's unrelated note so the only file delta is runtime.
    manifest_path = other / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'].pop('synthetic-independent-note.txt')
    (other / 'synthetic-independent-note.txt').unlink()
    runtime_path = other.joinpath(*RUNTIME.split('/'))
    before = runtime_path.read_bytes()
    after = before + b'\n# synthetic non-executable cache-identity regression\n'
    assert ast.dump(ast.parse(before), include_attributes=False) == ast.dump(
        ast.parse(after), include_attributes=False)
    runtime_path.write_bytes(after)
    manifest['files'][RUNTIME] = hashlib.sha256(after).hexdigest().upper()
    raw_manifest = pinned._write_json(manifest_path, manifest)
    other_pin = hashlib.sha256(raw_manifest).hexdigest()

    base_manifest = json.loads((base / 'deployment-manifest.json').read_bytes())
    assert set(manifest['files']) == set(base_manifest['files'])
    assert [name for name in manifest['files']
            if manifest['files'][name] != base_manifest['files'][name]] == [RUNTIME]
    assert base_pin != other_pin
    cache = _cache(root)
    cold = pinned._run(base, base_pin, cache)
    warm = pinned._run(other, other_pin, cache)
    assert cold['status'] == 'MISS_STORED' and cold['passages'] == cold['records'] > 0
    assert warm['before'] == cold['after']
    assert warm['status'] == 'HIT_STORED'
    assert warm['passages'] == 0
    assert warm['binding'] == cold['binding']
    assert warm['vectors_sha256'] == cold['vectors_sha256']


def test_pinned_cache_model_change_requires_rebuild(tmp_path):
    root = _audit_root(tmp_path)
    base, base_pin, other, other_pin = pinned._synthetic_pair(root, 'model')
    cache = _cache(root)
    cold = pinned._run(base, base_pin, cache)
    changed = pinned._run(other, other_pin, cache)
    assert cold['status'] == 'MISS_STORED' and cold['passages'] == cold['records'] > 0
    assert changed['before'] == cold['after']
    assert changed['status'] == 'MISS_STORED'
    assert changed['passages'] == changed['records'] > 0
    assert changed['binding']['model_sha256'] != cold['binding']['model_sha256']
