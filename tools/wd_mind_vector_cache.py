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
import hmac
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
_MAX_ROWS = 250000
_MAX_HEADER = 8192
_MAGIC = b"WDMVC1\n"
_CACHE_ROOT = Path(r"C:\wd-mind-private\vector-cache")
_CACHE_NAME = "vectors.bin"
# ONE-OFF AUDIT WIP. Do not ship this exception as production configuration.
_AUDIT_ROOT = Path(r"C:\Python\project2\.codex-audit\pr2179-real-e5-160-20261117")
_AUDIT_CACHE_ROOT = _AUDIT_ROOT / "private" / "vector-cache"
_AUDIT_SOURCE = _AUDIT_ROOT / "source-runtime" / "shared" / "events.jsonl"
_AUDIT_SOURCE_SHA256 = "12e4d84e121a712e225da840cc7a16802a6588c3e91adfe93f4c7bc31e02e4f5"
_AUDIT_SOURCE_SIZE = 38400
_AUDIT_BUNDLE_NAME = re.compile(r"git-bundle-([0-9a-f]{8})-real-pending\Z")
_AUDIT_IDENTITY = re.compile(r"windows-v1:[0-9a-f]{8}:[0-9a-f]{16}\Z")
# Namespace ancestry is not yet proved safe against non-admin replacement.
# This is an internal test seam, not an operator-controlled runtime option.
_AUDIT_WIP_ENABLED = False
# The isolated relative-leaf experiment is inert until ancestor namespace
# ownership, rename semantics and integration are independently proven.
_AUDIT_NAMESPACE_PROVEN = False
_AUDIT_LEAF = re.compile(r"vectors\.bin(?:\.lock|\.[0-9a-f]{32}\.tmp)?\Z")


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


class _UnicodeString(ctypes.Structure):
    _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT),
                ("buffer", wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                ("name", ctypes.POINTER(_UnicodeString)), ("attributes", wintypes.ULONG),
                ("security_descriptor", ctypes.c_void_p), ("quality", ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("status_or_pointer", ctypes.c_void_p), ("information", ctypes.c_size_t)]


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

    def open_relative(self, parent, name, disposition, access, share, *, directory=False, missing_ok=False):
        """Inert audit-only leaf experiment; ancestor proof is still required."""
        if _AUDIT_NAMESPACE_PROVEN is not True:
            raise CacheRefused("audit_namespace_unverified")
        if (type(name) is not str or not _AUDIT_LEAF.fullmatch(name) or directory is not False
                or disposition not in (1, 3) or type(access) is not int or type(share) is not int):
            raise CacheRefused("audit_leaf")
        nt = ctypes.WinDLL("ntdll", use_last_error=True)
        nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
            ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
            wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
        nt.NtCreateFile.restype = ctypes.c_long
        chars = ctypes.create_unicode_buffer(name)
        text = _UnicodeString(len(name.encode("utf-16-le")), (len(name) + 1) * 2,
                              ctypes.cast(chars, wintypes.LPWSTR))
        attrs = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(text),
                                  0x40, None, None)  # OBJ_CASE_INSENSITIVE; single leaf only.
        result, handle = _IoStatusBlock(), wintypes.HANDLE()
        desired = access | 0x80 | 0x20000 | 0x100000 | (0x80000 if disposition == 1 else 0)
        status = nt.NtCreateFile(ctypes.byref(handle), desired, ctypes.byref(attrs), ctypes.byref(result),
            None, 0x80, share, 2 if disposition == 1 else 1, 0x00200020, None, 0) & 0xffffffff
        if status == 0xc0000034 and disposition == 3:
            if missing_ok:
                return None
            raise CacheMiss("missing")
        if status != 0:
            raise CacheRefused("audit_relative_open")
        try:
            if result.information != (2 if disposition == 1 else 1):
                raise CacheRefused("audit_relative_disposition")
            info = _HandleInfo()
            if (not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or self.kernel.GetFileType(handle) != 1 or info.attributes & 0x410):
                raise CacheRefused("audit_relative_type")
            if info.links != 1:
                raise CacheRefused("audit_hardlink")
            if disposition == 1:
                self.set_created_user_owner(handle)
            self.user_acl(handle)
            return handle.value
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def rename_relative(self, source_handle, parent, name):
        """Experimental NT rename using a held target directory handle."""
        if _AUDIT_NAMESPACE_PROVEN is not True:
            raise CacheRefused("audit_namespace_unverified")
        if name != _CACHE_NAME:
            raise CacheRefused("audit_leaf")
        source_info, parent_info = _HandleInfo(), _HandleInfo()
        if (not self.kernel.GetFileInformationByHandle(source_handle, ctypes.byref(source_info))
                or not self.kernel.GetFileInformationByHandle(parent, ctypes.byref(parent_info))
                or source_info.attributes & 0x410 or source_info.links != 1
                or not parent_info.attributes & 0x10 or parent_info.attributes & 0x400):
            raise CacheRefused("audit_relative_type")
        self.user_acl(source_handle)
        self.user_acl(parent, provisioned_dir=True)
        class RenameInfo(ctypes.Structure):
            _fields_ = [("replace", ctypes.c_ubyte), ("root", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", wintypes.WCHAR * len(name))]
        # Allocate at least sizeof(FILE_RENAME_INFORMATION) plus filename bytes,
        # including padding and a terminator, as required by the NT contract.
        name_bytes = len(name.encode("utf-16-le"))
        storage = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + name_bytes + 2)
        info = ctypes.cast(storage, ctypes.POINTER(RenameInfo)).contents
        info.replace, info.root, info.length, info.name = 1, parent, name_bytes, name
        nt = ctypes.WinDLL("ntdll", use_last_error=True)
        nt.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock),
                                            ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
        nt.NtSetInformationFile.restype = ctypes.c_long
        result = _IoStatusBlock()
        status = nt.NtSetInformationFile(source_handle, ctypes.byref(result), storage,
                                         len(storage), 10) & 0xffffffff
        if status != 0:
            raise CacheRefused("audit_relative_rename")

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


def _same_path(left, right):
    if not isinstance(left, (str, Path)):
        return False
    path = Path(left)
    return (path.is_absolute() and ".." not in path.parts
            and os.path.normcase(os.path.normpath(str(path))) == os.path.normcase(os.path.normpath(str(right))))


class _AuditPermit:
    """One pinned, synthetic-only audit route; never an operator configuration."""
    __slots__ = ("bundle", "manifest_pin", "source_identity")

    def __init__(self, bundle, manifest_pin, source_identity):
        object.__setattr__(self, "bundle", bundle)
        object.__setattr__(self, "manifest_pin", manifest_pin)
        object.__setattr__(self, "source_identity", source_identity)

    def __setattr__(self, name, value):
        raise AttributeError("immutable")


def _audit_permit(bundle, manifest_pin, manifest_bytes, source_path, prefix_sha, offset, source_identity):
    if _AUDIT_WIP_ENABLED is not True:
        raise CacheRefused("audit_path_unverified")
    bundle = Path(bundle)
    if (not _same_path(bundle.parent, _AUDIT_ROOT) or not _AUDIT_BUNDLE_NAME.fullmatch(bundle.name)
            or type(manifest_pin) is not str or not _HEX.fullmatch(manifest_pin.lower())
            or type(manifest_bytes) is not bytes
            or not hmac.compare_digest(hashlib.sha256(manifest_bytes).hexdigest(), manifest_pin.lower())
            or not _same_path(source_path, _AUDIT_SOURCE) or prefix_sha != _AUDIT_SOURCE_SHA256
            or type(offset) is not int or offset != _AUDIT_SOURCE_SIZE
            or type(source_identity) is not str or not _AUDIT_IDENTITY.fullmatch(source_identity)):
        raise CacheRefused("audit_binding")
    document = json.loads(manifest_bytes.decode("utf-8-sig"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(CacheRefused("audit_manifest")))
    commit = document.get("source_commit") if type(document) is dict else None
    if (type(document) is not dict or type(document.get("schema_version")) is not int
            or document['schema_version'] != 1 or type(document.get("files")) is not dict
            or type(commit) is not str or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or bundle.name != "git-bundle-" + commit[:8] + "-real-pending"):
        raise CacheRefused("audit_bundle")
    return _AuditPermit(bundle, manifest_pin.lower(), source_identity)


def _audit_for_bundle(bundle, manifest_pin, manifest_bytes, args):
    path = Path(bundle)
    if not path.is_absolute() or ".." in path.parts:
        raise CacheRefused("bundle_path")
    root = os.path.normcase(os.path.normpath(str(_AUDIT_ROOT)))
    selected = os.path.normcase(os.path.normpath(str(path)))
    lexical_audit = selected == root or selected.startswith(root + os.sep)
    real_root = os.path.normcase(os.path.normpath(os.path.realpath(str(_AUDIT_ROOT))))
    real_selected = os.path.normcase(os.path.normpath(os.path.realpath(str(path))))
    real_audit = real_selected == real_root or real_selected.startswith(real_root + os.sep)
    if real_audit and not lexical_audit:
        raise CacheRefused("audit_bundle_alias")
    if not lexical_audit:
        return None  # Normal production binding; no audit path is enabled.
    if _AUDIT_WIP_ENABLED is not True:
        raise CacheRefused("audit_path_unverified")
    if type(args) is not dict:
        raise CacheRefused("audit_binding")
    cursor = args.get("cursor")
    if cursor is None or getattr(cursor, "generation", object()) is not None:
        raise CacheRefused("audit_binding")
    return _audit_permit(path, manifest_pin, manifest_bytes, args.get("source_path"),
                         args.get("expected_prefix_sha256"), getattr(cursor, "offset", None),
                         getattr(cursor, "file_identity", None))


def _audit_cache_path(permit):
    if type(permit) is not _AuditPermit:
        raise CacheRefused("audit_binding")
    return _AUDIT_CACHE_ROOT / _CACHE_NAME


def _audit_binding(binding, permit):
    if permit is None:
        return
    if (type(permit) is not _AuditPermit or binding['prefix_offset'] != _AUDIT_SOURCE_SIZE
            or binding['prefix_sha256'] != _AUDIT_SOURCE_SHA256
            or binding['source_identity_sha256'] != hashlib.sha256(_canonical(permit.source_identity)).hexdigest()
            or binding['source_generation_sha256'] != hashlib.sha256(_canonical(None)).hexdigest()):
        raise CacheRefused("audit_binding")


class _AuditScope:
    """Hold synthetic source and manifest handles throughout cache I/O."""
    def __init__(self, native, permit):
        self.native, self.permit, self.handles, self.files = native, permit, [], []
        self.manifest = self.source = None

    def _parents(self, path, *, private):
        current = Path(path.anchor)
        opened = []
        for part in (None, *path.parts[1:]):
            if part is not None:
                current /= part
            handle = self.native.open(current, 3, 0x80, 3, directory=True)
            self.handles.append(handle)
            opened.append(handle)
        if private:
            self.native.user_acl(opened[-1], provisioned_dir=True)

    def _file(self, path, *, private):
        import msvcrt
        self._parents(path.parent, private=private)
        try:
            handle = self.native.open(path, 3, 0x80000000, 1)
        except CacheMiss:
            raise CacheRefused("audit_file_missing") from None
        try:
            if private:
                self.native.user_acl(handle)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.native.kernel.CloseHandle(handle)
            raise
        try:
            file = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        self.files.append(file)
        return file

    def _check(self):
        import msvcrt
        source_handle = msvcrt.get_osfhandle(self.source.fileno())
        info = _HandleInfo()
        if not self.native.kernel.GetFileInformationByHandle(source_handle, ctypes.byref(info)):
            raise CacheRefused("audit_source_stat")
        identity = f"windows-v1:{info.volume:08x}:{info.index_high:08x}{info.index_low:08x}"
        if identity != self.permit.source_identity or (info.size_high << 32 | info.size_low) != _AUDIT_SOURCE_SIZE:
            raise CacheRefused("audit_source_identity")
        self.source.seek(0)
        raw = self.source.read(_AUDIT_SOURCE_SIZE + 1)
        if len(raw) != _AUDIT_SOURCE_SIZE or hashlib.sha256(raw).hexdigest() != _AUDIT_SOURCE_SHA256:
            raise CacheRefused("audit_source_sha")
        self.manifest.seek(0)
        raw_manifest = self.manifest.read(4 * 1024 * 1024 + 1)
        if not 0 < len(raw_manifest) <= 4 * 1024 * 1024 or hashlib.sha256(raw_manifest).hexdigest() != self.permit.manifest_pin:
            raise CacheRefused("audit_manifest")
        document = json.loads(raw_manifest.decode("utf-8-sig"), object_pairs_hook=_unique)
        commit = document.get("source_commit") if type(document) is dict else None
        if (type(commit) is not str or not re.fullmatch(r"[0-9a-f]{40}", commit)
                or self.permit.bundle.name != "git-bundle-" + commit[:8] + "-real-pending"):
            raise CacheRefused("audit_bundle")

    def __enter__(self):
        try:
            if (not _same_path(self.permit.bundle.parent, _AUDIT_ROOT)
                    or not _AUDIT_BUNDLE_NAME.fullmatch(self.permit.bundle.name)):
                raise CacheRefused("audit_bundle")
            self.manifest = self._file(self.permit.bundle / "deployment-manifest.json", private=False)
            self.source = self._file(_AUDIT_SOURCE, private=True)
            self._check()
            return self
        except BaseException:
            self.__exit__(None, None, None, _verify=False)
            raise

    def __exit__(self, exc_type, exc, tb, _verify=True):
        try:
            if _verify and exc_type is None:
                self._check()
        finally:
            for file in reversed(self.files):
                file.close()
            for handle in reversed(self.handles):
                self.native.kernel.CloseHandle(handle)
            self.files.clear()
            self.handles.clear()


class _Directory:
    def __init__(self, path, audit=None):
        self.path, self.native, self.handles = path, _Native(), []
        self.audit, self.scope = audit, None

    def __enter__(self):
        path = Path(self.path)
        if not path.is_absolute() or ".." in path.parts or path.drive.upper() != "C:":
            raise CacheRefused("path")
        if self.audit is None:
            valid = _same_path(path, _CACHE_ROOT)
        else:
            if _AUDIT_WIP_ENABLED is not True:
                raise CacheRefused("audit_path_unverified")
            valid = type(self.audit) is _AuditPermit and _same_path(path, _AUDIT_CACHE_ROOT)
        if not valid:
            raise CacheRefused("cache_root")
        current = Path(path.anchor)
        try:
            if self.audit is not None:
                self.scope = _AuditScope(self.native, self.audit)
                self.scope.__enter__()
            for part in (None, *path.parts[1:]):
                if part is not None:
                    current /= part
                self.handles.append(self.native.open(current, 3, 0x80, 3, directory=True))
            # The operator provisions both directories: a private target alone
            # is insufficient when another identity can alter its parent.
            if len(self.handles) < 3:
                raise CacheRefused("cache_root")
            self.native.user_acl(self.handles[-2], provisioned_dir=True)
            self.native.user_acl(self.handles[-1], provisioned_dir=True)
            return self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            for handle in reversed(self.handles):
                self.native.kernel.CloseHandle(handle)
            self.handles.clear()
        finally:
            if self.scope is not None:
                scope, self.scope = self.scope, None
                scope.__exit__(exc_type, exc, tb)


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


def preflight(path, *, _audit=None):
    """Validate root/ACL/owner/lock and report presence, without reading data.

    MISSING is only the OS's exact FILE_NOT_FOUND for the single fixed cache
    entry inside the pinned directory while holding its fixed writer lock. It
    is not proof that a later create/write, disk allocation, or rename succeeds.
    The root must already have been provisioned by an authorized operator.
    """
    path = _plain_path(path)
    try:
        directory_context = _Directory(path.parent) if _audit is None else _Directory(path.parent, _audit)
        with directory_context as directory, _WriterLock(directory, path):
            fd = _fd(directory.native, path, 3, 0x80000000, 1, os.O_RDONLY, missing_ok=True)
            if fd is None:
                return "MISSING"
            os.close(fd)
            return "PRESENT"
    except CacheRefused:
        raise
    except Exception:
        raise CacheRefused("preflight") from None


def write_cache(path, binding, rows, *, _audit=None):
    """Persist vectors only; the destination directory must already be private."""
    path, binding, rows = _plain_path(path), _binding(binding), _rows(rows)
    _audit_binding(binding, _audit)
    header = _canonical(_header(binding, rows))
    if len(header) > _MAX_HEADER:
        raise CacheRefused("header")
    try:
        directory_context = _Directory(path.parent) if _audit is None else _Directory(path.parent, _audit)
        with directory_context as directory, _WriterLock(directory, path):
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


def load_cache(path, expected_binding, verify_prefix, *, _audit=None):
    """Return a complete, checked cache or raise; compare passage hashes again at use."""
    path, expected = _plain_path(path), _binding(expected_binding)
    _audit_binding(expected, _audit)
    if not callable(verify_prefix):
        raise CacheRefused("prefix_verifier")
    try:
        directory_context = _Directory(path.parent) if _audit is None else _Directory(path.parent, _audit)
        with directory_context as directory:
            # Do not share write/delete while parsing: a concurrent writer must
            # either publish before this open or wait until this read closes.
            try:
                fd = _fd(directory.native, path, 3, 0x80000000, 1, os.O_RDONLY)
            except FileNotFoundError:
                # A synthetic file opener can surface the OS exception rather
                # than the native wrapper's typed missing-cache result.
                raise CacheMiss("missing") from None
            with os.fdopen(fd, "rb") as source:
                initial = source.read(len(_MAGIC) + 4)
                if len(initial) != len(_MAGIC) + 4 or not initial.startswith(_MAGIC):
                    raise CacheRefused("header")
                length = struct.unpack("<I", initial[-4:])[0]
                if not 0 < length <= _MAX_HEADER:
                    raise CacheRefused("header")
                raw = source.read(length)
                header = json.loads(raw.decode("ascii"), object_pairs_hook=_unique)
                if (type(header) is not dict or set(header) != {"schema", "binding", "dimension", "dtype",
                        "count", "payload_sha256", "vectors_sha256", "record_set_sha256"} or _canonical(header) != raw
                        or header["schema"] != "wd.mind-vector-cache.v1" or header["dimension"] != 384
                        or type(header["dimension"]) is not int or header["dtype"] != "f32le"
                        or type(header["count"]) is not int or not 0 <= header["count"] <= _MAX_ROWS
                        or any(type(header[key]) is not str or not _HEX.fullmatch(header[key])
                               for key in ("payload_sha256", "vectors_sha256", "record_set_sha256"))):
                    raise CacheRefused("header")
                stored = _binding(header["binding"])
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
                    block = source.read(_ROW_SIZE)
                    if len(block) != _ROW_SIZE:
                        raise CacheRefused("truncated")
                    payload_hash.update(block)
                    vector_hash.update(block[64:])
                    record_set.update(block[:32])
                    rows.append((block[:32].hex(), block[32:64].hex(), block[64:]))
                if (source.read(1) or payload_hash.hexdigest() != header["payload_sha256"]
                        or vector_hash.hexdigest() != header["vectors_sha256"]
                        or record_set.hexdigest() != header["record_set_sha256"]):
                    raise CacheMiss("payload")
                if verify_prefix(stored["prefix_offset"], stored["prefix_sha256"]) is not True:
                    raise CacheMiss("prefix_changed")
                return CacheResult(MappingProxyType(stored), _rows(tuple(rows)), record_set.hexdigest())
    except CacheRefused:
        raise
    except Exception:
        raise CacheRefused("read") from None
