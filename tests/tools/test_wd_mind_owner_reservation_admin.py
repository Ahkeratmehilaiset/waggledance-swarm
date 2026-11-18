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


def test_synthetic_root_is_worktree_relative_and_disjoint_from_live_audit():
    assert admin._SYNTHETIC_ROOT == admin._TEST_REPOSITORY / '.codex-audit' / 'marker-admin-synthetic-tests'
    assert os.path.commonpath((os.path.normcase(str(admin._SYNTHETIC_ROOT)),
                               os.path.normcase(str(child._AUDIT_ROOT)))) != os.path.normcase(str(child._AUDIT_ROOT))


def test_synthetic_root_refuses_missing_worktree_metadata_before_filesystem_open(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, '_TEST_REPOSITORY', tmp_path)
    monkeypatch.setattr(admin.absent, '_plain', lambda *a, **k: pytest.fail('must not open synthetic root'))
    with pytest.raises(admin.ReservationAdminRefused, match='^SYNTHETIC_SCOPE_REQUIRED$'):
        admin._assert_synthetic_root(tmp_path)


def test_production_repair_wrapper_is_disabled_before_guard_or_path_access(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('disabled production repair must not inspect a path or handle')
    monkeypatch.setattr(admin.os, 'fstat', unexpected)
    monkeypatch.setattr(admin.os, 'lstat', unexpected)
    monkeypatch.setattr(admin, '_current_binding', unexpected)
    monkeypatch.setattr(admin, '_observe_windows', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._repair_production_marker(None, None, bundle_root='untrusted', manifest_sha256='a'*64)


@pytest.mark.parametrize('scope', ['production', 'invalid'])
def test_common_repair_rejects_unavailable_scope_before_guard_access(scope, monkeypatch):
    def unexpected(*args, **kwargs): pytest.fail('must refuse before guard or path access')
    monkeypatch.setattr(admin.os, 'fstat', unexpected)
    value = admin._PRODUCTION_SCOPE if scope == 'production' else object()
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._repair_marker_locked(None, None, None, None, None, None, scope=value)


def test_common_repair_refuses_synthetic_guard_for_a_foreign_leaf(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    foreign = root / 'other-file'
    foreign.write_bytes(b'broken')
    fd = child._open_owned_marker(foreign)
    info, parent = os.fstat(fd), os.lstat(root)
    guard = child._LocalOwnerReservation(root, foreign, fd,
        (parent.st_dev, parent.st_ino), (info.st_dev, info.st_ino))
    try:
        with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
            admin._repair_marker_locked(guard, None, _clear(), runtime, None, lambda: None,
                                        scope=admin._SYNTHETIC_SCOPE)
    finally:
        guard.abandon()
    assert foreign.read_bytes() == b'broken' and not marker.exists()


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


@pytest.mark.parametrize('damaged', [b'', b'WD_MIND_', child._OWNER_RESERVATION_MAGIC + b'X',
                                    child._OWNER_RESERVATION_MAGIC + b'RR', b'x'*4096])
def test_reset_repairs_damaged_marker_only_after_safe_observations(tmp_path, damaged):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(damaged)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


@pytest.mark.parametrize('fault', ['no_later_boot', 'runtime_alive', 'observation_unknown', 'binding_changed'])
def test_damaged_reset_refuses_uncertain_proof_without_changing_marker(tmp_path, fault):
    root, runtime, marker = _paths(tmp_path)
    damaged = b'broken synthetic marker'
    marker.write_bytes(damaged)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    program = runtime / 'python.exe'
    program.write_bytes(b'synthetic')
    def observe():
        if fault == 'observation_unknown': raise OSError('private')
        return (timestamp if fault == 'no_later_boot' else timestamp + 1_000_000_000,
                ((os.getpid()+1000, str(program)),) if fault == 'runtime_alive' else ())
    calls = []
    def binding():
        calls.append(1)
        if fault == 'binding_changed' and len(calls) > 1: raise OSError('private')
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=observe, verify_binding=binding)
    assert marker.read_bytes() == damaged


def test_damaged_reset_guard_denies_other_write_and_replace_during_observation(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    other = tmp_path/'other-marker'
    other.write_bytes(b'alternate')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    observed = []
    def observe():
        with pytest.raises(OSError):
            fd = os.open(marker, os.O_RDWR | os.O_BINARY)
            os.close(fd)
        with pytest.raises(OSError): os.replace(other, marker)
        observed.append('guarded')
        return timestamp + 1_000_000_000, ()
    admin._marker(root, runtime, operation='reset', observe=observe)
    assert observed == ['guarded']
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    assert other.read_bytes() == b'alternate'


def test_damaged_reset_rechecks_binding_after_last_handle_check(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    original = admin._assert_open_directory_identity
    calls = []
    def handle_check(*args):
        original(*args)
        calls.append('checked')
    def binding():
        if len(calls) >= 2: raise ValueError('synthetic binding changed')
    monkeypatch.setattr(admin, '_assert_open_directory_identity', handle_check)
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000),
                      verify_binding=binding)
    assert calls == ['checked', 'checked']
    assert marker.read_bytes() == b'broken'


def test_damaged_reset_refuses_replaced_file_before_any_write(tmp_path):
    import msvcrt
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'old-damaged')
    old = os.lstat(marker)
    replacement = root / 'replacement'
    replacement.write_bytes(b'new-damaged')
    os.replace(replacement, marker)
    new = os.lstat(marker)
    assert (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    with admin._pin_directory_chain(root) as directory_handle:
        fd = child._open_owned_marker(marker)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
        parent = os.lstat(root)
        guard = child._LocalOwnerReservation(root, marker, fd, (parent.st_dev, parent.st_ino),
                                              (old.st_dev, old.st_ino))
        try:
            with pytest.raises(ValueError, match='marker_changed'):
                admin._repair_synthetic_marker(guard, directory_handle,
                    _clear(timestamp + 1_000_000_000), runtime, None, lambda: None)
        finally:
            guard.abandon()
    assert marker.read_bytes() == b'new-damaged'


def test_damaged_reset_rechecks_guard_file_identity_before_write(tmp_path):
    import msvcrt
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    with admin._pin_directory_chain(root) as directory_handle:
        fd = child._open_owned_marker(marker)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, len(child._OWNER_RESERVATION_MAGIC) + 1)
        parent, info = os.lstat(root), os.fstat(fd)
        guard = child._LocalOwnerReservation(root, marker, fd, (parent.st_dev, parent.st_ino),
                                              (info.st_dev, info.st_ino))
        def observe():
            guard.file_id = (info.st_dev, info.st_ino + 1)
            return timestamp + 1_000_000_000, ()
        try:
            with pytest.raises(ValueError, match='marker_changed'):
                admin._repair_synthetic_marker(guard, directory_handle, observe, runtime, None, lambda: None)
        finally:
            guard.abandon()
    assert marker.read_bytes() == b'broken'


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit])
def test_damaged_reset_propagates_interruption_after_write_started(tmp_path, monkeypatch, interrupt):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    write = os.write
    def interrupted_write(fd, data):
        write(fd, data[:3])
        raise interrupt('synthetic interruption')
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, 'write', interrupted_write)
        with pytest.raises(interrupt):
            admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert marker.read_bytes() != b'broken'
    # The caller has no clean-release proof and must not automatically retry.
    assert marker.read_bytes() != child._OWNER_RESERVATION_MAGIC + b'C'


@pytest.mark.parametrize('operation', ['write', 'ftruncate', 'fsync'])
def test_damaged_reset_partial_publication_is_reported_unknown(tmp_path, monkeypatch, operation):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    def fail(*args): raise OSError('private synthetic error')
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, operation, fail)
        with pytest.raises(admin.ReservationAdminRefused, match='^RESET_PUBLICATION_UNCERTAIN$') as exc:
            admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert 'private' not in str(exc.value)
    assert marker.exists()


def test_damaged_reset_short_write_leaves_publication_uncertain(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    original_write = os.write
    def short_write(fd, data):
        return original_write(fd, data[:3])
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, 'write', short_write)
        with pytest.raises(admin.ReservationAdminRefused, match='^RESET_PUBLICATION_UNCERTAIN$'):
            admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC[:3] + b'ken'
    assert marker.read_bytes() != child._OWNER_RESERVATION_MAGIC + b'C'


def test_damaged_reset_refuses_hardlinked_marker_without_writing(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    marker.write_bytes(b'broken')
    other = tmp_path / 'hardlink'
    os.link(marker, other)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    with pytest.raises(admin.ReservationAdminRefused):
        admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
    assert marker.read_bytes() == other.read_bytes() == b'broken'


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


@pytest.mark.parametrize('fail_before_close', [False, True])
def test_reset_publication_and_cleanup_failures_are_both_uncertain(tmp_path, monkeypatch, fail_before_close):
    root, runtime, marker = _paths(tmp_path)
    _reserved(marker)
    timestamp = 1_600_000_000_000_000_000
    os.utime(marker, ns=(timestamp, timestamp))
    real_abandon = child._LocalOwnerReservation.abandon
    failed_guard = []

    def fail_cleanup(guard):
        failed_guard.append(guard)
        if not fail_before_close:
            real_abandon(guard)
        raise OSError('private-cleanup-error')

    try:
        with monkeypatch.context() as patch:
            patch.setattr(admin.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('private-fsync-error')))
            patch.setattr(child._LocalOwnerReservation, 'abandon', fail_cleanup)
            with pytest.raises(admin.ReservationAdminRefused,
                               match='^RESET_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN$') as exc:
                admin._marker(root, runtime, operation='reset', observe=_clear(timestamp + 1_000_000_000))
        assert (failed_guard[0].fd >= 0) is fail_before_close
    finally:
        for guard in failed_guard:
            if guard.fd >= 0:
                real_abandon(guard)
    assert 'private-' not in str(exc.value)
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


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


@pytest.mark.parametrize('case', ['empty', 'missing_self', 'duplicate_self', 'iteration_error', 'complete'])
def test_synthetic_process_snapshot_requires_own_pid_and_complete_enumeration(monkeypatch, case):
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    own = 91919
    monkeypatch.setattr(admin.os, 'getpid', lambda: own)
    rows = {'empty': (), 'missing_self': (123,), 'duplicate_self': (own, own),
            'iteration_error': (own,), 'complete': (own, 123)}[case]
    closed = []
    class Kernel:
        def __init__(self):
            self.index = 0
            self.CreateToolhelp32Snapshot = Function(lambda *_: 111)
            self.Process32FirstW = Function(self.first)
            self.Process32NextW = Function(self.next)
            self.CloseHandle = Function(lambda handle: (closed.append(handle), 1)[1])
        def first(self, handle, pointer):
            if not rows:
                ctypes.set_last_error(18)
                return 0
            return self.fill(pointer)
        def fill(self, pointer):
            entry = ctypes.cast(pointer, ctypes.POINTER(admin._ProcessEntry)).contents
            entry.pid, entry.parent, entry.exe = rows[self.index], 44, 'synthetic.exe'
            return 1
        def next(self, handle, pointer):
            self.index += 1
            if self.index >= len(rows):
                ctypes.set_last_error(5 if case == 'iteration_error' else 18)
                return 0
            return self.fill(pointer)
    kernel = Kernel()
    if case == 'complete':
        assert tuple(row[0] for row in admin._process_snapshot(kernel)) == rows
    else:
        with pytest.raises(ValueError):
            admin._process_snapshot(kernel)
    assert closed == [111]


def test_observer_refuses_inaccessible_process_image(monkeypatch):
    monkeypatch.setattr(admin, '_windows_kernel_boot_ns', lambda: 123)
    monkeypatch.setattr(admin, '_windows_process_images', lambda: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(admin.ReservationAdminRefused, match='^host_observation_unavailable$'):
        admin._observe_windows()


@pytest.mark.parametrize('second_has_observer', [True, False])
def test_synthetic_vanished_pid_requires_second_snapshot_before_skip(monkeypatch, second_has_observer):
    """A vanished PID is safe to skip only after a complete second snapshot."""
    own = 91919
    monkeypatch.setattr(admin.os, 'getpid', lambda: own)
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    class Kernel:
        def __init__(self):
            self.snapshots = 0
            self.index = 0
            self.closed = []
            self.CreateToolhelp32Snapshot = Function(self.snapshot)
            self.Process32FirstW = Function(self.first)
            self.Process32NextW = Function(self.next)
            self.OpenProcess = Function(self.open)
            self.QueryFullProcessImageNameW = Function(lambda *a: 0)
            self.GetExitCodeProcess = Function(lambda *a: 0)
            self.CloseHandle = Function(lambda handle: (self.closed.append(handle), 1)[1])
        def snapshot(self, *args):
            self.snapshots += 1
            self.index = 0
            return self.snapshots
        def first(self, handle, pointer):
            return self.fill(handle, pointer)
        def fill(self, handle, pointer):
            rows = (12345, own) if handle == 1 else ((own,) if second_has_observer else ())
            if self.index < len(rows):
                entry = ctypes.cast(pointer, ctypes.POINTER(admin._ProcessEntry)).contents
                entry.pid, entry.parent, entry.exe = rows[self.index], 44, 'synthetic.exe'
                return 1
            ctypes.set_last_error(18)
            return 0
        def next(self, handle, pointer):
            self.index += 1
            return self.fill(handle, pointer)
        def open(self, access, inherit, pid):
            if pid == own:
                return 777
            ctypes.set_last_error(87)
            return 0
    kernel = Kernel()
    monkeypatch.setattr(admin.ctypes, 'WinDLL', lambda *a, **k: kernel)
    monkeypatch.setattr(admin, '_image_or_proven_exited', lambda *_: 'C:\\synthetic\\observer.exe')
    if second_has_observer:
        assert admin._windows_process_images() == ((own, 'C:\\synthetic\\observer.exe'),)
        assert kernel.closed == [1, 2, 777]
    else:
        with pytest.raises(ValueError, match='process_snapshot_incomplete'):
            admin._windows_process_images()
        assert kernel.closed == [1, 2]
    assert kernel.snapshots == 2


@pytest.mark.parametrize('error', [31, 5, 87])
def test_image_exit_proof_is_limited_to_image_error_31_or_5(error):
    class Kernel:
        @staticmethod
        def QueryFullProcessImageNameW(*args):
            ctypes.set_last_error(error)
            return False
        @staticmethod
        def GetExitCodeProcess(handle, output):
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
            return True
    if error in (31, 5):
        assert admin._image_or_proven_exited(Kernel(), 1) is None
    else:
        with pytest.raises(ValueError, match='image_access'):
            admin._image_or_proven_exited(Kernel(), 1)


@pytest.mark.parametrize('pid,parent,name,peb,protection,native_pid,native_parent,query_ok,expected', [
    (4, 0, 'System', None, 0x72, 4, 0, True, True),
    (20, 4, 'Registry', None, 0x72, 20, 4, True, True),
    (21, 4, 'Memory Compression', None, 0x72, 21, 4, True, True),
    (0, 0, 'Idle', None, 0x72, 0, 0, True, False),
    (20, 5, 'Registry', None, 0x72, 20, 5, True, False),
    (20, 4, 'other.exe', None, 0x72, 20, 4, True, False),
    (20, 4, 'Registry', 1, 0x72, 20, 4, True, False),
    (20, 4, 'Registry', None, 0x71, 20, 4, True, False),
    (20, 4, 'Registry', None, 0x72, 22, 4, True, False),
    (20, 4, 'Registry', None, 0x72, 20, 5, True, False),
    (20, 4, 'Registry', None, 0x72, 20, 4, False, False),
])
def test_synthetic_kernel_identity_needs_all_same_handle_facts(
        monkeypatch, pid, parent, name, peb, protection, native_pid, native_parent, query_ok, expected):
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    handles = []
    def query(handle, kind, output, size, returned):
        handles.append(handle)
        if not query_ok: return -1
        ctypes.cast(returned, ctypes.POINTER(wintypes.ULONG)).contents.value = size
        if kind == 0:
            basic = ctypes.cast(output, ctypes.POINTER(admin._ProcessBasicInfo)).contents
            basic.peb, basic.pid, basic.parent = peb, native_pid, native_parent
        elif kind == 61:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_ubyte)).contents.value = protection
        else: raise AssertionError('unexpected query')
        return 0
    ntdll = type('FakeNtdll', (), {})()
    ntdll.NtQueryInformationProcess = Function(query)
    monkeypatch.setattr(admin.ctypes, 'WinDLL', lambda *a, **kw: ntdll)
    assert admin._verified_kernel_process(999, pid, parent, name) is expected
    assert all(handle == 999 for handle in handles)


@pytest.mark.parametrize('pid', [4, 123])
@pytest.mark.parametrize('error,second_has_pid,expected', [(87,False,True), (87,True,False),
                                                           (5,False,False), (31,False,False)])
def test_synthetic_open_failure_only_error_87_with_second_absence_is_skipped(
        monkeypatch, error, second_has_pid, expected, pid):
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    class Kernel:
        OpenProcess = Function(lambda *a: (ctypes.set_last_error(error), 0)[1])
        QueryFullProcessImageNameW = Function(lambda *a: 0)
        GetExitCodeProcess = Function(lambda *a: 0)
    snapshots = []
    def snapshot(kernel):
        snapshots.append(1)
        return ((pid, 4, 'Registry'),) if len(snapshots) == 1 or second_has_pid else ()
    monkeypatch.setattr(admin.ctypes, 'WinDLL', lambda *a, **kw: Kernel())
    monkeypatch.setattr(admin, '_process_snapshot', snapshot)
    if expected:
        assert admin._windows_process_images() == ()
        assert len(snapshots) == 2
    else:
        with pytest.raises(ValueError, match='process_access'):
            admin._windows_process_images()
        assert len(snapshots) == (2 if error == 87 else 1)


@pytest.mark.parametrize('error,allowed', [(87, True), (5, False), (31, False)])
def test_synthetic_idle_pid_zero_skips_only_documented_open_error(monkeypatch, error, allowed):
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    class Kernel:
        OpenProcess = Function(lambda *a: (ctypes.set_last_error(error), 0)[1])
        QueryFullProcessImageNameW = Function(lambda *a: 0)
        GetExitCodeProcess = Function(lambda *a: 0)
    monkeypatch.setattr(admin.ctypes, 'WinDLL', lambda *a, **kw: Kernel())
    snapshots = []
    monkeypatch.setattr(admin, '_process_snapshot', lambda kernel: (snapshots.append(1), ((0, 0, 'Idle'),))[1])
    if allowed:
        assert admin._windows_process_images() == ()
    else:
        with pytest.raises(ValueError, match='process_access'):
            admin._windows_process_images()
    assert len(snapshots) == 1


@pytest.mark.parametrize('verify', [True, False])
def test_synthetic_image_failure_uses_same_open_handle_for_kernel_facts(monkeypatch, verify):
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    observed = []
    def image(handle, *args):
        observed.append(('image', handle))
        ctypes.set_last_error(5)
        return 0
    def exit_status(handle, output):
        observed.append(('exit', handle))
        ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = 259
        return 1
    class Kernel:
        OpenProcess = Function(lambda *args: 999)
        QueryFullProcessImageNameW = Function(image)
        GetExitCodeProcess = Function(exit_status)
        CloseHandle = Function(lambda handle: (observed.append(('close', handle)), 1)[1])
    def verify_kernel(handle, pid, parent, name):
        observed.append(('facts', handle))
        return verify
    monkeypatch.setattr(admin.ctypes, 'WinDLL', lambda *a, **kw: Kernel())
    monkeypatch.setattr(admin, '_process_snapshot', lambda kernel: ((20, 4, 'Registry'),))
    monkeypatch.setattr(admin, '_verified_kernel_process', verify_kernel)
    if verify: assert admin._windows_process_images() == ()
    else:
        with pytest.raises(ValueError, match='image_access'): admin._windows_process_images()
    assert observed == [('image', 999), ('exit', 999), ('facts', 999), ('close', 999)]


@pytest.mark.parametrize('result,code', [(False, 0), (True, 259)])
def test_unknown_or_active_process_with_unreadable_image_refuses(result, code):
    class Kernel:
        @staticmethod
        def QueryFullProcessImageNameW(*args):
            ctypes.set_last_error(5)
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
            ctypes.set_last_error(5)
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


def test_direct_production_candidate_refuses_before_open_or_binding(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('must not inspect or mutate live state')
    monkeypatch.setattr(admin, '_current_binding', unexpected)
    monkeypatch.setattr(admin, '_pin_directory_chain', unexpected)
    monkeypatch.setattr(admin.os, 'open', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._production_marker(operation='create', bundle_root='untrusted', manifest_sha256='a' * 64)
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._production_marker(operation='reset', bundle_root='untrusted', manifest_sha256='a' * 64)


def test_production_scope_refuses_direct_publish_and_release_before_open(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('must not access marker')
    monkeypatch.setattr(admin, '_final_handle_path', unexpected)
    monkeypatch.setattr(admin.os, 'write', unexpected)
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._atomic_reserved(admin._FIXED_PRODUCTION_ROOT / child._OWNER_RESERVATION_NAME, None,
                               _scope=admin._PRODUCTION_SCOPE, verify_binding=unexpected)
    guard = type('Guard', (), {'root': admin._FIXED_PRODUCTION_ROOT})()
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._release_verified(guard, unexpected, _scope=admin._PRODUCTION_SCOPE)


def test_synthetic_directory_handle_must_match_named_root(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    with admin._pin_directory_chain(root) as correct, admin._pin_directory_chain(other) as wrong:
        admin._assert_open_directory_identity(root, correct)
        with pytest.raises(admin.ReservationAdminRefused, match='^DIRECTORY_IDENTITY_UNKNOWN$'):
            admin._assert_open_directory_identity(root, wrong)
    assert not marker.exists()


def test_release_rejects_forged_or_nonmarker_synthetic_guard(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    foreign = root / 'other-file'
    foreign.write_bytes(child._OWNER_RESERVATION_MAGIC + b'R')
    with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
        admin._release_verified(type('Guard', (), {'root': root})(), lambda: None)
    fd = os.open(foreign, os.O_RDWR | getattr(os, 'O_BINARY', 0))
    root_stat, file_stat = os.lstat(root), os.fstat(fd)
    guard = child._LocalOwnerReservation(root, foreign, fd,
        (root_stat.st_dev, root_stat.st_ino), (file_stat.st_dev, file_stat.st_ino))
    try:
        with pytest.raises(admin.ReservationAdminRefused, match='^PRODUCTION_ROUTE_UNAVAILABLE$'):
            admin._release_verified(guard, lambda: None)
    finally:
        guard.abandon()
    assert foreign.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'
    assert not marker.exists()


@pytest.mark.parametrize('fail_check', [1, 2, 3])
def test_synthetic_publish_binding_change_never_publishes(tmp_path, fail_check):
    root, runtime, marker = _paths(tmp_path)
    checks = []
    def verify():
        checks.append(len(checks) + 1)
        if len(checks) == fail_check:
            raise ValueError('synthetic binding changed')
    with admin._pin_directory_chain(root) as directory_handle:
        with pytest.raises(ValueError, match='synthetic binding changed'):
            admin._atomic_reserved(marker, directory_handle, b'C', verify_binding=verify)
    assert checks == list(range(1, fail_check + 1))
    assert not marker.exists()
    assert not list(root.glob(child._OWNER_RESERVATION_NAME + '.init-*'))


def test_synthetic_publish_checks_binding_before_create_write_and_publish(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    states = []
    def verify():
        staging = list(root.glob(child._OWNER_RESERVATION_NAME + '.init-*'))
        states.append((marker.exists(), len(staging), staging[0].stat().st_size if staging else None))
    with admin._pin_directory_chain(root) as directory_handle:
        admin._atomic_reserved(marker, directory_handle, b'C', verify_binding=verify)
    assert states == [(False, 0, None), (False, 1, 0),
                      (False, 1, len(child._OWNER_RESERVATION_MAGIC) + 1)]
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_synthetic_publish_post_rename_check_failure_reports_uncertain(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    real_lstat = os.lstat
    def fail_published(path, *args, **kwargs):
        if Path(path) == marker:
            raise OSError('private-published-check')
        return real_lstat(path, *args, **kwargs)
    with admin._pin_directory_chain(root) as directory_handle:
        with monkeypatch.context() as patch:
            patch.setattr(admin.os, 'lstat', fail_published)
            with pytest.raises(admin.ReservationAdminRefused, match='^CREATE_PUBLICATION_UNCERTAIN$') as exc:
                admin._atomic_reserved(marker, directory_handle, b'C', verify_binding=lambda: None)
    assert 'private-published-check' not in str(exc.value)
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_synthetic_publish_and_cleanup_failures_are_both_uncertain(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    real_lstat, real_close, real_create = os.lstat, os.close, admin._relative_create
    created = []
    def capture(*args, **kwargs):
        fd = real_create(*args, **kwargs)
        created.append(fd)
        return fd
    def fail_published(path, *args, **kwargs):
        if Path(path) == marker:
            raise OSError('private-published-check')
        return real_lstat(path, *args, **kwargs)
    def fail_after_close(fd):
        real_close(fd)
        if created and fd == created[0]:
            raise OSError('private-close')
    with admin._pin_directory_chain(root) as directory_handle:
        with monkeypatch.context() as patch:
            patch.setattr(admin, '_relative_create', capture)
            patch.setattr(admin.os, 'lstat', fail_published)
            patch.setattr(admin.os, 'close', fail_after_close)
            with pytest.raises(admin.ReservationAdminRefused,
                               match='^CREATE_PUBLICATION_UNCERTAIN;CLEANUP_UNCERTAIN$') as exc:
                admin._atomic_reserved(marker, directory_handle, b'C', verify_binding=lambda: None)
    assert 'private-' not in str(exc.value)
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_synthetic_release_refuses_changed_binding_before_write(tmp_path):
    root, runtime, marker = _paths(tmp_path)
    admin._marker(root, runtime, operation='create', observe=_clear())
    guard = child._reserve_local_owner(root)
    try:
        def changed():
            raise ValueError('synthetic binding changed')
        with pytest.raises(ValueError, match='synthetic binding changed'):
            admin._release_verified(guard, changed)
        assert guard._read() == child._OWNER_RESERVATION_MAGIC + b'R'
    finally:
        guard.abandon()


def test_synthetic_release_checks_binding_immediately_before_clear(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    admin._marker(root, runtime, operation='create', observe=_clear())
    guard = child._reserve_local_owner(root)
    states = []
    real_write = os.write
    def write(fd, data):
        states.append('write')
        return real_write(fd, data)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(admin.os, 'write', write)
            admin._release_verified(guard, lambda: states.append('verify'))
        assert states == ['verify', 'write']
        assert guard.fd == -1
        assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'
    finally:
        guard.abandon()


def test_synthetic_release_fsync_failure_reports_uncertain(tmp_path, monkeypatch):
    root, runtime, marker = _paths(tmp_path)
    admin._marker(root, runtime, operation='create', observe=_clear())
    guard = child._reserve_local_owner(root)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(admin.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('private')))
            with pytest.raises(admin.ReservationAdminRefused, match='^RESET_PUBLICATION_UNCERTAIN$'):
                admin._release_verified(guard, lambda: None)
        assert guard._read() == child._OWNER_RESERVATION_MAGIC + b'C'
    finally:
        guard.abandon()
