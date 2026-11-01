"""Windows-only synthetic file-handle checks; no production or model inputs."""
import ctypes
from ctypes import wintypes
import hashlib
import os
import threading

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")


@pytest.mark.parametrize("drive_type,target", [
    (4, r"\Device\Mup\server\share"),
    (3, r"\??\C:\mapped"),
    (3, ""),
])
def test_nonlocal_or_subst_mapping_refuses(drive_type, target):
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def GetDriveTypeW(self, root):
            assert root == "C:\\"
            return drive_type
        def QueryDosDeviceW(self, name, buffer, size):
            assert name == "C:"
            buffer.value = target
            return len(target) + 1 if target else 0
    with pytest.raises(parent.AttestationRefused):
        parent._verify_fixed_drive(FakeKernel(), r"C:\synthetic\file.bin")


def test_matching_fixed_drive_and_final_dos_path():
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def GetDriveTypeW(self, root):
            return 3
        def QueryDosDeviceW(self, name, buffer, size):
            buffer.value = r"\Device\HarddiskVolume8"
            return len(buffer.value) + 1
        def GetFinalPathNameByHandleW(self, handle, buffer, size, flags):
            assert handle == 123 and flags == 0
            buffer.value = r"\\?\C:\synthetic\file.bin"
            return len(buffer.value)
    k = FakeKernel()
    parent._verify_fixed_drive(k, r"C:\synthetic\file.bin")
    parent._verify_final_path(k, 123, r"C:\synthetic\file.bin")


@pytest.mark.parametrize("value", ["", r"\\?\D:\synthetic\file.bin", r"\\?\C:\synthetic\other.bin"])
def test_mismatched_or_unavailable_final_path_refuses(value):
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def GetFinalPathNameByHandleW(self, handle, buffer, size, flags):
            buffer.value = value
            return len(value)
    with pytest.raises(parent.AttestationRefused):
        parent._verify_final_path(FakeKernel(), 123, r"C:\synthetic\file.bin")


def test_final_path_refusal_closes_handle_before_hash(monkeypatch, tmp_path):
    from tools import wd_q4_fixed_parent_attestation as parent
    closes = []
    class FakeKernel:
        def CreateFileW(self, *args):
            return 31415
        def CloseHandle(self, handle):
            closes.append(handle)
            return True
        def GetFileType(self, handle):
            pytest.fail("hash/inspection must not start")
    monkeypatch.setattr(parent, "_api", FakeKernel)
    monkeypatch.setattr(parent, "_verify_fixed_drive", lambda *args: None)
    def bad_final(*args):
        raise parent.AttestationRefused("synthetic")
    monkeypatch.setattr(parent, "_verify_final_path", bad_final)
    with pytest.raises(parent.AttestationRefused):
        parent.attest_file(str(tmp_path / "file.bin"), "a" * 64, max_bytes=4, enabled=True)
    assert closes == [31415]


def test_hardlink_writer_blocked_until_lease_closed(tmp_path):
    from tools.wd_q4_fixed_parent_attestation import attest_file
    path, alias = tmp_path / "synthetic.bin", tmp_path / "synthetic-hardlink.bin"
    path.write_bytes(b"synthetic")
    os.link(path, alias)
    held = attest_file(str(path), hashlib.sha256(b"synthetic").hexdigest(), max_bytes=64, enabled=True)
    k, invalid = _kernel(), ctypes.c_void_p(-1).value
    try:
        writer = _open(k, alias, 0x40000000, 7)
        if writer != invalid:
            k.CloseHandle(writer)
        assert writer == invalid
    finally:
        held.close()
    writer = _open(k, alias, 0x40000000, 7)
    try:
        assert writer != invalid
    finally:
        if writer != invalid:
            k.CloseHandle(writer)


def _kernel():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.CloseHandle.restype = wintypes.BOOL
    return k


def _open(k, path, access, sharing):
    return k.CreateFileW(str(path), access, sharing, None, 3, 0, None)


def test_pinned_read_handle_blocks_write_and_delete_until_closed(tmp_path):
    from tools.wd_q4_fixed_parent_attestation import attest_file
    path = tmp_path / "synthetic.bin"
    contents = b"synthetic-attestation"
    path.write_bytes(contents)
    k = _kernel()
    invalid = ctypes.c_void_p(-1).value
    held = attest_file(str(path), hashlib.sha256(contents).hexdigest(), max_bytes=1024, enabled=True)
    try:
        assert held.sha256 == hashlib.sha256(contents).hexdigest() and held.size == len(contents)
        for access in (0x40000000, 0x00010000):  # GENERIC_WRITE, DELETE
            handle = _open(k, path, access, 7)
            if handle != invalid:
                k.CloseHandle(handle)
            assert handle == invalid
    finally:
        held.close()
    handle = _open(k, path, 0x40000000, 7)
    try:
        assert handle != invalid
    finally:
        if handle != invalid:
            k.CloseHandle(handle)


def test_already_open_writer_blocks_attestation(tmp_path):
    from tools.wd_q4_fixed_parent_attestation import attest_file, AttestationRefused
    path = tmp_path / "synthetic-writer.bin"
    path.write_bytes(b"synthetic")
    k, invalid = _kernel(), ctypes.c_void_p(-1).value
    writer = _open(k, path, 0x40000000, 7)
    assert writer != invalid
    try:
        with pytest.raises(AttestationRefused):
            attest_file(str(path), hashlib.sha256(b"synthetic").hexdigest(), max_bytes=20, enabled=True)
    finally:
        k.CloseHandle(writer)


@pytest.mark.parametrize("fault", ["digest", "limit", "disabled", "directory", "invalid_pin"])
def test_failures_close_any_acquired_handle(tmp_path, fault):
    from tools.wd_q4_fixed_parent_attestation import attest_file, AttestationRefused
    path = tmp_path / "synthetic-failure.bin"
    path.write_bytes(b"synthetic")
    digest = hashlib.sha256(b"synthetic").hexdigest()
    kwargs = {"path": str(path), "expected_sha256": digest, "max_bytes": 20, "enabled": True}
    if fault == "digest":
        kwargs["expected_sha256"] = "0" * 64
    elif fault == "limit":
        kwargs["max_bytes"] = 8
    elif fault == "disabled":
        kwargs["enabled"] = False
    elif fault == "directory":
        kwargs["path"] = str(tmp_path)
    else:
        kwargs["expected_sha256"] = "bad"
    with pytest.raises(AttestationRefused, match="^attestation_refused$") as caught:
        attest_file(**kwargs)
    assert caught.value.__context__ is None
    k, invalid = _kernel(), ctypes.c_void_p(-1).value
    writer = _open(k, path, 0x40000000, 7)
    try:
        assert writer != invalid
    finally:
        if writer != invalid:
            k.CloseHandle(writer)


def test_leaf_symlink_refuses(tmp_path):
    from tools.wd_q4_fixed_parent_attestation import attest_file, AttestationRefused
    target = tmp_path / "target.bin"
    target.write_bytes(b"synthetic")
    link = tmp_path / "link.bin"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation not available")
    with pytest.raises(AttestationRefused):
        attest_file(str(link), hashlib.sha256(b"synthetic").hexdigest(), max_bytes=20, enabled=True)


def test_context_manager_releases_handle(tmp_path):
    from tools.wd_q4_fixed_parent_attestation import attest_file
    path = tmp_path / "synthetic-context.bin"
    path.write_bytes(b"x")
    with attest_file(str(path), hashlib.sha256(b"x").hexdigest(), max_bytes=1, enabled=True) as held:
        assert held.size == 1
    held.close()  # idempotent


def test_cleanup_failure_quarantines_handle_without_blind_retry(monkeypatch, tmp_path):
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def __init__(self):
            self.closes = []
        def CreateFileW(self, *args):
            return 31415
        def GetFileType(self, handle):
            return 0  # force refusal after acquiring the handle
        def CloseHandle(self, handle):
            self.closes.append(handle)
            return len(self.closes) > 1
    fake = FakeKernel()
    monkeypatch.setattr(parent, "_api", lambda: fake)
    monkeypatch.setattr(parent, "_verify_fixed_drive", lambda *args: None)
    monkeypatch.setattr(parent, "_verify_final_path", lambda *args: None)
    quarantine = []
    monkeypatch.setattr(parent, "_QUARANTINED", quarantine)
    with pytest.raises(parent.AttestationRefused) as caught:
        parent.attest_file(str(tmp_path / "fake.bin"), "a" * 64, max_bytes=10, enabled=True)
    assert type(caught.value).__name__ == "AttestationCleanupUnknown"
    assert str(caught.value) == "attestation_cleanup_unknown"
    assert caught.value.__context__ is None
    lease = caught.value.lease
    assert fake.closes == [31415]
    assert quarantine == [lease]
    with pytest.raises(parent.AttestationCleanupUnknown):
        lease.close()  # Never blindly retry an uncertain, possibly reused handle value.
    with pytest.raises(parent.AttestationCleanupUnknown):
        lease.__enter__()
    assert fake.closes == [31415]
    with pytest.raises(parent.AttestationRefused):
        parent.attest_file(str(tmp_path / "fake.bin"), "a" * 64, max_bytes=10, enabled=True)
    assert fake.closes == [31415]


@pytest.mark.parametrize("failure", ["false", "raises"])
def test_explicit_close_failure_quarantines_once(monkeypatch, failure):
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def __init__(self):
            self.calls = 0
        def CloseHandle(self, handle):
            self.calls += 1
            if failure == "raises":
                raise OSError("synthetic")
            return False
    fake = FakeKernel()
    quarantine = []
    monkeypatch.setattr(parent, "_QUARANTINED", quarantine)
    lease = parent.HeldFile(fake, 123, "a" * 64, 1)
    with pytest.raises(parent.AttestationCleanupUnknown) as caught:
        lease.close()
    assert caught.value.lease is lease and quarantine == [lease] and fake.calls == 1
    with pytest.raises(parent.AttestationCleanupUnknown):
        lease.close()
    assert fake.calls == 1 and quarantine == [lease]


def test_concurrent_close_never_closes_a_handle_twice():
    from tools import wd_q4_fixed_parent_attestation as parent
    first_entered, second_started, second_entered = (threading.Event() for _ in range(3))
    class FakeKernel:
        def __init__(self):
            self.calls = 0
        def CloseHandle(self, handle):
            self.calls += 1
            if self.calls == 1:
                first_entered.set()
                assert second_started.wait(2)
                second_entered.wait(0.5)
            else:
                second_entered.set()
            return True
    fake = FakeKernel()
    lease = parent.HeldFile(fake, 123, "a" * 64, 1)
    errors = []
    def close(second=False):
        if second:
            second_started.set()
        try:
            lease.close()
        except Exception as exc:
            errors.append(exc)
    t1 = threading.Thread(target=close)
    t2 = threading.Thread(target=close, kwargs={"second": True})
    t1.start()
    assert first_entered.wait(2)
    t2.start()
    t1.join(3)
    t2.join(3)
    assert not t1.is_alive() and not t2.is_alive()
    assert errors == [] and fake.calls == 1


def test_quarantine_and_next_admission_are_atomic(monkeypatch, tmp_path):
    from tools import wd_q4_fixed_parent_attestation as parent
    first_entered, second_started, second_entered = (threading.Event() for _ in range(3))
    quarantine, calls, errors = [], [], []
    monkeypatch.setattr(parent, "_QUARANTINED", quarantine)
    def open_and_hash(*_):
        calls.append(1)
        if len(calls) == 1:
            first_entered.set()
            assert second_started.wait(2)
            second_entered.wait(0.5)
            lease = object()
            quarantine.append(lease)
            raise parent.AttestationCleanupUnknown(lease)
        second_entered.set()
        return object()
    monkeypatch.setattr(parent, "_open_and_hash", open_and_hash)
    def attest(second=False):
        if second:
            second_started.set()
        try:
            parent.attest_file(str(tmp_path / "fake"), "a" * 64, max_bytes=4, enabled=True)
        except parent.AttestationRefused as exc:
            errors.append(exc)
    t1 = threading.Thread(target=attest)
    t2 = threading.Thread(target=attest, kwargs={"second": True})
    t1.start()
    assert first_entered.wait(2)
    t2.start()
    t1.join(3)
    t2.join(3)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(calls) == 1 and len(errors) == 2


def test_failed_automatic_close_exception_keeps_handle_quarantined(monkeypatch, tmp_path):
    from tools import wd_q4_fixed_parent_attestation as parent
    class FakeKernel:
        def CreateFileW(self, *args):
            return 31415
        def GetFileType(self, handle):
            return 0
        def CloseHandle(self, handle):
            raise OSError("synthetic")
    quarantine = []
    monkeypatch.setattr(parent, "_api", FakeKernel)
    monkeypatch.setattr(parent, "_verify_fixed_drive", lambda *args: None)
    monkeypatch.setattr(parent, "_verify_final_path", lambda *args: None)
    monkeypatch.setattr(parent, "_QUARANTINED", quarantine)
    with pytest.raises(parent.AttestationCleanupUnknown) as caught:
        parent.attest_file(str(tmp_path / "fake"), "a" * 64, max_bytes=4, enabled=True)
    assert len(quarantine) == 1 and quarantine[0] is caught.value.lease
    assert caught.value.__context__ is None
