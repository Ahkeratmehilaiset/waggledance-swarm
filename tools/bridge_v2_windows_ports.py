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
PowerShell). Each lock has its OWN bounded budget: the transaction passes the same
``lock_timeout_seconds`` to both holds, and each measures only its own wait. The PowerShell
twin is ``.agent-bridge/bin/BridgeV2QueuePorts.ps1``.

Existing-object ACL (Tools 8c6066ff F8-DACL-INHERITED): the shared creation policy
``tools.bridge_named_mutex.create_bridge_named_mutex`` creates a NEW mutex with the bridge's
minimum-access DACL, but for an EXISTING object it only prints a diagnostic and returns the
handle: that is NOT an admission gate. This port therefore requires explicit existing-object
ACL evidence before it waits: ``inspect_acl(name, handle)`` must return exactly ``"match"``;
anything else (mismatch, unknown, an exception) refuses before the body. There is no reviewed
read-only inspector seam yet (the factory's private ``_inspect_existing`` returns free text and
is not a reviewed contract), so the default has NO inspector and every ``hold`` refuses: the
adapter stays dormant until a reviewed inspector exists. ``inspect_acl``, ``kernel32``,
``create`` and ``platform`` are fixture seams, never a way to assert trust.

Lifecycle: the mutex is created or opened through the creation policy (a refused create/open
raises ``MutexUnavailable``); ``WaitForSingleObject`` has a bounded timeout (never INFINITE):
WAIT_OBJECT_0 runs the body; WAIT_TIMEOUT raises ``LockTimeout`` (nothing mutated);
WAIT_ABANDONED means the previous holder died inside a transaction: the mutex is released
again WITHOUT running the body and ``MutexAbandoned`` asks for WAL reconciliation first;
WAIT_FAILED or any other value raises ``MutexUnavailable``.

Cleanup (Tools 8c6066ff F8-CLEANUP; 51ada W-SECONDARY-EXCEPTIONS): ``ReleaseMutex`` and
``CloseHandle`` are each attempted independently and their BOOL results are checked (FALSE or
an ordinary exception is a failure). A primary wait or body error is never masked by an
ORDINARY cleanup failure (an ``Exception``, a FALSE BOOL, or a failing note): it is re-raised
unchanged, with the bounded secondary cleanup diagnostic attached as an exception note when
the note can be attached. After a clean body, any cleanup failure raises ``MutexUnavailable``:
there is no success on failed cleanup. A failed release leaves the mutex owned by this thread
until it exits. The honest limit: an asynchronous interrupt (a ``BaseException`` such as
``KeyboardInterrupt`` or ``SystemExit``) raised INSIDE a cleanup call is not a cleanup failure
and propagates, after the remaining cleanup calls were still attempted.

A Windows mutex is owned by a thread: hold it on the thread that runs the transaction.
Off Windows there is no fallback lock: ``hold`` raises ``MutexUnavailable``. This does not
fence the legacy writers, which do not take this mutex (no mixed-generation safety claim),
and makes no physical alias or open-race claim beyond the transaction's lexical containment.
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
ACL_MATCH = "match"
MAX_DIAGNOSTIC_CHARS = 300


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


def _attempt(label: str, call: Callable[[], Any], failures: list[str]) -> None:
    """One cleanup call: a FALSE BOOL or an Exception is recorded, never raised."""
    try:
        if not call():
            failures.append(label + " returned FALSE")
    except Exception as exc:  # noqa: BLE001 - every cleanup is attempted and reported
        failures.append(label + " raised " + type(exc).__name__)


def _close(kernel32: Any, handle: int) -> list[str]:
    failures: list[str] = []
    _attempt("CloseHandle", lambda: kernel32.CloseHandle(handle), failures)
    return failures


def _release_and_close(kernel32: Any, handle: int) -> list[str]:
    """ReleaseMutex, then CloseHandle, each attempted independently; returns the failures."""
    failures: list[str] = []
    try:
        _attempt("ReleaseMutex", lambda: kernel32.ReleaseMutex(handle), failures)
    finally:
        _attempt("CloseHandle", lambda: kernel32.CloseHandle(handle), failures)
    return failures


def _diagnostic(failures: list[str]) -> str:
    return "; ".join(failures)[:MAX_DIAGNOSTIC_CHARS]


def _suffix(failures: list[str]) -> str:
    return "; cleanup also failed: " + _diagnostic(failures) if failures else ""


def _note(exc: BaseException, failures: list[str]) -> None:
    """Keep the primary error unchanged; attach the bounded secondary cleanup diagnostic. The
    note can never replace the primary: a missing or failing add_note (an old runtime, or an
    exception type whose note machinery raises) is ignored (Tools 51ada W-SECONDARY-EXCEPTIONS)."""
    if not failures:
        return
    try:
        exc.add_note("bridge v2 runtime-root mutex cleanup also failed: " + _diagnostic(failures))
    except Exception:  # noqa: BLE001 - the diagnostic is secondary; the primary error wins
        pass


class WindowsRootMutex:
    """``MutexPort`` over a Windows named mutex. Constructing it touches nothing; the kernel
    is used only inside ``hold``. ``kernel32``, ``create``, ``platform`` and ``inspect_acl``
    are fixture seams; the defaults are the real kernel32, the bridge creation policy and NO
    ACL inspector (so every ``hold`` refuses until a reviewed inspector seam exists)."""

    def __init__(self, *, kernel32: Any = None, create: Callable[[str], int] | None = None,
                 platform: str | None = None, inspect_acl: Callable[[str, int], Any] | None = None) -> None:
        self._kernel32, self._create, self._inspect_acl = kernel32, create, inspect_acl
        self._platform = os.name if platform is None else platform

    def _api(self) -> tuple[Any, Callable[[str], int], Callable[[str, int], Any]]:
        if self._platform != "nt":
            raise MutexUnavailable("a Windows named mutex is required; there is no fallback lock")
        inspect_acl = self._inspect_acl
        if inspect_acl is None:
            raise MutexUnavailable("existing-object ACL evidence unavailable: no reviewed inspector seam, "
                                   "the F8 adapter stays refused")
        kernel32 = self._kernel32 if self._kernel32 is not None else _default_kernel32()
        create = self._create
        if create is None:
            from tools.bridge_named_mutex import create_bridge_named_mutex
            def create(name: str) -> int:
                return create_bridge_named_mutex(name, kernel32=kernel32)
        return kernel32, create, inspect_acl

    @contextmanager
    def hold(self, name: str, timeout_seconds: float) -> Iterator[None]:
        milliseconds = _check(name, timeout_seconds)
        kernel32, create, inspect_acl = self._api()
        try:
            handle = create(name)
        except (OSError, ValueError) as exc:
            raise MutexUnavailable("runtime-root mutex create/open refused: " + type(exc).__name__) from None
        if not handle:
            raise MutexUnavailable("runtime-root mutex create/open returned no handle")
        try:
            verdict = inspect_acl(name, handle)
        except Exception as exc:  # noqa: BLE001 - an inspection failure is unknown evidence
            verdict = "unknown: inspector raised " + type(exc).__name__
        if verdict != ACL_MATCH or type(verdict) is not str:
            raise MutexUnavailable("existing-object ACL evidence is not a match (" + str(verdict)[:80] + ")"
                                   + _suffix(_close(kernel32, handle)))
        try:
            outcome = kernel32.WaitForSingleObject(handle, milliseconds)
        except BaseException as exc:
            _note(exc, _close(kernel32, handle))
            raise
        if outcome == WAIT_ABANDONED:
            failures = _release_and_close(kernel32, handle)
            released = not any(item.startswith("ReleaseMutex") for item in failures)
            raise MutexAbandoned("the previous holder died inside a queue transaction; reconcile the WAL first"
                                 + ("" if released else "; ReleaseMutex also failed") + _suffix(failures))
        if outcome != WAIT_OBJECT_0:
            failures = _close(kernel32, handle)
            if outcome == WAIT_TIMEOUT:
                raise LockTimeout("runtime-root mutex busy: bounded wait expired, nothing mutated" + _suffix(failures))
            raise MutexUnavailable(f"WaitForSingleObject returned {int(outcome):#x}" + _suffix(failures))
        try:
            yield
        except BaseException as exc:
            _note(exc, _release_and_close(kernel32, handle))
            raise
        failures = _release_and_close(kernel32, handle)
        if failures:
            raise MutexUnavailable("cleanup failed after a clean body (" + _diagnostic(failures)
                                   + "): a failed ReleaseMutex stays owned until this thread exits")


def opt_in_windows_queue_transactions(runtime_root: str | Path, *, opt_in: bool,
                                      lock_timeout_seconds: float = 4.0) -> QueueTransactions:
    """The Windows port pair (runtime-root mutex, then the legacy sibling lock) for one explicit
    root. Refused unless the caller opts in explicitly; nothing calls this by default. The mutex
    has no ACL inspector, so its ``hold`` refuses until a reviewed inspector seam exists."""
    if opt_in is not True:
        raise QueueTransactionError("Windows queue ports are off unless explicitly opted in")
    return QueueTransactions(runtime_root, mutex=WindowsRootMutex(), claim_lock=FileClaimLock(),
                             lock_timeout_seconds=lock_timeout_seconds)
