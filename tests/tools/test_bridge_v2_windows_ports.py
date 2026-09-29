# SPDX-License-Identifier: BUSL-1.1
"""Windows runtime-root mutex port fixtures (authored per operator directive; NOT executed yet).

Deterministic: kernel32 and the creation policy are stubs, so no live mutex is created,
waited on or probed, and every WaitForSingleObject outcome is scripted.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from tools import bridge_v2_windows_ports as ports
from tools.bridge_v2_queue_transactions import LockTimeout, Plan, QueueTransactionError, QueueTransactions, mutex_name
from tools.bridge_v2_windows_ports import (WAIT_ABANDONED, WAIT_FAILED, WAIT_OBJECT_0, WAIT_TIMEOUT, MutexAbandoned,
                                           MutexUnavailable, WindowsRootMutex)

ROOT = Path(__file__).resolve().parents[2]
HANDLE = 4242


class Kernel32:
    """Scripted kernel32: records every call; nothing touches the OS."""

    def __init__(self, outcome=WAIT_OBJECT_0, release_ok=True):
        self.outcome, self.release_ok, self.calls = outcome, release_ok, []

    def WaitForSingleObject(self, handle, milliseconds):
        self.calls.append(("wait", handle, milliseconds))
        return self.outcome

    def ReleaseMutex(self, handle):
        self.calls.append(("release", handle))
        return 1 if self.release_ok else 0

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1


def port(kernel32, created=None, handle=HANDLE):
    created = [] if created is None else created

    def create(name):
        created.append(name)
        if isinstance(handle, Exception):
            raise handle
        return handle
    return WindowsRootMutex(kernel32=kernel32, create=create, platform="nt"), created


def name_for(tmp_path):
    return mutex_name(tmp_path)


def names(kernel32):
    return [call[0] for call in kernel32.calls]


def test_success_runs_the_body_then_releases_and_closes(tmp_path):
    kernel32 = Kernel32()
    mutex, created = port(kernel32)
    ran = []
    with mutex.hold(name_for(tmp_path), 4.0):
        ran.append(names(kernel32))
    assert ran == [["wait"]] and created == [name_for(tmp_path)]
    assert names(kernel32) == ["wait", "release", "close"] and kernel32.calls[0][2] == 4000


def test_timeout_raises_lock_timeout_closes_and_never_releases(tmp_path):
    kernel32 = Kernel32(outcome=WAIT_TIMEOUT)
    mutex, _ = port(kernel32)
    with pytest.raises(LockTimeout):
        with mutex.hold(name_for(tmp_path), 1.0):
            pytest.fail("the body must not run")
    assert names(kernel32) == ["wait", "close"]


def test_abandoned_is_released_without_running_the_body_and_asks_for_reconciliation(tmp_path):
    kernel32 = Kernel32(outcome=WAIT_ABANDONED)
    mutex, _ = port(kernel32)
    with pytest.raises(MutexAbandoned, match="reconcile"):
        with mutex.hold(name_for(tmp_path), 1.0):
            pytest.fail("the body must not run on possibly half-written state")
    assert names(kernel32) == ["wait", "release", "close"]


@pytest.mark.parametrize("outcome", [WAIT_FAILED, 0x00000001, 0x00000081])
def test_a_failed_or_unexpected_wait_is_unavailable_and_closed(tmp_path, outcome):
    kernel32 = Kernel32(outcome=outcome)
    mutex, _ = port(kernel32)
    with pytest.raises(MutexUnavailable):
        with mutex.hold(name_for(tmp_path), 1.0):
            pytest.fail("the body must not run")
    assert names(kernel32) == ["wait", "close"]


@pytest.mark.parametrize("handle", [OSError(5, "access denied"), ValueError("no name"), 0, None])
def test_a_refused_create_locks_nothing_and_waits_on_nothing(tmp_path, handle):
    kernel32 = Kernel32()
    mutex, _ = port(kernel32, handle=handle)
    with pytest.raises(MutexUnavailable):
        with mutex.hold(name_for(tmp_path), 1.0):
            pytest.fail("the body must not run")
    assert kernel32.calls == []


def test_a_body_exception_still_releases_and_closes_and_is_never_masked(tmp_path):
    kernel32 = Kernel32(release_ok=False)
    mutex, _ = port(kernel32)
    with pytest.raises(KeyError):
        with mutex.hold(name_for(tmp_path), 1.0):
            raise KeyError("body")
    assert names(kernel32) == ["wait", "release", "close"]


def test_a_failed_release_after_a_clean_body_is_reported(tmp_path):
    kernel32 = Kernel32(release_ok=False)
    mutex, _ = port(kernel32)
    with pytest.raises(MutexUnavailable, match="stays owned"):
        with mutex.hold(name_for(tmp_path), 1.0):
            pass
    assert names(kernel32) == ["wait", "release", "close"]


def test_off_windows_there_is_no_fallback_lock(tmp_path):
    kernel32 = Kernel32()
    mutex = WindowsRootMutex(kernel32=kernel32, create=lambda name: HANDLE, platform="posix")
    with pytest.raises(MutexUnavailable, match="no fallback"):
        with mutex.hold(name_for(tmp_path), 1.0):
            pytest.fail("the body must not run")
    assert kernel32.calls == []


@pytest.mark.parametrize("name,timeout", [("Global\\Other", 1.0), ("Global\\WaggleDanceBridgeV2Queue-" + "Z" * 32, 1.0),
                                          (None, 1.0), ("__valid__", 0), ("__valid__", 61), ("__valid__", True)])
def test_names_and_timeouts_outside_the_contract_are_refused(tmp_path, name, timeout):
    kernel32 = Kernel32()
    mutex, created = port(kernel32)
    with pytest.raises(QueueTransactionError):
        with mutex.hold(name_for(tmp_path) if name == "__valid__" else name, timeout):
            pytest.fail("the body must not run")
    assert created == [] and kernel32.calls == []


def test_constructing_the_port_touches_nothing():
    WindowsRootMutex()   # no kernel32 load, no create, no wait until hold() is entered


def test_the_transaction_locks_the_derived_name_before_the_claim_lock(tmp_path):
    kernel32 = Kernel32()
    mutex, created = port(kernel32)
    order = []

    class ClaimLock:
        @contextmanager
        def hold(self, lock_path, timeout_seconds):
            order.append(("claim_lock", Path(lock_path).name, names(kernel32)[:]))
            yield

    txns = QueueTransactions(tmp_path, mutex=mutex, claim_lock=ClaimLock())
    claim = tmp_path / "work_queue" / "claims" / "task.json"
    claim.parent.mkdir(parents=True)
    txns.transact("claim", claim, "claim:task", lambda before: Plan(after={"task_id": "task"}, expect_absent=True))
    assert created == [mutex_name(tmp_path)]
    assert order == [("claim_lock", "task.json.lock", ["wait"])]   # the mutex was already held
    assert names(kernel32) == ["wait", "release", "close"]


def test_opt_in_is_required():
    with pytest.raises(QueueTransactionError, match="opted in"):
        ports.opt_in_windows_queue_transactions("C:/runtime", opt_in=False)


def test_source_has_no_subprocess_environment_or_fallback_lock():
    tree = ast.parse((ROOT / "tools" / "bridge_v2_windows_ports.py").read_text(encoding="utf-8"))
    imported = {getattr(n, "module", None) or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m.split(".")[0] for m in imported} & {"subprocess", "socket", "threading", "multiprocessing", "waggledance"}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attributes & {"environ", "getenv", "Lock", "RLock", "Semaphore"}
