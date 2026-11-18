"""Pinned synthetic bundle/cache integration in sequential OS processes.

Real manifest loading, source binding, projection and cache fingerprinting run
from each synthetic bundle. Only private cache ACL/OS handle I/O is doubled;
no real model, installed bundle, live bridge or production cache is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from wd_mind_owned_compat import run_owned
import test_wd_mind_full_runtime as fixtures


ROOT = Path(__file__).resolve().parents[2]
MODEL = 'model/model.safetensors'
TOKENIZER = 'model/tokenizer.json'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_json(path, value):
    raw = fixtures.canonical(value)
    path.write_bytes(raw)
    return raw


def test_compatibility_descriptor_is_closed_and_unpopulated_by_default():
    from tools import wd_mind_full_runtime as runtime
    source = ROOT / 'ops/windows/reboot/wd-mind-cache-compatibility.json'
    assert runtime._parse_cache_compatibility(source.read_bytes()) == ()
    fields = ('old_model_sha256', 'old_projection_code_sha256',
              'new_model_sha256', 'new_projection_code_sha256')
    pair = dict(zip(fields, ('1' * 64, '2' * 64, '3' * 64, '4' * 64)))
    pair['old_encoder_binding_sha256'] = '5' * 64
    pair['old_content_pins'] = {path:'6' * 64 for path in runtime._CACHE_CONTENT_PATHS}
    document = {'schema':'wd.mind-cache-compatibility.v1', 'enabled':True, 'pairs':[pair]}
    assert runtime._parse_cache_compatibility(fixtures.canonical(document)) == (
        tuple(pair[k] for k in fields) + (pair['old_encoder_binding_sha256'],
            tuple(pair['old_content_pins'][path] for path in runtime._CACHE_CONTENT_PATHS)),)
    for invalid in (
        dict(document, enabled=False),
        dict(document, pairs=[pair, pair]),
        dict(document, pairs=[dict(pair, unexpected='5' * 64)]),
        dict(document, pairs=[dict(pair, old_model_sha256='A' * 64)]),
        dict(document, extra=True),
    ):
        with pytest.raises(ValueError):
            runtime._parse_cache_compatibility(fixtures.canonical(invalid))
    with pytest.raises(ValueError):
        runtime._parse_cache_compatibility(b'{"schema":"wd.mind-cache-compatibility.v1","enabled":false,"enabled":true,"pairs":[]}')


def _synthetic_pair(tmp_path, change):
    case = fixtures.synthetic_bundle.__wrapped__(tmp_path)
    base, source_root = case['bundle'], case['model_root']
    cache_file = base / 'tools-bootstrap' / 'tools' / 'wd_mind_vector_cache.py'
    cache_file.write_bytes((ROOT / 'tools' / 'wd_mind_vector_cache.py').read_bytes())
    rotating_file = base / 'tools-bootstrap' / 'tools' / 'wd_mind_e5_rotating_client.py'
    rotating_file.write_bytes((ROOT / 'tools' / 'wd_mind_e5_rotating_client.py').read_bytes())
    model_tokenizer = source_root.joinpath(*TOKENIZER.split('/'))
    model_tokenizer.write_bytes(b'synthetic-tokenizer-v1')
    descriptor_path = base / 'wd-mind-runtime-manifest.json'
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor['files'].append({'path':TOKENIZER, 'size':model_tokenizer.stat().st_size,
                                'sha256':_sha(model_tokenizer.read_bytes()).upper()})
    descriptor_raw = _write_json(descriptor_path, descriptor)
    location_path = base / 'wd-mind-runtime-location.json'
    location = json.loads(location_path.read_bytes())
    location['runtime_manifest_sha256'] = _sha(descriptor_raw)
    _write_json(location_path, location)

    def finalize(bundle, extra=False):
        manifest_path = bundle / 'deployment-manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['tools-bootstrap/tools/wd_mind_vector_cache.py'] = _sha(cache_file.read_bytes())
        manifest['files']['tools-bootstrap/tools/wd_mind_e5_rotating_client.py'] = _sha(rotating_file.read_bytes())
        for relative in ('wd-mind-runtime-manifest.json', 'wd-mind-runtime-location.json',
                         'wd-mind-e5-encoder-binding.json'):
            manifest['files'][relative] = _sha((bundle / relative).read_bytes())
        if extra:
            note = bundle / 'synthetic-independent-note.txt'
            note.write_bytes(b'synthetic independent bundle difference')
            manifest['files'][note.name] = _sha(note.read_bytes())
        manifest['files'] = {key: value.upper() for key, value in manifest['files'].items()}
        raw = _write_json(manifest_path, manifest)
        assert all(value == value.upper() for value in manifest['files'].values())
        return _sha(raw)

    base_pin = finalize(base)
    other = tmp_path / 'synthetic-other-bundle'
    shutil.copytree(base, other)
    if change == 'lifecycle':
        child = other / 'tools-bootstrap/tools/wd_mind_runtime_child.py'
        child.write_bytes(child.read_bytes() + b'\n# synthetic non-vector lifecycle change\n')
        manifest_path = other / 'deployment-manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['tools-bootstrap/tools/wd_mind_runtime_child.py'] = _sha(child.read_bytes()).upper()
        _write_json(manifest_path, manifest)
    if change in ('model', 'tokenizer'):
        asset_root = tmp_path / 'synthetic-other-assets'
        shutil.copytree(source_root, asset_root)
        relative = MODEL if change == 'model' else TOKENIZER
        asset = asset_root.joinpath(*relative.split('/'))
        asset.write_bytes(b'synthetic-changed-' + change.encode('ascii'))
        other_descriptor_path = other / 'wd-mind-runtime-manifest.json'
        other_descriptor = json.loads(other_descriptor_path.read_bytes())
        entry = next(row for row in other_descriptor['files'] if row['path'] == relative)
        entry.update(size=asset.stat().st_size, sha256=_sha(asset.read_bytes()).upper())
        other_descriptor_raw = _write_json(other_descriptor_path, other_descriptor)
        other_location_path = other / 'wd-mind-runtime-location.json'
        other_location = json.loads(other_location_path.read_bytes())
        other_location.update(runtime_root=str(asset_root), runtime_manifest_sha256=_sha(other_descriptor_raw))
        if change == 'model':
            e5_path = other / 'wd-mind-e5-encoder-binding.json'
            e5 = json.loads(e5_path.read_bytes())
            e5['encoder']['weights_sha256'] = _sha(asset.read_bytes())
            e5_raw = _write_json(e5_path, e5)
            other_location['encoder_binding_sha256'] = _sha(e5_raw)
        _write_json(other_location_path, other_location)
    other_pin = finalize(other, extra=True)
    assert base_pin != other_pin
    return base, base_pin, other, other_pin


ACTOR = r'''
import hashlib, json, os, pathlib, struct, sys
from contextlib import nullcontext
from types import SimpleNamespace
bundle, pin, cache_root = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
if (not bundle.is_absolute() or not cache_root.is_absolute()
        or '.codex-audit' not in str(bundle).lower()
        or '.codex-audit' not in str(cache_root).lower()):
    raise RuntimeError('synthetic audit paths required')
sys.path.insert(0, str(bundle / 'tools-bootstrap'))
from tools import wd_mind_full_runtime as runtime
if sys.argv[4] == 'synthetic-enable':
    runtime._CACHE_COMPATIBILITY_PROTOTYPE_ENABLED = True
policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
binding = runtime.assemble_full_runtime(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
    identifier_policy_bytes=policy, expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest())
provider = runtime._load_pinned_vector_cache(binding)
module = provider._module
assert pathlib.Path(module.__file__) == bundle / 'tools-bootstrap' / 'tools' / 'wd_mind_vector_cache.py'
cache_path = cache_root / 'vectors.bin'
provider._path = str(cache_path)
module._CACHE_ROOT = cache_root
def checked_directory(path):
    assert pathlib.Path(path) == cache_root
    return nullcontext(SimpleNamespace(native=None))
def checked_lock(directory, path):
    assert pathlib.Path(path) == cache_path
    return nullcontext()
def checked_fd(native, path, disposition, access, sharing, flags, *, missing_ok=False):
    path = pathlib.Path(path)
    assert path.parent == cache_root
    assert path.name == 'vectors.bin' or (path.name.startswith('vectors.bin.') and path.name.endswith('.tmp'))
    if missing_ok and not path.exists(): return None
    if disposition == 1: flags |= os.O_CREAT | os.O_EXCL
    return os.open(path, flags | getattr(os, 'O_BINARY', 0), 0o600)
module._Directory, module._WriterLock, module._fd = checked_directory, checked_lock, checked_fd
encoder_source = json.loads((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes())
encoder = runtime.reader._canonical({'schema':'wd.mind-readonly-encoder-binding.v1',
    'encoder':encoder_source['encoder'], 'code_sha256':encoder_source['code_sha256'],
    'query_policy':encoder_source['query_policy']})
frozen = binding.snapshot_for_query()
projection, _, _ = runtime.reader._verified_complete(frozen)
cache_binding = runtime._cache_snapshot_binding(binding, frozen, projection, encoder,
                                                 hashlib.sha256(encoder).hexdigest())
calls = []
def passage(values):
    calls.extend(values)
    return struct.pack('<384f', 1.0, *([0.0]*383)) * len(values)
before = hashlib.sha256(cache_path.read_bytes()).hexdigest() if cache_path.exists() else None
memory = runtime._build_flat_memory_immutable(binding, passage, encoder, hashlib.sha256(encoder).hexdigest(),
                                              5, None, vector_cache=provider)
after = hashlib.sha256(cache_path.read_bytes()).hexdigest()
print(json.dumps({'pid':os.getpid(), 'status':memory.cache_status, 'passages':len(calls),
    'records':len(memory.ids), 'before':before, 'after':after, 'binding':cache_binding,
    'content_pins':runtime._cache_content_pins(binding), 'encoder_sha256':hashlib.sha256(encoder).hexdigest(),
    'vectors_sha256':hashlib.sha256(memory.vectors).hexdigest(),
    'manifest_uppercase':all(v == v.upper() for v in json.loads((bundle/'deployment-manifest.json').read_bytes())['files'].values())},
    sort_keys=True), flush=True)
'''


def _run(bundle, pin, cache_root, *, enable_compat=False):
    allowed = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'PATH', 'PATHEXT',
               'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}
    env = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    # Resolve and constrain both inputs before any child can be spawned. This
    # is a synthetic path guard, not proof of race-free physical isolation.
    bundle_path, cache_path = Path(bundle).resolve(strict=True), Path(cache_root).resolve(strict=True)
    bundle_audit = next((path for path in bundle_path.parents if path.name == '.codex-audit'), None)
    cache_audit = next((path for path in cache_path.parents if path.name == '.codex-audit'), None)
    if (bundle_audit is None or bundle_audit != cache_audit
            or bundle_path == cache_path or bundle_path in cache_path.parents or cache_path in bundle_path.parents):
        raise ValueError('synthetic_audit_paths_required')
    # Empty input closes the owned stdin pipe; interruption or timeout retains
    # the child through exit and both output EOFs, without killing it.
    proc = run_owned([sys.executable, '-I', '-B', '-c', ACTOR, str(bundle_path), pin, str(cache_path),
                      'synthetic-enable' if enable_compat else 'disabled'],
        cwd=bundle_path, env=env, input='', capture_output=True, timeout=90,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)



@pytest.mark.parametrize('deadline', [False, True], ids=['success', 'deadline_owned_drain'])
def test_pinned_driver_owns_synthetic_child_until_eof(monkeypatch, tmp_path, deadline):
    import wd_mind_owned_compat as owned
    audit_root = tmp_path / '.codex-audit'
    audit_root.mkdir()
    bundle, cache_root = audit_root / 'bundle', audit_root / 'cache'
    bundle.mkdir()
    cache_root.mkdir()
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'must-not-propagate')
    events = []
    class Process:
        returncode = 0
        def communicate(self, input=None, timeout=None):
            events.append(('communicate', input, timeout))
            if deadline and len(events) == 2:
                raise subprocess.TimeoutExpired('synthetic', 90)
            return json.dumps({'result':'synthetic'}), ''
    def spawn(argv, **kwargs):
        events.append(('spawn', argv, kwargs))
        assert kwargs['stdin'] is subprocess.PIPE
        assert kwargs['stdout'] is subprocess.PIPE and kwargs['stderr'] is subprocess.PIPE
        assert kwargs['cwd'] == bundle.resolve()
        assert 'WD_REBOOT_EXPECTED_MANIFEST_HASH' not in kwargs['env']
        return Process()
    monkeypatch.setattr(owned.subprocess, 'Popen', spawn)
    monkeypatch.setattr(owned.time, 'sleep', lambda _seconds: None)
    if deadline:
        with pytest.raises(AssertionError, match='late output discarded'):
            _run(bundle, 'a' * 64, cache_root)
        assert len(events) == 3 and events[2][0] == 'communicate'
    else:
        assert _run(bundle, 'a' * 64, cache_root) == {'result':'synthetic'}
        assert len(events) == 2
    assert events[1][0] == 'communicate' and events[1][1] == ''


def test_pinned_driver_rejects_non_audit_paths_before_spawn(monkeypatch, tmp_path):
    import wd_mind_owned_compat as owned
    audit_root = tmp_path / '.codex-audit'
    audit_root.mkdir()
    audit_cache = audit_root / 'cache'
    audit_cache.mkdir()
    monkeypatch.setattr(owned.subprocess, 'Popen', lambda *_a, **_kw: pytest.fail('unexpected spawn'))
    with pytest.raises(ValueError, match='synthetic_audit_paths_required'):
        _run(ROOT, 'a' * 64, audit_cache)
    with pytest.raises(ValueError, match='synthetic_audit_paths_required'):
        _run(audit_cache, 'a' * 64, audit_cache)
    nested_cache = audit_cache / 'nested'
    nested_cache.mkdir()
    with pytest.raises(ValueError, match='synthetic_audit_paths_required'):
        _run(audit_cache, 'a' * 64, nested_cache)
    different_audit_cache = tmp_path / 'other' / '.codex-audit' / 'cache'
    different_audit_cache.mkdir(parents=True)
    with pytest.raises(ValueError, match='synthetic_audit_paths_required'):
        _run(audit_cache, 'a' * 64, different_audit_cache)


@pytest.mark.parametrize('change,expected', [('unrelated','HIT_STORED'),
                                             ('model','MISS_STORED'), ('tokenizer','MISS_STORED')])
def test_pinned_two_process_cache_binding_and_installer_uppercase(tmp_path, change, expected):
    tmp_path = tmp_path / '.codex-audit'
    tmp_path.mkdir()
    assert '.codex-audit' in str(tmp_path).lower()
    base, base_pin, other, other_pin = _synthetic_pair(tmp_path, change)
    cache_root = tmp_path / 'synthetic-private-cache'
    cache_root.mkdir()
    cold = _run(base, base_pin, cache_root)
    second = _run(other, other_pin, cache_root)
    assert cold['pid'] != second['pid']
    assert cold['manifest_uppercase'] and second['manifest_uppercase']
    assert cold['status'] == 'MISS_STORED' and cold['before'] is None
    assert cold['passages'] == cold['records'] and cold['records'] > 0
    assert second['before'] == cold['after'] and second['status'] == expected
    assert second['records'] == cold['records']
    if change == 'unrelated':
        assert second['passages'] == 0
        assert second['binding'] == cold['binding'] and second['after'] == cold['after']
        assert second['vectors_sha256'] == cold['vectors_sha256']
    else:
        assert second['passages'] == second['records']
        assert second['binding']['model_sha256'] != cold['binding']['model_sha256']


@pytest.mark.parametrize('change', ['lifecycle', 'model', 'tokenizer', 'wrong_worker_pin',
                                    'wrong_old_aggregate', 'wrong_new_aggregate', 'wrong_encoder'])
def test_exact_manifest_pinned_pair_reuses_only_unchanged_synthetic_model(tmp_path, change):
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    base, base_pin, other, other_pin = _synthetic_pair(audit, change if change in ('model', 'tokenizer') else 'lifecycle')
    old_cache, probe_cache = audit / 'old-cache', audit / 'probe-cache'
    old_cache.mkdir()
    probe_cache.mkdir()
    old = _run(base, base_pin, old_cache)
    probe = _run(other, other_pin, probe_cache)
    if change == 'lifecycle':
        miss_cache = audit / 'missing-approval-cache'
        shutil.copytree(old_cache, miss_cache)
        no_pair = _run(other, other_pin, miss_cache)
        assert no_pair['status'] == 'MISS_STORED' and no_pair['passages'] == no_pair['records']
    keys = ('model_sha256', 'projection_code_sha256')
    assert tuple(old['binding'][key] for key in keys) != tuple(probe['binding'][key] for key in keys)
    pair = {'old_model_sha256':old['binding'][keys[0]], 'old_projection_code_sha256':old['binding'][keys[1]],
            'new_model_sha256':probe['binding'][keys[0]], 'new_projection_code_sha256':probe['binding'][keys[1]],
            'old_encoder_binding_sha256':old['encoder_sha256'], 'old_content_pins':old['content_pins']}
    if change == 'wrong_worker_pin':
        pair['old_content_pins']['tools-bootstrap/tools/wd_mind_e5_worker.py'] = '0' * 64
    elif change == 'wrong_old_aggregate':
        pair['old_projection_code_sha256'] = '0' * 64
    elif change == 'wrong_new_aggregate':
        pair['new_projection_code_sha256'] = '0' * 64
    elif change == 'wrong_encoder':
        pair['old_encoder_binding_sha256'] = '0' * 64
    descriptor = other / 'wd-mind-cache-compatibility.json'
    raw = _write_json(descriptor, {'schema':'wd.mind-cache-compatibility.v1', 'enabled':True, 'pairs':[pair]})
    manifest_path = other / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][descriptor.name] = _sha(raw).upper()
    pin = _sha(_write_json(manifest_path, manifest))
    # The shipping prototype cannot activate a real mapping merely because
    # a descriptor is present. Only this isolated synthetic actor flips it.
    with pytest.raises(AssertionError):
        _run(other, pin, old_cache)
    if change != 'lifecycle':
        with pytest.raises(AssertionError):
            _run(other, pin, old_cache, enable_compat=True)
        return
    loaded = _run(other, pin, old_cache, enable_compat=True)
    assert loaded['status'] == 'HIT_STORED' and loaded['passages'] == 0
    assert loaded['binding'] == probe['binding']
    assert loaded['vectors_sha256'] == old['vectors_sha256']
    assert loaded['before'] == old['after'] and loaded['after'] != old['after']
    source = audit / 'runtime' / 'shared' / 'events.jsonl'
    new_record = json.loads(source.read_bytes().splitlines()[0])
    new_record['message'] = 'new synthetic work after compatible bundle'
    with source.open('ab') as stream:
        stream.write(fixtures.canonical(new_record) + b'\n')
    appended = _run(other, pin, old_cache, enable_compat=True)
    assert appended['status'] == 'HIT_STORED' and appended['passages'] == 1
    assert appended['records'] == loaded['records'] + 1
    descriptor.write_bytes(raw + b' ')
    with pytest.raises(AssertionError):
        _run(other, pin, old_cache, enable_compat=True)
