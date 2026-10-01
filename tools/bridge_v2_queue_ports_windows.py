#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8 queue ports on Windows: the runtime-root named mutex, and one factory for both ports.

Dormant and tools-owned: nothing on the running path imports this module, and it is not in the
bridge package list. ``NamedMutexPort`` is the ``MutexPort`` of
``tools.bridge_v2_queue_transactions``. Its ``hold(name, timeout_seconds)`` accepts only a
``mutex_name(root)`` value (``Global\\WaggleDanceBridgeV2Queue-<32 hex>``, derived from the
normalized runtime root, so a test root and the production root never share one), creates or
opens it through ``tools.bridge_named_mutex.create_bridge_named_mutex`` (the bridge's fail-closed
creation policy: a logon-SID DACL on a new object, never an adopted ACL) and waits at most
``timeout_seconds`` (0 < t <= 60). Both checks run before anything is created.

Wait outcomes (``WaitForSingleObject``):

- WAIT_OBJECT_0: held until the ``with`` block ends, then released and the handle closed. A
  Windows mutex is owned by a THREAD, so the block must be entered and left on one thread (as
  ``QueueTransactions.locked`` does), and it is re-entrant per thread, as every Windows mutex is:
  one transaction holds it once.
- WAIT_TIMEOUT: ``LockTimeout``; nothing was read or mutated; the handle is closed.
- WAIT_ABANDONED: the previous holder ended while holding it (a crash inside a transaction). The
  ownership this wait granted is released at once and the call is refused with
  ``MutexAbandoned``: this call read and changed nothing, and the next attempt, which acquires
  normally, recovers the claim's WAL under the locks first (see the transactions module).
  Refusing once keeps the crash visible instead of silently carrying on.
- WAIT_FAILED or any other value: ``QueueTransactionError`` naming it; the handle is closed.

Cleanup is never silent (the transactions module's descriptor policy): with an error raised in the
block, a release or close failure is recorded on that error, which keeps propagating
(``descriptor_close_unknown`` entries with the steps ``mutex_release`` and ``mutex_close``); with
no error in flight it propagates visibly as ``OSError``. A failed release still closes the handle.
Such an ``OSError`` carries the Windows error code as ``winerror`` (Python maps it to ``errno`` and the
subclass), never as ``errno`` itself. A creation failure, whether an ``OSError`` or the creation policy's
``ValueError`` (its token checks), is refused as ``QueueTransactionError`` naming its type, before any wait.

``windows_queue_transactions(root)`` wires ``NamedMutexPort`` and the PowerShell-compatible
``FileClaimLock`` (claim and fence sibling locks) into ``QueueTransactions`` for one explicit
runtime root. It is a constructor only: activation, the consumer cutover and a PowerShell twin of
the mutex are separate slices.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator

from tools.bridge_v2_queue_transactions import (MUTEX_PREFIX, FileClaimLock, LockTimeout, QueueTransactionError,
                                                QueueTransactions, _record_cleanup_unknown)

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
MAX_TIMEOUT_SECONDS = 60
_NAME = re.compile(re.escape(MUTEX_PREFIX) + r"[0-9a-f]{32}")


class MutexAbandoned(QueueTransactionError):
    """The runtime-root mutex was abandoned by a holder that ended inside a transaction. This call took the
    ownership the wait granted, released it at once and read and changed nothing; retry, and the next
    transaction on the claim recovers its WAL under the locks."""


def _kernel32() -> Any:
    """The real kernel32 with the signatures this port calls; Windows only."""
    if os.name != "nt":
        raise QueueTransactionError("the runtime-root mutex port needs Windows")
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _create(name: str, kernel32: Any) -> int:
    """Create or open the named mutex with the bridge's creation policy; the caller owns the handle."""
    from tools.bridge_named_mutex import create_bridge_named_mutex
    return create_bridge_named_mutex(name, kernel32=kernel32)


def _last_error() -> int:
    """The calling thread's last Win32 error as ctypes saved it (0 where ctypes has no such error)."""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _windows_error(code: int, text: str) -> OSError:
    """An OSError carrying a Windows error code as ``winerror``, never as ``errno``: ``OSError(code, text)`` would
    read the code as an errno and name a wrong subclass (5, access denied, reads as EIO). The four-argument form
    also builds off Windows, where Python ignores ``winerror`` and ``errno`` stays None (the fakes run anywhere)."""
    return OSError(None, text, None, code)


def _creation_failure(exc: Exception) -> str:
    """Names a creation failure by its type: an OSError's errno and winerror (read with getattr), or a
    ValueError's text (the creation policy's token checks raise fixed literal messages)."""
    if isinstance(exc, OSError):
        return "%s errno=%s winerror=%s" % (type(exc).__name__, getattr(exc, "errno", None),
                                            getattr(exc, "winerror", None))
    return "%s: %s" % (type(exc).__name__, exc)


def _close_during(kernel32: Any, handle: int, primary: BaseException, last_error: Callable[[], int]) -> None:
    """Close the handle while ``primary`` propagates; a failure is recorded on it, never dropped."""
    if not kernel32.CloseHandle(handle):
        _record_cleanup_unknown(primary, int(handle), "mutex_close", _windows_error(last_error(), "CloseHandle failed"))


def _release_and_close_during(kernel32: Any, handle: int, primary: BaseException,
                              last_error: Callable[[], int]) -> None:
    """Release, then close, while ``primary`` propagates; each failure is recorded on it, and a failed
    release still closes the handle."""
    if not kernel32.ReleaseMutex(handle):
        _record_cleanup_unknown(primary, int(handle), "mutex_release",
                                _windows_error(last_error(), "ReleaseMutex failed"))
    _close_during(kernel32, handle, primary, last_error)


def _release_and_close(kernel32: Any, handle: int, last_error: Callable[[], int]) -> None:
    """No error in flight: a failure propagates visibly. A failed release still closes the handle first, and
    a close failure then is recorded on the release error."""
    if not kernel32.ReleaseMutex(handle):
        error = _windows_error(last_error(), "the runtime-root mutex release failed")
        _close_during(kernel32, handle, error, last_error)
        raise error
    if not kernel32.CloseHandle(handle):
        raise _windows_error(last_error(), "the runtime-root mutex handle close failed")


class NamedMutexPort:
    """``MutexPort`` over a Windows named mutex (module docstring). ``kernel32``, ``create`` and
    ``last_error`` are fixture seams; the defaults are the real kernel32 (Windows only), the bridge's
    creation policy and ctypes' saved last error."""

    def __init__(self, *, kernel32: Any = None, create: Callable[[str, Any], int] | None = None,
                 last_error: Callable[[], int] | None = None) -> None:
        self._kernel32 = kernel32
        self._create = _create if create is None else create
        self._last_error = _last_error if last_error is None else last_error

    @contextmanager
    def hold(self, name: str, timeout_seconds: float) -> Iterator[None]:
        if type(name) is not str or _NAME.fullmatch(name) is None:
            raise QueueTransactionError("the runtime-root mutex name is not a mutex_name(root) value")
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise QueueTransactionError("the runtime-root mutex timeout must be in (0, 60] seconds")
        kernel32 = _kernel32() if self._kernel32 is None else self._kernel32
        try:
            handle = self._create(name, kernel32)
        except (OSError, ValueError) as exc:   # ValueError: the creation policy's token checks
            raise QueueTransactionError("the runtime-root mutex could not be created or opened ("
                                        + _creation_failure(exc) + ")") from None
        if not handle:
            raise QueueTransactionError("the runtime-root mutex could not be created or opened (no handle)")
        code = 0
        try:
            wait = int(kernel32.WaitForSingleObject(handle, max(1, int(timeout_seconds * 1000))))
            if wait == WAIT_FAILED:
                code = self._last_error()
        except BaseException as primary:
            _close_during(kernel32, handle, primary, self._last_error)
            raise
        if wait == WAIT_ABANDONED:
            refusal: QueueTransactionError = MutexAbandoned(
                "the runtime-root mutex was abandoned by a holder that ended inside a transaction; this call read "
                "and changed nothing, and the next attempt recovers the claim's WAL under the locks")
            _release_and_close_during(kernel32, handle, refusal, self._last_error)
            raise refusal
        if wait != WAIT_OBJECT_0:
            if wait == WAIT_TIMEOUT:
                refusal = LockTimeout("runtime-root mutex busy")
            elif wait == WAIT_FAILED:
                refusal = QueueTransactionError("the runtime-root mutex wait failed (" + str(code) + ")")
            else:
                refusal = QueueTransactionError("the runtime-root mutex wait returned 0x%08x" % wait)
            _close_during(kernel32, handle, refusal, self._last_error)
            raise refusal
        try:
            yield
        except BaseException as body_error:
            _release_and_close_during(kernel32, handle, body_error, self._last_error)
            raise
        _release_and_close(kernel32, handle, self._last_error)


def windows_queue_transactions(runtime_root: str | Path, **options: Any) -> QueueTransactions:
    """``QueueTransactions`` for one explicit runtime root with both Windows ports: ``NamedMutexPort`` (the
    root mutex) and ``FileClaimLock`` (the PowerShell-compatible sibling locks, fences included). ``options``
    are QueueTransactions' own (``lock_timeout_seconds``, ``clock``, ``new_id``). Nothing on the running path
    calls it."""
    return QueueTransactions(runtime_root, mutex=NamedMutexPort(), claim_lock=FileClaimLock(), **options)
