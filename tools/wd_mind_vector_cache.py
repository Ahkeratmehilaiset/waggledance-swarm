"""Private vector-only cache for the pinned Mind runtime.

Cache bytes are derived data, not an authenticated statement.  A writer running
as the same Windows user can replace both vectors and checksums: UNKNOWN.
The fixed cache root is outside both the task repo and the reboot bundles. The
caller must bind all vector-affecting model and projection dependencies.

On a failed new-lock owner/ACL check, the empty lock object can remain; the
next writer independently rechecks its owner and ACL and refuses if unknown.
Never remove or take ownership of an unexpected existing lock automatically.
An authorized operator may clear an orphan only after independently excluding
active writers and verifying the intended directory/object; otherwise HOLD.
Failed publication can similarly leave a restricted temporary file; it is not
reused and requires the same careful offline inspection for later cleanup.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from types import MappingProxyType
from typing import NamedTuple
import uuid

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset(("source_identity_sha256", "source_generation_sha256", "prefix_offset",
                     "prefix_sha256", "model_sha256", "policy_sha256", "projection_code_sha256",
                     "projection_sha256"))
_ROW_SIZE = 64 + 384 * 4
# Bounded interim capacity: cache rows are loaded eagerly, so this remains a
# finite allocation limit, not a guarantee that every 10 GiB source will fit.
_MAX_ROWS = 500000
_MAX_HEADER = 8192
_MAGIC = b"WDMVC1\n"
_CACHE_ROOT = Path(r"C:\wd-mind-private\vector-cache")
_CACHE_NAME = "vectors.bin"


class CacheRefused(ValueError):
    """The whole cache is unusable; never return partial data."""


class CacheMiss(CacheRefused):
    """A verified missing or stale cache may be rebuilt from the source."""


class CacheResult(NamedTuple):
    binding: object
    rows: tuple
    record_set_sha256: str


def _binding(value):
    if type(value) is not dict or set(value) != _FIELDS:
        raise CacheRefused("binding")
    for name in _FIELDS - {"prefix_offset"}:
        if type(value[name]) is not str or not _HEX.fullmatch(value[name]):
            raise CacheRefused("binding")
    if type(value["prefix_offset"]) is not int or not 0 <= value["prefix_offset"] <= (1 << 63) - 1:
        raise CacheRefused("binding")
    return dict(value)


def _rows(rows):
    if type(rows) is not tuple or len(rows) > _MAX_ROWS:
        raise CacheRefused("rows")
    previous = ""
    for row in rows:
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not str
                or not _HEX.fullmatch(row[0]) or row[0] <= previous
                or type(row[1]) is not str or not _HEX.fullmatch(row[1])
                or type(row[2]) is not bytes or len(row[2]) != 1536):
            raise CacheRefused("rows")
        values = struct.unpack("<384f", row[2])
        if not all(math.isfinite(x) for x in values):
            raise CacheRefused("vector")
        norm = math.sqrt(sum(x * x for x in values))
        if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
            raise CacheRefused("vector_norm")
        previous = row[0]
    return rows


def _encoded(rows):
    for record_hash, passage_hash, vector in rows:
        yield bytes.fromhex(record_hash) + bytes.fromhex(passage_hash) + vector


def _header(binding, rows):
    payload, vectors, record_set = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    for row, block in zip(rows, _encoded(rows)):
        payload.update(block)
        vectors.update(row[2])
        record_set.update(bytes.fromhex(row[0]))
    return {"schema": "wd.mind-vector-cache.v1", "binding": binding, "dimension": 384,
            "dtype": "f32le", "count": len(rows), "payload_sha256": payload.hexdigest(),
            "vectors_sha256": vectors.hexdigest(), "record_set_sha256": record_set.hexdigest()}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CacheRefused("duplicate_key")
        result[key] = value
    return result


class _HandleInfo(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                ("accessed", wintypes.FILETIME), ("modified", wintypes.FILETIME),
                ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [("revision", ctypes.c_ubyte), ("pad", ctypes.c_ubyte), ("size", wintypes.WORD),
                ("count", wintypes.WORD), ("pad2", wintypes.WORD)]


class _Native:
    """Pin every parent directory without delete sharing, reject reparse nodes."""

    def __init__(self):
        if os.name != "nt":
            raise CacheRefused("windows_required")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HandleInfo)]
        self.kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.GetFileType.argtypes = [wintypes.HANDLE]
        self.kernel.GetFileType.restype = wintypes.DWORD
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        self.advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p)]
        self.advapi.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
            ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        self.advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        self.advapi.SetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self.advapi.SetSecurityInfo.restype = wintypes.DWORD
        self.advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self.advapi.GetAce.restype = wintypes.BOOL
        self.advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi.OpenProcessToken.restype = wintypes.BOOL
        self.advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi.GetTokenInformation.restype = wintypes.BOOL
        self.advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.advapi.EqualSid.restype = wintypes.BOOL
        self.advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        self.advapi.ConvertStringSidToSidW.restype = wintypes.BOOL

    def open(self, path, disposition, access, share, directory=False, missing_ok=False):
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        handle = self.kernel.CreateFileW(str(path), access | 0x20000, share, None, disposition, flags, None)
        if handle == wintypes.HANDLE(-1).value:
            if not directory and disposition == 3 and ctypes.get_last_error() == 2:  # ERROR_FILE_NOT_FOUND
                if missing_ok:
                    return None
                raise CacheMiss("missing")
            raise CacheRefused("open")
        try:
            info = _HandleInfo()
            if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise CacheRefused("stat")
            if self.kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
                raise CacheRefused("not_disk")
            if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                raise CacheRefused("reparse_or_type")
            if not directory and info.links != 1:
                raise CacheRefused("hardlink")
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def user_acl(self, handle, *, provisioned_dir=False):
        owner, acl, security, token, system = (ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p(),
                                                wintypes.HANDLE(), ctypes.c_void_p())
        try:
            if self.advapi.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl), None,
                                            ctypes.byref(security)) or not acl or not owner:
                raise CacheRefused("acl_unknown")
            if provisioned_dir:
                control, revision = wintypes.WORD(), wintypes.DWORD()
                if not self.advapi.GetSecurityDescriptorControl(security, ctypes.byref(control),
                                                                  ctypes.byref(revision)):
                    raise CacheRefused("acl_unknown")
                if not control.value & 0x1000:  # SE_DACL_PROTECTED
                    raise CacheRefused("acl_not_protected")
            if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
                raise CacheRefused("acl_unknown")
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise CacheRefused("acl_unknown")
            data = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, data, size.value, ctypes.byref(size)):
                raise CacheRefused("acl_unknown")
            user = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            if not user or not self.advapi.ConvertStringSidToSidW("S-1-5-18", ctypes.byref(system)):
                raise CacheRefused("acl_unknown")
            if not (self.advapi.EqualSid(owner, user) or
                    (not provisioned_dir and self.advapi.EqualSid(owner, system))):
                raise CacheRefused("owner_not_trusted")
            entries = ctypes.cast(acl, ctypes.POINTER(_Acl)).contents
            if not entries.count or entries.count > 4096:
                raise CacheRefused("acl_unknown")
            user_allowed = system_allowed = False
            for index in range(entries.count):
                ace = ctypes.c_void_p()
                if not self.advapi.GetAce(acl, index, ctypes.byref(ace)) or not ace:
                    raise CacheRefused("acl_unknown")
                ace_type = ctypes.c_ubyte.from_address(ace.value).value
                ace_flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                ace_size = ctypes.c_ushort.from_address(ace.value + 2).value
                if ace_size < 16 or ace_type not in (0, 1):
                    raise CacheRefused("acl_unknown")
                if provisioned_dir and ace_flags & 0x10:  # INHERITED_ACE
                    raise CacheRefused("acl_inherited")
                if ace_type == 0:  # allow only the current user and LocalSystem
                    sid = ctypes.c_void_p(ace.value + 8)
                    is_user = bool(self.advapi.EqualSid(sid, user))
                    is_system = bool(self.advapi.EqualSid(sid, system))
                    if not is_user and not is_system:
                        raise CacheRefused("acl_not_user_only")
                    # INHERIT_ONLY grants rights only to child objects, not
                    # the directory/file currently being authorized.
                    user_allowed |= is_user and not (ace_flags & 0x08)
                    system_allowed |= is_system and not (ace_flags & 0x08)
            if not user_allowed or (provisioned_dir and not system_allowed):
                raise CacheRefused("acl_unknown")
        finally:
            if token:
                self.kernel.CloseHandle(token)
            if system:
                self.kernel.LocalFree(system)
            if security:
                self.kernel.LocalFree(security)

    def set_created_user_owner(self, handle):
        """Assign only a file created by this call, never an existing object."""
        token = wintypes.HANDLE()
        try:
            if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
                raise CacheRefused("owner_unknown")
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise CacheRefused("owner_unknown")
            data = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, data, size.value, ctypes.byref(size)):
                raise CacheRefused("owner_unknown")
            user = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            if not user or self.advapi.SetSecurityInfo(handle, 1, 1, user, None, None, None):
                raise CacheRefused("owner_unknown")
        finally:
            if token:
                self.kernel.CloseHandle(token)


class _Directory:
    def __init__(self, path):
        self.path, self.native, self.handles = path, _Native(), []

    def __enter__(self):
        path = Path(self.path)
        if not path.is_absolute() or ".." in path.parts or path.drive.upper() != "C:":
            raise CacheRefused("path")
        if os.path.normcase(os.path.normpath(str(path))) != os.path.normcase(os.path.normpath(str(_CACHE_ROOT))):
            raise CacheRefused("cache_root")
        current = Path(path.anchor)
        try:
            for part in (None, *path.parts[1:]):
                if part is not None:
                    current /= part
                # FILE_LIST_DIRECTORY makes Windows enforce the no-delete-share
                # lease for the directory itself, including rename attempts.
                self.handles.append(self.native.open(current, 3, 0x81, 3, directory=True))
            # The operator provisions both directories: a private target alone
            # is insufficient when another identity can alter its parent.
            if len(self.handles) < 3:
                raise CacheRefused("cache_root")
            self.native.user_acl(self.handles[-2], provisioned_dir=True)
            self.native.user_acl(self.handles[-1], provisioned_dir=True)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for handle in reversed(self.handles):
            self.native.kernel.CloseHandle(handle)
        self.handles.clear()


def _plain_path(path):
    if not isinstance(path, (str, Path)):
        raise CacheRefused("path")
    result = Path(path)
    if (not result.is_absolute() or ".." in result.parts or result.name != _CACHE_NAME
            or ":" in result.name or result.name.endswith((" ", "."))):
        raise CacheRefused("path")
    return result


def _fd(native, path, disposition, access, share, flags, *, missing_ok=False):
    import msvcrt
    created = disposition == 1  # CREATE_NEW; caller never changes existing owners
    handle = native.open(path, disposition, access | (0x80000 if created else 0), share,
                         missing_ok=missing_ok)
    if handle is None:
        return None
    try:
        if created:
            native.set_created_user_owner(handle)
        native.user_acl(handle)
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except BaseException:
        native.kernel.CloseHandle(handle)
        raise


class _WriterLock:
    def __init__(self, directory, cache):
        self.directory, self.path, self.fd = directory, cache.with_name(cache.name + ".lock"), None

    def __enter__(self):
        import msvcrt
        # The lock object is fixed for the one allowed cache name. A failed
        # racing CREATE_NEW refuses; it never adopts or changes an unknown file.
        disposition = 3 if os.path.lexists(self.path) else 1
        self.fd = _fd(self.directory.native, self.path, disposition, 0xC0000000, 3, os.O_RDWR)
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            return self
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise CacheRefused("writer_busy") from None

    def __exit__(self, *_):
        import msvcrt
        if self.fd is not None:
            os.lseek(self.fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self.fd)
                self.fd = None


def preflight(path):
    """Validate root/ACL/owner/lock and report presence, without reading data.

    MISSING is only the OS's exact FILE_NOT_FOUND for the single fixed cache
    entry inside the pinned directory while holding its fixed writer lock. It
    is not proof that a later create/write, disk allocation, or rename succeeds.
    The root must already have been provisioned by an authorized operator.
    """
    path = _plain_path(path)
    try:
        with _Directory(path.parent) as directory, _WriterLock(directory, path):
            fd = _fd(directory.native, path, 3, 0x80000000, 1, os.O_RDONLY, missing_ok=True)
            if fd is None:
                return "MISSING"
            os.close(fd)
            return "PRESENT"
    except CacheRefused:
        raise
    except Exception:
        raise CacheRefused("preflight") from None


def write_cache(path, binding, rows):
    """Persist vectors only; the destination directory must already be private."""
    path, binding, rows = _plain_path(path), _binding(binding), _rows(rows)
    header = _canonical(_header(binding, rows))
    if len(header) > _MAX_HEADER:
        raise CacheRefused("header")
    try:
        with _Directory(path.parent) as directory, _WriterLock(directory, path):
            if os.path.lexists(path):
                # A pre-existing link or a broadly readable file must not be
                # silently replaced, even though rename itself is not a read.
                existing = _fd(directory.native, path, 3, 0x80000000, 1, os.O_RDONLY)
                os.close(existing)
            tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            fd = _fd(directory.native, tmp, 1, 0x40000000, 0, os.O_WRONLY)
            with os.fdopen(fd, "wb") as output:
                output.write(_MAGIC + struct.pack("<I", len(header)) + header)
                for block in _encoded(rows):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            # Never unlink by pathname on failure: CREATE_NEW may have failed
            # against another object's name or the entry may have changed.
            os.replace(tmp, path)
    except CacheRefused:
        raise
    except Exception:
        raise CacheRefused("write") from None


def load_cache(path, expected_binding, verify_prefix):
    """Return a complete, checked cache or raise; compare passage hashes again at use."""
    path, expected = _plain_path(path), _binding(expected_binding)
    if not callable(verify_prefix):
        raise CacheRefused("prefix_verifier")
    try:
        with _Directory(path.parent) as directory:
            # Do not share write/delete while parsing: a concurrent writer must
            # either publish before this open or wait until this read closes.
            try:
                fd = _fd(directory.native, path, 3, 0x80000000, 1, os.O_RDONLY)
            except FileNotFoundError:
                # A synthetic file opener can surface the OS exception rather
                # than the native wrapper's typed missing-cache result.
                raise CacheMiss("missing") from None
            with os.fdopen(fd, "rb") as source:
                # An OS read error can indicate access or device failure, so it
                # must never be mistaken for recoverable corrupt cache bytes.
                def read_content(size):
                    try:
                        return source.read(size)
                    except OSError:
                        raise CacheRefused("read") from None

                initial = read_content(len(_MAGIC) + 4)
                if len(initial) != len(_MAGIC) + 4 or not initial.startswith(_MAGIC):
                    raise CacheMiss("header")
                length = struct.unpack("<I", initial[-4:])[0]
                if not 0 < length <= _MAX_HEADER:
                    raise CacheMiss("header")
                raw = read_content(length)
                if len(raw) != length:
                    raise CacheMiss("header")
                try:
                    header = json.loads(raw.decode("ascii"), object_pairs_hook=_unique)
                except (UnicodeError, ValueError, RecursionError):
                    raise CacheMiss("header") from None
                if (type(header) is not dict or set(header) != {"schema", "binding", "dimension", "dtype",
                        "count", "payload_sha256", "vectors_sha256", "record_set_sha256"} or _canonical(header) != raw
                        or header["schema"] != "wd.mind-vector-cache.v1" or header["dimension"] != 384
                        or type(header["dimension"]) is not int or header["dtype"] != "f32le"
                        or type(header["count"]) is not int or not 0 <= header["count"] <= _MAX_ROWS
                        or any(type(header[key]) is not str or not _HEX.fullmatch(header[key])
                               for key in ("payload_sha256", "vectors_sha256", "record_set_sha256"))):
                    raise CacheMiss("header")
                try:
                    stored = _binding(header["binding"])
                except CacheRefused:
                    raise CacheMiss("header") from None
                # Check the actual opened file before allocating or reading
                # rows.  A failed stat remains a hard refusal, not a miss.
                expected_size = len(_MAGIC) + 4 + length + header["count"] * _ROW_SIZE
                if os.fstat(source.fileno()).st_size != expected_size:
                    raise CacheMiss("truncated")
                for key in _FIELDS - {"prefix_offset", "prefix_sha256", "projection_sha256"}:
                    if stored[key] != expected[key]:
                        raise CacheMiss("binding_changed")
                if (stored["prefix_offset"] > expected["prefix_offset"]
                        or (stored["prefix_offset"] == expected["prefix_offset"]
                            and (stored["prefix_sha256"] != expected["prefix_sha256"]
                                 or stored["projection_sha256"] != expected["projection_sha256"]))
                        or verify_prefix(stored["prefix_offset"], stored["prefix_sha256"]) is not True):
                    raise CacheMiss("prefix_changed")
                rows, payload_hash, vector_hash, record_set = [], hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
                for _ in range(header["count"]):
                    block = read_content(_ROW_SIZE)
                    if len(block) != _ROW_SIZE:
                        raise CacheMiss("truncated")
                    payload_hash.update(block)
                    vector_hash.update(block[64:])
                    record_set.update(block[:32])
                    rows.append((block[:32].hex(), block[32:64].hex(), block[64:]))
                if (read_content(1) or payload_hash.hexdigest() != header["payload_sha256"]
                        or vector_hash.hexdigest() != header["vectors_sha256"]
                        or record_set.hexdigest() != header["record_set_sha256"]):
                    raise CacheMiss("payload")
                if verify_prefix(stored["prefix_offset"], stored["prefix_sha256"]) is not True:
                    raise CacheMiss("prefix_changed")
                try:
                    checked_rows = _rows(tuple(rows))
                except CacheRefused as exc:
                    raise CacheMiss(str(exc)) from None
                return CacheResult(MappingProxyType(stored), checked_rows, record_set.hexdigest())
    except CacheRefused:
        raise
    except Exception:
        raise CacheRefused("read") from None
