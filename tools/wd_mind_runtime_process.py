# SPDX-License-Identifier: BUSL-1.1
"""Offline preflight and separate default-off Windows runtime building blocks.

The caller must independently anchor every supplied pin and authorize every
path. This preflight checks three exact files and the external runtime manifest
using the pinned verifier before any runtime module, bootstrap, or model is
executed. The verifier is the separate packaged adaptation of
``sandbox/mind/step2/runtime_manifest.py`` at commit
``e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb``. It is loaded from the exact
bytes whose digest is checked, not by an ambient import path.

The default preflight has no subprocess, network, model load or write. The
separate, default-off guardian attempt reservation below can create one fixed
marker in the existing C: audit directory. It never starts a process or removes
that marker, including on failure. A separate opt-in local named-pipe primitive
transports one fixed public startup query and at most three untrusted reference
triples. An explicit-start service loop can invoke a caller-provided reference
resolver. A separate explicit guardian can start only the manifest-pinned owner
in a held non-kill Job and retain it through uncertain shutdown. Its marker is
removed only after EOF, owner exit zero, empty Job and completed/closed output
pumps. These blocks do not independently verify the callback's content.
There is also no assertion of process readiness, native DLL closure, OS network
isolation, or protection against same-user changes after verification. The
file and tree checks are synchronous: their wall-clock duration is not bounded
by a process watchdog. Those capabilities require an independently reviewed
runtime launcher and are deliberately not simulated by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CODE = 2 * 1024 * 1024
_MAX_INTERPRETER = 512 * 1024 * 1024
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)
# SHA256 of the packaged e98-derived verifier. This file and its constant must
# themselves be covered by an independently anchored bundle manifest.
_PACKAGED_VERIFIER_SHA256 = "d488a8495f0ee89aebc26d2d1345878d26e5a6c85b228e2c10086f4134680a64"
_VERIFIER_RELATIVE = "tools-bootstrap/tools/wd_mind_runtime_manifest.py"


class RuntimePreflightRefused(ValueError):
    """Fixed, path-free refusal for the offline preflight."""


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_pinned_file(value, pin, *, limit, retain=False):
    if type(pin) is not str or not _SHA256.fullmatch(pin):
        raise ValueError("pin")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            (os.name == "nt" and path.anchor.startswith("\\\\"))):
        raise ValueError("path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if ":" in part or part.endswith((".", " ")) or _DEVICE.fullmatch(part):
            raise ValueError("path")
        current /= part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse")
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= limit):
        raise ValueError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    blocks = [] if retain else None
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _identity(before) != _identity(opened):
            raise ValueError("file_changed")
        remaining = opened.st_size
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("file_changed")
            remaining -= len(block)
            digest.update(block)
            if retain:
                blocks.append(block)
        after = os.fstat(stream.fileno())
        final = os.lstat(path)
        if (stream.read(1) or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                or _identity(before) != _identity(after) or _identity(before) != _identity(final)):
            raise ValueError("file_changed")
    if not hmac.compare_digest(digest.hexdigest(), pin):
        raise ValueError("pin")
    return b"".join(blocks) if retain else None


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_manifest_key")
        result[key] = value
    return result


_GUARDIAN_AUDIT_ROOT = Path(r"C:\Python\project2\.codex-audit\wd-mind-runtime")
_GUARDIAN_MARKER = "mind-owner-attempt.json"
_PROCESS_RELATIVE = "tools-bootstrap/tools/wd_mind_runtime_process.py"
_LOCATION_NAME = "wd-mind-runtime-location.json"


class GuardianAttemptHold(ValueError):
    """Fixed refusal. reservation retains any handles whose close failed."""

    def __init__(self, reservation=None):
        super().__init__("mind_guardian_attempt_hold")
        self.reservation = reservation


class _GuardianNative:
    """Windows handle-relative create only; no pathname-create fallback."""

    def __init__(self):
        if os.name != "nt":
            raise ValueError("windows_required")
        import ctypes as c
        from ctypes import wintypes as w

        class UnicodeString(c.Structure):
            _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]

        class ObjectAttributes(c.Structure):
            _fields_ = [("Length", w.ULONG), ("RootDirectory", w.HANDLE),
                        ("ObjectName", c.POINTER(UnicodeString)), ("Attributes", w.ULONG),
                        ("SecurityDescriptor", c.c_void_p), ("SecurityQualityOfService", c.c_void_p)]

        class StatusUnion(c.Union):
            _fields_ = [("Status", w.LONG), ("Pointer", c.c_void_p)]

        class IoStatus(c.Structure):
            _fields_ = [("Status", StatusUnion), ("Information", c.c_size_t)]

        class FileInformation(c.Structure):
            _fields_ = [("attributes", w.DWORD), ("creation", w.FILETIME),
                        ("access", w.FILETIME), ("write", w.FILETIME), ("volume", w.DWORD),
                        ("size_high", w.DWORD), ("size_low", w.DWORD), ("links", w.DWORD),
                        ("index_high", w.DWORD), ("index_low", w.DWORD)]

        self.c, self.w = c, w
        self.UnicodeString, self.ObjectAttributes = UnicodeString, ObjectAttributes
        self.IoStatus, self.FileInformation = IoStatus, FileInformation
        self.handles = []
        self.nt = c.WinDLL("ntdll", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "GetFileInformationByHandle": ([w.HANDLE, c.POINTER(FileInformation)], w.BOOL),
            "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
            "WriteFile": ([w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
            "ReadFile": ([w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
            "FlushFileBuffers": ([w.HANDLE], w.BOOL),
            "SetFilePointerEx": ([w.HANDLE, c.c_longlong, c.POINTER(c.c_longlong), w.DWORD], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.nt.NtCreateFile.argtypes = [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(ObjectAttributes),
            c.POINTER(IoStatus), c.c_void_p, w.ULONG, w.ULONG, w.ULONG, w.ULONG, c.c_void_p, w.ULONG]
        self.nt.NtCreateFile.restype = w.LONG

    def open(self, parent, name, *, directory, delete_access=False):
        c, w = self.c, self.w
        buffer = c.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if not 0 < length < 32760:
            raise ValueError("name")
        text = self.UnicodeString(length, length + 2, c.cast(buffer, w.LPWSTR))
        # OBJ_DONT_REPARSE, OBJ_CASE_INSENSITIVE, no inheritable handle.
        attrs = self.ObjectAttributes(c.sizeof(self.ObjectAttributes), parent, c.pointer(text), 0x1040, None, None)
        handle, status = w.HANDLE(), self.IoStatus()
        # All ancestors stay open without FILE_SHARE_DELETE. Marker is created
        # relative to the retained last directory, never a reconstructed path.
        access = 0x100000 | 0x80 | (0x20 if directory else 0x3)
        if delete_access:
            if directory:
                raise ValueError("directory_delete_access")
            access |= 0x10000
        disposition = 1 if directory else 2  # FILE_OPEN / exclusive FILE_CREATE
        options = 0x200000 | 0x20 | (0x1 if directory else 0x40)
        result = self.nt.NtCreateFile(c.byref(handle), access, c.byref(attrs), c.byref(status),
            None, 0x80, 3 if directory else 1, disposition, options, None, 0)
        # Register the returned handle before validating result/disposition.
        # A later refusal or CloseHandle error must retain its owner channel.
        if handle.value:
            self.handles.append(handle.value)
        if result != 0:
            raise OSError("native_create_refused")
        # FILE_OPENED / FILE_CREATED are fixed results for these dispositions.
        if not handle.value or status.Information != (1 if directory else 2):
            raise OSError("native_disposition_refused")
        return handle.value

    def inspect(self, handle, expected_path, *, directory):
        c = self.c
        info = self.FileInformation()
        if not self.kernel.GetFileInformationByHandle(handle, c.byref(info)):
            raise OSError("native_information_refused")
        if bool(info.attributes & 0x10) != directory or info.attributes & 0x400:
            raise ValueError("reparse_or_type")
        buffer = c.create_unicode_buffer(32768)
        count = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer) or buffer.value.casefold() != ("\\\\?\\" + str(expected_path)).casefold():
            raise ValueError("opened_path")
        if not directory and info.links != 1:
            raise ValueError("hardlink")
        return ((info.volume, info.index_high, info.index_low), (info.size_high << 32) | info.size_low)

    def write(self, handle, raw):
        count = self.w.DWORD()
        if (not self.kernel.WriteFile(handle, raw, len(raw), self.c.byref(count), None)
                or count.value != len(raw) or not self.kernel.FlushFileBuffers(handle)):
            raise OSError("marker_write_refused")

    def read(self, handle, length):
        if not self.kernel.SetFilePointerEx(handle, 0, None, 0):
            raise OSError("marker_seek_refused")
        buffer, count = self.c.create_string_buffer(length + 1), self.w.DWORD()
        if not self.kernel.ReadFile(handle, buffer, len(buffer), self.c.byref(count), None):
            raise OSError("marker_read_refused")
        return buffer.raw[:count.value]

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise OSError("native_close_refused")


def _guardian_controls(bundle_root, manifest_pin):
    bundle = Path(bundle_root)
    raw = _read_pinned_file(bundle / "deployment-manifest.json", manifest_pin, limit=4 * 1024 * 1024, retain=True)
    manifest = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    if (type(manifest) is not dict or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1 or type(manifest.get("source_commit")) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"])
            or type(manifest.get("files")) is not dict):
        raise ValueError("manifest")
    pins = {}
    for name, limit in ((_PROCESS_RELATIVE, _MAX_CODE), (_LOCATION_NAME, 16384)):
        pin = manifest["files"].get(name)
        if type(pin) is not str or not _SHA256.fullmatch(pin.lower()):
            raise ValueError("manifest_pin")
        pins[name] = pin.lower()
        data = _read_pinned_file(bundle.joinpath(*name.split("/")), pin.lower(), limit=limit, retain=True)
        if name == _LOCATION_NAME:
            location = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
            if (type(location) is not dict or location.get("schema") != "wd.mind-runtime-location.v1"
                    or location.get("source_commit") != manifest["source_commit"]):
                raise ValueError("location")
    packaged = bundle.joinpath(*_PROCESS_RELATIVE.split("/"))
    if os.path.normcase(os.path.abspath(__file__)) != os.path.normcase(os.path.abspath(packaged)):
        raise ValueError("module_origin")
    return {"source_commit": manifest["source_commit"], "deployment_manifest_sha256": manifest_pin,
            "location_sha256": pins[_LOCATION_NAME]}


class _GuardianAttempt:
    def __init__(self, native, bundle, manifest_pin, controls):
        self._native, self._bundle, self._pin, self._controls = native, bundle, manifest_pin, controls
        self._handles, self._directories = native.handles, []
        self._marker, self._raw, self._marker_identity = None, None, None
        self._closing = False

    def _check(self):
        if self._closing or not self._handles or self._marker is None or self._raw is None:
            raise ValueError("incomplete_or_closed")
        for handle, path, identity in self._directories:
            if self._native.inspect(handle, path, directory=True)[0] != identity:
                raise ValueError("root_identity")
        root = self._directories[-1][1]
        identity, length = self._native.inspect(self._marker, root / _GUARDIAN_MARKER, directory=False)
        if (identity != self._marker_identity or length != len(self._raw)
                or self._native.read(self._marker, len(self._raw)) != self._raw
                or _guardian_controls(self._bundle, self._pin) != self._controls):
            raise ValueError("attempt_changed")
        return {"status": "RESERVED_ATTEMPT_ONLY", "authority": "none", "process_started": False,
                "same_user_race": "UNKNOWN", **self._controls}

    def check(self):
        try:
            return self._check()
        except Exception:
            pass
        raise GuardianAttemptHold(self) from None

    def close(self):
        """Release handles only. The marker ALWAYS remains for reconciliation."""
        self._closing = True
        failed = False
        while self._handles:
            handle = self._handles[-1]
            try:
                self._native.close(handle)
            except Exception:
                failed = True
                break  # Retain all ancestors until the child handle closes.
            self._handles.pop()
        if failed:
            raise GuardianAttemptHold(self) from None
        self._marker = None


def reserve_guardian_attempt(*, enabled=False, bundle_root=None, deployment_manifest_sha256=None):
    """Reserve a fixed attempt before any future spawn; never spawn or clean up.

    The external manifest pin must be independently authorized. This checks its
    module/location binding, not runtime assets, READY, IPC, EOF or job state.
    One existing marker blocks all users/generations at this fixed audit root.
    A partial write, crash or failed check leaves HOLD for operator reconciliation.
    Directory/marker handles must be retained by a future guardian. Closing them
    does not authorize deleting the marker. No same-user or power-loss guarantee.
    """
    return _reserve_guardian_attempt(enabled=enabled, bundle_root=bundle_root,
        deployment_manifest_sha256=deployment_manifest_sha256, cleanup_capable=False)


def _reserve_guardian_attempt(*, enabled, bundle_root, deployment_manifest_sha256, cleanup_capable):
    if enabled is False:
        return {"status": "OFF"}
    attempt = None
    try:
        if enabled is not True or os.name != "nt":
            raise ValueError("opt_in")
        controls = _guardian_controls(bundle_root, deployment_manifest_sha256)
        root = _GUARDIAN_AUDIT_ROOT
        audit = Path(r"C:\Python\project2\.codex-audit")
        if (not isinstance(root, Path) or root.drive != "C:" or not root.is_relative_to(audit)
                or root == audit or len(str(root)) > 1024):
            raise ValueError("audit_root")
        for part in root.parts[1:]:
            if (part in {".", ".."} or any(ord(ch) < 32 or ch in ':<>"|?*/' for ch in part)
                    or part.endswith((".", " ")) or _DEVICE.fullmatch(part)):
                raise ValueError("audit_component")
        native = _GuardianNative()
        attempt = _GuardianAttempt(native, bundle_root, deployment_manifest_sha256, controls)
        parent, path = None, Path(root.anchor)
        for name in ("\\??\\C:\\", *root.parts[1:]):
            if parent is not None:
                path /= name
            handle = native.open(parent, name, directory=True)
            identity, _ = native.inspect(handle, path, directory=True)
            attempt._directories.append((handle, path, identity))
            parent = handle
        # The only write-open uses a single basename plus RootDirectory, with
        # FILE_CREATE. Existing, partial, stale and malformed markers all HOLD.
        attempt._marker = (native.open(parent, _GUARDIAN_MARKER, directory=False, delete_access=True)
                           if cleanup_capable else native.open(parent, _GUARDIAN_MARKER, directory=False))
        attempt._marker_identity, _ = native.inspect(attempt._marker, root / _GUARDIAN_MARKER, directory=False)
        attempt._raw = json.dumps({"schema": "wd.mind-guardian-attempt.v1", **controls,
            "attempt_nonce": secrets.token_hex(32)}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(attempt._raw) > 1024:
            raise ValueError("marker_size")
        native.write(attempt._marker, attempt._raw)
        attempt._check()
        return attempt
    except Exception:
        if attempt is not None:
            try:
                attempt.close()
            except Exception:
                pass  # Failed handles remain attached to the returned HOLD.
    raise GuardianAttemptHold(attempt if attempt and attempt._handles else None) from None


# Transport only. These routing labels confer no identity or authority. The
# independent consumer must rederive all candidates from its pinned source.
_PIPE_NAMESPACE = "wd-mind-startup-candidates-v1"
_PIPE_LANES = frozenset(("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2"))
_PIPE_BYTES = 2048
_PIPE_OWNERS = set()
_PIPE_OWNERS_LOCK = threading.Lock()


class CandidatePipeHold(ValueError):
    """Fixed refusal; a pending operation remains owned by reservation/registry."""

    def __init__(self, reservation=None):
        super().__init__("mind_candidate_pipe_hold")
        self.reservation = reservation


class _PipeNotAvailable(OSError):
    """Only FILE_NOT_FOUND/PIPE_BUSY before a client handle was acquired."""


class _PipePeerClosed(OSError):
    """A completed read observed peer disconnect; no pending I/O remains."""


class _PipeConnectCancelled(OSError):
    """Our timed-out CONNECT cancellation completed with OPERATION_ABORTED."""


class _CandidatePipeIdle(CandidatePipeHold):
    """Only a confirmed, unconnected CONNECT timeout can be retried."""


_PIPE_MAX_TIMEOUT_SECONDS = 360
_PIPE_SERVER_IO_MAX_TIMEOUT_SECONDS = 15
_CANDIDATE_CALLBACK_TIMEOUT_SECONDS = 300
_CANDIDATE_QUERY_TIMEOUT_SECONDS = 330


def _pipe_deadline(timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= _PIPE_MAX_TIMEOUT_SECONDS):
        raise ValueError("timeout")
    return time.monotonic() + timeout


def _pipe_server_deadline(timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= _PIPE_SERVER_IO_MAX_TIMEOUT_SECONDS):
        raise ValueError("timeout")
    return _pipe_deadline(timeout)


def _pipe_validate(value, *, response=False, request=None):
    common = {"schema", "nonce", "lane", "scope"}
    keys = common | ({"status", "candidates"} if response else {"query_id"})
    if (type(value) is not dict or set(value) != keys
            or any(type(value[key]) is not str for key in common)
            or not _SHA256.fullmatch(value["nonce"]) or value["lane"] not in _PIPE_LANES
            or value["scope"] not in {"own", "shared"}):
        raise ValueError("frame_schema")
    if not response:
        if value["schema"] != "wd.mind-pipe-request.v1" or value["query_id"] != "startup_work_overview":
            raise ValueError("public_query_only")
    else:
        rows = value["candidates"]
        if (value["schema"] != "wd.mind-pipe-candidates.v1" or type(rows) is not list or len(rows) > 3
                or type(value["status"]) is not str
                or value["status"] != ("CANDIDATES" if rows else "ABSTAIN")):
            raise ValueError("candidates")
        seen = set()
        for row in rows:
            names = ("ref_sha256", "doc_id_sha256", "source_event_sha256")
            if (type(row) is not dict or set(row) != set(names)
                    or any(type(row[name]) is not str or not _SHA256.fullmatch(row[name]) for name in names)):
                raise ValueError("candidate")
            triple = tuple(row[name] for name in names)
            if triple in seen:
                raise ValueError("duplicate_candidate")
            seen.add(triple)
        if request is not None and any(value[key] != request[key] for key in ("nonce", "lane", "scope")):
            raise ValueError("response_binding")
    return value


def _pipe_frame(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 0 < len(raw) <= _PIPE_BYTES:
        raise ValueError("frame_size")
    return len(raw).to_bytes(4, "little") + raw


def _pipe_parse(raw, *, response=False, request=None):
    if (type(raw) is not bytes or not 4 < len(raw) <= _PIPE_BYTES + 4
            or int.from_bytes(raw[:4], "little") != len(raw) - 4):
        raise ValueError("frame_size")
    # Decode exactly once; json.loads(bytes) would auto-detect UTF16/32.
    value = json.loads(raw[4:].decode("utf-8"), object_pairs_hook=_unique_json_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    return _pipe_validate(value, response=response, request=request)


class _PipeNative(_GuardianNative):
    def __init__(self):
        super().__init__()
        c, w = self.c, self.w

        class Overlapped(c.Structure):
            _fields_ = [("Internal", c.c_size_t), ("InternalHigh", c.c_size_t),
                        ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]

        class SecurityAttributes(c.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]

        class SidAttributes(c.Structure):
            _fields_ = [("sid", c.c_void_p), ("attributes", w.DWORD)]

        class TokenGroups(c.Structure):
            _fields_ = [("count", w.DWORD), ("groups", SidAttributes * 1)]

        self.Overlapped, self.SecurityAttributes = Overlapped, SecurityAttributes
        self.SidAttributes, self.TokenGroups = SidAttributes, TokenGroups
        self.allocations = []
        self.advapi = c.WinDLL("advapi32", use_last_error=True)
        functions = {
            "GetCurrentProcess": ([], w.HANDLE),
            "LocalFree": ([c.c_void_p], c.c_void_p),
            "CreateNamedPipeW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
                                  c.POINTER(SecurityAttributes)], w.HANDLE),
            "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            "SetNamedPipeHandleState": ([w.HANDLE, c.POINTER(w.DWORD), c.c_void_p, c.c_void_p], w.BOOL),
            "CreateEventW": ([c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            "ConnectNamedPipe": ([w.HANDLE, c.POINTER(Overlapped)], w.BOOL),
            "GetOverlappedResult": ([w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL], w.BOOL),
            "CancelIoEx": ([w.HANDLE, c.POINTER(Overlapped)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        }
        for name, (args, result) in functions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        for name, args in {
            "OpenProcessToken": [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)],
            "GetTokenInformation": [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)],
            "ConvertSidToStringSidW": [c.c_void_p, c.POINTER(c.c_void_p)],
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": [w.LPCWSTR, w.DWORD,
                c.POINTER(c.c_void_p), c.POINTER(w.DWORD)],
        }.items():
            function = getattr(self.advapi, name)
            function.argtypes, function.restype = args, w.BOOL

    def retain(self, handle):
        if not handle or handle == self.c.c_void_p(-1).value:
            raise OSError("pipe_handle")
        self.handles.append(handle)
        return handle

    def free(self, pointer):
        if self.kernel.LocalFree(pointer):
            raise OSError("native_free")
        self.allocations.remove(pointer)

    def token_info(self, token, kind):
        needed = self.w.DWORD()
        ok = self.advapi.GetTokenInformation(token, kind, None, 0, self.c.byref(needed))
        if ok or self.c.get_last_error() != 122 or not 0 < needed.value <= 65536:
            raise ValueError("token_size")
        raw = self.c.create_string_buffer(needed.value)
        returned = self.w.DWORD()
        if (not self.advapi.GetTokenInformation(token, kind, raw, len(raw), self.c.byref(returned))
                or returned.value != len(raw)):
            raise ValueError("token_information")
        return raw

    def elevation(self, token):
        c, w = self.c, self.w
        elevation, returned = w.DWORD(), w.DWORD()
        if (not self.advapi.GetTokenInformation(token, 20, c.byref(elevation), c.sizeof(elevation), c.byref(returned))
                or returned.value != c.sizeof(elevation) or elevation.value not in (0, 1)):
            raise ValueError("elevation")
        return elevation.value

    def logon_sid(self, *, require_non_elevated=True):
        c, w = self.c, self.w
        token = w.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, c.byref(token)):
            raise ValueError("token")
        self.retain(token.value)
        elevation = self.elevation(token)
        if type(require_non_elevated) is not bool or (require_non_elevated and elevation != 0):
            raise ValueError("non_elevated_only")
        raw = self.token_info(token, 2)
        offset, stride = self.TokenGroups.groups.offset, c.sizeof(self.SidAttributes)
        count = c.cast(raw, c.POINTER(w.DWORD)).contents.value
        if len(raw) < offset or offset + count * stride > len(raw):
            raise ValueError("groups")
        found = []
        for index in range(count):
            item = self.SidAttributes.from_address(c.addressof(raw) + offset + index * stride)
            if item.attributes & 0xC0000000 != 0xC0000000:
                continue
            if not item.attributes & 4 or item.attributes & 16:
                raise ValueError("logon_attributes")
            text = c.c_void_p()
            if not self.advapi.ConvertSidToStringSidW(item.sid, c.byref(text)) or not text.value:
                raise ValueError("sid")
            self.allocations.append(text.value)
            found.append(c.wstring_at(text))
            self.free(text.value)
        self.close(token.value)
        self.handles.remove(token.value)
        if len(found) != 1 or not re.fullmatch(r"S-1-5-5-[0-9]+-[0-9]+", found[0]):
            raise ValueError("logon_sid")
        return found[0]

    def server(self, name, sid):
        c, w = self.c, self.w
        descriptor = c.c_void_p()
        # Exact data/read-write-attributes/synchronize; excludes generic write
        # and FILE_CREATE_PIPE_INSTANCE. No Everyone/anonymous/default DACL.
        sddl = "D:P(A;;0x00100183;;;" + sid + ")"
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, c.byref(descriptor), None):
            raise ValueError("pipe_acl")
        if not descriptor.value:
            raise ValueError("pipe_acl")
        self.allocations.append(descriptor.value)
        attributes = self.SecurityAttributes(c.sizeof(self.SecurityAttributes), descriptor, False)
        # DUPLEX | OVERLAPPED | FIRST_INSTANCE; MESSAGE | READMODE_MESSAGE |
        # REJECT_REMOTE_CLIENTS. Only one instance exists for this logon name.
        handle = self.retain(self.kernel.CreateNamedPipeW(name, 0x40080003, 0xE, 1,
            _PIPE_BYTES + 4, _PIPE_BYTES + 4, 0, c.byref(attributes)))
        self.free(descriptor.value)
        return handle

    def client(self, name):
        # Explicit IDENTIFICATION prevents the server impersonating our token.
        handle = self.kernel.CreateFileW(name, 0x00100183, 0, None, 3,
            0x40000000 | 0x00100000 | 0x00010000, None)
        if not handle or handle == self.c.c_void_p(-1).value:
            if self.c.get_last_error() in (2, 231):
                raise _PipeNotAvailable("pipe_temporarily_absent_or_busy")
            raise OSError("pipe_open_refused")
        self.retain(handle)
        mode = self.w.DWORD(2)
        if not self.kernel.SetNamedPipeHandleState(handle, self.c.byref(mode), None, None):
            raise OSError("pipe_mode")
        return handle


class _CandidatePipe:
    def __init__(self, bundle, pin, controls):
        self._bundle, self._pin, self._controls = bundle, pin, controls
        self._native = self._pipe = self._pending = self._request = None
        self._failed = self._replied = False
        self._lock = threading.Lock()

    def _controls_check(self):
        if _guardian_controls(self._bundle, self._pin) != self._controls:
            raise ValueError("controls_changed")

    def _poll(self):
        operation = self._pending
        if operation is None or not operation["pending"]:
            return True, 0, 0
        native = self._native
        count = native.w.DWORD()
        ok = native.kernel.GetOverlappedResult(self._pipe, native.c.byref(operation["overlapped"]),
                                               native.c.byref(count), False)
        error = 0 if ok else native.c.get_last_error()
        if not ok and error == 996:  # ERROR_IO_INCOMPLETE: still owns buffers.
            return False, 0, error
        # These are the completion results used by our three pipe operations.
        # An unexpected API failure does not prove that pending I/O has ended.
        if not ok and error not in (995, 109, 232, 233, 234):
            return False, 0, error
        operation["pending"] = False
        return True, count.value, error

    def _release_operation(self):
        operation = self._pending
        if operation is not None:
            if operation["pending"]:
                raise ValueError("pending")
            event = operation["overlapped"].hEvent
            self._native.close(event)
            self._native.handles.remove(event)
            self._pending = None

    def _io(self, kind, deadline, raw=None):
        if self._pending is not None or self._failed or time.monotonic() >= deadline:
            raise ValueError("io_state_or_deadline")
        native = self._native
        c, w = native.c, native.w
        event = native.retain(native.kernel.CreateEventW(None, True, False, None))
        overlapped = native.Overlapped()
        overlapped.hEvent = event
        buffer = c.create_string_buffer(raw) if raw is not None else c.create_string_buffer(_PIPE_BYTES + 5)
        self._pending = {"overlapped": overlapped, "buffer": buffer, "pending": False}
        count = w.DWORD()
        if kind == "connect":
            ok = native.kernel.ConnectNamedPipe(self._pipe, c.byref(overlapped))
        else:
            function = native.kernel.WriteFile if kind == "write" else native.kernel.ReadFile
            ok = function(self._pipe, buffer, len(raw) if raw is not None else len(buffer), c.byref(count), c.byref(overlapped))
        error = 0 if ok else c.get_last_error()
        if not ok and kind == "connect" and error == 535:
            error = 0  # Client connected before ConnectNamedPipe; no pending I/O.
        elif not ok and error == 997:
            self._pending["pending"] = True
            remaining = deadline - time.monotonic()
            # The validated deadline, rather than a separate 15s ceiling, owns
            # this operation. Timeout still takes the same cancellation path.
            wait = native.kernel.WaitForSingleObject(event, max(0, int(remaining * 1000)))
            complete, transferred, error = self._poll()
            if wait != 0 or not complete or time.monotonic() >= deadline:
                cancelled = native.kernel.CancelIoEx(self._pipe, c.byref(overlapped))
                settled, _, cancellation_error = self._poll()
                if kind == "connect" and wait == 258 and cancelled and settled and cancellation_error == 995:
                    self._release_operation()
                    raise _PipeConnectCancelled("idle_connect_cancelled")
                raise ValueError("deadline_or_wait")
            count.value = transferred
        if kind == "read" and error in (109, 232, 233) and time.monotonic() < deadline:
            self._release_operation()
            raise _PipePeerClosed("peer_closed")
        if error or time.monotonic() >= deadline or (kind == "write" and count.value != len(raw)):
            raise ValueError("io_refused")
        result = buffer.raw[:count.value] if kind == "read" else None
        self._release_operation()
        return result

    def receive(self, *, timeout=5):
        if not self._lock.acquire(blocking=False):
            raise CandidatePipeHold(self) from None
        result, idle = None, False
        try:
            deadline = _pipe_server_deadline(timeout)
            if self._request is not None or self._replied or self._pipe is None:
                raise ValueError("one_request_only")
            self._controls_check()
            self._io("connect", deadline)
            result = _pipe_parse(self._io("read", deadline))
            self._controls_check()
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            self._request = dict(result)
        except _PipeConnectCancelled:
            self._failed, idle = True, True
        except Exception:
            self._failed, result = True, None
        finally:
            self._lock.release()
        if result is None:
            if idle:
                raise _CandidatePipeIdle(self) from None
            raise CandidatePipeHold(self) from None
        return result

    def reply(self, *, candidates, timeout=5):
        if not self._lock.acquire(blocking=False):
            raise CandidatePipeHold(self) from None
        success = False
        try:
            deadline = _pipe_server_deadline(timeout)
            if self._request is None or self._replied:
                raise ValueError("request")
            request = self._request
            value = {"schema": "wd.mind-pipe-candidates.v1", "nonce": request["nonce"],
                "lane": request["lane"], "scope": request["scope"],
                "status": "CANDIDATES" if candidates else "ABSTAIN", "candidates": candidates}
            # Serialized bytes are validated again to exclude callback/concurrent
            # mutations during serialization; only those exact bytes are sent.
            raw = _pipe_frame(_pipe_validate(value, response=True, request=request))
            _pipe_parse(raw, response=True, request=request)
            self._controls_check()
            self._io("write", deadline, raw)
            self._controls_check()
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            self._replied, success = True, True
        except Exception:
            self._failed = True
        finally:
            self._lock.release()
        if not success:
            raise CandidatePipeHold(self) from None

    def close(self):
        """Cancel, then release only completed I/O. HOLD retains every buffer."""
        if not self._lock.acquire(blocking=False):
            return {"state": "BUSY", "owned": True}
        try:
            self._failed = True
            native = self._native
            if native is not None:
                if self._pending is not None and self._pending["pending"]:
                    native.kernel.CancelIoEx(self._pipe, native.c.byref(self._pending["overlapped"]))
                    if not self._poll()[0]:
                        return {"state": "HOLD", "owned": True}
                self._release_operation()
                while native.handles:
                    native.close(native.handles[-1])
                    native.handles.pop()
                while native.allocations:
                    native.free(native.allocations[-1])
            self._pipe = None
            with _PIPE_OWNERS_LOCK:
                _PIPE_OWNERS.discard(self)
            return {"state": "CLOSED", "owned": False}
        except Exception:
            return {"state": "HOLD", "owned": True}
        finally:
            self._lock.release()

    def _wait_for_client_close(self, timeout):
        # Do not discard a queued reply by immediately replacing the server
        # instance. The ordinary client closes only after it validates the reply.
        # This is transport settlement, not acknowledgement of source relevance.
        if not self._lock.acquire(blocking=False):
            raise CandidatePipeHold(self) from None
        success = False
        try:
            deadline = _pipe_server_deadline(timeout)
            if not self._replied:
                raise ValueError("not_replied")
            try:
                self._io("read", deadline)
            except _PipePeerClosed:
                success = True
            self._controls_check()
            if not success or time.monotonic() >= deadline:
                raise ValueError("extra_request_or_deadline")
        except Exception:
            self._failed, success = True, False
        finally:
            self._lock.release()
        if not success:
            raise CandidatePipeHold(self) from None


def _candidate_pipe_open(bundle_root, pin, *, server, deadline=None):
    controls = _guardian_controls(bundle_root, pin)
    channel = _CandidatePipe(bundle_root, pin, controls)
    with _PIPE_OWNERS_LOCK:
        if len(_PIPE_OWNERS) >= 6:
            raise CandidatePipeHold() from None
        _PIPE_OWNERS.add(channel)
    try:
        channel._native = native = _PipeNative()
        # The server/owner remains Limited. Elevated clients send only the
        # fixed public query and use IDENTIFICATION SQOS, not impersonation.
        sid = native.logon_sid(require_non_elevated=server)
        if not re.fullmatch(r"[a-z0-9-]{1,80}", _PIPE_NAMESPACE):
            raise ValueError("namespace")
        name = "\\\\.\\pipe\\" + _PIPE_NAMESPACE + "-" + hashlib.sha256(sid.encode("ascii")).hexdigest()
        if server:
            channel._pipe = native.server(name, sid)
        else:
            while True:
                channel._controls_check()
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("open_deadline")
                try:
                    channel._pipe = native.client(name)
                    break
                except _PipeNotAvailable:
                    # No handle or pending I/O exists on these two errors.
                    # ACL, pin, mode and every unknown failure are never retried.
                    if deadline is None or time.monotonic() >= deadline:
                        raise
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        channel._controls_check()
        return channel
    except Exception:
        channel.close()
    raise CandidatePipeHold(channel if channel in _PIPE_OWNERS else None) from None


def create_candidate_pipe(*, enabled=False, bundle_root=None, deployment_manifest_sha256=None):
    """Default-off one-connection transport; not READY, owner or peer authentication.

    No process is started. The external caller must retain/close this object;
    unresolved I/O also remains in the bounded per-module ownership registry.
    No marker is created or removed by this API. Same-user squatting remains
    possible; only a fixed public query and untrusted references cross the pipe.
    """
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True:
            raise ValueError("opt_in")
        return _candidate_pipe_open(bundle_root, deployment_manifest_sha256, server=True)
    except CandidatePipeHold:
        raise
    except Exception:
        pass
    raise CandidatePipeHold() from None


def query_candidate_pipe(*, enabled=False, bundle_root=None, deployment_manifest_sha256=None,
                         lane=None, scope="shared", timeout=5):
    """Send only the fixed public startup query; return UNTRUSTED references.

    Revalidate every candidate using independently pinned local source/policy.
    This does not prove ranking, model execution, peer identity or completeness.
    Elevated clients are permitted with the real logon SID and IDENTIFICATION
    SQOS; servers remain non-elevated-only. No client identity is authenticated.
    Native/control calls are synchronous; the deadline is checked around them,
    not an OS-enforced guarantee that every kernel/filesystem call terminates.
    """
    if enabled is False:
        return {"status": "OFF"}
    channel = result = None
    try:
        deadline = _pipe_deadline(timeout)
        if enabled is not True:
            raise ValueError("opt_in")
        request = _pipe_validate({"schema": "wd.mind-pipe-request.v1", "query_id": "startup_work_overview",
                                 "nonce": secrets.token_hex(32), "lane": lane, "scope": scope})
        raw = _pipe_frame(request)
        channel = _candidate_pipe_open(bundle_root, deployment_manifest_sha256, server=False, deadline=deadline)
        channel._io("write", deadline, raw)
        result = _pipe_parse(channel._io("read", deadline), response=True, request=request)
        channel._controls_check()
        if time.monotonic() >= deadline:
            raise ValueError("deadline")
    except CandidatePipeHold as error:
        if channel is None:
            channel = error.reservation
        result = None
    except Exception:
        result = None
    if channel is not None and channel.close() != {"state": "CLOSED", "owned": False}:
        result = None
    if result is None:
        raise CandidatePipeHold(channel if channel in _PIPE_OWNERS else None) from None
    return result


_SERVICE_OWNERS = set()
_SERVICE_OWNERS_LOCK = threading.Lock()


class CandidateServiceHold(ValueError):
    def __init__(self, service=None):
        super().__init__("mind_candidate_service_hold")
        self.service = service


class _CandidateService:
    """One explicit thread loop; caller retains source/provider authority."""

    def __init__(self, bundle, pin, handler, receive_timeout, reply_timeout, callback_timeout):
        self._bundle, self._pin, self._handler = bundle, pin, handler
        self._receive_timeout, self._reply_timeout, self._callback_timeout = receive_timeout, reply_timeout, callback_timeout
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._runner = self._callback = self._pipe = None
        self._state = "NEW"

    def _observation(self, state=None):
        job, channel = self._callback, self._pipe
        return {"state": state or self._state, "owned": self in _SERVICE_OWNERS,
                "callback_pending": job is not None and (job["starting"] or job["thread"].is_alive()),
                "io_pending": channel is not None and channel._pending is not None and channel._pending["pending"]}

    def status(self):
        with self._lock:
            return self._observation()

    def _set_state(self, value):
        with self._lock:
            self._state = value

    def start(self):
        result = None
        with self._lock:
            if self._state != "NEW" or self._stop.is_set() or self._runner is not None:
                raise CandidateServiceHold(self) from None
            self._state = "STARTING"
            self._runner = threading.Thread(target=self._loop, name="mind-candidate-loop", daemon=False)
            try:
                self._runner.start()
            except BaseException:
                self._stop.set()
                self._state = "HOLD"
            else:
                result = self._observation()
        if result is None:
            raise CandidateServiceHold(self) from None
        return result

    def _call(self, request):
        frozen = dict(request)
        supplied = dict(request)
        done = threading.Event()
        job = {"done": done, "result": None, "error": False, "starting": True}
        handler = self._handler
        def invoke():
            try:
                job["result"] = handler(supplied)
            except BaseException:
                job["error"] = True
            finally:
                done.set()
        thread = threading.Thread(target=invoke, name="mind-candidate-callback", daemon=False)
        job["thread"] = thread
        deadline = time.monotonic() + self._callback_timeout
        with self._lock:
            if self._stop.is_set():
                raise ValueError("stopping")
            self._callback = job  # Retain before start, including start errors.
            self._state = "CALLBACK"
            try:
                thread.start()
            finally:
                job["starting"] = False
        while not done.is_set() and not self._stop.is_set() and time.monotonic() < deadline:
            done.wait(min(0.02, max(0, deadline - time.monotonic())))
        if self._stop.is_set() or not done.is_set():
            raise ValueError("callback_pending_or_stopped")
        thread.join(max(0, deadline - time.monotonic()))
        if thread.is_alive() or time.monotonic() >= deadline or job["error"] or supplied != frozen:
            raise ValueError("callback_refused")
        # Freeze and revalidate the exact scalar-only callback output. This is
        # a format check; the caller must supply the actual source-bound resolver.
        rows = job["result"]
        response = {"schema": "wd.mind-pipe-candidates.v1", "nonce": frozen["nonce"],
            "lane": frozen["lane"], "scope": frozen["scope"],
            "status": "CANDIDATES" if rows else "ABSTAIN", "candidates": rows}
        raw = _pipe_frame(_pipe_validate(response, response=True, request=frozen))
        rows = _pipe_parse(raw, response=True, request=frozen)["candidates"]
        job["result"] = None
        with self._lock:
            self._callback = None  # Actual callback thread exit was established.
        return rows

    def _loop(self):
        failed = False
        try:
            while not self._stop.is_set():
                channel = create_candidate_pipe(enabled=True, bundle_root=self._bundle, deployment_manifest_sha256=self._pin)
                with self._lock:
                    self._pipe = channel
                    self._state = "RECEIVING"
                if self._stop.is_set():
                    break
                try:
                    request = channel.receive(timeout=self._receive_timeout)
                except _CandidatePipeIdle:
                    # Exactly our unconnected CONNECT was cancelled and its
                    # completion observed. No request/callback may exist, and
                    # every endpoint handle must close before a new instance.
                    if self._callback is not None or channel._request is not None or channel._pending is not None:
                        raise ValueError("idle_not_settled")
                    if channel.close() != {"state": "CLOSED", "owned": False}:
                        raise ValueError("idle_handle_retained")
                    with self._lock:
                        self._pipe = None
                    continue
                if self._stop.is_set():
                    break
                rows = self._call(request)
                if self._stop.is_set():
                    break
                self._set_state("REPLYING")
                channel.reply(candidates=rows, timeout=self._reply_timeout)
                channel._wait_for_client_close(self._reply_timeout)
                if channel.close() != {"state": "CLOSED", "owned": False}:
                    raise ValueError("pipe_retained")
                with self._lock:
                    self._pipe = None
        except BaseException as error:
            failed = True
            if isinstance(error, CandidatePipeHold) and self._pipe is None:
                with self._lock:
                    self._pipe = error.reservation
        finally:
            channel = self._pipe
            if channel is not None:
                if channel.close() == {"state": "CLOSED", "owned": False}:
                    with self._lock:
                        self._pipe = None
                else:
                    failed = True
            # Registry remains until stop() observes this loop thread's exit.
            self._set_state("HOLD" if failed else "STOPPING")

    def stop(self, *, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 15:
            raise CandidateServiceHold(self) from None
        deadline = time.monotonic() + timeout
        self._stop.set()  # Does not claim a callback/IO already in flight ended.
        if not self._lock.acquire(blocking=False):
            return {"state": "BUSY", "owned": True, "callback_pending": True, "io_pending": True}
        try:
            runner, job = self._runner, self._callback
        finally:
            self._lock.release()
        for thread in (runner, None if job is None else job["thread"]):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        with self._lock:
            if runner is not None and runner.is_alive():
                return self._observation("BUSY")
            if self._callback is not None and (self._callback["starting"] or self._callback["thread"].is_alive()):
                self._state = "HOLD"
                return self._observation()
            if self._pipe is not None and self._pipe.close() != {"state": "CLOSED", "owned": False}:
                self._state = "HOLD"
                return self._observation()
            self._pipe = self._callback = None
            self._state = "STOPPED"
            with _SERVICE_OWNERS_LOCK:
                _SERVICE_OWNERS.discard(self)
            return self._observation()


def create_candidate_service(*, enabled=False, bundle_root=None, deployment_manifest_sha256=None, handler=None,
                             receive_timeout=5, reply_timeout=5, callback_timeout=5):
    """Return an inert, explicit-start service for a trusted ref-only callback.

    The callback must independently bind source/policy and return only the
    validated reference triples. This wrapper cannot prove that responsibility.
    It starts no worker/process and claims neither READY nor peer authentication.
    A callback can allocate arbitrarily or never finish: its non-daemon thread
    and service slot then remain HOLD. There is no kill, restart or recovery.
    Only our confirmed cancelled CONNECT plus closed endpoint permits another
    idle wait. A connection race, unknown/pending completion, close error or
    callback/reply failure stays HOLD. Call stop() to settle ownership explicitly.
    """
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True or not callable(handler) or isinstance(handler, type):
            raise ValueError("handler")
        for value in (receive_timeout, reply_timeout):
            _pipe_server_deadline(value)
        _pipe_deadline(callback_timeout)
        _guardian_controls(bundle_root, deployment_manifest_sha256)
        service = _CandidateService(bundle_root, deployment_manifest_sha256, handler,
                                    receive_timeout, reply_timeout, callback_timeout)
        with _SERVICE_OWNERS_LOCK:
            if _SERVICE_OWNERS:
                raise ValueError("service_retained")
            _SERVICE_OWNERS.add(service)
        return service
    except Exception:
        pass
    raise CandidateServiceHold() from None


class GuardianProcessHold(ValueError):
    def __init__(self, guardian=None):
        super().__init__("mind_guardian_process_hold")
        self.guardian = guardian


_GUARDIAN_OWNERS = set()
_GUARDIAN_OWNERS_LOCK = threading.Lock()
_OWNER_TRAMPOLINE = ("import sys,runpy;sys.path.insert(0,sys.argv.pop(1));"
                     "runpy.run_module('tools.wd_mind_full_runtime',run_name='__main__')")


def _guardian_fast_build(document):
    """Only an externally pinned, explicit v2 release selects the fast path."""
    if (type(document) is not dict or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1):
        raise ValueError('manifest')
    if 'mind_startup_guardian' not in document:
        return False  # Preserve the explicit legacy guardian API.
    config = document['mind_startup_guardian']
    keys = {'schema', 'enabled', 'entrypoint', 'ready_timeout_seconds'}
    if type(config) is not dict:
        raise ValueError('guardian_config')
    fast = config.get('schema') == 'wd.mind-startup-guardian.v2'
    if (set(config) != (keys | {'fast_build'} if fast else keys)
            or config.get('schema') not in ('wd.mind-startup-guardian.v1', 'wd.mind-startup-guardian.v2')
            or config['enabled'] is not True or config['entrypoint'] != 'tools/wd_mind_runtime_process.py'
            or type(config['ready_timeout_seconds']) is not int
            or (not fast and config['ready_timeout_seconds'] != 15)
            or (fast and not 15 <= config['ready_timeout_seconds'] <= 600)
            or (fast and config['fast_build'] is not True)):
        raise ValueError('guardian_config')
    return fast


def _guardian_owner_argv(executable, bootstrap, bundle, pin):
    raw = _read_pinned_file(Path(bundle) / 'deployment-manifest.json', pin, limit=4 * 1024 * 1024, retain=True)
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_json_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
    args = [executable, '-I', '-S', '-B', '-c', _OWNER_TRAMPOLINE, bootstrap,
            '--owner', '--bundle-root', str(bundle), '--deployment-manifest-sha256', pin]
    if _guardian_fast_build(document):
        args.append('--fast-build')
    return args


def _guardian_launch_inputs(bundle_root, pin):
    """Check every importable bundle member before importing the owner module."""
    _guardian_controls(bundle_root, pin)
    bundle = Path(bundle_root)
    raw = _read_pinned_file(bundle / "deployment-manifest.json", pin, limit=4 * 1024 * 1024, retain=True)
    document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_json_object)
    _guardian_fast_build(document)
    files = document["files"]
    root = bundle / "tools-bootstrap"
    expected = {name[len("tools-bootstrap/"):]: digest for name, digest in files.items()
                if type(name) is str and name.startswith("tools-bootstrap/")}
    if not expected or len(expected) > 4096:
        raise ValueError("bootstrap_members")
    found, stack, total = set(), [root], 0
    while stack:
        directory = stack.pop()
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("bootstrap_directory")
        for path in directory.iterdir():
            total += 1
            if total > 8192:
                raise ValueError("bootstrap_budget")
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("bootstrap_reparse")
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
            else:
                relative = path.relative_to(root).as_posix()
                if relative not in expected:
                    raise ValueError("unbound_import_member")
                digest = expected[relative]
                if type(digest) is not str:
                    raise ValueError("bootstrap_pin")
                _read_pinned_file(path, digest.lower(), limit=8 * 1024 * 1024)
                found.add(relative)
    if found != set(expected):
        raise ValueError("missing_import_member")
    # Also reject an already imported owner from an ambient package origin.
    path = root / "tools/wd_mind_full_runtime.py"
    package = sys.modules.get("tools")
    if package is None:
        # The packaged PowerShell route executes a file, not -m. Resolve its
        # parent only after the complete bootstrap tree was hash-checked.
        parent_spec = importlib.util.find_spec("tools")
        expected_parent = root / "tools/__init__.py"
        if ("tools/__init__.py" not in found or parent_spec is None
                or os.path.normcase(os.path.abspath(parent_spec.origin or "")) != os.path.normcase(str(expected_parent))):
            raise ValueError("tools_import_origin")
        package = importlib.import_module("tools")
    locations = tuple(getattr(package, "__path__", ()))
    if (len(locations) != 1 or os.path.normcase(os.path.abspath(locations[0]))
            != os.path.normcase(str(root / "tools"))):
        raise ValueError("tools_package_origin")
    existing = sys.modules.get("tools.wd_mind_full_runtime")
    if existing is not None and os.path.normcase(os.path.abspath(getattr(existing, "__file__", ""))) != os.path.normcase(str(path)):
        raise ValueError("owner_origin")
    spec = importlib.util.find_spec("tools.wd_mind_full_runtime")
    if spec is None or os.path.normcase(os.path.abspath(spec.origin or "")) != os.path.normcase(str(path)):
        raise ValueError("owner_import_origin")
    owner = importlib.import_module("tools.wd_mind_full_runtime")
    if os.path.normcase(os.path.abspath(getattr(owner, "__file__", ""))) != os.path.normcase(str(path)):
        raise ValueError("owner_origin")
    if owner._owner_preflight(bundle_root=str(bundle), manifest_sha256=pin) is not True:
        raise ValueError("owner_preflight")
    resolved = owner.resolve_manifest_runtime_location(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin)
    if (type(resolved) is not dict or resolved.get("runtime_status") != "VERIFIED"
            or resolved.get("interpreter_status") != "VERIFIED"):
        raise ValueError("runtime_preflight")
    executable, digest = resolved["interpreter"], resolved["interpreter_sha256"]
    _read_pinned_file(executable, digest, limit=_MAX_INTERPRETER)
    return str(Path(executable)), str(root)


class _GuardianProcessNative(_PipeNative):
    """Raw process handles remain registered even before native calls return."""

    def __init__(self):
        super().__init__()
        c, w = self.c, self.w
        class Startup(c.Structure):
            _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                        ("x", w.DWORD), ("y", w.DWORD), ("cx", w.DWORD), ("cy", w.DWORD),
                        ("xchars", w.DWORD), ("ychars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", c.c_void_p),
                        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]
        class StartupEx(c.Structure):
            _fields_ = [("startup", Startup), ("attributes", c.c_void_p)]
        class ProcessInformation(c.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]
        class BasicLimit(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
                        ("min_working", c.c_size_t), ("max_working", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IoCounters(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimit(c.Structure):
            _fields_ = [("basic", BasicLimit), ("io", IoCounters), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        class Accounting(c.Structure):
            _fields_ = [(name, c.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
                (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]
        self.StartupEx, self.ExtendedLimit, self.Accounting = StartupEx, ExtendedLimit, Accounting
        self.process_info = ProcessInformation()  # Preallocated before any spawn.
        self.pipes = [(w.HANDLE(), w.HANDLE()) for _ in range(3)]
        self.job = None
        self._attribute_buffer = None
        functions = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "QueryInformationJobObject": ([w.HANDLE, w.DWORD, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "IsProcessInJob": ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CreatePipe": ([c.POINTER(w.HANDLE), c.POINTER(w.HANDLE), c.POINTER(self.SecurityAttributes), w.DWORD], w.BOOL),
            "SetHandleInformation": ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            "InitializeProcThreadAttributeList": ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
            "UpdateProcThreadAttribute": ([c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p], w.BOOL),
            "DeleteProcThreadAttributeList": ([c.c_void_p], None),
            "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p,
                                w.LPCWSTR, c.POINTER(StartupEx), c.POINTER(ProcessInformation)], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            "GetWindowsDirectoryW": ([w.LPWSTR, w.UINT], w.UINT),
            "SetFileInformationByHandle": ([w.HANDLE, w.DWORD, c.c_void_p, w.DWORD], w.BOOL),
        }
        for name, (args, result) in functions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result

    def in_job(self, process, job=None):
        member = self.w.BOOL()
        if not self.kernel.IsProcessInJob(process, job, self.c.byref(member)):
            raise OSError("job_membership")
        return bool(member.value)

    def job_limits(self):
        limits = self.ExtendedLimit()
        if not self.kernel.QueryInformationJobObject(self.job, 9, self.c.byref(limits), self.c.sizeof(limits), None):
            raise OSError("job_limits")
        if limits.basic.flags != 0:
            raise ValueError("job_limits_not_empty")

    def active(self):
        value = self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.job, 1, self.c.byref(value), self.c.sizeof(value), None):
            raise OSError("job_accounting")
        return value.active

    def spawn_suspended(self, executable, bootstrap, bundle, pin):
        c, w = self.c, self.w
        args = _guardian_owner_argv(executable, bootstrap, bundle, pin)
        if self.in_job(self.kernel.GetCurrentProcess()):
            raise ValueError("external_job")
        self.job = self.kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("job")
        self.job_limits()
        sa = self.SecurityAttributes(c.sizeof(self.SecurityAttributes), None, True)
        for read, write in self.pipes:
            if not self.kernel.CreatePipe(c.byref(read), c.byref(write), c.byref(sa), 4096):
                raise OSError("stdio_pipe")
        child_handles = [self.pipes[0][0].value, self.pipes[1][1].value, self.pipes[2][1].value]
        for handle in (self.pipes[0][1].value, self.pipes[1][0].value, self.pipes[2][0].value):
            if not self.kernel.SetHandleInformation(handle, 1, 0):
                raise OSError("stdio_inheritance")
        size = c.c_size_t()
        if (self.kernel.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
                or c.get_last_error() != 122 or not 0 < size.value <= 65536):
            raise OSError("attribute_size")
        self._attribute_buffer = c.create_string_buffer(size.value)
        if not self.kernel.InitializeProcThreadAttributeList(self._attribute_buffer, 1, 0, c.byref(size)):
            raise OSError("attributes")
        handles = (w.HANDLE * 3)(*child_handles)
        if not self.kernel.UpdateProcThreadAttribute(self._attribute_buffer, 0, 0x20002, handles, c.sizeof(handles), None, None):
            raise OSError("handle_list")
        startup = self.StartupEx()
        startup.startup.cb = c.sizeof(startup)
        startup.startup.flags = 0x101  # STARTF_USESTDHANDLES | USESHOWWINDOW.
        startup.startup.show = 0
        startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = child_handles
        startup.attributes = c.cast(self._attribute_buffer, c.c_void_p)
        command = c.create_unicode_buffer(subprocess.list2cmdline(args))
        windows = c.create_unicode_buffer(32768)
        count = self.kernel.GetWindowsDirectoryW(windows, len(windows))
        if not 0 < count < len(windows):
            raise ValueError("windows_directory")
        environment = c.create_unicode_buffer("SystemRoot=" + windows.value + "\0TEMP=NUL\0TMP=NUL\0WINDIR=" + windows.value + "\0\0")
        if not self.kernel.CreateProcessW(executable, command, None, None, True, 0x08080404,
                environment, bootstrap, c.byref(startup), c.byref(self.process_info)):
            raise OSError("create_process")
        self.kernel.DeleteProcThreadAttributeList(self._attribute_buffer)
        self._attribute_buffer = None
        # A process now exists, but no owner code can run before assignment.
        for handle in (self.pipes[0][0], self.pipes[1][1], self.pipes[2][1]):
            self.close(handle.value)
            handle.value = None
        if self.in_job(self.process_info.process) or self.in_job(self.kernel.GetCurrentProcess()):
            raise ValueError("unexpected_job")
        self.job_limits()
        if not self.kernel.AssignProcessToJobObject(self.job, self.process_info.process):
            raise OSError("job_assignment")
        if (not self.in_job(self.process_info.process, self.job)
                or self.in_job(self.kernel.GetCurrentProcess(), self.job)):
            raise ValueError("job_membership")

    def exit_code(self):
        state = self.kernel.WaitForSingleObject(self.process_info.process, 0)
        if state == 258:
            return None
        if state != 0:
            raise OSError("process_wait")
        code = self.w.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process_info.process, self.c.byref(code)):
            raise OSError("process_exit")
        return code.value


    def resume(self):
        if self.kernel.ResumeThread(self.process_info.thread) != 1:
            raise OSError("resume_count")

    def output(self, index):
        buffer, count = self.c.create_string_buffer(512), self.w.DWORD()
        if not self.kernel.ReadFile(self.pipes[index][0].value, buffer, len(buffer), self.c.byref(count), None):
            if self.c.get_last_error() == 109:
                return b""
            raise OSError("owner_output")
        return buffer.raw[:count.value]

    def close_pipe(self, index, end):
        handle = self.pipes[index][end]
        if handle.value:
            self.close(handle.value)
            handle.value = None

    def dispose(self, attempt):
        # FileDispositionInfo uses BOOLEAN, not BOOL. This is a handle-only
        # commit point; there is no pathname delete or reopen fallback.
        value = self.c.c_ubyte(1)
        if not self.kernel.SetFileInformationByHandle(attempt._marker, 4, self.c.byref(value), self.c.sizeof(value)):
            raise OSError("marker_disposition")

    def close_settled(self):
        if any(handle.value for pair in self.pipes for handle in pair) or self.handles or self.allocations:
            raise ValueError("native_io_retained")
        for name in ("thread", "process"):
            handle = getattr(self.process_info, name)
            if handle:
                self.close(handle)
                setattr(self.process_info, name, None)
        if self.job:
            self.close(self.job)
            self.job = None


class _ManifestGuardian:
    """One retained, non-daemon owner of the attempt, process, Job and pumps.

    All blocking native calls run on its own thread, never under the status
    lock or on the caller's bounded stop path. A failed close is not completion.
    A suspended child cannot be safely terminated here: failed assignment,
    resume or checks keep this guardian and marker in permanent HOLD.
    """

    def __init__(self, native, bundle, pin):
        self._native, self._bundle, self._pin = native, bundle, pin
        self._attempt, self._runner = None, None
        self._pumps, self._pump_closed = [], set()
        self._stop, self._hold = threading.Event(), threading.Event()
        self._state, self._owner_report = "STARTING", "UNKNOWN"
        self._fault = self._eof = self._committed = False
        self._spawn_attempted = False
        self._lock = threading.Lock()

    def status(self):
        with self._lock:
            return {"schema": "wd.mind-guardian-status.v1", "state": self._state,
                    "owner_report": self._owner_report, "owned": self in _GUARDIAN_OWNERS,
                    "marker_disposition_committed": self._committed,
                    "authority": "none", "same_user_race": "UNKNOWN"}

    def _state_is(self, value):
        with self._lock:
            self._state = value

    def _pump(self, index):
        total, pending = 0, b""
        try:
            while True:
                block = self._native.output(index)
                if not block:
                    if pending:
                        raise ValueError("owner_partial_status")
                    break
                total += len(block)
                if total > 4096 or index == 2:
                    raise ValueError("owner_output_budget_or_stderr")
                pending += block
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if not 0 < len(line) <= 512:
                        raise ValueError("owner_status_length")
                    record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_json_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
                    if (type(record) is not dict or set(record) != {"schema", "state"}
                            or record["schema"] != "wd.mind-owner-status.v1"
                            or record["state"] not in ("STARTING", "READY", "HOLD")):
                        raise ValueError("owner_status")
                    with self._lock:
                        self._owner_report = record["state"]
                if len(pending) > 512:
                    raise ValueError("owner_status_length")
        except BaseException:
            self._fault = True
        finally:
            try:
                self._native.close_pipe(index, 0)
            except BaseException:
                self._fault = True
            else:
                self._pump_closed.add(index)

    def _finish(self):
        # Admission is sealed by _stop; this object cannot create another owner.
        if (not self._stop.is_set() or not self._eof or self._fault or self._committed
                or len(self._pumps) != 2 or any(thread.is_alive() for thread in self._pumps)
                or self._pump_closed != {1, 2} or self._native.exit_code() != 0):
            raise ValueError("owner_not_settled")
        self._native.job_limits()
        if self._native.active() != 0:
            raise ValueError("job_not_empty")
        self._attempt.check()
        if self._native.exit_code() != 0 or self._native.active() != 0:
            raise ValueError("terminal_proof_changed")
        self._native.dispose(self._attempt)
        self._committed = True
        # Once disposition succeeded, subsequent failures cannot promise that
        # the marker remains. Kernel handle teardown may finalize its deletion.
        self._attempt.close()
        self._native.close_settled()
        self._state_is("STOPPED")
        with _GUARDIAN_OWNERS_LOCK:
            _GUARDIAN_OWNERS.discard(self)

    def _run(self, executable, bootstrap):
        try:
            if self._fault:
                raise ValueError("start_failed")
            self._attempt = _reserve_guardian_attempt(enabled=True, bundle_root=self._bundle,
                deployment_manifest_sha256=self._pin, cleanup_capable=True)
            self._spawn_attempted = True
            self._native.spawn_suspended(executable, bootstrap, self._bundle, self._pin)
            self._attempt.check()
            if _guardian_launch_inputs(self._bundle, self._pin) != (executable, bootstrap):
                raise ValueError("launch_inputs_changed")
            for index in (1, 2):
                thread = threading.Thread(target=self._pump, args=(index,), name="mind-guardian-output", daemon=False)
                self._pumps.append(thread)
                thread.start()
            if self._fault:
                raise ValueError("start_or_pump_failed")
            self._native.resume()
            self._state_is("ACTIVE")
            while not self._stop.wait(0.02):
                if self._fault or self._native.exit_code() is not None:
                    raise ValueError("unexpected_owner_exit_or_output")
            self._state_is("STOPPING")
            self._native.close_pipe(0, 1)
            self._eof = True
            while True:
                if self._fault:
                    raise ValueError("owner_fault")
                code = self._native.exit_code()
                if code is not None and code != 0:
                    raise ValueError("owner_exit")
                if (code == 0 and not any(thread.is_alive() for thread in self._pumps)
                        and self._native.active() == 0):
                    break
                time.sleep(0.02)
            self._finish()
            return
        except BaseException as error:
            if isinstance(error, GuardianAttemptHold) and self._attempt is None:
                self._attempt = error.reservation
            self._fault = True
            self._state_is("CLEANUP_FAILURE" if self._committed else "HOLD")
        # No timeout, garbage collection, parent EOF or lost caller reference
        # authorizes orphan recovery. The marker requires operator reconciliation.
        self._hold.wait()

    def request_stop(self, *, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 15:
            raise GuardianProcessHold(self) from None
        self._stop.set()
        thread = self._runner
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
        return self.status()


def start_manifest_guardian(*, enabled=False, bundle_root=None, expected_manifest_sha256=None):
    """Explicit opt-in, fixed owner argv; retain the returned guardian on HOLD.

    Preflight is synchronous and has no hard deadline. Owner code is resumed
    only after assignment to a held, private, non-kill Job. The caller must
    separately authorize the external manifest pin and bootstrap this guardian
    itself from pinned bytes. Owner reports are observations, not peer/model
    authentication. Only request_stop requests EOF; abandoned callers do not.
    """
    if enabled is False:
        return {"status": "OFF"}
    guardian = None
    try:
        if enabled is not True:
            raise ValueError("opt_in")
        executable, bootstrap = _guardian_launch_inputs(bundle_root, expected_manifest_sha256)
        native = _GuardianProcessNative()
        guardian = _ManifestGuardian(native, bundle_root, expected_manifest_sha256)
        with _GUARDIAN_OWNERS_LOCK:
            if _GUARDIAN_OWNERS:
                raise ValueError("guardian_retained")
            _GUARDIAN_OWNERS.add(guardian)
        native.logon_sid(require_non_elevated=True)
        if native.handles or native.allocations or native.in_job(native.kernel.GetCurrentProcess()):
            raise ValueError("guardian_token_or_external_job")
        guardian._runner = threading.Thread(target=guardian._run, args=(executable, bootstrap),
                                            name="mind-manifest-guardian", daemon=False)
        guardian._runner.start()
        return guardian
    except Exception:
        if guardian is not None:
            guardian._fault = True
            guardian._state_is("HOLD")
            # Before thread creation no spawn was admitted; only a fully empty
            # native owner can be released. Any returned token handle stays held.
            if (guardian._runner is None and not guardian._native.handles and not guardian._native.allocations):
                with _GUARDIAN_OWNERS_LOCK:
                    _GUARDIAN_OWNERS.discard(guardian)
    raise GuardianProcessHold(guardian) from None


def _guardian_entrypoint(argv, stdin, stdout):
    """Fixed CLI. Parent EOF/output closure is not owner stop authorization."""
    guardian = None
    try:
        if (type(argv) is not list or len(argv) != 5 or argv.count("--guardian") != 1
                or argv.count("--bundle-root") != 1 or argv.count("--deployment-manifest-sha256") != 1):
            raise ValueError("arguments")
        i, j = argv.index("--bundle-root"), argv.index("--deployment-manifest-sha256")
        if i + 1 >= len(argv) or j + 1 >= len(argv) or len({argv.index("--guardian"), i, i+1, j, j+1}) != 5:
            raise ValueError("arguments")
        guardian = start_manifest_guardian(enabled=True, bundle_root=argv[i+1], expected_manifest_sha256=argv[j+1])
    except GuardianProcessHold as error:
        guardian = error.guardian
    except Exception:
        pass
    if guardian is None:
        try:
            stdout.write('{"schema":"wd.mind-guardian-status.v1","state":"HOLD","owner_report":"UNKNOWN",'
                         '"owned":false,"marker_disposition_committed":false,"authority":"none","same_user_race":"UNKNOWN"}\n')
            stdout.flush()
        except Exception:
            pass
        return 1
    stop, bad_control = threading.Event(), threading.Event()
    def controls():
        try:
            line = stdin.readline(16)
            if line in ("STOP\n", "STOP\r\n"):
                stop.set()
            elif line != "":
                bad_control.set()
        except BaseException:
            bad_control.set()
    try:
        threading.Thread(target=controls, name="mind-guardian-control", daemon=True).start()
    except Exception:
        bad_control.set()
    last, output_open = None, True
    while True:
        state = guardian.request_stop(timeout=0) if stop.is_set() else guardian.status()
        if bad_control.is_set() and state["state"] != "STOPPED":
            state = dict(state, state="HOLD")
        if output_open and state != last:
            try:
                stdout.write(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
                stdout.flush()
                last = state
            except Exception:
                output_open = False  # The guardian survives a departed wrapper.
        if state["state"] == "STOPPED" and state.get("owned") is False:
            return 0
        time.sleep(0.05)


def verify_runtime_process_preflight(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256,
                                     bundle_root, deployment_manifest_sha256, verifier, verifier_sha256,
                                     runtime_root, runtime_manifest,
                                     runtime_manifest_sha256, generation, revision):
    """Verify external pins and runtime tree; return evidence, not permission to run.

    All pins must come from an independent manifest/authority, not the runtime
    tree being checked. Interpreter-tree and native-library closure remain the
    caller's independent responsibility. No query or private message is read.
    """
    result = None
    try:
        bundle = Path(bundle_root)
        if not bundle.is_absolute() or ".." in bundle.parts:
            raise ValueError("bundle")
        manifest_bytes = _read_pinned_file(bundle / "deployment-manifest.json", deployment_manifest_sha256,
                                           limit=4 * 1024 * 1024, retain=True)
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if (type(manifest) is not dict or manifest.get("schema_version") != 1
                or type(manifest.get("files")) is not dict
                or type(manifest["files"].get(_VERIFIER_RELATIVE)) is not str
                or manifest["files"][_VERIFIER_RELATIVE].lower() != _PACKAGED_VERIFIER_SHA256):
            raise ValueError("verifier_not_manifest_bound")
        packaged_verifier = bundle.joinpath(*_VERIFIER_RELATIVE.split("/"))
        if os.path.normcase(os.path.abspath(os.fspath(verifier))) != os.path.normcase(os.path.abspath(packaged_verifier)):
            raise ValueError("verifier_not_packaged")
        if (type(verifier_sha256) is not str or
                not hmac.compare_digest(verifier_sha256, _PACKAGED_VERIFIER_SHA256)):
            raise ValueError("unapproved_verifier")
        # Wrong bootstrap/interpreter/verifier pins refuse before verifier code.
        _read_pinned_file(interpreter, interpreter_sha256, limit=_MAX_INTERPRETER)
        _read_pinned_file(bootstrap, bootstrap_sha256, limit=_MAX_CODE)
        verifier_bytes = _read_pinned_file(verifier, verifier_sha256, limit=_MAX_CODE, retain=True)
        namespace = {"__name__": "_wd_pinned_runtime_manifest", "__file__": str(verifier)}
        exec(compile(verifier_bytes, "<pinned-runtime-manifest>", "exec"), namespace)
        verify = namespace.get("verify_runtime")
        if not callable(verify):
            raise ValueError("verifier")
        evidence = verify(runtime_root, runtime_manifest, runtime_manifest_sha256,
                          expected_generation=generation, expected_revision=revision)
        if (type(evidence) is not dict or evidence.get("manifest_sha256") != runtime_manifest_sha256
                or evidence.get("generation") != generation or evidence.get("revision") != revision
                or type(evidence.get("file_count")) is not int or type(evidence.get("total_bytes")) is not int):
            raise ValueError("evidence")
        result = {"schema": "wd.mind-runtime-process-preflight.v1", "status": "VERIFIED_PREFLIGHT_ONLY",
                  "process_started": False, "model_loaded": False, "network_isolation": "UNKNOWN",
                  "native_closure": "UNKNOWN", "same_user_race": "UNKNOWN",
                  "deployment_manifest_sha256": deployment_manifest_sha256,
                  "runtime_manifest_sha256": runtime_manifest_sha256,
                  "generation": generation, "revision": revision,
                  "file_count": evidence["file_count"], "total_bytes": evidence["total_bytes"]}
    except Exception:
        pass
    if result is None:
        raise RuntimePreflightRefused("runtime_process_preflight_refused") from None
    return result


if __name__ == "__main__":
    if "tools.wd_mind_runtime_process" not in sys.modules:
        sys.modules["tools.wd_mind_runtime_process"] = sys.modules[__name__]
    raise SystemExit(_guardian_entrypoint(sys.argv[1:], sys.stdin, sys.stdout))
