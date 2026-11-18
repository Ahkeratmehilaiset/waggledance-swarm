"""Synthetic marker administration tests; never touch the live marker."""
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time

import pytest

from tools import wd_mind_owner_reservation_admin as admin
from tools import wd_mind_runtime_child as child
from tools import wd_mind_vector_cache as cache


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range locking')
REPO = Path(__file__).resolve().parents[2]


def _paths(tmp_path):
    root = tmp_path / 'audit'
    root.mkdir()
    runtime = tmp_path / 'interpreter'
    runtime.mkdir()
    return root, runtime, root / child._OWNER_RESERVATION_NAME


def _clear(boot=1):
    return lambda: (boot, ())


def _reserved(path):
    path.write_bytes(child._OWNER_RESERVATION_MAGIC + b'R')


def test_marker_refuses_synthetic_directory_not_named_audit(tmp_path):
    root = tmp_path / 'forbidden'
    root.mkdir()
    runtime = tmp_path / 'interpreter'
    runtime.mkdir()
    with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert not (root / child._OWNER_RESERVATION_NAME).exists()


def test_marker_refuses_extra_nested_synthetic_audit_root(tmp_path):
    root = tmp_path / 'extra' / 'audit'
    root.mkdir(parents=True)
    runtime = tmp_path / 'interpreter'
    runtime.mkdir()
    with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert not (root / child._OWNER_RESERVATION_NAME).exists()


def test_relative_create_refuses_handle_to_non_test_root(tmp_path):
    root = tmp_path / 'forbidden'
    root.mkdir()
    fd = None
    try:
        with admin._pin_directory_chain(root) as directory_handle:
            with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
                fd = admin._relative_create(directory_handle, 'synthetic-test-file')
    finally:
        if fd is not None:
            os.close(fd)
    assert not (root / 'synthetic-test-file').exists()


@pytest.mark.parametrize('root', [Path('C:\\'), Path(r'C:\Python'), child._AUDIT_ROOT.parent])
def test_marker_refuses_live_ancestors_before_open_or_write(tmp_path, monkeypatch, root):
    def unexpected(*args, **kwargs):
        raise AssertionError('must_not_open_live_scope')
    monkeypatch.setattr(admin, '_pin_directory_chain', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
        admin._marker(root, tmp_path, operation='create', observe=_clear())


def test_live_root_case_alias_is_refused_before_mutation(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('must_not_open_live_scope')
    monkeypatch.setattr(admin, '_pin_directory_chain', unexpected)
    alias = Path(str(child._AUDIT_ROOT).swapcase())
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._marker(alias, tmp_path, operation='create', observe=_clear())


def test_live_descendant_is_refused_before_mutation(tmp_path, monkeypatch):
    live_descendant = child._AUDIT_ROOT / 'wd-mind-runtime'
    if not live_descendant.is_dir():
        pytest.skip('live descendant absent; no creation attempted')
    def unexpected(*args, **kwargs):
        raise AssertionError('must_not_open_live_scope')
    monkeypatch.setattr(admin, '_pin_directory_chain', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
        admin._marker(live_descendant, tmp_path, operation='create', observe=_clear())


def test_symlink_alias_to_live_root_is_refused_before_mutation(tmp_path, monkeypatch):
    alias = tmp_path / 'audit'
    try:
        alias.symlink_to(child._AUDIT_ROOT, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'symlink setup unavailable: {exc.winerror}')
    def unexpected(*args, **kwargs):
        raise AssertionError('must_not_open_live_scope')
    monkeypatch.setattr(admin, '_pin_directory_chain', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^(ROOT_UNAVAILABLE|PRODUCTION_ROUTE_UNAVAILABLE)$'):
        admin._marker(alias, tmp_path, operation='create', observe=_clear())


def test_atomic_publish_rejects_root_handle_path_mismatch(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    other = tmp_path.parent / (tmp_path.name + '-peer') / 'audit'
    other.mkdir(parents=True)
    with admin._pin_directory_chain(root) as directory_handle:
        with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
            admin._atomic_reserved(other / marker.name, directory_handle)
    assert not marker.exists() and not (other / marker.name).exists()


def test_native_discard_refuses_handle_outside_synthetic_test_leaf(tmp_path):
    import msvcrt
    root = tmp_path / 'forbidden'
    root.mkdir()
    file = root / (child._OWNER_RESERVATION_NAME + '.init-' + 'a' * 32)
    file.write_bytes(b'keep')
    fd = os.open(file, os.O_RDWR | os.O_BINARY)
    try:
        with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
            admin._discard_new_native_handle(msvcrt.get_osfhandle(fd))
    finally:
        os.close(fd)
    assert file.read_bytes() == b'keep'


def test_missing_root_returns_fixed_reason_without_path(tmp_path):
    missing = tmp_path / 'private-missing-root'
    with pytest.raises(admin.ReservationAdminRefused, match='^ROOT_UNAVAILABLE$') as exc:
        admin._marker(missing, tmp_path, operation='create', observe=_clear())
    assert 'private-missing-root' not in str(exc.value)


def test_reset_missing_marker_has_actionable_fixed_reason(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    with pytest.raises(admin.ReservationAdminRefused, match='^MARKER_MISSING$'):
        admin._marker(root, runtime, operation='reset', observe=_clear())


def test_reset_same_boot_has_actionable_fixed_reason(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    with pytest.raises(admin.ReservationAdminRefused, match='^REBOOT_REQUIRED$'):
        admin._marker(root, runtime, operation='reset', observe=_clear(marker.stat().st_mtime_ns))


def _make_junction(path, target):
    path.mkdir()
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x02200000, None)
    if h == wintypes.HANDLE(-1).value:
        pytest.skip('junction setup unavailable')
    try:
        substitute = ('\\??\\' + str(target)).encode('utf-16-le')
        display = str(target).encode('utf-16-le')
        names = substitute + b'\x00\x00' + display + b'\x00\x00'
        record = struct.pack('<IHHHHHH', 0xa0000003, 8 + len(names), 0,
            0, len(substitute), len(substitute) + 2, len(display)) + names
        k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.DeviceIoControl.restype = wintypes.BOOL
        returned, buf = wintypes.DWORD(), ctypes.create_string_buffer(record)
        if not k.DeviceIoControl(h, 0x000900a4, buf, len(record), None, 0, ctypes.byref(returned), None):
            pytest.skip(f'junction setup unavailable: {ctypes.get_last_error()}')
    finally:
        k.CloseHandle(h)


def test_create_clear_once_and_refuse_existing(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    admin._marker(root, runtime, operation='create', observe=_clear())
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    with pytest.raises(admin.ReservationAdminRefused, match='^MARKER_EXISTS$'):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    assert list(root.glob(marker.name + '.init-*')) == []


def test_create_refuses_existing_link(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    target = tmp_path / 'target'
    target.write_bytes(b'unchanged')
    os.link(target, marker)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert target.read_bytes() == b'unchanged'


def test_partial_create_never_publishes_malformed_marker_and_retry_succeeds(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    real_write = os.write
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, 'write', lambda fd, data: real_write(fd, data[:2]))
        with pytest.raises(admin.ReservationAdminRefused):
            admin._marker(root, runtime, operation='create', observe=_clear())
    assert not marker.exists()
    assert list(root.glob(marker.name + '.init-*')) == []
    admin._marker(root, runtime, operation='create', observe=_clear())
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_concurrent_creates_have_one_winner_and_complete_marker(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    code = r'''import os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools import wd_mind_owner_reservation_admin as a
root,runtime,barrier,slot=map(Path,sys.argv[2:])
real=os.fsync
def sync(fd):
    real(fd)
    (barrier/slot).write_bytes(b'ready')
    deadline=time.monotonic()+20
    while not (barrier/'go').exists():
        if time.monotonic()>=deadline: raise RuntimeError('barrier_timeout')
        time.sleep(.01)
a.os.fsync=sync
try:
    a._marker(root,runtime,operation='create',observe=lambda:(1,()))
    print('SUCCESS',flush=True)
except a.ReservationAdminRefused:
    print('REFUSED',flush=True)
'''
    children = [subprocess.Popen([sys.executable, '-I', '-c', code, str(REPO), str(root),
        str(runtime), str(tmp_path), slot], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW) for slot in ('a', 'b')]
    try:
        deadline = time.monotonic() + 20
        while not all((tmp_path / slot).exists() for slot in ('a', 'b')):
            if time.monotonic() >= deadline or any(p.poll() is not None for p in children):
                pytest.fail('synthetic publishers did not both reach fsync')
            time.sleep(.01)
    finally:
        (tmp_path / 'go').write_bytes(b'go')
        results = [p.communicate() for p in children]
    assert all(p.returncode == 0 for p in children), results
    assert sorted(out.strip() for out, err in results) == [b'REFUSED', b'SUCCESS']
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    assert list(root.glob(marker.name + '.init-*')) == []


def test_cleanup_refuses_different_file_identity_without_deleting_it(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    other = root / 'foreign'
    other.write_bytes(b'unknown-keep')
    with admin._pin_directory_chain(root) as directory_handle:
        fd = admin._relative_create(directory_handle, 'owned-staging')
        try:
            os.write(fd, b'owned')
            stat = os.fstat(fd)
            with pytest.raises(ValueError, match='staging_cleanup_unproven'):
                admin._discard_owned_temp(fd, other, (stat.st_dev, stat.st_ino))
        finally:
            os.close(fd)
    assert other.read_bytes() == b'unknown-keep'
    assert (root / 'owned-staging').read_bytes() == b'owned'


def test_fstat_failure_after_relative_create_closes_and_removes_owned_staging(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    captured = []
    real_create, real_fstat = admin._relative_create, os.fstat
    def capture(*args):
        fd = real_create(*args)
        captured.append(fd)
        return fd
    def fail_once(fd):
        if captured and fd == captured[0]:
            raise OSError('synthetic_fstat_failure')
        return real_fstat(fd)
    try:
        with admin._pin_directory_chain(root) as directory_handle:
            with monkeypatch.context() as patch:
                patch.setattr(admin, '_relative_create', capture)
                patch.setattr(admin.os, 'fstat', fail_once)
                with pytest.raises(OSError):
                    admin._atomic_reserved(marker, directory_handle)
        with pytest.raises(OSError):
            real_fstat(captured[0])
        assert not marker.exists() and list(root.glob(marker.name + '.init-*')) == []
    finally:
        if captured:
            try: os.close(captured[0])
            except OSError: pass


def test_open_osfhandle_failure_removes_exact_native_created_staging(tmp_path, monkeypatch):
    import msvcrt
    root, runtime, marker = _paths(tmp_path)
    with admin._pin_directory_chain(root) as directory_handle:
        monkeypatch.setattr(msvcrt, 'open_osfhandle', lambda *a: (_ for _ in ()).throw(OSError('conversion')))
        with pytest.raises(OSError):
            admin._atomic_reserved(marker, directory_handle)
    assert not marker.exists() and list(root.glob(marker.name + '.init-*')) == []


def test_interruption_after_native_create_closes_and_removes_owned_staging(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    real_load = ctypes.WinDLL
    real_native = real_load('ntdll', use_last_error=True)
    captured = []
    class InterruptingCreate:
        def __call__(self, *args):
            real = real_native.NtCreateFile
            real.argtypes, real.restype = self.argtypes, self.restype
            status = real(*args)
            if status == 0:
                captured.append(ctypes.cast(args[0], ctypes.POINTER(wintypes.HANDLE)).contents.value)
                raise KeyboardInterrupt('synthetic_after_native_create')
            return status
    class NativeProxy:
        def __init__(self): self.NtCreateFile = InterruptingCreate()
        def __getattr__(self, name): return getattr(real_native, name)
    def load(name, *args, **kwargs):
        return NativeProxy() if name == 'ntdll' else real_load(name, *args, **kwargs)
    with admin._pin_directory_chain(root) as directory_handle:
        with monkeypatch.context() as patch:
            patch.setattr(admin.ctypes, 'WinDLL', load)
            with pytest.raises(KeyboardInterrupt, match='synthetic_after_native_create'):
                admin._atomic_reserved(marker, directory_handle)
    kernel = real_load('kernel32', use_last_error=True)
    kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    flags = wintypes.DWORD()
    assert len(captured) == 1 and not kernel.GetHandleInformation(captured[0], ctypes.byref(flags))
    assert not marker.exists() and list(root.glob(marker.name + '.init-*')) == []


def test_open_osfhandle_and_native_cleanup_failure_closes_handle_and_keeps_leaf(tmp_path, monkeypatch):
    import msvcrt
    root, runtime, marker = _paths(tmp_path)
    captured = []
    def denied(handle):
        captured.append(handle.value if hasattr(handle, 'value') else handle)
        raise admin.ReservationAdminRefused('staging_cleanup_unproven')
    with admin._pin_directory_chain(root) as directory_handle:
        monkeypatch.setattr(admin, '_discard_new_native_handle', denied)
        monkeypatch.setattr(msvcrt, 'open_osfhandle', lambda *a: (_ for _ in ()).throw(OSError('conversion')))
        with pytest.raises(admin.ReservationAdminRefused, match='staging_cleanup_unproven'):
            admin._atomic_reserved(marker, directory_handle)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetHandleInformation.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    assert len(captured) == 1 and not kernel.GetHandleInformation(captured[0], ctypes.byref(flags))
    assert not marker.exists() and len(list(root.glob(marker.name + '.init-*'))) == 1


def test_fstat_and_native_cleanup_failure_still_closes_fd_and_keeps_leaf(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    captured = []
    real_create, real_fstat = admin._relative_create, os.fstat
    def capture(*args):
        fd = real_create(*args)
        captured.append(fd)
        return fd
    def fail(fd):
        if captured and fd == captured[0]:
            raise OSError('synthetic_fstat_failure')
        return real_fstat(fd)
    with admin._pin_directory_chain(root) as directory_handle:
        with monkeypatch.context() as patch:
            patch.setattr(admin, '_relative_create', capture)
            patch.setattr(admin, '_discard_new_native_handle', lambda h: (_ for _ in ()).throw(
                admin.ReservationAdminRefused('staging_cleanup_unproven')))
            patch.setattr(admin.os, 'fstat', fail)
            with pytest.raises(admin.ReservationAdminRefused, match='staging_cleanup_unproven'):
                admin._atomic_reserved(marker, directory_handle)
    with pytest.raises(OSError):
        real_fstat(captured[0])
    assert not marker.exists() and len(list(root.glob(marker.name + '.init-*'))) == 1


def test_open_staging_leaf_cannot_be_renamed_by_other_process(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    source, moved = root / 'owned-staging', root / 'moved-staging'
    code = ('import os,sys\ntry: os.rename(sys.argv[1],sys.argv[2])\n'
            'except OSError: sys.exit(0)\nsys.exit(3)')
    with admin._pin_directory_chain(root) as directory_handle:
        fd = admin._relative_create(directory_handle, source.name)
        try:
            os.write(fd, b'owned')
            info = os.fstat(fd)
            result = subprocess.run([sys.executable, '-I', '-c', code, str(source), str(moved)],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0
            by_path = os.lstat(source)
            assert (info.st_dev, info.st_ino) == (by_path.st_dev, by_path.st_ino)
        finally:
            os.close(fd)
    assert source.read_bytes() == b'owned' and not moved.exists()


@pytest.mark.parametrize('point,code', [('partial', 31), ('after_fsync', 32), ('after_publish', 33)])
def test_interrupted_synthetic_publisher_leaves_recoverable_final(tmp_path, point, code):
    root, runtime, marker = _paths(tmp_path)
    program = r'''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools import wd_mind_owner_reservation_admin as a
root,runtime=map(Path,sys.argv[2:4]); point=sys.argv[4]
if point=='partial':
    real=os.write
    def interrupted(fd,data):
        real(fd,data[:3]); os._exit(31)
    a.os.write=interrupted
elif point=='after_fsync':
    real=os.fsync
    def interrupted(fd):
        real(fd); os._exit(32)
    a.os.fsync=interrupted
else:
    real=a._atomic_reserved
    def interrupted(*args):
        real(*args); os._exit(33)
    a._atomic_reserved=interrupted
a._marker(root,runtime,operation='create',observe=lambda:(1,()))
sys.exit(9)
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(REPO), str(root),
        str(runtime), point], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == code, result.stderr
    if point == 'after_publish':
        assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    else:
        assert not marker.exists()
        admin._marker(root, runtime, operation='create', observe=_clear())
        assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_existing_symlink_leaf_is_never_replaced(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    target = tmp_path / 'target'
    target.write_bytes(b'untouched')
    try:
        marker.symlink_to(target)
    except OSError as exc:
        pytest.skip(f'symlink creation unavailable: {exc.winerror}')
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert marker.is_symlink() and target.read_bytes() == b'untouched'


def test_directory_junction_is_rejected(tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    marker = target / child._OWNER_RESERVATION_NAME
    root = tmp_path / 'junction'
    _make_junction(root, target)
    with pytest.raises(Exception):
        with admin._pin_directory_chain(root):
            pass
    assert not marker.exists()


@pytest.mark.parametrize('component', ['first', 'second', 'root'])
@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_reparse_inserted_before_each_synthetic_component_is_refused(tmp_path, monkeypatch, component, kind):
    base = tmp_path / 'chain'
    first, second, root = base / 'first', base / 'first' / 'second', base / 'first' / 'second' / 'root'
    root.mkdir(parents=True)
    target = tmp_path / 'foreign-target'
    target.mkdir()
    sentinel = target / 'sentinel'
    sentinel.write_bytes(b'untouched')
    attack = {'first': first, 'second': second, 'root': root}[component]
    original_open = cache._Native.open
    did_swap = False
    def open_after_swap(self, path, *args, **kwargs):
        nonlocal did_swap
        if Path(path) == attack and not did_swap:
            did_swap = True
            os.rename(attack, attack.with_name(attack.name + '-old'))
            if kind == 'junction':
                _make_junction(attack, target)
            else:
                try:
                    attack.symlink_to(target, target_is_directory=True)
                except OSError as exc:
                    pytest.skip(f'symlink setup unavailable: {exc.winerror}')
        return original_open(self, path, *args, **kwargs)
    monkeypatch.setattr(cache._Native, 'open', open_after_swap)
    with pytest.raises(Exception):
        with admin._pin_directory_chain(root):
            pass
    assert did_swap and sentinel.read_bytes() == b'untouched'
    assert not (target / child._OWNER_RESERVATION_NAME).exists()


def test_directory_handles_block_rename_while_pinned(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    with admin._pin_directory_chain(root):
        with pytest.raises(OSError):
            os.rename(root, root.with_name('moved'))
    assert root.exists() and not marker.exists()


def test_directory_handles_block_crossprocess_rename_while_pinned(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    code = ('import os,sys\n'
            'try: os.rename(sys.argv[1],sys.argv[2])\n'
            'except OSError: sys.exit(0)\n'
            'sys.exit(3)\n')
    with admin._pin_directory_chain(root):
        result = subprocess.run([sys.executable, '-I', '-c', code, str(root), str(root.with_name('moved'))],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0
    assert root.exists() and not marker.exists()


def test_root_change_between_observation_and_pin_refuses_before_marker(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    @contextlib.contextmanager
    def changed(path):
        os.rename(root, root.with_name('old-root'))
        root.mkdir()
        yield
    monkeypatch.setattr(admin, '_pin_directory_chain', changed)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert not marker.exists()


@pytest.mark.parametrize('boot_relation', ['equal', 'older'])
def test_reset_refuses_without_strictly_later_boot(tmp_path, boot_relation):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    mtime = marker.stat().st_mtime_ns
    boot = mtime if boot_relation == 'equal' else mtime - 1
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(boot))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


def test_reset_after_later_boot_and_no_runtime_process(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    admin._marker(root, runtime, operation='reset', observe=_clear(marker.stat().st_mtime_ns + 1))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_reset_fsync_failure_reports_uncertain_without_reverting_clear(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('private-fsync-error')))
        with pytest.raises(admin.ReservationAdminRefused, match='^RESET_PUBLICATION_UNCERTAIN$') as exc:
            admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert 'private-fsync-error' not in str(exc.value)
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    guard = child._reserve_local_owner(root)
    assert guard._read() == child._OWNER_RESERVATION_MAGIC + b'R'
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


def test_reset_release_failure_before_write_is_also_uncertain(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    monkeypatch.setattr(child._LocalOwnerReservation, 'release_clean',
        lambda *a, **kw: (_ for _ in ()).throw(child.LaunchRefused('private-before-write')))
    with pytest.raises(admin.ReservationAdminRefused, match='^RESET_PUBLICATION_UNCERTAIN$'):
        admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


def test_reset_rejects_future_boot(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(time.time_ns() + 10**12))
    assert marker.read_bytes().endswith(b'R')


def test_reset_refuses_hardlinked_marker(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    sibling = tmp_path / 'hardlink'
    os.link(marker, sibling)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(1))
    assert sibling.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


@pytest.mark.parametrize('state', [b'C', b'X', b'', b'RR'])
def test_reset_refuses_non_reserved_or_malformed(tmp_path, state):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + state)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(10**20))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + state


def test_reset_refuses_live_runtime_image(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    (runtime / 'python.exe').write_bytes(b'synthetic')
    observe = lambda: (marker.stat().st_mtime_ns + 1, ((os.getpid() + 1000, str(runtime / 'python.exe')),))
    with pytest.raises(admin.ReservationAdminRefused, match='^RUNTIME_PROCESS_OBSERVED$'):
        admin._marker(root, runtime, operation='reset', observe=observe)
    assert marker.read_bytes().endswith(b'R')


def test_runtime_executable_hardlink_alias_outside_root_is_detected(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    binary = runtime / 'python.exe'
    binary.write_bytes(b'synthetic')
    alias = tmp_path / 'other.exe'
    os.link(binary, alias)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._no_runtime_process(((os.getpid() + 1000, str(alias)),), runtime, str(binary))


def test_reset_refuses_failed_observation_and_keeps_reserved(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    def observe():
        raise PermissionError('synthetic-private-process-data')
    with pytest.raises(admin.ReservationAdminRefused, match='^HOST_OBSERVATION_UNAVAILABLE$'):
        admin._marker(root, runtime, operation='reset', observe=observe)
    assert marker.read_bytes().endswith(b'R')


def test_create_failed_first_observation_does_not_publish_marker(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='create', observe=lambda: (_ for _ in ()).throw(OSError()))
    assert not marker.exists()


def test_create_failed_second_observation_leaves_no_marker_and_retry_succeeds(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    count = 0
    def observe():
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('unknown_after_publication')
        return 1, ()
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='create', observe=observe)
    assert not marker.exists()
    admin._marker(root, runtime, operation='create', observe=_clear())
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_reset_refuses_changed_timestamp_during_observation(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    before = marker.stat().st_mtime_ns
    def observe():
        os.utime(marker, ns=(before + 1_000_000_000, before + 1_000_000_000))
        return before + 2_000_000_000, ()
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=observe)
    assert marker.read_bytes().endswith(b'R')


def test_reset_refuses_locked_marker(tmp_path):
    import msvcrt
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    fd = os.open(marker, os.O_RDWR | os.O_BINARY)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
        with pytest.raises(admin.ReservationAdminRefused):
            admin._marker(root, runtime, operation='reset', observe=_clear(10**20))
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
        os.close(fd)
    assert marker.read_bytes().endswith(b'R')


def test_observer_refuses_inaccessible_process_image(monkeypatch):
    monkeypatch.setattr(admin, '_windows_kernel_boot_ns', lambda: 123)
    monkeypatch.setattr(admin, '_windows_process_images', lambda: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(admin.ReservationAdminRefused, match='^host_observation_unavailable$'):
        admin._observe_windows()


@pytest.mark.parametrize('result,code', [(False, 0), (True, 259)])
def test_unknown_or_active_process_with_unreadable_image_refuses(result, code):
    class Kernel:
        @staticmethod
        def QueryFullProcessImageNameW(*args):
            return False
        @staticmethod
        def GetExitCodeProcess(handle, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = code
            return result
    with pytest.raises(ValueError, match='image_access'):
        admin._image_or_proven_exited(Kernel(), 1)


def test_unreadable_image_of_proven_exited_process_does_not_block():
    class Kernel:
        @staticmethod
        def QueryFullProcessImageNameW(*args):
            return False
        @staticmethod
        def GetExitCodeProcess(handle, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
            return True
    assert admin._image_or_proven_exited(Kernel(), 1) is None


def test_observer_refuses_failed_enumeration(monkeypatch):
    monkeypatch.setattr(admin, '_windows_kernel_boot_ns', lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(admin.ReservationAdminRefused, match='^host_observation_unavailable$'):
        admin._observe_windows()


def test_observer_converts_boot_and_preserves_image(monkeypatch):
    expected = (133000000000000000 - 116444736000000000) * 100
    monkeypatch.setattr(admin, '_windows_kernel_boot_ns', lambda: expected)
    monkeypatch.setattr(admin, '_windows_process_images', lambda: ((12345, 'C:\\safe\\python.exe'),))
    boot, processes = admin._observe_windows()
    assert boot == expected
    assert processes == ((12345, 'C:\\safe\\python.exe'),)


def test_cli_rejects_injected_root_and_proof():
    with pytest.raises(SystemExit):
        admin.main(['reset', '--bundle-root', 'C:\\b', '--manifest-sha256', 'a' * 64,
                    '--marker-root', 'C:\\elsewhere'])


def test_production_entry_and_root_refuse_before_mutation(tmp_path, monkeypatch, capsys):
    root, runtime, marker = _paths(tmp_path)
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    assert admin.main(['create', '--bundle-root', 'C:\\b', '--manifest-sha256', 'a' * 64]) == 2
    assert capsys.readouterr().err.strip() == 'owner_marker_production_route_unavailable'
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._marker(root, runtime, operation='create', observe=_clear())
    assert not marker.exists()


def _synthetic_installed_binding(tmp_path, monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    commit = 'a' * 40
    store = tmp_path / 'bundles'
    bundle = store / commit
    source = bundle / 'tools-bootstrap' / 'tools' / 'wd_mind_owner_reservation_admin.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'synthetic-code')
    manifest = {'source_commit': commit, 'files': {
        'tools-bootstrap/tools/wd_mind_owner_reservation_admin.py': hashlib.sha256(source.read_bytes()).hexdigest()}}
    raw = json.dumps(manifest).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    pointer = tmp_path / 'pointer.json'
    pointer.write_text(json.dumps({'source_commit': commit, 'final_commit': commit,
        'manifest_sha256': pin, 'final_manifest_sha256': pin, 'active_bundle': str(bundle)}))
    monkeypatch.setattr(admin, '_CURRENT_POINTER', pointer)
    monkeypatch.setattr(admin, '_BUNDLE_STORE', store)
    monkeypatch.setattr(admin.os.path, 'samefile', lambda a, b: True)
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kw: {'interpreter_root': 'synthetic'})
    return bundle, pin, pointer, source


def test_binding_rejects_noncurrent_bundle_or_manifest(tmp_path, monkeypatch):
    bundle, pin, pointer, source = _synthetic_installed_binding(tmp_path, monkeypatch)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._current_binding(str(bundle), 'b' * 64)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._current_binding(str(bundle.parent / ('b' * 40)), pin)


def test_binding_rechecks_current_pointer(tmp_path, monkeypatch):
    bundle, pin, pointer, source = _synthetic_installed_binding(tmp_path, monkeypatch)
    location, verify = admin._current_binding(str(bundle), pin)
    assert location == {'interpreter_root': 'synthetic'}
    verify()
    pointer.write_text(pointer.read_text() + ' ')
    with pytest.raises(admin.ReservationAdminRefused):
        verify()


def test_binding_rejects_admin_source_tampering(tmp_path, monkeypatch):
    bundle, pin, pointer, source = _synthetic_installed_binding(tmp_path, monkeypatch)
    source.write_bytes(b'tampered')
    with pytest.raises(admin.ReservationAdminRefused):
        admin._current_binding(str(bundle), pin)
