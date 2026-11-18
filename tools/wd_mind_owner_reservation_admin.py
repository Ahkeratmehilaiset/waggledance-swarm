"""Synthetic-only experimental owner-marker administration.

Production entry is disabled pending review, integration and host validation.
Do not deploy this draft. A command cannot authenticate a human against
another process using the same account.

This is a cooperating-owner guard, not an authentication boundary.  Process
enumeration is a point-in-time observation: a legacy or same-user process may
start after it, and a legacy interpreter at a different root is not identified.
Those risks and same-user changes remain UNKNOWN.  An uncertain observation
always refuses.  The CLI has no synthetic-path or proof overrides.
"""
from __future__ import annotations

import argparse
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
import time

from tools import wd_mind_absent_generation_prefix_readonly as absent
from tools import wd_mind_runtime_child as child


class ReservationAdminRefused(ValueError):
    """Fixed refusal: never print host process details or paths."""


_CURRENT_POINTER = Path(r'C:\Python\WD_REBOOT_STATE_CURRENT.json')
_BUNDLE_STORE = Path(r'C:\Python\wd-reboot-bundles')


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


def _windows_process_images():
    """Native snapshot without shell execution or killing any process."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
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
        if not available:
            raise ValueError('process_first')
        while available:
            pid = int(entry.pid)
            if pid in seen:
                raise ValueError('duplicate_pid')
            seen.add(pid)
            if pid not in (0, 4):
                process = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
                if not process:
                    raise ValueError('process_access')
                try:
                    size = wintypes.DWORD(32768)
                    path = ctypes.create_unicode_buffer(size.value)
                    if not kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                        raise ValueError('image_access')
                    if not path.value or not Path(path.value).is_absolute():
                        raise ValueError('image_path')
                    items.append((pid, path.value))
                finally:
                    kernel.CloseHandle(process)
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ValueError('process_next')
        return tuple(items)
    finally:
        kernel.CloseHandle(snapshot)


def _observe_windows():
    """One native boot and process observation; unknown images refuse."""
    if os.name != 'nt':
        raise ReservationAdminRefused('host_observation_unavailable')
    try:
        return _windows_kernel_boot_ns(), _windows_process_images()
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
            if os.path.commonpath((root, path)) == root:
                raise ReservationAdminRefused('runtime_process_observed')
        except ValueError:
            raise ReservationAdminRefused('runtime_identity_invalid') from None


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


class _UnicodeString(ctypes.Structure):
    _fields_ = [('length', wintypes.USHORT), ('maximum', wintypes.USHORT), ('buffer', wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.ULONG), ('root', wintypes.HANDLE),
                ('name', ctypes.POINTER(_UnicodeString)), ('attributes', wintypes.ULONG),
                ('security', ctypes.c_void_p), ('qos', ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [('status', ctypes.c_void_p), ('information', ctypes.c_size_t)]


def _relative_create(directory_handle, leaf):
    """Create one ASCII leaf relative to an already pinned directory handle."""
    import msvcrt
    if (type(leaf) is not str or not leaf or leaf in ('.', '..') or not leaf.isascii()
            or any(c in leaf for c in ('/', '\\', ':'))):
        raise ValueError('invalid_leaf')
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
    status = native(ctypes.byref(out), 0xc0110080, ctypes.byref(attributes), ctypes.byref(status_block),
        None, 0x80, 3, 2, 0x00200060, None, 0)  # FILE_CREATE, no reparse, synchronous
    if status != 0:
        raise ValueError('relative_create_refused')
    try:
        return msvcrt.open_osfhandle(out.value, os.O_RDWR | os.O_BINARY | getattr(os, 'O_NOINHERIT', 0))
    except BaseException:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(out)
        raise


def _discard_owned_temp(fd, tmp, identity):
    """Disposition the known open staging handle only; never unlink a path."""
    import msvcrt
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


def _atomic_reserved(path, directory_handle):
    """Publish complete bytes; cleanup only the exact owned staging handle.

    A process crash may leave an incomplete or complete staging leaf, but never
    deliberately deletes an unknown leaf. Power loss, interruption within the
    native calls, same-user ABA and malicious legacy processes remain UNKNOWN.
    """
    import msvcrt
    if path.name != child._OWNER_RESERVATION_NAME:
        raise ValueError('invalid_marker_leaf')
    tmp = path.with_name(path.name + '.init-' + secrets.token_hex(16))
    fd = _relative_create(directory_handle, tmp.name)
    info = os.fstat(fd)
    identity = (info.st_dev, info.st_ino)
    published = False
    try:
        raw = child._OWNER_RESERVATION_MAGIC + b'R'
        if os.write(fd, raw) != len(raw):
            raise ValueError('write')
        os.fsync(fd)
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
        if native(msvcrt.get_osfhandle(fd), ctypes.byref(io), ctypes.byref(rename),
                  ctypes.sizeof(rename), 10) != 0:  # FileRenameInformation, no replace
            raise ValueError('publish_refused')
        published = True
        by_handle = os.fstat(fd)
        by_path = os.lstat(path)
        if ((by_handle.st_dev, by_handle.st_ino) != identity
                or (by_path.st_dev, by_path.st_ino) != identity or by_handle.st_nlink != 1
                or by_path.st_nlink != 1):
            raise ValueError('published_changed')
    finally:
        try:
            if not published:
                _discard_owned_temp(fd, tmp, identity)
        finally:
            os.close(fd)
    return identity


def _validate_observation(boot_ns, processes, interpreter_root, interpreter, marker_time=None):
    if (type(boot_ns) is not int or boot_ns <= 0 or boot_ns > time.time_ns()
            or (marker_time is not None and boot_ns <= marker_time)):
        raise ValueError('boot_not_later')
    _no_runtime_process(processes, interpreter_root, interpreter)


def _marker(root, interpreter_root, *, operation, observe, interpreter=None, verify_binding=lambda: None):
    """Synthetic seam only; CLI supplies fixed root, native observer and binding."""
    if os.name != 'nt' or operation not in ('create', 'reset'):
        raise ReservationAdminRefused('invalid_operation')
    if (os.path.normcase(os.path.abspath(str(root))) == os.path.normcase(os.path.abspath(str(child._AUDIT_ROOT)))
            or os.path.normcase(str(Path(root).resolve(strict=True))) ==
               os.path.normcase(str(child._AUDIT_ROOT.resolve(strict=True)))):
        raise ReservationAdminRefused('production_route_unavailable')
    fd = None
    guard = None
    try:
        import msvcrt
        root = Path(root)
        absent._plain(root, directory=True)
        root_stat = os.lstat(root)
        with _pin_directory_chain(root) as directory_handle:
            absent._plain(root, directory=True)
            now_root = os.lstat(root)
            if (now_root.st_dev, now_root.st_ino) != (root_stat.st_dev, root_stat.st_ino):
                raise ValueError('root_changed')
            verify_binding()
            path = root / child._OWNER_RESERVATION_NAME
            if operation == 'create':
                # Refusal on the first host observation must not leave a new
                # reserved marker which cannot be recovered until next boot.
                boot_ns, processes = observe()
                _validate_observation(boot_ns, processes, interpreter_root, interpreter)
                verify_binding()
                published = _atomic_reserved(path, directory_handle)
            else:
                published = None
            absent._plain(path, directory=False)
            fd = os.open(path, os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0))
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
            path_stat = os.lstat(path)
            if published is not None and (path_stat.st_dev, path_stat.st_ino) != published:
                raise ValueError('published_changed')
            guard = child._LocalOwnerReservation(root, path, fd,
                (root_stat.st_dev, root_stat.st_ino), (path_stat.st_dev, path_stat.st_ino))
            guard._check()
            if guard._read() != child._OWNER_RESERVATION_MAGIC + b'R':
                raise ValueError('not_reserved')
            marker_time = os.fstat(fd).st_mtime_ns
            boot_ns, processes = observe()
            _validate_observation(boot_ns, processes, interpreter_root, interpreter,
                                  marker_time if operation == 'reset' else None)
            verify_binding()
            guard._check()
            if os.fstat(fd).st_mtime_ns != marker_time or guard._read() != child._OWNER_RESERVATION_MAGIC + b'R':
                raise ValueError('marker_changed')
            guard.release_clean(service_stopped=True, owner_stopped=True,
                                owner_owned=False, control_stopped=True)
            fd = None
    except Exception:
        raise ReservationAdminRefused('owner_marker_admin_refused') from None
    finally:
        if guard is not None and guard.fd >= 0:
            guard.abandon()
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('create', 'reset'))
    parser.add_argument('--bundle-root', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args(argv)
    print('owner_marker_production_route_unavailable', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
