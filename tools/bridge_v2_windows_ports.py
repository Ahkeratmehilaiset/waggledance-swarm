#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8: the Windows runtime-root named-mutex port for ``QueueTransactions``.

Explicit opt-in library, OFF by default: nothing imports or constructs it, it has no
default runtime root, reads no environment, starts no process and edits no existing
writer. It implements the frozen ``MutexPort`` signature of
``tools.bridge_v2_queue_transactions`` (``hold(name, timeout_seconds)``) and locks exactly
the name that module derives with ``mutex_name(root)`` (the normalized root identity);
the lock order stays the transaction's own: this runtime-root mutex FIRST, then the legacy
sibling ``<claim>.json.lock`` (``FileClaimLock`` there, ``Enter-BridgeClaimLock`` in
PowerShell). The PowerShell twin is ``.agent-bridge/bin/BridgeV2QueuePorts.ps1``.

Lifecycle, all outcomes truthful and every handle closed:

* the mutex is created or opened through ``tools.bridge_named_mutex.create_bridge_named_mutex``
  (the bridge's fail-closed DACL policy, minimum rights SYNCHRONIZE | MUTEX_MODIFY_STATE);
  a refused create/open raises ``MutexUnavailable``;
* ``WaitForSingleObject`` with a bounded timeout (never INFINITE):
  WAIT_OBJECT_0 runs the body and releases after it; WAIT_TIMEOUT raises ``LockTimeout``
  (nothing mutated); WAIT_ABANDONED means the previous holder died inside a transaction:
  the mutex is released again WITHOUT running the body and ``MutexAbandoned`` tells the
  caller to reconcile the queue WAL first; WAIT_FAILED or any other value raises
  ``MutexUnavailable``;
* a failed ``ReleaseMutex`` is reported (the mutex stays owned by this thread until it
  exits) unless the body already raised, which is never masked; ``CloseHandle`` always runs.

A Windows mutex is owned by a thread: hold it on the thread that runs the transaction.
Off Windows there is no fallback lock: ``hold`` raises ``MutexUnavailable``. This does not
fence the legacy writers, which do not take this mutex (no mixed-generation safety claim).
Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.bridge_v2_queue_transactions import (MUTEX_PREFIX, FileClaimLock, LockTimeout, QueueTransactionError,
                                                QueueTransactions)

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
MAX_TIMEOUT_SECONDS = 60


class MutexUnavailable(QueueTransactionError):
    """The platform or the kernel refused the runtime-root mutex; nothing was locked or mutated."""


class MutexAbandoned(QueueTransactionError):
    """The previous holder died while holding the mutex. It was taken and released again without
    running the body; reconcile the queue WAL (QueueTransactions.reconcile) before retrying."""


def _check(name: Any, timeout_seconds: Any) -> int:
    if (not isinstance(name, str) or not name.startswith(MUTEX_PREFIX)
            or len(name) != len(MUTEX_PREFIX) + 32
            or any(c not in "0123456789abcdef" for c in name[len(MUTEX_PREFIX):])):
        raise QueueTransactionError("not a v2 queue runtime-root mutex name")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise QueueTransactionError("mutex timeout must be in (0, 60] seconds")
    return max(1, int(timeout_seconds * 1000))


def _default_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class WindowsRootMutex:
    """``MutexPort`` over a Windows named mutex. Constructing it touches nothing; the kernel
    is used only inside ``hold``. ``kernel32``, ``create`` and ``platform`` are injection
    points for fixtures; the defaults are the real kernel32 and the bridge creation policy."""

    def __init__(self, *, kernel32: Any = None, create: Callable[[str], int] | None = None,
                 platform: str | None = None) -> None:
        self._kernel32, self._create = kernel32, create
        self._platform = os.name if platform is None else platform

    def _api(self) -> tuple[Any, Callable[[str], int]]:
        if self._platform != "nt":
            raise MutexUnavailable("a Windows named mutex is required; there is no fallback lock")
        kernel32 = self._kernel32 if self._kernel32 is not None else _default_kernel32()
        create = self._create
        if create is None:
            from tools.bridge_named_mutex import create_bridge_named_mutex
            def create(name: str) -> int:
                return create_bridge_named_mutex(name, kernel32=kernel32)
        return kernel32, create

    @contextmanager
    def hold(self, name: str, timeout_seconds: float) -> Iterator[None]:
        milliseconds = _check(name, timeout_seconds)
        kernel32, create = self._api()
        try:
            handle = create(name)
        except (OSError, ValueError) as exc:
            raise MutexUnavailable("runtime-root mutex create/open refused: " + type(exc).__name__) from None
        if not handle:
            raise MutexUnavailable("runtime-root mutex create/open returned no handle")
        try:
            outcome = kernel32.WaitForSingleObject(handle, milliseconds)
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
        if outcome == WAIT_ABANDONED:
            released = bool(kernel32.ReleaseMutex(handle))
            kernel32.CloseHandle(handle)
            raise MutexAbandoned("the previous holder died inside a queue transaction; reconcile the WAL first"
                                 + ("" if released else "; ReleaseMutex also failed"))
        if outcome != WAIT_OBJECT_0:
            kernel32.CloseHandle(handle)
            if outcome == WAIT_TIMEOUT:
                raise LockTimeout("runtime-root mutex busy: bounded wait expired, nothing mutated")
            raise MutexUnavailable(f"WaitForSingleObject returned {int(outcome):#x}")
        body_failed = False
        try:
            yield
        except BaseException:
            body_failed = True
            raise
        finally:
            released = bool(kernel32.ReleaseMutex(handle))
            kernel32.CloseHandle(handle)
            if not released and not body_failed:
                raise MutexUnavailable("ReleaseMutex failed: the mutex stays owned until this thread exits")


def opt_in_windows_queue_transactions(runtime_root: str | Path, *, opt_in: bool,
                                      lock_timeout_seconds: float = 4.0) -> QueueTransactions:
    """The Windows port pair (runtime-root mutex, then the legacy sibling lock) for one explicit
    root. Refused unless the caller opts in explicitly; nothing calls this by default."""
    if opt_in is not True:
        raise QueueTransactionError("Windows queue ports are off unless explicitly opted in")
    return QueueTransactions(runtime_root, mutex=WindowsRootMutex(), claim_lock=FileClaimLock(),
                             lock_timeout_seconds=lock_timeout_seconds)
