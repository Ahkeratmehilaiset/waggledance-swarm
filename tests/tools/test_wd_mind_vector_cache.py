"""Synthetic-only cache checks; pytest basetemp must be inside .codex-audit."""
import math
import json
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tools import wd_mind_vector_cache as cache


def binding():
    return {"source_identity_sha256": "1" * 64, "source_generation_sha256": "2" * 64,
            "prefix_offset": 100, "prefix_sha256": "3" * 64, "model_sha256": "4" * 64,
            "policy_sha256": "5" * 64, "projection_code_sha256": "6" * 64,
            "projection_sha256": "7" * 64}


def rows():
    return (("a" * 64, "b" * 64, struct.pack("<384f", 1.0, *([0.0] * 383))),
            ("c" * 64, "d" * 64, struct.pack("<384f", 0.0, 1.0, *([0.0] * 382))))


@pytest.fixture
def private_dir(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("native Windows ACL and directory leases")
    assert ".codex-audit" in str(tmp_path).lower()
    user = subprocess.check_output(["whoami"], text=True).strip()
    target = tmp_path / "synthetic-vector-cache"
    target.mkdir()
    for directory in (tmp_path, target):
        result = subprocess.run(["icacls", str(directory), "/setowner", user], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        result = subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r",
                                 user + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        result = subprocess.run(["icacls", str(directory), "/remove:g", "*S-1-5-32-544", "*S-1-3-4"],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    monkeypatch.setattr(cache, "_CACHE_ROOT", target)
    return target


def test_round_trip_and_raw_text_absent(private_dir):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    result = cache.load_cache(path, binding(), lambda offset, digest: offset == 100 and digest == "3" * 64)
    assert result.rows == rows() and dict(result.binding) == binding()
    assert result.record_set_sha256 == hashlib.sha256(bytes.fromhex("a" * 64 + "c" * 64)).hexdigest()
    with pytest.raises(TypeError):
        result.binding["prefix_offset"] = 999
    raw = path.read_bytes()
    assert b"not included raw sample text" not in raw
    assert b"stored_text" not in raw and b"passage: " not in raw
    header_len = struct.unpack("<I", raw[len(cache._MAGIC):len(cache._MAGIC) + 4])[0]
    header_end = len(cache._MAGIC) + 4 + header_len
    assert raw[header_end:] == b"".join(cache._encoded(rows()))
    assert json.loads(raw[len(cache._MAGIC) + 4:header_end]) == cache._header(binding(), rows())


def test_preflight_missing_only_inside_valid_private_root_and_then_present(private_dir):
    path = private_dir / "vectors.bin"
    assert cache.preflight(path) == "MISSING"
    assert not path.exists() and (private_dir / "vectors.bin.lock").exists()
    cache.write_cache(path, binding(), rows())
    assert cache.preflight(path) == "PRESENT"


def test_missing_and_stale_cache_are_typed_misses(private_dir):
    path = private_dir / "vectors.bin"
    with pytest.raises(cache.CacheMiss, match="missing"):
        cache.load_cache(path, binding(), lambda *_: True)
    cache.write_cache(path, binding(), rows())
    changed = dict(binding(), policy_sha256="a" * 64)
    with pytest.raises(cache.CacheMiss, match="binding_changed"):
        cache.load_cache(path, changed, lambda *_: True)
    with pytest.raises(cache.CacheMiss, match="prefix_changed"):
        cache.load_cache(path, binding(), lambda *_: False)


@pytest.fixture
def relative_dir(private_dir, monkeypatch):
    # Relative helpers remain gated off in every non-synthetic invocation.
    monkeypatch.setattr(cache, '_AUDIT_NAMESPACE_PROVEN', True)
    return private_dir


def test_audit_relative_default_off_refuses_before_native_io(monkeypatch):
    monkeypatch.setattr(cache, '_AUDIT_NAMESPACE_PROVEN', False)
    # An uninitialized object has no native handles or kernel library. A
    # native access before the guard would fail instead of returning refusal.
    native = object.__new__(cache._Native)
    with pytest.raises(cache.CacheRefused, match='audit_namespace_unverified'):
        native.open_relative(None, 'vectors.bin', 3, 0x80000000, 1)
    with pytest.raises(cache.CacheRefused, match='audit_namespace_unverified'):
        native.rename_relative(None, None, 'vectors.bin')


def test_audit_relative_lock_excludes_second_handle_until_release(relative_dir):
    import msvcrt
    with cache._Directory(relative_dir) as directory:
        native, parent = directory.native, directory.handles[-1]
        first = native.open_relative(parent, 'vectors.bin.lock', 1, 0xC0000000, 3)
        first_fd = msvcrt.open_osfhandle(first, os.O_RDWR | os.O_BINARY)
        try:
            os.write(first_fd, b'\0')
            second = native.open_relative(parent, 'vectors.bin.lock', 3, 0xC0000000, 3)
            second_fd = msvcrt.open_osfhandle(second, os.O_RDWR | os.O_BINARY)
            try:
                os.lseek(first_fd, 0, os.SEEK_SET)
                msvcrt.locking(first_fd, msvcrt.LK_NBLCK, 1)
                try:
                    os.lseek(second_fd, 0, os.SEEK_SET)
                    with pytest.raises(OSError):
                        msvcrt.locking(second_fd, msvcrt.LK_NBLCK, 1)
                finally:
                    os.lseek(first_fd, 0, os.SEEK_SET)
                    msvcrt.locking(first_fd, msvcrt.LK_UNLCK, 1)
                os.lseek(second_fd, 0, os.SEEK_SET)
                msvcrt.locking(second_fd, msvcrt.LK_NBLCK, 1)
                os.lseek(second_fd, 0, os.SEEK_SET)
                msvcrt.locking(second_fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(second_fd)
        finally:
            os.close(first_fd)


def test_audit_relative_open_create_and_existing_collision(relative_dir):
    import msvcrt
    with cache._Directory(relative_dir) as directory:
        native, parent = directory.native, directory.handles[-1]
        assert native.open_relative(parent, 'vectors.bin', 3, 0x80000000, 1, missing_ok=True) is None
        handle = native.open_relative(parent, 'vectors.bin', 1, 0xC0000000, 3)
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        try:
            os.write(fd, b'synthetic-vector')
        finally:
            os.close(fd)
        with pytest.raises(cache.CacheRefused):
            native.open_relative(parent, 'vectors.bin', 1, 0xC0000000, 3)
        handle = native.open_relative(parent, 'vectors.bin', 3, 0x80000000, 1)
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        try:
            assert os.read(fd, 128) == b'synthetic-vector'
        finally:
            os.close(fd)


@pytest.mark.parametrize('name', ['', '.', '..', r'..\vectors.bin', r'.\vectors.bin',
                                 'vectors.bin:stream', 'vectors.bin.', 'vectors.bin ',
                                 'other.bin', 'vectors.bin.' + 'a' * 31 + '.tmp'])
def test_audit_relative_open_refuses_non_cache_leaf(relative_dir, name):
    with cache._Directory(relative_dir) as directory:
        with pytest.raises(cache.CacheRefused):
            directory.native.open_relative(directory.handles[-1], name, 1, 0xC0000000, 3)
    assert list(relative_dir.iterdir()) == []


def test_audit_relative_rename_uses_only_held_parent(relative_dir):
    import msvcrt
    target = relative_dir / 'vectors.bin'
    target.write_bytes(b'synthetic-old')
    other = relative_dir.parent / 'synthetic-neighbour'
    other.write_bytes(b'unchanged')
    temp_name = 'vectors.bin.' + 'a' * 32 + '.tmp'
    with cache._Directory(relative_dir) as directory:
        native, parent = directory.native, directory.handles[-1]
        handle = native.open_relative(parent, temp_name, 1, 0x40010000, 0)
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        try:
            os.write(fd, b'synthetic-new')
            native.rename_relative(msvcrt.get_osfhandle(fd), parent, 'vectors.bin')
        finally:
            os.close(fd)
    assert target.read_bytes() == b'synthetic-new'
    assert not (relative_dir / temp_name).exists()
    assert other.read_bytes() == b'unchanged'


@pytest.mark.parametrize('name', ['', '.', '..', r'..\synthetic-neighbour', 'vectors.bin:stream', 'other.bin'])
def test_audit_relative_rename_refuses_non_target_leaf(relative_dir, name):
    import msvcrt
    temp_name = 'vectors.bin.' + 'b' * 32 + '.tmp'
    with cache._Directory(relative_dir) as directory:
        native, parent = directory.native, directory.handles[-1]
        handle = native.open_relative(parent, temp_name, 1, 0x40010000, 0)
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        try:
            with pytest.raises(cache.CacheRefused):
                native.rename_relative(msvcrt.get_osfhandle(fd), parent, name)
        finally:
            os.close(fd)
    assert (relative_dir / temp_name).exists()
    assert not (relative_dir / 'vectors.bin').exists()


def test_audit_relative_open_refuses_directory_and_hardlink(relative_dir):
    target = relative_dir / 'vectors.bin'
    target.mkdir()
    with cache._Directory(relative_dir) as directory:
        with pytest.raises(cache.CacheRefused):
            directory.native.open_relative(directory.handles[-1], 'vectors.bin', 3, 0x80000000, 1)
    target.rmdir()
    target.write_bytes(b'synthetic')
    os.link(target, relative_dir / 'synthetic-hardlink')
    with cache._Directory(relative_dir) as directory:
        with pytest.raises(cache.CacheRefused, match='hardlink'):
            directory.native.open_relative(directory.handles[-1], 'vectors.bin', 3, 0x80000000, 1)


def test_audit_relative_open_refuses_reparse_leaf(relative_dir):
    real = relative_dir / 'synthetic-real'
    real.write_bytes(b'untouched')
    try:
        os.symlink(real, relative_dir / 'vectors.bin')
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    with cache._Directory(relative_dir) as directory:
        with pytest.raises(cache.CacheRefused):
            directory.native.open_relative(directory.handles[-1], 'vectors.bin', 3, 0x80000000, 1)
    assert real.read_bytes() == b'untouched'


@pytest.fixture
def audit_cache_case(tmp_path, monkeypatch):
    """Real Windows handles/ACLs in an isolated synthetic directory only."""
    if os.name != 'nt':
        pytest.skip('native Windows ACL and file identity')
    from waggledance.core import bridge_log_reader as bridge
    assert '.codex-audit' in str(tmp_path).lower()
    root = tmp_path / 'audit'
    root.mkdir()
    shared = root / 'source-runtime' / 'shared'
    shared.mkdir(parents=True)
    cache_root = root / 'private' / 'vector-cache'
    cache_root.mkdir(parents=True)
    bundle = root / ('git-bundle-' + 'a' * 8 + '-real-pending')
    bundle.mkdir()
    source = shared / 'events.jsonl'
    raw = b's' * 38400
    source.write_bytes(raw)
    user = subprocess.check_output(['whoami'], text=True).strip()
    for path, flags in ((shared, '(OI)(CI)F'), (cache_root.parent, '(OI)(CI)F'),
                        (cache_root, '(OI)(CI)F'), (source, 'F')):
        for args in (['/setowner', user], ['/inheritance:r', '/grant:r',
                     user + ':' + flags, 'SYSTEM:' + flags],
                     ['/remove:g', '*S-1-5-32-544', '*S-1-3-4']):
            result = subprocess.run(['icacls', str(path), *args], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
    manifest = json.dumps({'schema_version': 1, 'source_commit': 'a' * 40, 'files': {}}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    with bridge._open_log(source) as stream:
        identity = bridge._file_identity(stream)
    monkeypatch.setattr(cache, '_AUDIT_ROOT', root)
    monkeypatch.setattr(cache, '_AUDIT_SOURCE', source)
    monkeypatch.setattr(cache, '_AUDIT_CACHE_ROOT', cache_root)
    monkeypatch.setattr(cache, '_AUDIT_SOURCE_SHA256', hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(cache, '_AUDIT_WIP_ENABLED', True)
    # This overrides the unproved namespace gate only for synthetic fixtures.
    monkeypatch.setattr(cache, '_AUDIT_NAMESPACE_PROVEN', True)
    pin = hashlib.sha256(manifest).hexdigest()
    permit = cache._audit_permit(bundle, pin, manifest, source, cache._AUDIT_SOURCE_SHA256, len(raw), identity)
    bound = dict(binding(), prefix_offset=len(raw), prefix_sha256=cache._AUDIT_SOURCE_SHA256,
                 source_identity_sha256=hashlib.sha256(cache._canonical(identity)).hexdigest(),
                 source_generation_sha256=hashlib.sha256(cache._canonical(None)).hexdigest())
    return {'bundle': bundle, 'manifest': manifest, 'pin': pin, 'source': source, 'identity': identity,
            'permit': permit, 'path': cache_root / 'vectors.bin', 'binding': bound}


def test_audit_cache_real_acl_cold_warm_and_production_root_unchanged(audit_cache_case):
    case = audit_cache_case
    path, permit = case['path'], case['permit']
    assert str(cache._CACHE_ROOT).lower() == r'c:\wd-mind-private\vector-cache'
    assert cache._audit_cache_path(permit) == path
    with pytest.raises(cache.CacheRefused, match='cache_root'):
        cache.preflight(path)  # Audit path never works without a permit.
    assert cache.preflight(path, _audit=permit) == 'MISSING'
    cache.write_cache(path, case['binding'], rows(), _audit=permit)
    loaded = cache.load_cache(path, case['binding'], lambda *_: True, _audit=permit)
    assert loaded.rows == rows()


def test_audit_cache_cold_warm_rejects_absolute_leaf_operations(audit_cache_case, monkeypatch):
    case = audit_cache_case
    original_replace, original_lexists = os.replace, os.path.lexists

    def is_cache_leaf(value):
        return isinstance(value, (str, os.PathLike)) and Path(value).parent == case['path'].parent

    def checked_replace(src, dst, *args, **kwargs):
        if is_cache_leaf(src) or is_cache_leaf(dst):
            raise AssertionError('audit cache must rename through held parent')
        return original_replace(src, dst, *args, **kwargs)

    def checked_lexists(path):
        if is_cache_leaf(path):
            raise AssertionError('audit cache must open through held parent')
        return original_lexists(path)

    monkeypatch.setattr(cache.os, 'replace', checked_replace)
    monkeypatch.setattr(cache.os.path, 'lexists', checked_lexists)
    assert cache.preflight(case['path'], _audit=case['permit']) == 'MISSING'
    cache.write_cache(case['path'], case['binding'], rows(), _audit=case['permit'])
    assert cache.load_cache(case['path'], case['binding'], lambda *_: True,
                            _audit=case['permit']).rows == rows()


def test_audit_scope_final_check_keeps_cache_directory_handles_open(audit_cache_case, monkeypatch):
    import ctypes
    case = audit_cache_case
    check_original = cache._AuditScope._check
    observed = []
    held_directory = []

    def checked_scope(scope):
        if held_directory:
            directory = held_directory[0]
            assert directory.handles
            for handle in directory.handles:
                info = cache._HandleInfo()
                assert directory.native.kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            observed.append(True)
        return check_original(scope)

    monkeypatch.setattr(cache._AuditScope, '_check', checked_scope)
    with cache._Directory(case['path'].parent, case['permit']) as directory:
        held_directory.append(directory)
    assert observed == [True]


def test_unverified_audit_gate_refuses_before_any_cache_source_or_manifest_open(audit_cache_case, monkeypatch):
    case = audit_cache_case
    opened = []
    monkeypatch.setattr(cache, '_AUDIT_WIP_ENABLED', False)
    def unexpected_open(*args, **kwargs):
        opened.append(True)
        raise AssertionError('unverified audit must not open files')
    monkeypatch.setattr(cache._Native, 'open', unexpected_open)
    with pytest.raises(cache.CacheRefused, match='audit_path_unverified'):
        cache.preflight(case['path'], _audit=case['permit'])
    assert opened == []
    assert not case['path'].exists() and not case['path'].with_name('vectors.bin.lock').exists()


@pytest.mark.parametrize('operation', ['preflight', 'load', 'write'])
def test_unverified_namespace_refuses_before_any_open(audit_cache_case, monkeypatch, operation):
    case = audit_cache_case
    monkeypatch.setattr(cache, '_AUDIT_NAMESPACE_PROVEN', False)
    opened = []

    def unexpected_open(*args, **kwargs):
        opened.append(True)
        raise AssertionError('unverified namespace must not open files')

    monkeypatch.setattr(cache._Native, 'open', unexpected_open)
    monkeypatch.setattr(cache._Native, 'open_relative', unexpected_open)
    if operation == 'preflight':
        action = lambda: cache.preflight(case['path'], _audit=case['permit'])
    elif operation == 'load':
        action = lambda: cache.load_cache(case['path'], case['binding'], lambda *_: True, _audit=case['permit'])
    else:
        action = lambda: cache.write_cache(case['path'], case['binding'], rows(), _audit=case['permit'])
    with pytest.raises(cache.CacheRefused, match='audit_namespace_unverified'):
        action()
    assert opened == []
    assert not case['path'].exists() and not case['path'].with_name('vectors.bin.lock').exists()


def test_audit_bundle_selector_never_falls_back_for_bad_audit_binding(audit_cache_case):
    from types import SimpleNamespace
    case = audit_cache_case
    cursor = SimpleNamespace(offset=38400, file_identity=case['identity'], generation=None)
    args = {'cursor': cursor, 'source_path': str(case['source']),
            'expected_prefix_sha256': cache._AUDIT_SOURCE_SHA256}
    permit = cache._audit_for_bundle(case['bundle'], case['pin'], case['manifest'], args)
    assert cache._audit_cache_path(permit) == case['path']
    assert cache._audit_for_bundle(Path(r'C:\Python\wd-reboot-bundles\normal'),
                                   'not-inspected', b'', {}) is None
    with pytest.raises(cache.CacheRefused):
        cache._audit_for_bundle(case['bundle'] / 'unexpected', case['pin'], case['manifest'], args)
    args['source_path'] = r'C:\Python\project2-master\.agent-bridge\shared\events.jsonl'
    with pytest.raises(cache.CacheRefused, match='audit_binding'):
        cache._audit_for_bundle(case['bundle'], case['pin'], case['manifest'], args)


def test_audit_bundle_alias_never_falls_back_to_production(audit_cache_case, monkeypatch):
    from types import SimpleNamespace
    case = audit_cache_case
    alias = Path(r'C:\synthetic-alias\git-bundle-aaaaaaaa-real-pending')
    original = cache.os.path.realpath
    def resolve(path):
        return str(case['bundle']) if str(path) == str(alias) else original(path)
    monkeypatch.setattr(cache.os.path, 'realpath', resolve)
    args = {'cursor': SimpleNamespace(offset=38400, file_identity=case['identity'], generation=None),
            'source_path': str(case['source']), 'expected_prefix_sha256': cache._AUDIT_SOURCE_SHA256}
    with pytest.raises(cache.CacheRefused, match='audit_bundle_alias'):
        cache._audit_for_bundle(alias, case['pin'], case['manifest'], args)


@pytest.mark.parametrize('fault', ['source_path', 'source_sha', 'source_size', 'source_identity',
                                   'manifest_pin', 'bundle_name', 'manifest_commit'])
def test_audit_grant_refuses_unbound_source_or_bundle(audit_cache_case, fault):
    case = audit_cache_case
    bundle, raw, pin, source, sha, size, identity = (case['bundle'], case['manifest'], case['pin'],
        case['source'], cache._AUDIT_SOURCE_SHA256, cache._AUDIT_SOURCE_SIZE, case['identity'])
    if fault == 'source_path': source = Path(r'C:\Python\project2-master\.agent-bridge\shared\events.jsonl')
    if fault == 'source_sha': sha = '0' * 64
    if fault == 'source_size': size -= 1
    if fault == 'source_identity': identity = 'not-a-file-identity'
    if fault == 'manifest_pin': pin = '0' * 64
    if fault == 'bundle_name': bundle = bundle.with_name('wrong-bundle')
    if fault == 'manifest_commit':
        raw = json.dumps({'schema_version': 1, 'source_commit': 'b' * 40, 'files': {}}).encode()
        pin = hashlib.sha256(raw).hexdigest()
    with pytest.raises(cache.CacheRefused):
        cache._audit_permit(bundle, pin, raw, source, sha, size, identity)
    assert not case['path'].exists()


@pytest.mark.parametrize('fault', ['source_content', 'source_identity', 'source_acl', 'source_hardlink',
                                  'cache_parent_acl', 'cache_target_acl', 'manifest_content'])
def test_audit_revalidates_actual_inputs_before_cache_io(audit_cache_case, fault):
    case = audit_cache_case
    if fault == 'source_content':
        case['source'].write_bytes(b't' * 38400)
    elif fault == 'source_identity':
        case['permit'] = cache._audit_permit(case['bundle'], case['pin'], case['manifest'], case['source'],
            cache._AUDIT_SOURCE_SHA256, 38400, 'windows-v1:00000000:0000000000000000')
    elif fault == 'source_acl':
        result = subprocess.run(['icacls', str(case['source'].parent), '/grant', '*S-1-1-0:(OI)(CI)R'],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    elif fault == 'source_hardlink':
        os.link(case['source'], case['source'].with_name('synthetic-second-link'))
    elif fault in ('cache_parent_acl', 'cache_target_acl'):
        directory = case['path'].parent.parent if fault == 'cache_parent_acl' else case['path'].parent
        result = subprocess.run(['icacls', str(directory), '/grant', '*S-1-1-0:(OI)(CI)R'],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        (case['bundle'] / 'deployment-manifest.json').write_bytes(b'changed')
    with pytest.raises(cache.CacheRefused):
        cache.preflight(case['path'], _audit=case['permit'])
    assert not case['path'].exists() and not case['path'].with_name('vectors.bin.lock').exists()


def test_audit_rejects_binding_from_another_source_before_write(audit_cache_case):
    case = audit_cache_case
    foreign = dict(case['binding'], source_identity_sha256='0' * 64)
    with pytest.raises(cache.CacheRefused, match='audit_binding'):
        cache.write_cache(case['path'], foreign, rows(), _audit=case['permit'])
    assert not case['path'].exists()


def test_preflight_missing_root_is_not_cache_miss(private_dir, monkeypatch):
    absent = private_dir / "unprovisioned"
    monkeypatch.setattr(cache, "_CACHE_ROOT", absent)
    with pytest.raises(cache.CacheRefused):
        cache.preflight(absent / "vectors.bin")
    assert not absent.exists()


def test_preflight_open_error_is_not_cache_miss(private_dir, monkeypatch):
    path = private_dir / "vectors.bin"
    original = cache._Native.open
    def fail(self, selected, *args, **kwargs):
        if Path(selected) == path:
            raise cache.CacheRefused("synthetic_access_denied")
        return original(self, selected, *args, **kwargs)
    monkeypatch.setattr(cache._Native, "open", fail)
    with pytest.raises(cache.CacheRefused, match="synthetic_access_denied"):
        cache.preflight(path)


def test_preflight_busy_writer_is_not_cache_miss(private_dir):
    path = private_dir / "vectors.bin"
    program = ("from tools import wd_mind_vector_cache as c; from pathlib import Path; import sys; "
               "p=Path(sys.argv[1]); c._CACHE_ROOT=p.parent; c.preflight(p)")
    with cache._Directory(private_dir) as directory, cache._WriterLock(directory, path):
        result = subprocess.run([sys.executable, "-c", program, str(path)], capture_output=True, text=True)
    assert result.returncode != 0 and "CacheRefused" in result.stderr


@pytest.mark.parametrize("key", ["source_identity_sha256", "source_generation_sha256", "model_sha256",
                                  "policy_sha256", "projection_code_sha256"])
def test_pin_change_refuses_whole_cache(private_dir, key):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    current = binding()
    current[key] = "f" * 64
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, current, lambda *_: True)


def test_append_requires_valid_old_prefix(private_dir):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    current = dict(binding(), prefix_offset=120, prefix_sha256="f" * 64)
    seen = []
    assert cache.load_cache(path, current, lambda offset, digest: seen.append((offset, digest)) or True).rows == rows()
    assert seen and all(pair == (100, "3" * 64) for pair in seen)
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, current, lambda *_: False)
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, dict(current, prefix_offset=99), lambda *_: True)
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, dict(binding(), prefix_sha256="f" * 64), lambda *_: True)
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, dict(binding(), projection_sha256="f" * 64), lambda *_: True)
    calls = []
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, current, lambda *_: calls.append(1) or len(calls) == 1)
    assert len(calls) == 2


def test_prefix_verifier_exception_is_refusal(private_dir):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    def fail(*_):
        raise RuntimeError("synthetic")
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, binding(), fail)


@pytest.mark.parametrize("mutation", ["truncate", "flip", "extra", "header"])
def test_corruption_never_returns_partial_rows(private_dir, mutation):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    raw = bytearray(path.read_bytes())
    if mutation == "truncate":
        raw = raw[:-1]
    elif mutation == "flip":
        raw[-1] ^= 1
    elif mutation == "extra":
        raw.extend(b"x")
    else:
        raw[0] ^= 1
    path.write_bytes(raw)
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, binding(), lambda *_: True)


def test_unknown_header_field_refused(private_dir):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    raw = path.read_bytes()
    start = len(cache._MAGIC) + 4
    old_len = struct.unpack("<I", raw[len(cache._MAGIC):start])[0]
    header = json.loads(raw[start:start + old_len])
    header["unknown"] = "not allowed"
    changed = cache._canonical(header)
    path.write_bytes(cache._MAGIC + struct.pack("<I", len(changed)) + changed + raw[start + old_len:])
    with pytest.raises(cache.CacheRefused, match="header"):
        cache.load_cache(path, binding(), lambda *_: True)


def test_nonunit_vector_refused_even_if_checksums_are_recomputed(private_dir):
    path = private_dir / "vectors.bin"
    invalid = (("a" * 64, "b" * 64, bytes(1536)),)
    header = cache._canonical(cache._header(binding(), invalid))
    path.write_bytes(cache._MAGIC + struct.pack("<I", len(header)) + header + b"".join(cache._encoded(invalid)))
    user = subprocess.check_output(["whoami"], text=True).strip()
    result = subprocess.run(["icacls", str(path), "/setowner", user], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with pytest.raises(cache.CacheRefused, match="vector_norm"):
        cache.load_cache(path, binding(), lambda *_: True)


def test_invalid_vectors_and_ambiguous_rows_rejected_before_write(private_dir):
    path = private_dir / "vectors.bin"
    for invalid in ((("a" * 64, "b" * 64, b"short"),),
                    (("a" * 64, "b" * 64, struct.pack("<384f", math.nan, *([0.0] * 383))),),
                    (("a" * 64, "b" * 64, bytes(1536)),),
                    (rows()[0], rows()[0])):
        with pytest.raises(cache.CacheRefused):
            cache.write_cache(path, binding(), invalid)
        assert not path.exists()


def test_interrupted_replace_preserves_old_complete_cache(private_dir, monkeypatch):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    def fail(*_):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(cache.os, "replace", fail)
    with pytest.raises(cache.CacheRefused):
        cache.write_cache(path, binding(), rows()[:1])
    assert cache.load_cache(path, binding(), lambda *_: True).rows == rows()
    # No path-based cleanup of an object another process could replace.
    assert len(list(private_dir.glob("*.tmp"))) == 1


def test_colliding_temp_object_is_never_deleted(private_dir, monkeypatch):
    class FixedUuid:
        hex = "a" * 32
    foreign = private_dir / ("vectors.bin." + FixedUuid.hex + ".tmp")
    foreign.write_bytes(b"foreign synthetic file")
    monkeypatch.setattr(cache.uuid, "uuid4", lambda: FixedUuid())
    with pytest.raises(cache.CacheRefused):
        cache.write_cache(private_dir / "vectors.bin", binding(), rows())
    assert foreign.read_bytes() == b"foreign synthetic file"


def test_failed_new_lock_owner_check_leaves_lock_for_safe_followup(private_dir, monkeypatch):
    path = private_dir / "vectors.bin"
    def fail(*_):
        raise cache.CacheRefused("synthetic_owner_failure")
    monkeypatch.setattr(cache._Native, "set_created_user_owner", fail)
    with pytest.raises(cache.CacheRefused, match="synthetic_owner_failure"):
        cache.write_cache(path, binding(), rows())
    lock = private_dir / "vectors.bin.lock"
    assert lock.exists() and not path.exists()
    monkeypatch.undo()
    # The name/ACL/owner are checked afresh. In an elevated process the
    # abandoned lock often has the Administrators default owner and must HOLD.
    monkeypatch.setattr(cache, "_CACHE_ROOT", private_dir)
    native = cache._Native()
    handle = native.open(lock, 3, 0, 3)
    try:
        try:
            native.user_acl(handle)
            trusted = True
        except cache.CacheRefused:
            trusted = False
    finally:
        native.kernel.CloseHandle(handle)
    if trusted:
        cache.write_cache(path, binding(), rows())
    else:
        with pytest.raises(cache.CacheRefused):
            cache.write_cache(path, binding(), rows())
        assert lock.exists() and not path.exists()


def test_second_process_cannot_write_while_first_holds_lock(private_dir):
    path = private_dir / "vectors.bin"
    program = ("from tools import wd_mind_vector_cache as c; from pathlib import Path; import sys; "
               "p=Path(sys.argv[1]); c._CACHE_ROOT=p.parent; "
               "b={'source_identity_sha256':'1'*64,'source_generation_sha256':'2'*64,"
               "'prefix_offset':100,'prefix_sha256':'3'*64,'model_sha256':'4'*64,"
               "'policy_sha256':'5'*64,'projection_code_sha256':'6'*64,'projection_sha256':'7'*64}; "
               "c.write_cache(p,b,())")
    with cache._Directory(private_dir) as directory, cache._WriterLock(directory, path):
        result = subprocess.run([sys.executable, "-c", program, str(path)], capture_output=True, text=True)
    assert result.returncode != 0 and "CacheRefused" in result.stderr
    assert not path.exists()


def test_second_cache_name_refused_even_while_first_writer_holds_lock(private_dir):
    path = private_dir / "vectors.bin"
    other = private_dir / "different.bin"
    program = ("from tools import wd_mind_vector_cache as c; from pathlib import Path; import sys; "
               "p=Path(sys.argv[1]); c._CACHE_ROOT=p.parent; c.write_cache(p," + repr(binding()) + ",())")
    with cache._Directory(private_dir) as directory, cache._WriterLock(directory, path):
        result = subprocess.run([sys.executable, "-c", program, str(other)], capture_output=True, text=True)
    assert result.returncode != 0 and "CacheRefused: path" in result.stderr
    assert not other.exists()


def test_current_user_owner_modify_acl_can_create_cache(private_dir):
    """The child sets only its own token's default owner; no live files are touched."""
    user = subprocess.check_output(["whoami"], text=True).strip()
    result = subprocess.run(["icacls", str(private_dir), "/grant:r", user + ":(OI)(CI)M"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(["icacls", str(private_dir), "/deny", user + ":(OI)(CI)(WO)"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    program = r'''
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
from tools import wd_mind_vector_cache as c

k=ctypes.WinDLL("kernel32", use_last_error=True)
a=ctypes.WinDLL("advapi32", use_last_error=True)
k.GetCurrentProcess.restype=wintypes.HANDLE
a.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
a.GetTokenInformation.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
a.SetTokenInformation.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
token=wintypes.HANDLE()
assert a.OpenProcessToken(k.GetCurrentProcess(),0x88,ctypes.byref(token))
size=wintypes.DWORD()
a.GetTokenInformation(token,1,None,0,ctypes.byref(size))
buf=ctypes.create_string_buffer(size.value)
assert a.GetTokenInformation(token,1,buf,size.value,ctypes.byref(size))
sid=ctypes.c_void_p(ctypes.cast(buf,ctypes.POINTER(ctypes.c_void_p))[0])
assert a.SetTokenInformation(token,4,ctypes.byref(sid),ctypes.sizeof(sid))
p=Path(sys.argv[1]); c._CACHE_ROOT=p.parent
b={"source_identity_sha256":"1"*64,"source_generation_sha256":"2"*64,
   "prefix_offset":100,"prefix_sha256":"3"*64,"model_sha256":"4"*64,
   "policy_sha256":"5"*64,"projection_code_sha256":"6"*64,"projection_sha256":"7"*64}
c.write_cache(p,b,())
assert c.load_cache(p,b,lambda *_:True).rows == ()
'''
    process = subprocess.run([sys.executable, "-c", program, str(private_dir / "vectors.bin")],
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_broad_acl_refused(private_dir):
    result = subprocess.run(["icacls", str(private_dir), "/grant", "*S-1-1-0:(OI)(CI)R"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with pytest.raises(cache.CacheRefused, match="acl_not_user_only"):
        cache.write_cache(private_dir / "vectors.bin", binding(), rows())
    with pytest.raises(cache.CacheRefused, match="acl_not_user_only"):
        cache.preflight(private_dir / "vectors.bin")


def test_parent_broad_acl_refused_before_any_cache_access(private_dir, monkeypatch):
    """A private target does not make a broadly writable parent private."""
    parent = private_dir
    target = parent / "synthetic-vector-cache"
    target.mkdir()
    user = subprocess.check_output(["whoami"], text=True).strip()
    for args in (["/setowner", user],
                 ["/inheritance:r", "/grant:r", user + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"]):
        result = subprocess.run(["icacls", str(target), *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = subprocess.run(["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)R"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr(cache, "_CACHE_ROOT", target)
    with pytest.raises(cache.CacheRefused, match="acl_not_user_only"):
        cache.preflight(target / "vectors.bin")
    assert not (target / "vectors.bin.lock").exists()


@pytest.mark.parametrize("which", ["parent", "target"])
@pytest.mark.parametrize("damage,reason", [
    ("broad", "acl_not_user_only"),
    ("unprotected", "acl_not_protected"),
    ("missing_system", "acl_unknown"),
])
def test_provisioned_directory_acl_is_checked_before_every_use(private_dir, which, damage, reason):
    directory = private_dir.parent if which == "parent" else private_dir
    args = {"broad": ["/grant", "*S-1-1-0:(OI)(CI)R"],
            "unprotected": ["/inheritance:e"],
            "missing_system": ["/remove:g", "*S-1-5-18"]}[damage]
    result = subprocess.run(["icacls", str(directory), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    path = private_dir / "vectors.bin"
    for operation in (lambda: cache.preflight(path),
                      lambda: cache.load_cache(path, binding(), lambda *_: True),
                      lambda: cache.write_cache(path, binding(), rows())):
        with pytest.raises(cache.CacheRefused, match=reason):
            operation()
        assert not path.exists() and not (private_dir / "vectors.bin.lock").exists()


def test_nonprivate_or_unknown_acl_refuses_access(private_dir, monkeypatch):
    def unknown(*_, **__):
        raise cache.CacheRefused("acl_unknown")
    monkeypatch.setattr(cache._Native, "user_acl", unknown)
    with pytest.raises(cache.CacheRefused, match="acl_unknown"):
        cache.write_cache(private_dir / "vectors.bin", binding(), rows())


def test_wrong_owner_rejected_even_with_restricted_dacl(private_dir):
    result = subprocess.run(["icacls", str(private_dir), "/setowner", "*S-1-5-32-544"],
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip("setting another owner requires privilege")
    with pytest.raises(cache.CacheRefused, match="owner_not_trusted"):
        cache.write_cache(private_dir / "vectors.bin", binding(), rows())
    with pytest.raises(cache.CacheRefused, match="owner_not_trusted"):
        cache.preflight(private_dir / "vectors.bin")


@pytest.mark.parametrize("filename", ["vectors.bin", "vectors.bin.lock"])
def test_wrong_file_owner_refused_without_reassigning(private_dir, filename):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    target = private_dir / filename
    result = subprocess.run(["icacls", str(target), "/setowner", "*S-1-5-32-544"],
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip("setting another owner requires privilege")
    operation = (lambda: cache.load_cache(path, binding(), lambda *_: True)) if filename == "vectors.bin" else (
        lambda: cache.write_cache(path, binding(), rows()))
    with pytest.raises(cache.CacheRefused, match="owner_not_trusted"):
        operation()
    with pytest.raises(cache.CacheRefused, match="owner_not_trusted"):
        cache.preflight(path)
    native = cache._Native()
    handle = native.open(target, 3, 0, 3)
    try:
        with pytest.raises(cache.CacheRefused, match="owner_not_trusted"):
            native.user_acl(handle)
    finally:
        native.kernel.CloseHandle(handle)


def test_inherit_only_user_ace_does_not_authorize_directory(private_dir):
    user = subprocess.check_output(["whoami"], text=True).strip()
    native = cache._Native()
    handle = native.open(private_dir, 3, 0, 3, directory=True)
    try:
        removed = subprocess.run(["icacls", str(private_dir), "/remove:g", user], capture_output=True, text=True)
        assert removed.returncode == 0, removed.stderr
        added = subprocess.run(["icacls", str(private_dir), "/grant:r", user + ":(OI)(CI)(IO)F"],
                               capture_output=True, text=True)
        assert added.returncode == 0, added.stderr
        with pytest.raises(cache.CacheRefused, match="acl_unknown"):
            native.user_acl(handle)
    finally:
        native.kernel.CloseHandle(handle)


def test_linked_directory_or_file_refused(private_dir, monkeypatch):
    target = private_dir / "real"
    target.mkdir()
    link = private_dir / "linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    monkeypatch.setattr(cache, "_CACHE_ROOT", link)
    with pytest.raises(cache.CacheRefused):
        cache.write_cache(link / "vectors.bin", binding(), rows())
    with pytest.raises(cache.CacheRefused):
        cache.preflight(link / "vectors.bin")


def test_hardlinked_cache_refused(private_dir):
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows())
    os.link(path, private_dir / "second-link.bin")
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, binding(), lambda *_: True)
    with pytest.raises(cache.CacheRefused):
        cache.write_cache(path, binding(), rows())


def test_linked_cache_destination_refused(private_dir):
    target = private_dir / "other.bin"
    target.write_bytes(b"unchanged")
    path = private_dir / "vectors.bin"
    try:
        os.symlink(target, path)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(cache.CacheRefused):
        cache.write_cache(path, binding(), rows())
    with pytest.raises(cache.CacheRefused):
        cache.load_cache(path, binding(), lambda *_: True)
    assert target.read_bytes() == b"unchanged"


def test_production_root_excludes_repo_and_bundles(tmp_path):
    assert str(cache._CACHE_ROOT).lower() == r"c:\wd-mind-private\vector-cache"
    with pytest.raises(cache.CacheRefused, match="cache_root"):
        with cache._Directory(tmp_path):
            pass
