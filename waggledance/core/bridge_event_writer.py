# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed writer for the canonical agent-bridge event stream.

The shared ``events.jsonl`` append is the commit boundary.  Outbox and
``last_<agent>.json`` files are projections: their failures are reported as
warnings after a canonical commit, but can never invite a retry of that event.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Protocol
import warnings

from waggledance.core.bridge_event_schema import validate_event


APPEND_MUTEX_NAME = r"Global\WaggleDanceBridgeAppendV1"
DEFAULT_MUTEX_TIMEOUT_MS = 10_000


class CanonicalAppendOutcome(str, Enum):
    """Known outcome after a canonical write was attempted."""

    ROLLED_BACK = "RolledBack"
    AMBIGUOUS = "Ambiguous"


class BridgeEventWriteError(OSError):
    """Base error for a bridge event that did not reach the commit boundary."""


class BridgeEventMutexError(BridgeEventWriteError):
    """The cross-runtime append mutex could not be acquired safely."""


class CanonicalAppendError(BridgeEventWriteError):
    """A failed attempted append with an explicit durable outcome."""

    def __init__(
        self,
        message: str,
        *,
        outcome: CanonicalAppendOutcome,
        append_error: BaseException,
        rollback_error: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.append_error = append_error
        self.rollback_error = rollback_error


class BridgeEventProjectionWarning(RuntimeWarning):
    """A noncanonical event projection could not be updated."""


@dataclass(frozen=True)
class BridgeEventWriteResult:
    """Successful canonical write and any non-fatal projection diagnostics."""

    events_path: Path
    outbox_path: Path
    last_agent_path: Path
    projection_warnings: tuple[str, ...] = ()
    finalization_warnings: tuple[str, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.projection_warnings + self.finalization_warnings


PrecommitCallback = Callable[[Path], None]


class _MutexLease(Protocol):
    def release(self) -> None: ...


@dataclass
class _WindowsMutexLease:
    kernel32: Any
    handle: int

    def release(self) -> None:
        errors: list[str] = []
        if not self.kernel32.ReleaseMutex(self.handle):
            errors.append(
                f"ReleaseMutex failed with Windows error {ctypes.get_last_error()}"
            )
        if not self.kernel32.CloseHandle(self.handle):
            errors.append(
                f"CloseHandle failed with Windows error {ctypes.get_last_error()}"
            )
        if errors:
            raise BridgeEventMutexError("; ".join(errors))


@dataclass
class _ThreadMutexLease:
    lock: Any

    def release(self) -> None:
        self.lock.release()


_NON_WINDOWS_APPEND_LOCK = threading.Lock()


def _windows_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _acquire_append_mutex(timeout_ms: int) -> _MutexLease:
    if timeout_ms < 0:
        raise ValueError("mutex timeout must be non-negative")
    if os.name != "nt":
        if not _NON_WINDOWS_APPEND_LOCK.acquire(timeout=timeout_ms / 1000):
            raise BridgeEventMutexError(
                f"timed out acquiring {APPEND_MUTEX_NAME} after {timeout_ms} ms"
            )
        return _ThreadMutexLease(_NON_WINDOWS_APPEND_LOCK)

    try:
        kernel32 = _windows_kernel32()
        handle = kernel32.CreateMutexW(None, False, APPEND_MUTEX_NAME)
    except BaseException as exc:
        raise BridgeEventMutexError(
            f"could not create append mutex {APPEND_MUTEX_NAME}: {exc}"
        ) from exc
    if not handle:
        raise BridgeEventMutexError(
            f"could not create append mutex {APPEND_MUTEX_NAME}: "
            f"Windows error {ctypes.get_last_error()}"
        )

    wait_object_0 = 0x00000000
    wait_abandoned = 0x00000080
    wait_timeout = 0x00000102
    try:
        wait_result = int(kernel32.WaitForSingleObject(handle, timeout_ms))
    except BaseException as exc:
        kernel32.CloseHandle(handle)
        raise BridgeEventMutexError(
            f"could not wait for append mutex {APPEND_MUTEX_NAME}: {exc}"
        ) from exc
    if wait_result in {wait_object_0, wait_abandoned}:
        return _WindowsMutexLease(kernel32, handle)

    wait_error = ctypes.get_last_error()
    close_error = ""
    if not kernel32.CloseHandle(handle):
        close_error = (
            f"; CloseHandle failed with Windows error {ctypes.get_last_error()}"
        )
    if wait_result == wait_timeout:
        raise BridgeEventMutexError(
            f"timed out acquiring {APPEND_MUTEX_NAME} after {timeout_ms} ms"
            f"{close_error}"
        )
    raise BridgeEventMutexError(
        f"WaitForSingleObject failed for {APPEND_MUTEX_NAME}: "
        f"result 0x{wait_result:08X}, Windows error {wait_error}"
        f"{close_error}"
    )


def _open_canonical(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_BINARY", 0)
    return os.open(path, flags, 0o666)


def _write_once(fd: int, payload: bytes) -> int:
    return os.write(fd, payload)


def _durable_flush(fd: int) -> None:
    os.fsync(fd)


def _truncate(fd: int, length: int) -> None:
    os.ftruncate(fd, length)


def _close_canonical(fd: int) -> None:
    os.close(fd)


def _verify_rollback(fd: int, original_length: int) -> None:
    actual_length = os.fstat(fd).st_size
    if actual_length != original_length:
        raise OSError(
            f"rollback length verification failed: expected {original_length}, "
            f"found {actual_length}"
        )


def _read_exact(fd: int, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _append_canonical(path: Path, payload: bytes) -> tuple[str, ...]:
    """Append once, durably verify, or roll back through the same handle."""

    fd = _open_canonical(path)
    attempted = False
    committed = False
    finalization_warnings: list[str] = []
    try:
        original_length = os.fstat(fd).st_size
        if original_length > 0:
            os.lseek(fd, -1, os.SEEK_END)
            if os.read(fd, 1) != b"\n":
                raise OSError("canonical event log does not end with LF")
        os.lseek(fd, 0, os.SEEK_END)
        attempted = True
        written = _write_once(fd, payload)
        if written != len(payload):
            raise OSError(
                f"short canonical append: expected {len(payload)} bytes, wrote {written}"
            )
        _durable_flush(fd)
        expected_length = original_length + len(payload)
        actual_length = os.fstat(fd).st_size
        if actual_length != expected_length:
            raise OSError(
                f"canonical append length mismatch: expected {expected_length}, "
                f"found {actual_length}"
            )
        os.lseek(fd, original_length, os.SEEK_SET)
        suffix = _read_exact(fd, len(payload))
        if suffix != payload:
            raise OSError("canonical append suffix verification failed")
        committed = True
    except BaseException as append_error:
        if not attempted:
            raise
        try:
            _truncate(fd, original_length)
            _durable_flush(fd)
            _verify_rollback(fd, original_length)
        except BaseException as rollback_error:
            raise CanonicalAppendError(
                "canonical append failed and rollback failed; outcome is Ambiguous "
                f"(append_error={append_error}; rollback_error={rollback_error})",
                outcome=CanonicalAppendOutcome.AMBIGUOUS,
                append_error=append_error,
                rollback_error=rollback_error,
            ) from append_error
        raise CanonicalAppendError(
            "canonical append failed and was durably rolled back; outcome is RolledBack "
            f"(append_error={append_error})",
            outcome=CanonicalAppendOutcome.ROLLED_BACK,
            append_error=append_error,
        ) from append_error
    finally:
        try:
            _close_canonical(fd)
        except OSError as exc:
            if committed:
                finalization_warnings.append(
                    "canonical append committed and verified, but handle finalization "
                    f"failed: {exc}; no retry is permitted"
                )
            # Before commit an active exception already describes the safe or
            # ambiguous write outcome.  Never mask it with a close failure.
    return tuple(finalization_warnings)


def _append_projection(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o666)
    try:
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError(
                f"short projection append: expected {len(payload)} bytes, wrote {written}"
            )
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_last_agent_projection(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(event), indent=2).encode("utf-8")
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, 0o666)
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(
                    f"short last-agent write: expected {len(payload)} bytes, "
                    f"wrote {written}"
                )
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _record_nonthrowing_warning(
    destination: list[str],
    message: str,
    *,
    category: type[Warning] = BridgeEventProjectionWarning,
) -> None:
    destination.append(message)
    try:
        warnings.warn(message, category, stacklevel=3)
    except BaseException:
        # A warnings filter may promote warnings to exceptions.  Once the
        # canonical event is committed even that must not make the call fail.
        pass


def write_bridge_event(
    *,
    bridge_root: Path,
    event: Mapping[str, Any],
    precommit: PrecommitCallback | None = None,
    mutex_timeout_ms: int = DEFAULT_MUTEX_TIMEOUT_MS,
) -> BridgeEventWriteResult:
    """Validate and commit one event, then best-effort its projections.

    ``precommit`` runs while the cross-runtime append mutex is held and before
    any canonical byte is written.  It is intended for state-dependent writes
    such as close-RCO, where a second reader must reject a now-closed request.
    """

    canonical_event = dict(event)
    validate_event(canonical_event)
    line = (
        json.dumps(canonical_event, separators=(",", ":"), sort_keys=False) + "\n"
    ).encode("utf-8")

    agent = str(canonical_event["agent"])
    date_name = str(canonical_event["ts_utc"])[:10] + ".jsonl"
    events_path = bridge_root / "shared" / "events.jsonl"
    outbox_path = bridge_root / "outbox" / agent / date_name
    last_path = bridge_root / "shared" / f"last_{agent}.json"

    lease = _acquire_append_mutex(mutex_timeout_ms)
    committed = False
    projection_warnings: list[str] = []
    finalization_warnings: list[str] = []
    try:
        if precommit is not None:
            precommit(events_path)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        append_finalization_warnings = _append_canonical(events_path, line)
        committed = True
        finalization_warnings.extend(append_finalization_warnings)

        try:
            _append_projection(outbox_path, line)
        except BaseException as exc:
            _record_nonthrowing_warning(
                projection_warnings,
                f"canonical event committed, but outbox projection failed: {exc}",
            )
        try:
            _write_last_agent_projection(last_path, canonical_event)
        except BaseException as exc:
            _record_nonthrowing_warning(
                projection_warnings,
                f"canonical event committed, but last-agent projection failed: {exc}",
            )
    finally:
        try:
            lease.release()
        except BaseException as exc:
            if committed:
                finalization_warnings.append(
                    "canonical event committed, but append mutex finalization failed: "
                    f"{exc}; no retry is permitted"
                )
            else:
                # No event committed, so the original precommit/append error is
                # authoritative and must not be hidden by cleanup diagnostics.
                pass

    for message in finalization_warnings:
        try:
            warnings.warn(message, RuntimeWarning, stacklevel=2)
        except BaseException:
            pass
    return BridgeEventWriteResult(
        events_path=events_path,
        outbox_path=outbox_path,
        last_agent_path=last_path,
        projection_warnings=tuple(projection_warnings),
        finalization_warnings=tuple(finalization_warnings),
    )


__all__ = [
    "APPEND_MUTEX_NAME",
    "BridgeEventMutexError",
    "BridgeEventProjectionWarning",
    "BridgeEventWriteError",
    "BridgeEventWriteResult",
    "CanonicalAppendError",
    "CanonicalAppendOutcome",
    "write_bridge_event",
]
