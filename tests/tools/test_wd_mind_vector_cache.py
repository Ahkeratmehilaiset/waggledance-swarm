"""Synthetic-only cache checks in a test-owned .codex-audit directory."""
import math
import json
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

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


def test_resource_preflight_enforces_row_disk_and_memory_bounds(monkeypatch):
    count = 65537
    required_disk = len(cache._MAGIC) + 4 + cache._MAX_HEADER + count * cache._ROW_SIZE
    required_memory = cache._MEMORY_FLOOR + count * cache._MEMORY_PER_ROW
    monkeypatch.setattr(cache, '_available_memory', lambda: required_memory)
    monkeypatch.setattr(cache.shutil, 'disk_usage',
                        lambda _path: SimpleNamespace(free=cache._DISK_FLOOR + required_disk))
    cache._resource_preflight(Path('.'), count)
    monkeypatch.setattr(cache, '_available_memory', lambda: required_memory - 1)
    with pytest.raises(cache.CacheRefused, match='memory_budget'):
        cache._resource_preflight(Path('.'), count)
    monkeypatch.setattr(cache, '_available_memory', lambda: required_memory)
    monkeypatch.setattr(cache.shutil, 'disk_usage',
                        lambda _path: SimpleNamespace(free=cache._DISK_FLOOR + required_disk - 1))
    with pytest.raises(cache.CacheRefused, match='disk_budget'):
        cache._resource_preflight(Path('.'), count)
    # Read-only cache loads do not need enough additional free space for a write.
    cache._resource_preflight(Path('.'), count, require_disk=False)
    for bad in (-1, True, 1.0, 1 << 63):
        with pytest.raises(cache.CacheRefused, match='row_budget'):
            cache._resource_preflight(Path('.'), bad)


@pytest.fixture
def private_dir(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("native Windows ACL and directory leases")
    user = subprocess.check_output(["whoami"], text=True).strip()
    audit_root = tmp_path / ".codex-audit"
    audit_root.mkdir()
    target = audit_root / "synthetic-vector-cache"
    target.mkdir()
    for directory in (audit_root, target):
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
    # Native cache access refuses non-Windows before checking the root path.
    reason = "cache_root" if os.name == "nt" else "windows_required"
    with pytest.raises(cache.CacheRefused, match=f"^{reason}$"):
        with cache._Directory(tmp_path):
            pass
