"""Synthetic and Windows named-mutex tests; no lock files are created."""

import os
import threading

import pytest

from tools import wd_mind_runtime_mutex as mutex


@pytest.fixture
def configured(tmp_path, monkeypatch):
    sentinel = tmp_path / "synthetic-sentinel.lock"
    monkeypatch.setattr(mutex, "_LOCK_SENTINEL", sentinel)
    monkeypatch.setattr(mutex, "_generation", None)
    mutex.configure_generation("a" * 40)
    return sentinel


class FakeKernel:
    def __init__(self, status=0): self.status, self.names, self.calls = status, [], []
    def CreateMutexW(self, attributes, owner, name): self.names.append(name); return 123
    def WaitForSingleObject(self, handle, timeout): self.calls.append(("wait", timeout)); return self.status
    def ReleaseMutex(self, handle): self.calls.append(("release", handle)); return True
    def CloseHandle(self, handle): self.calls.append(("close", handle)); return True


def test_same_generation_uses_fixed_global_mutex_without_creating_file(configured, monkeypatch):
    api = FakeKernel()
    monkeypatch.setattr(mutex, "_kernel", lambda: api)
    for _ in range(2):
        with mutex.reader_slot(configured, timeout_seconds=1.001):
            assert not configured.exists()
    assert api.names == ["Global\\WdMindQ4Reader-" + "a" * 40] * 2
    assert api.calls.count(("wait", 1001)) == 2 and not configured.exists()


@pytest.mark.parametrize("value", ["", "a" * 39, "A" * 40, "a" * 41, "bad/path"])
def test_bad_generation_refuses(value, monkeypatch):
    monkeypatch.setattr(mutex, "_generation", None)
    with pytest.raises(ValueError, match="mutex_generation_invalid"):
        mutex.configure_generation(value)


def test_generation_cannot_change_in_one_child(configured):
    mutex.configure_generation("a" * 40)
    with pytest.raises(ValueError, match="mutex_generation_changed"):
        mutex.configure_generation("b" * 40)


@pytest.mark.parametrize("value", [-1, 30.01, float("inf"), float("nan"), True, "1"])
def test_invalid_timeout_never_creates_mutex(configured, monkeypatch, value):
    monkeypatch.setattr(mutex, "_kernel", lambda: pytest.fail("mutex opened"))
    with pytest.raises(ValueError):
        with mutex.reader_slot(configured, timeout_seconds=value): pass


def test_other_path_and_traversal_are_inert(configured, monkeypatch):
    monkeypatch.setattr(mutex, "_kernel", lambda: pytest.fail("mutex opened"))
    for value in (configured.parent / "other.lock", configured.parent / "x" / ".." / configured.name):
        with pytest.raises(ValueError, match="mutex_sentinel_invalid"):
            with mutex.reader_slot(value, timeout_seconds=0): pass
    assert not configured.exists()


@pytest.mark.parametrize("status,error", [(0x102, mutex.ReaderBusy), (0x80, OSError), (0xFFFFFFFF, OSError)])
def test_timeout_abandoned_and_wait_errors_refuse_and_close(configured, monkeypatch, status, error):
    api = FakeKernel(status)
    monkeypatch.setattr(mutex, "_kernel", lambda: api)
    with pytest.raises(error):
        with mutex.reader_slot(configured, timeout_seconds=0): pass
    assert ("close", 123) in api.calls
    assert (("release", 123) in api.calls) is (status == 0x80)


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex required")
def test_real_windows_mutex_blocks_second_thread_without_file(configured, monkeypatch):
    # Use a unique generation to avoid collisions with real fleet work.
    import uuid
    monkeypatch.setattr(mutex, "_generation", None)
    mutex.configure_generation(uuid.uuid4().hex + "0" * 8)
    entered, release = threading.Event(), threading.Event()
    failures = []
    def hold():
        try:
            with mutex.reader_slot(configured, timeout_seconds=1):
                entered.set()
                assert release.wait(5)
        except Exception as exc:
            failures.append(type(exc).__name__)
            entered.set()
    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert entered.wait(5) and not failures
        with pytest.raises(mutex.ReaderBusy):
            with mutex.reader_slot(configured, timeout_seconds=0.05): pass
        assert not configured.exists()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not failures
