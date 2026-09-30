# SPDX-License-Identifier: BUSL-1.1
"""F8 Windows queue ports: the runtime-root named mutex (RCO1, Lead request d79f933d).

A fake kernel32 pins every wait outcome and cleanup path on any platform. The real-mutex tests run only on
Windows, each with names derived from its own tmp_path runtime root (never a production name).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
import threading

import pytest

from tools import bridge_v2_queue_ports_windows as ports
from tools import bridge_v2_work_queue as wq
from tools.bridge_v2_queue_ports_windows import MutexAbandoned, NamedMutexPort, windows_queue_transactions
from tools.bridge_v2_queue_transactions import LockTimeout, Plan, QueueTransactionError, QueueTransactions, mutex_name

WINDOWS = os.name == "nt"
NOW = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)
HANDLE = 77


class FakeKernel32:
    def __init__(self, wait=ports.WAIT_OBJECT_0, release=True, close=True):
        self.calls, self.wait, self.release, self.close = [], wait, release, close

    def WaitForSingleObject(self, handle, milliseconds):
        self.calls.append(("wait", handle, milliseconds))
        return self.wait

    def ReleaseMutex(self, handle):
        self.calls.append(("release", handle))
        return self.release

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return self.close


def fake_port(kernel32, created=None, handle=HANDLE):
    created = [] if created is None else created

    def create(name, k32):
        assert k32 is kernel32
        created.append(name)
        return handle

    return NamedMutexPort(kernel32=kernel32, create=create, last_error=lambda: 5), created


def test_a_held_mutex_is_released_then_closed_exactly_once(tmp_path):
    kernel32 = FakeKernel32()
    mutex, created = fake_port(kernel32)
    with mutex.hold(mutex_name(tmp_path), 0.25):
        assert kernel32.calls == [("wait", HANDLE, 250)]
    assert kernel32.calls == [("wait", HANDLE, 250), ("release", HANDLE), ("close", HANDLE)]
    assert created == [mutex_name(tmp_path)]


@pytest.mark.parametrize("name", [
    "Global\\WaggleDanceBridgeV2Queue-" + "A" * 32,        # upper-case hex is never a mutex_name value
    "Global\\WaggleDanceBridgeV2Queue-" + "a" * 31,
    "Global\\WaggleDanceBridgeV2Queue-" + "a" * 33,
    "Global\\WaggleDanceGrokAdmission-" + "a" * 32,        # another subsystem's mutex
    "Local\\WaggleDanceBridgeV2Queue-" + "a" * 32,
    b"Global\\WaggleDanceBridgeV2Queue-" + b"a" * 32,
    None,
])
def test_only_a_mutex_name_value_is_accepted_before_anything_is_created(name):
    kernel32 = FakeKernel32()
    mutex, created = fake_port(kernel32)
    with pytest.raises(QueueTransactionError, match="mutex_name"):
        with mutex.hold(name, 1):
            pytest.fail("the body must not run")
    assert created == [] and kernel32.calls == []


@pytest.mark.parametrize("timeout", [0, -1, 60.5, float("nan"), True, "1", None])
def test_the_timeout_must_be_in_range_before_anything_is_created(tmp_path, timeout):
    kernel32 = FakeKernel32()
    mutex, created = fake_port(kernel32)
    with pytest.raises(QueueTransactionError, match="timeout"):
        with mutex.hold(mutex_name(tmp_path), timeout):
            pytest.fail("the body must not run")
    assert created == [] and kernel32.calls == []
    with mutex.hold(mutex_name(tmp_path), 60):                              # success twin: the upper bound
        pass


def test_a_busy_mutex_is_a_lock_timeout_that_closes_and_never_releases(tmp_path):
    kernel32 = FakeKernel32(wait=ports.WAIT_TIMEOUT)
    mutex, _ = fake_port(kernel32)
    with pytest.raises(LockTimeout, match="busy"):
        with mutex.hold(mutex_name(tmp_path), 0.1):
            pytest.fail("the body must not run")
    assert kernel32.calls == [("wait", HANDLE, 100), ("close", HANDLE)]


def test_an_abandoned_mutex_is_released_closed_and_refused_without_running_the_body(tmp_path):
    kernel32 = FakeKernel32(wait=ports.WAIT_ABANDONED)
    mutex, _ = fake_port(kernel32)
    with pytest.raises(MutexAbandoned, match="abandoned") as refused:
        with mutex.hold(mutex_name(tmp_path), 1):
            pytest.fail("the body must not run")
    assert isinstance(refused.value, QueueTransactionError)
    assert kernel32.calls == [("wait", HANDLE, 1000), ("release", HANDLE), ("close", HANDLE)]


@pytest.mark.parametrize("wait,fragment", [(ports.WAIT_FAILED, r"wait failed \(5\)"), (0x42, "0x00000042")],
                         ids=["wait_failed", "unknown_value"])
def test_a_failed_or_unknown_wait_is_refused_and_closes_without_releasing(tmp_path, wait, fragment):
    kernel32 = FakeKernel32(wait=wait)
    mutex, _ = fake_port(kernel32)
    with pytest.raises(QueueTransactionError, match=fragment):
        with mutex.hold(mutex_name(tmp_path), 1):
            pytest.fail("the body must not run")
    assert kernel32.calls == [("wait", HANDLE, 1000), ("close", HANDLE)]


def test_a_close_failure_after_a_refused_wait_is_recorded_on_the_refusal(tmp_path):
    kernel32 = FakeKernel32(wait=ports.WAIT_TIMEOUT, close=False)
    mutex, _ = fake_port(kernel32)
    with pytest.raises(LockTimeout) as refused:
        with mutex.hold(mutex_name(tmp_path), 1):
            pass
    assert [step for step, _, _ in refused.value.descriptor_close_unknown] == ["mutex_close"]


@pytest.mark.parametrize("failure", [OSError(5, "CreateMutexExW failed"), None], ids=["oserror", "no_handle"])
def test_a_creation_failure_is_refused_before_any_wait(tmp_path, failure):
    kernel32 = FakeKernel32()

    def create(name, k32):
        if failure is not None:
            raise failure
        return 0

    mutex = NamedMutexPort(kernel32=kernel32, create=create, last_error=lambda: 0)
    with pytest.raises(QueueTransactionError, match="could not be created"):
        with mutex.hold(mutex_name(tmp_path), 1):
            pytest.fail("the body must not run")
    assert kernel32.calls == []


def test_a_body_error_propagates_unchanged_after_release_and_close(tmp_path):
    kernel32 = FakeKernel32()
    mutex, _ = fake_port(kernel32)
    boom = RuntimeError("body")
    with pytest.raises(RuntimeError) as raised:
        with mutex.hold(mutex_name(tmp_path), 1):
            raise boom
    assert raised.value is boom and not hasattr(boom, "descriptor_close_unknown")
    assert kernel32.calls[1:] == [("release", HANDLE), ("close", HANDLE)]


def test_cleanup_failures_during_a_body_error_are_recorded_on_it_and_the_handle_still_closes(tmp_path):
    kernel32 = FakeKernel32(release=False, close=False)
    mutex, _ = fake_port(kernel32)
    boom = RuntimeError("body")
    with pytest.raises(RuntimeError) as raised:
        with mutex.hold(mutex_name(tmp_path), 1):
            raise boom
    assert raised.value is boom
    assert [step for step, _, _ in boom.descriptor_close_unknown] == ["mutex_release", "mutex_close"]
    assert kernel32.calls[1:] == [("release", HANDLE), ("close", HANDLE)]


@pytest.mark.parametrize("release,close,fragment", [(False, True, "release failed"), (True, False, "close failed")],
                         ids=["release", "close"])
def test_a_cleanup_failure_without_a_body_error_is_visible_and_the_handle_still_closes(tmp_path, release, close,
                                                                                      fragment):
    kernel32 = FakeKernel32(release=release, close=close)
    mutex, _ = fake_port(kernel32)
    with pytest.raises(OSError, match=fragment):
        with mutex.hold(mutex_name(tmp_path), 1):
            pass
    assert kernel32.calls[1:] == [("release", HANDLE), ("close", HANDLE)]


def test_the_transactions_wait_on_their_own_root_mutex_before_the_claim_lock(tmp_path):
    kernel32 = FakeKernel32()
    mutex, created = fake_port(kernel32)
    seen = []

    class ClaimLock:
        @contextmanager
        def hold(self, lock_path, timeout_seconds):
            seen.append([call[0] for call in kernel32.calls])
            yield

    txns = QueueTransactions(tmp_path, mutex=mutex, claim_lock=ClaimLock(), clock=lambda: NOW)
    path = tmp_path / "work_queue" / "claims" / "t.json"
    path.parent.mkdir(parents=True)
    txns.transact("claim", path, "claim:t", lambda before: Plan(after={"agent": "a", "task_id": "t"},
                                                               expect_absent=True, result=True))
    assert created == [mutex_name(tmp_path)] and seen == [["wait"]]       # the mutex first, then the claim lock
    assert [call[0] for call in kernel32.calls] == ["wait", "release", "close"] and path.exists()


@pytest.mark.skipif(WINDOWS, reason="pins the refusal where Windows is absent")
def test_without_windows_the_default_port_refuses_and_the_queue_changes_nothing(tmp_path):
    txns = windows_queue_transactions(tmp_path)
    path = tmp_path / "work_queue" / "claims" / "t.json"
    path.parent.mkdir(parents=True)
    with pytest.raises(QueueTransactionError, match="needs Windows"):
        txns.transact("claim", path, "claim:t", lambda before: Plan(after={"agent": "a"}, expect_absent=True))
    assert not path.exists()


# -- real Windows named mutexes (per-test roots) ------------------------------------------------------------

def _contend(name, timeout_seconds, results):
    try:
        with NamedMutexPort().hold(name, timeout_seconds):
            results.append("acquired")
    except LockTimeout:
        results.append("timeout")


@pytest.mark.skipif(not WINDOWS, reason="real named mutexes are Windows-only")
def test_real_mutex_excludes_another_thread_until_it_is_released(tmp_path):
    name, results = mutex_name(tmp_path), []
    with NamedMutexPort().hold(name, 1):
        contender = threading.Thread(target=_contend, args=(name, 0.2, results))
        contender.start()
        contender.join(10)
    assert results == ["timeout"]
    contender = threading.Thread(target=_contend, args=(name, 0.2, results))   # success twin after release
    contender.start()
    contender.join(10)
    assert results == ["timeout", "acquired"]


@pytest.mark.skipif(not WINDOWS, reason="real named mutexes are Windows-only")
def test_real_mutexes_of_two_runtime_roots_are_independent(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert mutex_name(first) != mutex_name(second)
    results = []
    with NamedMutexPort().hold(mutex_name(first), 1):
        contender = threading.Thread(target=_contend, args=(mutex_name(second), 0.2, results))
        contender.start()
        contender.join(10)
    assert results == ["acquired"]


@pytest.mark.skipif(not WINDOWS, reason="real named mutexes are Windows-only")
def test_real_abandoned_mutex_is_refused_once_then_acquired(tmp_path):
    name, handles = mutex_name(tmp_path), []

    def abandon():
        kernel32 = ports._kernel32()
        handle = ports._create(name, kernel32)
        handles.append(handle)
        assert kernel32.WaitForSingleObject(handle, 1000) == ports.WAIT_OBJECT_0   # the thread ends holding it

    holder = threading.Thread(target=abandon)
    holder.start()
    holder.join(10)
    try:
        with pytest.raises(MutexAbandoned):
            with NamedMutexPort().hold(name, 1):
                pytest.fail("the body must not run")
        with NamedMutexPort().hold(name, 1):                                 # success twin: normal again
            pass
    finally:
        for handle in handles:
            ports._kernel32().CloseHandle(handle)


@pytest.mark.skipif(not WINDOWS, reason="real named mutexes are Windows-only")
def test_real_ports_run_a_claim_heartbeat_and_release_on_an_isolated_root(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / "wt" / "tools").mkdir(parents=True)
    txns = windows_queue_transactions(runtime, clock=lambda: NOW)
    owner = wq.OwnerIdentity("session-a", "token-a")
    wq.claim_task(txns, agent="claude-rco-1", task_id="team/task-1", summary="work", mode="write",
                  write_scope=("tools/a.py",), identity=owner, cwd=str(tmp_path / "wt"), now=NOW)
    wq.heartbeat(txns, agent="claude-rco-1", task_id="team/task-1", identity=owner, now=NOW)
    record = wq.release_task(txns, agent="claude-rco-1", task_id="team/task-1", identity=owner, now=NOW)
    assert record["release_status"] == "done" and wq.find_claim(txns, "team/task-1") is None
    outboxed = sorted(p.name for p in (runtime / "work_queue" / "v2" / "outbox").glob("*.json"))
    assert len(outboxed) == 2                                                # the claim and the release events
