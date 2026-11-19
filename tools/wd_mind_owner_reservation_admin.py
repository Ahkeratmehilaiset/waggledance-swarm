"""Pinned owner-marker administration candidate, pending review and deployment.

A command cannot authenticate a human against another process using the same account.
Success requires exit code 0 and the exact stdout receipt line
``owner_marker_<operation>_complete``; otherwise the result is UNKNOWN, even
if stderr is lost. Never automatically retry or revert a reset.

This is a cooperating-owner guard, not an authentication boundary.  Process
enumeration is a point-in-time observation: a legacy or same-user process may
start after it, and a legacy interpreter at a different root is not identified.
Hardlinks outside the runtime root to other runtime files are not exhaustively
identified; the explicitly pinned interpreter itself is compared by file ID.
Those risks and same-user changes remain UNKNOWN.  An uncertain observation
always refuses.  The CLI has no synthetic-path or proof overrides.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import types

from tools import wd_mind_absent_generation_prefix_readonly as absent
from tools import wd_mind_runtime_child as child


class ReservationAdminRefused(ValueError):
    """Fixed refusal: never print host process details or paths."""


class _ProcessObservationRefused(ReservationAdminRefused):
    """Fixed aggregate only; no process identifiers, names or paths."""
    _FIELDS = ('open_denied', 'open_other', 'image_unverified', 'handle_close_uncertain')

    def __init__(self, counts):
        if (type(counts) is not dict or set(counts) != set(self._FIELDS)
                or any(type(counts[key]) is not int or not 0 <= counts[key] <= 0xffffffff
                       for key in self._FIELDS) or not any(counts.values())):
            raise ValueError('invalid_observation_counts')
        self.counts = {key: counts[key] for key in self._FIELDS}
        super().__init__('host_observation_unavailable;' +
                         ';'.join(key + '=' + str(self.counts[key]) for key in self._FIELDS))


class _AdminReason(Exception):
    """Internal fixed-token branch, never constructed from host exception text."""


_REASONS = frozenset({'ROOT_UNAVAILABLE', 'PRODUCTION_ROUTE_UNAVAILABLE', 'MARKER_EXISTS',
                      'MARKER_MISSING', 'MARKER_LOCK_UNAVAILABLE', 'MARKER_NOT_RESERVED',
                      'HOST_OBSERVATION_UNAVAILABLE', 'HOST_TIME_INVALID', 'REBOOT_REQUIRED',
                      'PUBLISH_REFUSED', 'SYNTHETIC_SCOPE_REQUIRED', 'RESET_PUBLICATION_UNCERTAIN',
                      'CREATE_PUBLICATION_UNCERTAIN'})


_CURRENT_POINTER = Path(r'C:\Python\WD_REBOOT_STATE_CURRENT.json')
_BUNDLE_STORE = Path(r'C:\Python\wd-reboot-bundles')
# A source worktree owns its own test-only audit subtree. The installed bundle
# has no worktree metadata and is refused below before any synthetic write.
_TEST_REPOSITORY = Path(__file__).absolute().parent.parent
_SYNTHETIC_ROOT = _TEST_REPOSITORY / '.codex-audit' / 'marker-admin-synthetic-tests'
_FIXED_PRODUCTION_ROOT = Path(r'C:\Python\project2\.codex-audit')
_SYNTHETIC_SCOPE = object()
_PRODUCTION_SCOPE = object()
# Candidate route still requires the pinned loader and binding; it is not deployed.
_PRODUCTION_ENTRY_ENABLED = True
_PRODUCTION_CONTEXT = threading.local()


class _ProductionContext:
    """Accidental-call guard only; this is not caller or human authentication."""
    __slots__ = ('operation', 'bundle', 'manifest')

    def __init__(self, operation, bundle, manifest):
        self.operation, self.bundle, self.manifest = operation, bundle, manifest


class _TimeInfo(ctypes.Structure):
    _fields_ = [('boot', ctypes.c_longlong), ('current', ctypes.c_longlong),
                ('bias', ctypes.c_longlong), ('zone', wintypes.ULONG),
                ('reserved', wintypes.ULONG), ('boot_bias', ctypes.c_ulonglong),
                ('sleep_bias', ctypes.c_ulonglong)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                ('parent', wintypes.DWORD), ('priority', ctypes.c_long),
                ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]


class _ProcessBasicInfo(ctypes.Structure):
    _fields_ = [('reserved1', ctypes.c_void_p), ('peb', ctypes.c_void_p),
                ('reserved2', ctypes.c_void_p * 2), ('pid', ctypes.c_void_p),
                ('parent', ctypes.c_void_p)]


class _ImageUnavailable(ValueError):
    def __init__(self):
        super().__init__('image_access')


def _image_or_proven_exited(kernel, process):
    size = wintypes.DWORD(32768)
    path = ctypes.create_unicode_buffer(size.value)
    if not kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
        error = ctypes.get_last_error()
        # A stable process handle can prove this exact process has exited.
        # Unknown, STILL_ACTIVE and an unavailable exit status all refuse.
        code = wintypes.DWORD()
        if error in (5, 31) and kernel.GetExitCodeProcess(process, ctypes.byref(code)) and code.value != 259:
            return None
        raise _ImageUnavailable()
    if not path.value or not Path(path.value).is_absolute():
        raise ValueError('image_path')
    return path.value


def _windows_kernel_boot_ns():
    ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
    query = ntdll.NtQuerySystemInformation
    query.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = ctypes.c_long
    result, returned = _TimeInfo(), wintypes.ULONG()
    if query(3, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)) != 0:
        raise ValueError('boot_query')
    if (returned.value != ctypes.sizeof(result) or result.boot <= 116444736000000000
            or result.current < result.boot):
        raise ValueError('boot_shape')
    return (result.boot - 116444736000000000) * 100


def _process_snapshot(kernel):
    """Snapshot PID, parent and name; no identity claim survives PID reuse."""
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == wintypes.HANDLE(-1).value:
        raise ValueError('process_snapshot')
    items, seen = [], set()
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not available and ctypes.get_last_error() != 18:
            raise ValueError('process_first')
        while available:
            pid = int(entry.pid)
            if pid in seen:
                raise ValueError('duplicate_pid')
            seen.add(pid)
            items.append((pid, int(entry.parent), str(entry.exe)))
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ValueError('process_next')
        # The observing process itself cannot have vanished while it runs.
        # A snapshot missing it is incomplete and cannot prove absence.
        if os.getpid() not in seen:
            raise ValueError('process_snapshot_incomplete')
        return tuple(items)
    finally:
        if not kernel.CloseHandle(snapshot):
            raise ValueError('snapshot_handle_close')


def _verified_kernel_process(process, pid, parent, name):
    """Use facts from the same opened process; a bare name is insufficient."""
    if name not in ('System', 'Secure System', 'Registry', 'Memory Compression') or (pid != 4 and parent != 4):
        return False
    ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
    query = ntdll.NtQueryInformationProcess
    query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                      ctypes.POINTER(wintypes.ULONG)]
    query.restype = ctypes.c_long
    basic, protection, returned = _ProcessBasicInfo(), ctypes.c_ubyte(), wintypes.ULONG()
    if (query(process, 0, ctypes.byref(basic), ctypes.sizeof(basic), ctypes.byref(returned)) != 0
            or returned.value != ctypes.sizeof(basic) or basic.peb is not None
            or (basic.pid or 0) != pid or (basic.parent or 0) != parent):
        return False
    returned.value = 0
    if (query(process, 61, ctypes.byref(protection), ctypes.sizeof(protection), ctypes.byref(returned)) != 0
            or returned.value != ctypes.sizeof(protection) or protection.value != 0x72):
        return False
    return True


def _windows_process_images():
    """Read-only snapshot; unproven identities or access remain a refusal."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    items = []
    counts = {key: 0 for key in _ProcessObservationRefused._FIELDS}
    for pid, parent, name in _process_snapshot(kernel):
        process = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not process:
            error = ctypes.get_last_error()
            # Windows reserves PID 0 for the System Idle Process; OpenProcess
            # documents ERROR_INVALID_PARAMETER for that specific process.
            if pid == 0 and error == 87:
                continue
            if error == 87 and all(other_pid != pid for other_pid, _, _ in _process_snapshot(kernel)):
                continue
            counts['open_denied' if error == 5 else 'open_other'] += 1
            continue
        try:
            try:
                path = _image_or_proven_exited(kernel, process)
            except _ImageUnavailable:
                try:
                    known_kernel_process = _verified_kernel_process(process, pid, parent, name)
                except Exception:
                    known_kernel_process = False
                if known_kernel_process is not True:
                    counts['image_unverified'] += 1
                continue
            except Exception:
                counts['image_unverified'] += 1
                continue
            if path is not None:
                items.append((pid, path))
        finally:
            try:
                if not kernel.CloseHandle(process):
                    counts['handle_close_uncertain'] += 1
            except Exception:
                counts['handle_close_uncertain'] += 1
    if any(counts.values()):
        raise _ProcessObservationRefused(counts) from None
    return tuple(items)


def _observe_windows():
    """One native boot and process observation; unknown images refuse."""
    if os.name != 'nt':
        raise ReservationAdminRefused('host_observation_unavailable')
    try:
        return _windows_kernel_boot_ns(), _windows_process_images()
    except _ProcessObservationRefused:
        raise
    except Exception:
        raise ReservationAdminRefused('host_observation_unavailable') from None


def _no_runtime_process(processes, interpreter_root, interpreter=None):
    root = os.path.normcase(str(Path(interpreter_root).resolve(strict=True)))
    if not Path(interpreter_root).is_absolute():
        raise ReservationAdminRefused('runtime_identity_invalid')
    for pid, image in processes:
        if pid == os.getpid():
            continue  # This short-lived administrative interpreter is not the owner.
        # Strict native final-path resolution rejects inaccessible or vanished
        # images rather than treating them as proof of absence.
        path = os.path.normcase(str(Path(image).resolve(strict=True)))
        if interpreter is not None and os.path.samefile(path, interpreter):
            raise ReservationAdminRefused('runtime_process_observed')
        if os.path.splitdrive(root)[0] != os.path.splitdrive(path)[0]:
            continue  # A different volume cannot be inside the pinned root.
        try:
            common = os.path.commonpath((root, path))
        except ValueError:
            raise ReservationAdminRefused('runtime_identity_invalid') from None
        if common == root:
            raise ReservationAdminRefused('runtime_process_observed')


def _assert_synthetic_root(root):
    """Admit only fixed, disjoint pytest audit leaves; never a live root."""
    try:
        path = Path(root)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('root')
        metadata = _TEST_REPOSITORY / '.git'
        if not (metadata.is_dir() or metadata.is_file()):
            raise ValueError('not_worktree')
        repo = os.path.normcase(str(_TEST_REPOSITORY.resolve(strict=True)))
        installed = os.path.normcase(os.path.abspath(_BUNDLE_STORE))
        if os.path.commonpath((repo, installed)) in (repo, installed):
            raise ValueError('installed_scope')
        absent._plain(_SYNTHETIC_ROOT, directory=True)
        absent._plain(path, directory=True)
        base = os.path.normcase(str(_SYNTHETIC_ROOT.resolve(strict=True)))
        actual = os.path.normcase(str(path.resolve(strict=True)))
        live = os.path.normcase(str(child._AUDIT_ROOT.resolve(strict=True)))
        common = os.path.commonpath((actual, live))
        if common in (actual, live):
            raise ValueError('live_root_overlap')
        relative = Path(actual).relative_to(Path(base))
        if (len(relative.parts) != 3 or relative.parts[-1].lower() != 'audit'
                or any(re.fullmatch(r'[a-z0-9_-]{1,120}', part) is None
                       for part in relative.parts[:2])):
            raise ValueError('synthetic_leaf')
        return actual
    except Exception:
        raise ReservationAdminRefused('SYNTHETIC_SCOPE_REQUIRED') from None


def _require_production_activation():
    """Require the local guarded entry; same-user Python remains UNKNOWN."""
    context = getattr(_PRODUCTION_CONTEXT, 'value', None)
    if _PRODUCTION_ENTRY_ENABLED is not True or type(context) is not _ProductionContext:
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    return context


def _assert_scope_root(root, scope):
    if scope is _SYNTHETIC_SCOPE:
        return _assert_synthetic_root(root)
    if scope is not _PRODUCTION_SCOPE:
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    # Check the entry context before even a live path lookup.
    _require_production_activation()
    path = Path(root)
    expected = _FIXED_PRODUCTION_ROOT
    if (not path.is_absolute() or '..' in path.parts
            or os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(expected))
            or os.path.normcase(os.path.abspath(child._AUDIT_ROOT)) != os.path.normcase(os.path.abspath(expected))):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    absent._plain(path, directory=True)
    resolved = path.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(os.path.abspath(expected)):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    return os.path.normcase(str(resolved))


def _scoped_call(function, scope, *args):
    if scope is _SYNTHETIC_SCOPE:
        return function(*args)
    return function(*args, _scope=scope)


def _final_handle_path(handle):
    """Resolve the opened Windows object; do not trust the caller's path."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(32768)
    count = kernel.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    value = buf.value
    if (not count or count >= len(buf) or not value.startswith('\\\\?\\')
            or value.upper().startswith('\\\\?\\UNC\\')):
        raise ReservationAdminRefused('SYNTHETIC_SCOPE_REQUIRED')
    return Path(value[4:])


@contextlib.contextmanager
def _pin_directory_chain(root):
    """Hold every existing ancestor without delete sharing before any write."""
    from tools import wd_mind_vector_cache as cache
    native, handles = cache._Native(), []
    path = Path(root)
    if not path.is_absolute() or '..' in path.parts or path.drive.upper() != 'C:':
        raise ReservationAdminRefused('root_invalid')
    current = Path(path.anchor)
    try:
        parts = (None, *path.parts[1:])
        for index, part in enumerate(parts):
            if part is not None:
                current /= part
            # LIST_DIRECTORY is essential: READ_ATTRIBUTES alone did not stop
            # a cross-process rename in the Windows synthetic experiment.
            handles.append(native.open(current, 3, 0xa1 if index == len(parts) - 1 else 0x81,
                                       3, directory=True))
        yield handles[-1]
    finally:
        for handle in reversed(handles):
            native.kernel.CloseHandle(handle)


def _assert_open_directory_identity(root, handle):
    """Compare the pinned directory object with a fresh no-reparse open."""
    from tools import wd_mind_vector_cache as cache
    native = cache._Native()
    fresh = native.open(Path(root), 3, 0x81, 3, directory=True)
    try:
        left, right = cache._HandleInfo(), cache._HandleInfo()
        if (not native.kernel.GetFileInformationByHandle(handle, ctypes.byref(left))
                or not native.kernel.GetFileInformationByHandle(fresh, ctypes.byref(right))
                or native.kernel.GetFileType(handle) != 1
                or left.attributes & 0x400 or not left.attributes & 0x10
                or not (left.index_high or left.index_low)
                or (left.volume, left.index_high, left.index_low) !=
                   (right.volume, right.index_high, right.index_low)):
            raise ReservationAdminRefused('DIRECTORY_IDENTITY_UNKNOWN')
    finally:
        native.kernel.CloseHandle(fresh)


class _UnicodeString(ctypes.Structure):
    _fields_ = [('length', wintypes.USHORT), ('maximum', wintypes.USHORT), ('buffer', wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.ULONG), ('root', wintypes.HANDLE),
                ('name', ctypes.POINTER(_UnicodeString)), ('attributes', wintypes.ULONG),
                ('security', ctypes.c_void_p), ('qos', ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [('status', ctypes.c_void_p), ('information', ctypes.c_size_t)]


def _discard_new_native_handle(handle, *, _scope=_SYNTHETIC_SCOPE):
    """Dispose only an unrenamed handle returned by our successful FILE_CREATE."""
    from tools import wd_mind_vector_cache as cache
    final = _final_handle_path(handle)
    _assert_scope_root(final.parent, _scope)
    if re.fullmatch(re.escape(child._OWNER_RESERVATION_NAME) + r'\.init-[0-9a-f]{32}', final.name) is None:
        raise ReservationAdminRefused('staging_cleanup_unproven')
    kernel = cache._Native().kernel
    info = cache._HandleInfo()
    if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            or kernel.GetFileType(handle) != 1 or info.attributes & (0x10 | 0x400)
            or info.links != 1 or not (info.index_high or info.index_low)):
        raise ReservationAdminRefused('staging_cleanup_unproven')
    disposition, io = wintypes.BOOLEAN(1), _IoStatusBlock()
    native = ctypes.WinDLL('ntdll', use_last_error=True).NtSetInformationFile
    native.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                       wintypes.ULONG, ctypes.c_int]
    native.restype = ctypes.c_long
    if native(handle, ctypes.byref(io), ctypes.byref(disposition), ctypes.sizeof(disposition), 13) != 0:
        raise ReservationAdminRefused('staging_cleanup_unproven')


def _relative_create(directory_handle, leaf, *, _scope=_SYNTHETIC_SCOPE, verify_binding=None):
    """Create one ASCII leaf relative to an already pinned directory handle."""
    import msvcrt
    if (type(leaf) is not str or not leaf or leaf in ('.', '..') or not leaf.isascii()
            or any(c in leaf for c in ('/', '\\', ':'))):
        raise ValueError('invalid_leaf')
    _assert_scope_root(_final_handle_path(directory_handle), _scope)
    if _scope is _PRODUCTION_SCOPE and not callable(verify_binding):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    raw = leaf.encode('utf-16-le')
    buffer = ctypes.create_unicode_buffer(leaf)
    name = _UnicodeString(len(raw), len(raw) + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), directory_handle,
        ctypes.pointer(name), 0x40, None, None)
    status_block, out = _IoStatusBlock(), wintypes.HANDLE()
    native = ctypes.WinDLL('ntdll', use_last_error=True).NtCreateFile
    native.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    native.restype = ctypes.c_long
    fd = None
    try:
        if verify_binding is not None: verify_binding()
        status = native(ctypes.byref(out), 0xc0110080, ctypes.byref(attributes), ctypes.byref(status_block),
            None, 0x80, 1, 2, 0x00200060, None, 0)  # FILE_SHARE_READ only; FILE_CREATE, no reparse
        if status != 0:
            raise ValueError('relative_create_refused')
        fd = msvcrt.open_osfhandle(out.value, os.O_RDWR | os.O_BINARY | getattr(os, 'O_NOINHERIT', 0))
        return fd
    except BaseException:
        if out.value not in (None, wintypes.HANDLE(-1).value):
            try:
                _scoped_call(_discard_new_native_handle, _scope, out)
            finally:
                if fd is not None:
                    os.close(fd)
                else:
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel.CloseHandle(out)
        raise


def _discard_owned_temp(fd, tmp, identity, *, _scope=_SYNTHETIC_SCOPE):
    """Disposition the known open staging handle only; never unlink a path."""
    import msvcrt
    try:
        final = _final_handle_path(msvcrt.get_osfhandle(fd))
        handle_root = _assert_scope_root(final.parent, _scope)
        path_root = _assert_scope_root(Path(tmp).parent, _scope)
        if (handle_root != path_root or os.path.normcase(str(final)) != os.path.normcase(str(tmp))
                or re.fullmatch(re.escape(child._OWNER_RESERVATION_NAME) + r'\.init-[0-9a-f]{32}', final.name) is None):
            raise ValueError('scope')
    except Exception:
        raise ValueError('staging_cleanup_unproven') from None
    absent._plain(tmp, directory=False)
    by_handle, by_path = os.fstat(fd), os.lstat(tmp)
    if ((by_handle.st_dev, by_handle.st_ino) != identity
            or (by_path.st_dev, by_path.st_ino) != identity
            or by_handle.st_nlink != 1 or by_path.st_nlink != 1):
        raise ValueError('staging_cleanup_unproven')
    disposition = wintypes.BOOLEAN(1)
    io = _IoStatusBlock()
    native = ctypes.WinDLL('ntdll', use_last_error=True).NtSetInformationFile
    native.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                       wintypes.ULONG, ctypes.c_int]
    native.restype = ctypes.c_long
    if native(msvcrt.get_osfhandle(fd), ctypes.byref(io), ctypes.byref(disposition),
              ctypes.sizeof(disposition), 13) != 0:  # FileDispositionInformation
        raise ValueError('staging_cleanup_unproven')


def _atomic_reserved(path, directory_handle, state=b'R', *, _scope=_SYNTHETIC_SCOPE, verify_binding=None):
    """Publish complete bytes; cleanup only the exact owned staging handle.

    A process crash may leave an incomplete or complete staging leaf, but never
    deliberately deletes an unknown leaf. Power loss, interruption within the
    native calls, same-user ABA and malicious legacy processes remain UNKNOWN.
    """
    import msvcrt
    if path.name != child._OWNER_RESERVATION_NAME or state not in (b'R', b'C'):
        raise ValueError('invalid_marker_leaf')
    if _assert_scope_root(path.parent, _scope) != _assert_scope_root(_final_handle_path(directory_handle), _scope):
        raise ReservationAdminRefused('SYNTHETIC_SCOPE_REQUIRED')
    _assert_open_directory_identity(path.parent, directory_handle)
    if _scope is _PRODUCTION_SCOPE and not callable(verify_binding):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    tmp = path.with_name(path.name + '.init-' + secrets.token_hex(16))
    if verify_binding is None:
        fd = _scoped_call(_relative_create, _scope, directory_handle, tmp.name)
    elif _scope is _SYNTHETIC_SCOPE:
        fd = _relative_create(directory_handle, tmp.name, verify_binding=verify_binding)
    else:
        fd = _relative_create(directory_handle, tmp.name, _scope=_scope, verify_binding=verify_binding)
    identity = None
    published = False
    publication_started = False
    try:
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        raw = child._OWNER_RESERVATION_MAGIC + state
        if verify_binding is not None: verify_binding()
        if os.write(fd, raw) != len(raw):
            raise ValueError('write')
        os.fsync(fd)
        # Check the complete content through the same handle used to write and
        # publish it; a different path lookup would not prove staging bytes.
        os.lseek(fd, 0, os.SEEK_SET)
        if os.read(fd, len(raw) + 1) != raw:
            raise ValueError('staging_content_changed')
        absent._plain(tmp, directory=False)
        by_path = os.lstat(tmp)
        if ((by_path.st_dev, by_path.st_ino) != identity or by_path.st_nlink != 1
                or os.fstat(fd).st_nlink != 1):
            raise ValueError('initial_file_changed')
        leaf = path.name
        class _Rename(ctypes.Structure):
            _fields_ = [('replace', wintypes.BOOLEAN), ('root', wintypes.HANDLE),
                        ('length', wintypes.DWORD), ('name', wintypes.WCHAR * (len(leaf) + 1))]
        rename = _Rename(0, directory_handle, len(leaf.encode('utf-16-le')), leaf)
        io = _IoStatusBlock()
        native = ctypes.WinDLL('ntdll', use_last_error=True).NtSetInformationFile
        native.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                           wintypes.ULONG, ctypes.c_int]
        native.restype = ctypes.c_long
        if verify_binding is not None: verify_binding()
        publication_started = True
        status = native(msvcrt.get_osfhandle(fd), ctypes.byref(io), ctypes.byref(rename),
                        ctypes.sizeof(rename), 10)  # FileRenameInformation, no replace
        if status != 0:
            publication_started = False
            if status & 0xffffffff == 0xc0000035:  # STATUS_OBJECT_NAME_COLLISION
                raise _AdminReason('MARKER_EXISTS')
            raise _AdminReason('PUBLISH_REFUSED')
        published = True
        by_handle = os.fstat(fd)
        by_path = os.lstat(path)
        if ((by_handle.st_dev, by_handle.st_ino) != identity
                or (by_path.st_dev, by_path.st_ino) != identity or by_handle.st_nlink != 1
                or by_path.st_nlink != 1):
            raise ValueError('published_changed')
    except Exception:
        if publication_started:
            raise ReservationAdminRefused('CREATE_PUBLICATION_UNCERTAIN') from None
        raise
    finally:
        primary = sys.exc_info()[1]
        try:
            # Once the rename was attempted, never delete: it might already be
            # the public marker even if the syscall result was not observed.
            if not published and not publication_started:
                if identity is None:
                    _scoped_call(_discard_new_native_handle, _scope, msvcrt.get_osfhandle(fd))
                else:
                    _scoped_call(_discard_owned_temp, _scope, fd, tmp, identity)
        finally:
            try:
                os.close(fd)
            except Exception:
                if isinstance(primary, ReservationAdminRefused) and str(primary) == 'CREATE_PUBLICATION_UNCERTAIN':
                    raise ReservationAdminRefused('CREATE_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN') from None
                raise
    return identity


def _validate_observation(boot_ns, processes, interpreter_root, interpreter, marker_time=None):
    if type(boot_ns) is not int or boot_ns <= 0 or boot_ns > time.time_ns():
        raise _AdminReason('HOST_TIME_INVALID')
    if marker_time is not None and boot_ns <= marker_time:
        raise _AdminReason('REBOOT_REQUIRED')
    _no_runtime_process(processes, interpreter_root, interpreter)


def _checked_observe(observe):
    try:
        return observe()
    except _ProcessObservationRefused:
        raise
    except Exception:
        raise _AdminReason('HOST_OBSERVATION_UNAVAILABLE') from None


def _report_reset_interruption(original):
    """Best effort only; abnormal exit or absent receipt always means UNKNOWN."""
    try:
        print('RESET_PUBLICATION_UNCERTAIN', file=sys.stderr, flush=True)
    except BaseException as notification_failure:
        raise original from notification_failure


def _release_verified(guard, verify_binding, *, _scope=_SYNTHETIC_SCOPE):
    """Release a verified R marker; a failed post-write result is uncertain."""
    if _scope is _PRODUCTION_SCOPE:
        _require_production_activation()
    if type(guard) is not child._LocalOwnerReservation:
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    _assert_scope_root(guard.root, _scope)
    expected = Path(guard.root) / child._OWNER_RESERVATION_NAME
    if os.path.normcase(os.path.abspath(guard.path)) != os.path.normcase(os.path.abspath(expected)):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    if not callable(verify_binding):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    guard._check()
    if guard._read() != child._OWNER_RESERVATION_MAGIC + b'R':
        raise ReservationAdminRefused('MARKER_NOT_RESERVED')
    os.lseek(guard.fd, len(child._OWNER_RESERVATION_MAGIC), os.SEEK_SET)
    # The mandatory binding check is immediately before the R->C write.
    # A check/write race and same-account ABA remain UNKNOWN.
    verify_binding()
    try:
        if os.write(guard.fd, b'C') != 1:
            raise ValueError('write')
        os.fsync(guard.fd)
        guard._check()
        if guard._read() != child._OWNER_RESERVATION_MAGIC + b'C':
            raise ValueError('marker_changed')
        import msvcrt
        os.lseek(guard.fd, 0, os.SEEK_SET)
        msvcrt.locking(guard.fd, msvcrt.LK_UNLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
        os.close(guard.fd)
        guard.fd = -1
    except Exception:
        raise ReservationAdminRefused('RESET_PUBLICATION_UNCERTAIN') from None
    except BaseException as interruption:
        _report_reset_interruption(interruption)
        raise


def _repair_marker_locked(guard, directory_handle, observe, interpreter_root, interpreter, verify_binding, *, scope):
    """Shared damaged-byte repair only after scope and held-handle checks.

    Production requires the pinned guarded entry. Same-user ABA, the process
    snapshot/spawn race and wall-clock movement remain UNKNOWN. An interrupt
    after a write begins is propagated; publication remains uncertain.
    """
    if scope is _PRODUCTION_SCOPE:
        _require_production_activation()
    if ((scope is not _SYNTHETIC_SCOPE and scope is not _PRODUCTION_SCOPE)
            or type(guard) is not child._LocalOwnerReservation):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    _assert_scope_root(guard.root, scope)
    expected = Path(guard.root) / child._OWNER_RESERVATION_NAME
    if (os.path.normcase(os.path.abspath(guard.path)) != os.path.normcase(os.path.abspath(expected))
            or not callable(observe) or not callable(verify_binding)):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    initial = os.fstat(guard.fd)
    identity = (initial.st_dev, initial.st_ino)
    if identity != guard.file_id:
        raise ValueError('marker_changed')
    marker_time, marker_size = initial.st_mtime_ns, initial.st_size

    def check_held():
        _assert_scope_root(guard.root, scope)
        _assert_open_directory_identity(guard.root, directory_handle)
        absent._plain(guard.path, directory=False)
        root = os.lstat(guard.root)
        path = os.lstat(guard.path)
        opened = os.fstat(guard.fd)
        if ((root.st_dev, root.st_ino) != guard.root_id or not identity[1] or guard.file_id != identity
                or (path.st_dev, path.st_ino) != identity
                or (opened.st_dev, opened.st_ino) != identity
                or path.st_nlink != 1 or opened.st_nlink != 1
                or path.st_size != marker_size or opened.st_size != marker_size
                or path.st_mtime_ns != marker_time or opened.st_mtime_ns != marker_time):
            raise ValueError('marker_changed')

    check_held()
    boot_ns, processes = _checked_observe(observe)
    _validate_observation(boot_ns, processes, interpreter_root, interpreter, marker_time)
    verify_binding()
    check_held()
    os.lseek(guard.fd, 0, os.SEEK_SET)
    clear = child._OWNER_RESERVATION_MAGIC + b'C'
    # The binding may have changed during the handle/path revalidation.
    verify_binding()
    # Once a write starts, any later error leaves the published state unknown.
    try:
        if os.write(guard.fd, clear) != len(clear):
            raise ValueError('short_write')
        os.ftruncate(guard.fd, len(clear))
        os.fsync(guard.fd)
        guard._check()
        if guard._read() != clear:
            raise ValueError('marker_changed')
        import msvcrt
        os.lseek(guard.fd, 0, os.SEEK_SET)
        msvcrt.locking(guard.fd, msvcrt.LK_UNLCK, len(clear))
        os.close(guard.fd)
        guard.fd = -1
    except Exception:
        raise _AdminReason('RESET_PUBLICATION_UNCERTAIN') from None
    except BaseException as interruption:
        _report_reset_interruption(interruption)
        raise


def _repair_synthetic_marker(guard, directory_handle, observe, interpreter_root, interpreter, verify_binding):
    return _repair_marker_locked(guard, directory_handle, observe, interpreter_root, interpreter,
                                 verify_binding, scope=_SYNTHETIC_SCOPE)


def _repair_production_marker(guard, directory_handle, *, bundle_root, manifest_sha256):
    # No caller-provided root, observer or binding hook reaches this wrapper.
    context = _require_production_activation()
    if (context.operation != 'reset' or context.bundle != bundle_root
            or context.manifest != manifest_sha256):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    location, verify_binding = _current_binding(bundle_root, manifest_sha256)
    if type(location) is not dict or location.get('interpreter_status') != 'VERIFIED':
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    return _repair_marker_locked(guard, directory_handle, _observe_windows, location['interpreter_root'],
                                 location['interpreter'], verify_binding, scope=_PRODUCTION_SCOPE)


def _production_marker(*, operation, bundle_root, manifest_sha256):
    """Fixed-root implementation called only through the pinned entry.

    No caller-supplied root, process observer, or proof hook is admitted.
    Process snapshots are point-in-time only; legacy owner absence is UNKNOWN.
    """
    context = _require_production_activation()  # Before opening a pointer or live path.
    if (context.operation != operation or context.bundle != bundle_root
            or context.manifest != manifest_sha256):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    if operation not in ('create', 'reset'):
        raise ReservationAdminRefused('invalid_operation')
    root = _FIXED_PRODUCTION_ROOT
    _assert_scope_root(root, _PRODUCTION_SCOPE)
    location, verify_binding = _current_binding(bundle_root, manifest_sha256)
    if (type(location) is not dict or location.get('interpreter_status') != 'VERIFIED'
            or not callable(verify_binding)):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    interpreter_root, interpreter = location['interpreter_root'], location['interpreter']
    root_stat = os.lstat(root)
    guard = None
    fd = None
    try:
        import msvcrt
        with _pin_directory_chain(root) as directory_handle:
            _assert_scope_root(root, _PRODUCTION_SCOPE)
            _assert_open_directory_identity(root, directory_handle)
            current = os.lstat(root)
            if (current.st_dev, current.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
            verify_binding()
            path = root / child._OWNER_RESERVATION_NAME
            if operation == 'create':
                if os.path.lexists(path):
                    raise ReservationAdminRefused('MARKER_EXISTS')
                for _ in range(2):
                    boot_ns, processes = _checked_observe(_observe_windows)
                    _validate_observation(boot_ns, processes, interpreter_root, interpreter)
                    verify_binding()
                _atomic_reserved(path, directory_handle, b'C', _scope=_PRODUCTION_SCOPE,
                                 verify_binding=verify_binding)
                return
            try:
                absent._plain(path, directory=False)
            except FileNotFoundError:
                raise ReservationAdminRefused('MARKER_MISSING') from None
            path_stat = os.lstat(path)
            # Exactly the owner's share mode denies competing write/delete opens.
            try:
                fd = child._open_owned_marker(path)
            except OSError as exc:
                if getattr(exc, 'winerror', None) in (32, 33):  # sharing/lock violation
                    raise ReservationAdminRefused('MARKER_LOCK_UNAVAILABLE') from None
                raise
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
            except OSError:
                raise ReservationAdminRefused('MARKER_LOCK_UNAVAILABLE') from None
            guard = child._LocalOwnerReservation(root, path, fd,
                (root_stat.st_dev, root_stat.st_ino), (path_stat.st_dev, path_stat.st_ino))
            marker = guard._read()
            if marker == child._OWNER_RESERVATION_MAGIC + b'C':
                raise ReservationAdminRefused('MARKER_NOT_RESERVED')
            if marker != child._OWNER_RESERVATION_MAGIC + b'R':
                _repair_production_marker(guard, directory_handle, bundle_root=bundle_root,
                                          manifest_sha256=manifest_sha256)
                fd = None
                return
            guard._check()
            marker_time = os.fstat(fd).st_mtime_ns
            boot_ns, processes = _checked_observe(_observe_windows)
            _validate_observation(boot_ns, processes, interpreter_root, interpreter, marker_time)
            verify_binding()
            guard._check()
            if os.fstat(fd).st_mtime_ns != marker_time or guard._read() != child._OWNER_RESERVATION_MAGIC + b'R':
                raise ReservationAdminRefused('MARKER_NOT_RESERVED')
            _release_verified(guard, verify_binding, _scope=_PRODUCTION_SCOPE)
            fd = None
    finally:
        if guard is not None and guard.fd >= 0:
            primary = sys.exc_info()[1]
            try:
                guard.abandon()
            except BaseException as cleanup_failure:
                if primary is not None and not isinstance(primary, Exception):
                    primary.add_note('CLEANUP_UNCERTAIN')
                    raise primary from cleanup_failure
                if not isinstance(cleanup_failure, Exception):
                    raise
                if (isinstance(primary, (_AdminReason, ReservationAdminRefused))
                        and str(primary) == 'RESET_PUBLICATION_UNCERTAIN'):
                    raise ReservationAdminRefused('RESET_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN') from None
                raise
            fd = None
        if fd is not None:
            os.close(fd)


def _marker(root, interpreter_root, *, operation, observe, interpreter=None, verify_binding=lambda: None):
    """Synthetic seam only; CLI supplies fixed root, native observer and binding.

    RESET_PUBLICATION_UNCERTAIN means the marker may already be C or may still
    be R. Never automatically revert it or retry; inspect current ownership and
    marker state using an independently authorized operator procedure.
    A CLEANUP_UNCERTAIN suffix additionally means releasing the marker handle
    failed; it does not imply that the handle was either retained or closed.
    """
    if os.name != 'nt' or operation not in ('create', 'reset'):
        raise ReservationAdminRefused('invalid_operation')
    fd = None
    guard = None
    try:
        import msvcrt
        root = Path(root)
        try:
            resolved_root = root.resolve(strict=True)
            resolved_production = child._AUDIT_ROOT.resolve(strict=True)
            absent._plain(root, directory=True)
        except (OSError, ValueError):
            raise _AdminReason('ROOT_UNAVAILABLE') from None
        if (os.path.normcase(os.path.abspath(str(root))) == os.path.normcase(os.path.abspath(str(child._AUDIT_ROOT)))
                or os.path.normcase(str(resolved_root)) == os.path.normcase(str(resolved_production))):
            raise _AdminReason('PRODUCTION_ROUTE_UNAVAILABLE')
        _assert_synthetic_root(root)
        root_stat = os.lstat(root)
        with _pin_directory_chain(root) as directory_handle:
            absent._plain(root, directory=True)
            now_root = os.lstat(root)
            if (now_root.st_dev, now_root.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                raise ValueError('root_changed')
            verify_binding()
            path = root / child._OWNER_RESERVATION_NAME
            if operation == 'create':
                if os.path.lexists(path):
                    raise _AdminReason('MARKER_EXISTS')
                # Both observations precede publication.  Publish complete C
                # atomically; never delete a successfully published marker as
                # another owner may have acquired it immediately afterward.
                boot_ns, processes = _checked_observe(observe)
                _validate_observation(boot_ns, processes, interpreter_root, interpreter)
                verify_binding()
                boot_ns, processes = _checked_observe(observe)
                _validate_observation(boot_ns, processes, interpreter_root, interpreter)
                verify_binding()
                _atomic_reserved(path, directory_handle, b'C')
                return
            published = None
            try:
                os.lstat(path)
            except FileNotFoundError:
                raise _AdminReason('MARKER_MISSING') from None
            absent._plain(path, directory=False)
            fd = child._open_owned_marker(path)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
            except OSError:
                raise _AdminReason('MARKER_LOCK_UNAVAILABLE') from None
            path_stat = os.lstat(path)
            if published is not None and (path_stat.st_dev, path_stat.st_ino) != published:
                raise ValueError('published_changed')
            guard = child._LocalOwnerReservation(root, path, fd,
                (root_stat.st_dev, root_stat.st_ino), (path_stat.st_dev, path_stat.st_ino))
            marker = guard._read()
            if marker == child._OWNER_RESERVATION_MAGIC + b'C':
                raise _AdminReason('MARKER_NOT_RESERVED')
            if marker != child._OWNER_RESERVATION_MAGIC + b'R':
                _repair_synthetic_marker(guard, directory_handle, observe, interpreter_root, interpreter,
                                         verify_binding)
                fd = None
                return
            guard._check()
            marker_time = os.fstat(fd).st_mtime_ns
            boot_ns, processes = _checked_observe(observe)
            _validate_observation(boot_ns, processes, interpreter_root, interpreter,
                                  marker_time if operation == 'reset' else None)
            verify_binding()
            guard._check()
            if os.fstat(fd).st_mtime_ns != marker_time or guard._read() != child._OWNER_RESERVATION_MAGIC + b'R':
                raise ValueError('marker_changed')
            try:
                guard.release_clean(service_stopped=True, owner_stopped=True,
                                    owner_owned=False, control_stopped=True)
            except Exception:
                # C can be visible before fsync or the post-write check fails.
                # The wrapped guard error does not reveal whether write began.
                raise _AdminReason('RESET_PUBLICATION_UNCERTAIN') from None
            fd = None
    except _ProcessObservationRefused:
        raise
    except _AdminReason as exc:
        reason = str(exc)
        raise ReservationAdminRefused(reason if reason in _REASONS else 'ADMIN_REFUSED') from None
    except ReservationAdminRefused as exc:
        reason = {'runtime_process_observed': 'RUNTIME_PROCESS_OBSERVED',
                  'runtime_identity_invalid': 'RUNTIME_IDENTITY_UNKNOWN',
                  'SYNTHETIC_SCOPE_REQUIRED': 'SYNTHETIC_SCOPE_REQUIRED',
                  'CREATE_PUBLICATION_UNCERTAIN': 'CREATE_PUBLICATION_UNCERTAIN',
                  'CREATE_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN': 'CREATE_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN',
                  'staging_cleanup_unproven': 'STAGING_CLEANUP_UNPROVEN'}.get(str(exc), 'ADMIN_REFUSED')
        raise ReservationAdminRefused(reason) from None
    except Exception:
        raise ReservationAdminRefused('ADMIN_REFUSED') from None
    finally:
        if guard is not None and guard.fd >= 0:
            primary = sys.exc_info()[1]
            try:
                guard.abandon()
            except BaseException as cleanup_failure:
                if primary is not None and not isinstance(primary, Exception):
                    primary.add_note('CLEANUP_UNCERTAIN')
                    raise primary from cleanup_failure
                if not isinstance(cleanup_failure, Exception):
                    raise
                if (isinstance(primary, ReservationAdminRefused)
                        and str(primary) == 'RESET_PUBLICATION_UNCERTAIN'):
                    raise ReservationAdminRefused(
                        'RESET_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN') from None
                raise
            fd = None
        if fd is not None:
            os.close(fd)


def _current_binding(bundle_root, manifest_sha256):
    """Bind the supplied pair and this code to the actual installed pointer."""
    pointer = _CURRENT_POINTER
    absent._plain(pointer, directory=False)
    stat = os.lstat(pointer)
    if stat.st_nlink != 1 or stat.st_size > 65536:
        raise ReservationAdminRefused('current_pointer_invalid')
    raw = pointer.read_bytes()
    doc = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=absent._unique_object)
    commit, pin = doc.get('source_commit'), doc.get('manifest_sha256')
    if (type(commit) is not str or re.fullmatch('[0-9a-f]{40}', commit) is None
            or type(pin) is not str or re.fullmatch('[0-9A-Fa-f]{64}', pin) is None
            or doc.get('final_commit') != commit or str(doc.get('final_manifest_sha256', '')).lower() != pin.lower()
            or type(manifest_sha256) is not str or manifest_sha256.lower() != pin.lower()):
        raise ReservationAdminRefused('current_pointer_invalid')
    expected = _BUNDLE_STORE / commit
    if (os.path.normcase(os.path.abspath(bundle_root)) != os.path.normcase(str(expected))
            or os.path.normcase(os.path.abspath(str(doc.get('active_bundle', '')))) != os.path.normcase(str(expected))):
        raise ReservationAdminRefused('current_bundle_mismatch')
    manifest_path = expected / 'deployment-manifest.json'
    manifest_raw = absent._read_pinned(manifest_path, pin, absent.MAX_CONTROL_BYTES)
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=absent._unique_object)
    if manifest.get('source_commit') != commit:
        raise ReservationAdminRefused('manifest_commit')
    source = expected / 'tools-bootstrap' / 'tools' / 'wd_mind_owner_reservation_admin.py'
    absent._plain(source, directory=False)
    if not os.path.samefile(source, __file__):
        raise ReservationAdminRefused('admin_code_origin')
    source_pin = manifest.get('files', {}).get('tools-bootstrap/tools/wd_mind_owner_reservation_admin.py')
    if type(source_pin) is not str or hashlib.sha256(source.read_bytes()).hexdigest() != source_pin.lower():
        raise ReservationAdminRefused('admin_code_pin')
    identity = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    def verify():
        absent._plain(pointer, directory=False)
        current = os.lstat(pointer)
        if ((current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != identity
                or pointer.read_bytes() != raw
                or absent._read_pinned(manifest_path, pin, absent.MAX_CONTROL_BYTES) != manifest_raw):
            raise ReservationAdminRefused('current_pointer_changed')
    verify()
    from tools import wd_mind_full_runtime as runtime
    location = runtime.resolve_manifest_runtime_location(enabled=True, bundle_root=str(expected),
        expected_manifest_sha256=pin)
    verify()
    return location, verify


def _validate_bootstrap_loader(bundle):
    """Check the expected pinned loader, without claiming caller authentication."""
    module = sys.modules.get(__name__)
    if type(module) is not types.ModuleType or __name__ != 'tools.wd_mind_owner_reservation_admin':
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    spec = getattr(module, '__spec__', None)
    loader = getattr(spec, 'loader', None)
    if (loader is None or type(loader).__name__ != '_PinnedFinder'
            or type(loader).__module__ != 'wd_mind_admin_verified_bootstrap'
            or not sys.meta_path or sys.meta_path[0] is not loader
            or getattr(module, '__loader__', None) is not loader):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    blobs = getattr(loader, '_blobs', None)
    item = blobs.get(__name__) if type(blobs) is dict else None
    expected = Path(bundle) / 'tools-bootstrap' / 'tools' / 'wd_mind_owner_reservation_admin.py'
    if (type(item) is not tuple or len(item) != 3 or type(item[0]) is not str
            or type(item[1]) is not bytes or not 0 < len(item[1]) <= 8 * 1024 * 1024
            or item[2] is not False or os.path.normcase(os.path.abspath(item[0])) != os.path.normcase(str(expected))
            or os.path.normcase(os.path.abspath(__file__)) != os.path.normcase(str(expected))):
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
    if absent._read_pinned(expected, hashlib.sha256(item[1]).hexdigest(), 8 * 1024 * 1024) != item[1]:
        raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if (type(argv) not in (list, tuple) or len(argv) != 5
            or any(type(value) is not str for value in argv)
            or argv[0] not in ('create', 'reset')
            or set(argv[1::2]) != {'--source-commit', '--manifest-sha256'}):
        print('owner_marker_invalid_input', file=sys.stderr)
        return 2
    args = dict(zip(argv[1::2], argv[2::2]))
    if (re.fullmatch(r'[0-9a-f]{40}', args['--source-commit']) is None
            or re.fullmatch(r'[0-9a-fA-F]{64}', args['--manifest-sha256']) is None):
        print('owner_marker_invalid_input', file=sys.stderr)
        return 2
    if _PRODUCTION_ENTRY_ENABLED is not True:
        print('owner_marker_production_route_unavailable', file=sys.stderr)
        return 2
    bundle = str(_BUNDLE_STORE / args['--source-commit'])
    pin = args['--manifest-sha256'].lower()
    try:
        if getattr(_PRODUCTION_CONTEXT, 'value', None) is not None:
            raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
        _validate_bootstrap_loader(bundle)
        location, verify_binding = _current_binding(bundle, pin)
        if (type(location) is not dict or location.get('interpreter_status') != 'VERIFIED'
                or not callable(verify_binding)):
            raise ReservationAdminRefused('PRODUCTION_ROUTE_UNAVAILABLE')
        _validate_bootstrap_loader(bundle)
        verify_binding()
        context = _ProductionContext(argv[0], bundle, pin)
        try:
            _PRODUCTION_CONTEXT.value = context
            _production_marker(operation=argv[0], bundle_root=bundle, manifest_sha256=pin)
        finally:
            if getattr(_PRODUCTION_CONTEXT, 'value', None) is context:
                del _PRODUCTION_CONTEXT.value
        print('owner_marker_' + argv[0] + '_complete')
        return 0
    except _ProcessObservationRefused as exc:
        print(str(exc), file=sys.stderr)
    except (_AdminReason, ReservationAdminRefused) as exc:
        reason = str(exc)
        reason = {'runtime_process_observed': 'RUNTIME_PROCESS_OBSERVED',
                  'runtime_identity_invalid': 'RUNTIME_IDENTITY_UNKNOWN'}.get(reason, reason)
        safe = _REASONS | {'RUNTIME_PROCESS_OBSERVED', 'RUNTIME_IDENTITY_UNKNOWN',
                           'RESET_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN',
                           'CREATE_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN'}
        print('owner_marker_' + (reason.lower() if reason in safe else 'refused'), file=sys.stderr)
    except Exception:
        print('owner_marker_refused', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
