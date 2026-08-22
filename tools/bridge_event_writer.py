#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed Python implementation of the bridge AppendV1 writer.

The canonical bridge stream is exactly ``<bridge_root>/shared/events.jsonl``.
Every canonical append uses the same queue-publication fence, durable
pending-WAL, named append mutex, file sharing, validation-checkpoint, and
post-append ordering contract as
``.agent-bridge/bin/Write-AgentEvent.ps1``.  The production backend is Windows
only and uses ``ctypes`` directly; unsupported platforms never fall back to an
unfenced Python append.

Tests may inject a backend explicitly.  No environment variable or runtime
switch can select the portable test backend for production calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import codecs
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Literal, Mapping, Protocol
import uuid
import warnings


APPEND_MUTEX_NAME = r"Global\WaggleDanceBridgeAppendV1"
APPEND_MUTEX_TIMEOUT_MS = 10_000
QUEUE_PUBLICATION_MUTEX_NAME = (
    r"Global\WaggleDanceBridgeAcceptedQueuePublicationV1"
)
QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS = 10_000
CHECKPOINT_SCHEMA = "waggledance.bridge.append-v1-validation"
CHECKPOINT_SUFFIX = ".append-v1-validation.json"
TAIL_ANCHOR_MAX_BYTES = 4096
CHECKPOINT_MAX_BYTES = 8192
V1_EVENT_TYPES = frozenset(
    {
        "status",
        "intent",
        "claim",
        "release",
        "message",
        "finding",
        "decision",
        "test",
        "blocked",
        "handoff",
        "done",
        "heartbeat",
        "wake_request",
        "liveness",
    }
)
V1_AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
V1_REPLAYER_REQUIRED_FIELDS = ("agent", "type", "task_id", "status")


class BridgeEventWriteError(RuntimeError):
    """A fail-closed bridge write failure.

    ``wal_path`` identifies recovery state when one could be retained, but a
    raised error never promises that state was cleanly published or verified.
    Accepted canonical and queued deliveries return :class:`BridgeWriteResult`.
    """

    decision = "bridge_write_failed"

    def __init__(self, message: str, *, wal_path: Path | None = None) -> None:
        super().__init__(message)
        self.wal_path = wal_path
        self.canonical_durable = False


class _CanonicalAppendUncertainError(RuntimeError):
    """Canonical bytes may have changed without a durable rollback."""


@dataclass(frozen=True)
class BridgeWriteResult:
    events_path: Path
    delivery_status: Literal["canonical", "queued"]
    canonical_durable: bool
    checkpoint_advanced: bool
    retained_wal_path: Path | None
    retained_wal_sha256: str | None
    warning_messages: tuple[str, ...]
    outbox_written: bool
    last_file_written: bool


class AppendV1File(Protocol):
    path: Path

    def size(self) -> int: ...

    def identity(self) -> str: ...

    def read_at(self, offset: int, count: int) -> bytes: ...

    def write_at(self, offset: int, payload: bytes) -> None: ...

    def truncate(self, length: int) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class AppendV1Mutex(Protocol):
    acquired: bool
    abandoned: bool

    def release(self) -> None: ...

    def close(self) -> None: ...


class AppendV1DirectoryLease(Protocol):
    def close(self) -> None: ...


class AppendV1Backend(Protocol):
    """Low-level operations required by the platform-neutral protocol logic."""

    def ensure_supported(self) -> None: ...

    def mkdir(self, path: Path) -> None: ...

    def open_plain_directory_chain(
        self,
        path: Path,
    ) -> list[AppendV1DirectoryLease]: ...

    def create_new(self, path: Path, *, hidden: bool) -> AppendV1File: ...

    def open_or_create_shared_read(self, path: Path) -> AppendV1File: ...

    def acquire_mutex(self, name: str, timeout_ms: int) -> AppendV1Mutex: ...

    def read_path(self, path: Path) -> bytes: ...

    def path_size(self, path: Path) -> int: ...

    def path_exists(self, path: Path) -> bool: ...

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None: ...

    def set_hidden(self, path: Path, hidden: bool) -> None: ...

    def delete(self, path: Path) -> None: ...


@dataclass
class _CanonicalAppendResult:
    durable: bool
    checkpoint_advanced: bool
    checkpoint_error: str = ""


@dataclass
class _PendingWal:
    pending_path: Path
    ready_path: Path
    row: bytes
    row_sha256: str
    lease: AppendV1File | None
    directory_leases: tuple[AppendV1DirectoryLease, ...]


def _acquire_queue_publication_mutex(
    backend: AppendV1Backend,
) -> AppendV1Mutex:
    mutex: AppendV1Mutex | None = None
    try:
        mutex = backend.acquire_mutex(
            QUEUE_PUBLICATION_MUTEX_NAME,
            QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS,
        )
    except Exception as exc:  # noqa: BLE001 - no WAL exists yet
        raise BridgeEventWriteError(
            f"accepted queue publication fence acquisition failed: {exc}"
        ) from exc
    if mutex is not None and mutex.acquired and not mutex.abandoned:
        return mutex

    reason = (
        "ownership was abandoned"
        if mutex is not None and mutex.abandoned
        else "timed out"
    )
    cleanup_errors: list[str] = []
    if mutex is not None:
        if mutex.acquired:
            try:
                mutex.release()
            except Exception as exc:  # noqa: BLE001 - report pre-acceptance cleanup
                cleanup_errors.append(f"release failed: {exc}")
        try:
            mutex.close()
        except Exception as exc:  # noqa: BLE001 - report pre-acceptance cleanup
            cleanup_errors.append(f"close failed: {exc}")
    suffix = f"; {'; '.join(cleanup_errors)}" if cleanup_errors else ""
    raise BridgeEventWriteError(
        f"accepted queue publication fence {reason}{suffix}"
    )


def write_bridge_event(
    *,
    bridge_root: Path,
    event: Mapping[str, Any],
    events_path: Path | None = None,
    write_sidecars: bool = True,
    backend: AppendV1Backend | None = None,
) -> BridgeWriteResult:
    """Durably append one bridge event under the AppendV1 contract.

    The lexical target guard and platform gate run before any directory or file
    creation.  ``backend`` is dependency injection for tests only; production
    callers leave it unset and therefore fail closed away from Windows.
    """

    root = Path(bridge_root)
    expected_events = root / "shared" / "events.jsonl"
    target = Path(events_path) if events_path is not None else expected_events
    _assert_canonical_events_path(root, target)

    active_backend: AppendV1Backend = backend or WindowsAppendV1Backend()
    active_backend.ensure_supported()
    row = _event_row_bytes(event)
    agent = _safe_agent(event)
    outbox_path: Path | None = None
    last_path: Path | None = None
    last_bytes: bytes | None = None
    if write_sidecars:
        # Resolve and validate every fallible sidecar input before the
        # canonical append.  Once canonical bytes are durable, optional cache
        # preparation must never turn success into a retryable failure.
        outbox_path = root / "outbox" / agent / _event_date_name(event)
        last_path = root / "shared" / f"last_{agent}.json"
        last_bytes = _last_event_bytes(event)

    warning_messages: list[str] = []
    publication_mutex = _acquire_queue_publication_mutex(active_backend)
    try:
        wal = _open_pending_wal(
            backend=active_backend,
            bridge_root=root,
            row=row,
        )
    except Exception as exc:
        cleanup_errors: list[str] = []
        try:
            publication_mutex.release()
        except Exception as release_exc:  # noqa: BLE001
            cleanup_errors.append(f"release failed: {release_exc}")
        try:
            publication_mutex.close()
        except Exception as close_exc:  # noqa: BLE001
            cleanup_errors.append(f"close failed: {close_exc}")
        if cleanup_errors:
            raise BridgeEventWriteError(
                "accepted queue publication fence cleanup failed after WAL "
                f"creation error: {'; '.join(cleanup_errors)}"
            ) from exc
        raise
    mutex: AppendV1Mutex | None = None
    canonical_result: _CanonicalAppendResult | None = None
    retained_wal: Path | None = None
    retained_wal_sha256: str | None = None
    queued_reason = ""
    try:
        try:
            mutex = active_backend.acquire_mutex(
                APPEND_MUTEX_NAME,
                APPEND_MUTEX_TIMEOUT_MS,
            )
        except Exception as exc:  # noqa: BLE001 - queue only after verified WAL publish
            queued_reason = f"AppendV1 mutex acquisition failed: {exc}"

        if not queued_reason and mutex is None:
            queued_reason = "AppendV1 mutex acquisition returned no mutex"

        if not queued_reason and mutex is not None and (
            not mutex.acquired or mutex.abandoned
        ):
            queued_reason = (
                "AppendV1 was abandoned; dirty ownership cannot mutate canonical bytes"
                if mutex.abandoned
                else "bridge append mutex timeout"
            )

        if queued_reason:
            retained_wal, retained_wal_sha256 = _publish_wal_ready(
                active_backend,
                wal,
            )
        else:
            try:
                _close_wal_lease(wal)
                canonical_directory_leases = active_backend.open_plain_directory_chain(
                    target.parent,
                )
                wal.directory_leases = (
                    *wal.directory_leases,
                    *canonical_directory_leases,
                )
                canonical_result = _append_canonical_transactionally(
                    backend=active_backend,
                    path=target,
                    row=row,
                )
            except _CanonicalAppendUncertainError as exc:
                # The exact accepted WAL is the recovery authority for a torn
                # prefix. Targeted replay can bind, quarantine, and truncate
                # only a tail matching this WAL before appending it once.
                queued_reason = f"bridge canonical append requires recovery: {exc}"
                retained_wal, retained_wal_sha256 = _publish_wal_ready(
                    active_backend,
                    wal,
                )
            except Exception as exc:  # noqa: BLE001 - queue only after verified publish
                queued_reason = f"bridge canonical append failed: {exc}"
                retained_wal, retained_wal_sha256 = _publish_wal_ready(
                    active_backend,
                    wal,
                )

        if not queued_reason and canonical_result is not None:
            if not canonical_result.checkpoint_advanced:
                retained_wal = _retain_wal_best_effort(active_backend, wal)
                retained_wal_sha256 = wal.row_sha256
                _record_warning(
                    warning_messages,
                    "canonical bridge append is durable; validation checkpoint "
                    "advance failed and redundant WAL was retained at "
                    f"{retained_wal} ({canonical_result.checkpoint_error})",
                )
            else:
                try:
                    marker_path = _accepted_wal_digest_marker_path(wal)
                    active_backend.delete(marker_path)
                    if active_backend.path_exists(marker_path):
                        raise OSError("accepted WAL digest marker still exists after removal")
                    active_backend.delete(wal.pending_path)
                    if active_backend.path_exists(wal.pending_path):
                        raise OSError("pending WAL still exists after removal")
                except Exception as exc:  # noqa: BLE001 - canonical is durable
                    retained_wal = _retain_wal_best_effort(active_backend, wal)
                    retained_wal_sha256 = wal.row_sha256
                    _record_warning(
                        warning_messages,
                        "bridge append is durable but WAL cleanup failed; retained at "
                        f"{retained_wal} ({exc})",
                    )
    finally:
        pending_close_error: BridgeEventWriteError | None = None
        if wal.lease is not None:
            try:
                _close_wal_lease(wal)
            except Exception as exc:  # noqa: BLE001
                if canonical_result is not None and canonical_result.durable:
                    retained_wal = wal.pending_path
                    retained_wal_sha256 = wal.row_sha256
                    _record_warning(
                        warning_messages,
                        "canonical bridge append is durable but pending WAL lease "
                        f"close failed; retained at {retained_wal} ({exc})",
                    )
                elif not (
                    queued_reason
                    and retained_wal is not None
                    and retained_wal_sha256 is not None
                ):
                    pending_close_error = BridgeEventWriteError(
                        "pending WAL lease close failed before durable canonical "
                        f"success; retained at {wal.pending_path} ({exc})",
                        wal_path=wal.pending_path,
                    )
                    pending_close_error.__cause__ = exc
                else:
                    _record_warning(
                        warning_messages,
                        "accepted queued WAL lease close remains deferred at "
                        f"{wal.pending_path} ({exc})",
                    )
        if mutex is not None:
            if mutex.acquired:
                try:
                    mutex.release()
                except Exception as exc:  # noqa: BLE001 - do not negate durability
                    _record_warning(
                        warning_messages,
                        f"canonical AppendV1 release failed: {exc}",
                    )
            try:
                mutex.close()
            except Exception as exc:  # noqa: BLE001
                _record_warning(
                    warning_messages,
                    f"canonical AppendV1 close failed: {exc}",
                )
        for directory_lease in reversed(wal.directory_leases):
            try:
                directory_lease.close()
            except Exception as exc:  # noqa: BLE001 - transport is already decided
                _record_warning(
                    warning_messages,
                    f"accepted queue directory lease close failed: {exc}",
                )
        if publication_mutex is not None:
            if publication_mutex.acquired:
                try:
                    publication_mutex.release()
                except Exception as exc:  # noqa: BLE001 - transport is settled
                    _record_warning(
                        warning_messages,
                        f"accepted queue publication fence release failed: {exc}",
                    )
            try:
                publication_mutex.close()
            except Exception as exc:  # noqa: BLE001 - transport is settled
                _record_warning(
                    warning_messages,
                    f"accepted queue publication fence close failed: {exc}",
                )
        if pending_close_error is not None:
            raise pending_close_error

    if queued_reason:
        if retained_wal is None or retained_wal_sha256 is None:
            raise BridgeEventWriteError(
                "queued bridge delivery ended without a verified ready WAL",
                wal_path=retained_wal,
            )
        _record_warning(
            warning_messages,
            "bridge event accepted into the durable replay queue at "
            f"{retained_wal} ({queued_reason})",
        )
        return BridgeWriteResult(
            events_path=target,
            delivery_status="queued",
            canonical_durable=False,
            checkpoint_advanced=False,
            retained_wal_path=retained_wal,
            retained_wal_sha256=retained_wal_sha256,
            warning_messages=tuple(warning_messages),
            outbox_written=False,
            last_file_written=False,
        )

    if canonical_result is None or not canonical_result.durable:
        raise BridgeEventWriteError(
            "canonical bridge append did not establish durable success",
            wal_path=retained_wal,
        )

    outbox_written = False
    last_file_written = False
    if write_sidecars:
        if outbox_path is None or last_path is None or last_bytes is None:
            _record_warning(
                warning_messages,
                "canonical bridge event is durable; sidecar preparation was "
                "unexpectedly unavailable",
            )
        else:
            outbox_written = _append_auxiliary_best_effort(
                backend=active_backend,
                path=outbox_path,
                row=row,
                warning_messages=warning_messages,
            )
            last_file_written = _replace_best_effort(
                backend=active_backend,
                path=last_path,
                payload=last_bytes,
                warning_messages=warning_messages,
            )

    return BridgeWriteResult(
        events_path=target,
        delivery_status="canonical",
        canonical_durable=True,
        checkpoint_advanced=canonical_result.checkpoint_advanced,
        retained_wal_path=retained_wal,
        retained_wal_sha256=retained_wal_sha256,
        warning_messages=tuple(warning_messages),
        outbox_written=outbox_written,
        last_file_written=last_file_written,
    )


def _assert_canonical_events_path(bridge_root: Path, events_path: Path) -> None:
    expected = bridge_root / "shared" / "events.jsonl"
    if _lexical_path_key(events_path) != _lexical_path_key(expected):
        raise BridgeEventWriteError(
            "refusing non-canonical bridge event target; expected normalized "
            f"lexical path {expected}, got {events_path}"
        )


def _lexical_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def _event_row_bytes(event: Mapping[str, Any]) -> bytes:
    try:
        event_object = dict(event)
    except Exception as exc:  # noqa: BLE001 - normalize to the typed writer error
        raise BridgeEventWriteError(
            f"bridge event cannot be serialized as a JSON object: {exc}"
        ) from exc
    validate_v1_replayer_event(event_object)
    try:
        text = json.dumps(
            event_object,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        row = (text + "\n").encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeEventWriteError(f"bridge event is not strict JSON/UTF-8: {exc}") from exc
    if not row or not row.endswith(b"\n") or b"\n" in row[:-1] or b"\r" in row[:-1]:
        raise BridgeEventWriteError(
            "bridge WAL row must be one non-empty strict UTF-8 JSON row ending in LF"
        )
    return row


def validate_v1_replayer_event(event: Mapping[str, Any]) -> None:
    """Validate the exact core shape accepted by the AppendV1 replayer."""

    for field in V1_REPLAYER_REQUIRED_FIELDS:
        if field not in event:
            raise BridgeEventWriteError(
                f"bridge event is missing replayer core field {field!r}"
            )
        if not isinstance(event[field], str):
            raise BridgeEventWriteError(
                f"bridge event replayer core field {field!r} is not a string"
            )
    event_type = event["type"]
    if event_type not in V1_EVENT_TYPES:
        raise BridgeEventWriteError(f"bridge event has unknown event type: {event_type}")
    agent = event["agent"]
    if V1_AGENT_RE.fullmatch(agent) is None:
        raise BridgeEventWriteError(f"bridge event has invalid agent id: {agent}")


def _last_event_bytes(event: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(event),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeEventWriteError(f"last-event sidecar is not strict JSON/UTF-8: {exc}") from exc


def _safe_agent(event: Mapping[str, Any]) -> str:
    agent = event.get("agent")
    if not isinstance(agent, str) or V1_AGENT_RE.fullmatch(agent) is None:
        raise BridgeEventWriteError("bridge event agent is unsafe for sidecar/WAL paths")
    return agent


def _event_date_name(event: Mapping[str, Any]) -> str:
    timestamp = event.get("ts_utc")
    if isinstance(timestamp, str) and re.match(r"^\d{4}-\d{2}-\d{2}", timestamp):
        return timestamp[:10] + ".jsonl"
    raise BridgeEventWriteError("bridge event ts_utc cannot select an outbox date")


def _open_pending_wal(
    *,
    backend: AppendV1Backend,
    bridge_root: Path,
    row: bytes,
) -> _PendingWal:
    accepted_root = bridge_root / "spool" / "accepted-v1"
    pending_dir = accepted_root / "pending"
    ready_dir = accepted_root / "ready"
    quarantine_dir = accepted_root / "quarantine"
    replayed_dir = accepted_root / "replayed"
    directory_leases: list[AppendV1DirectoryLease] = []
    try:
        for directory in (
            pending_dir,
            ready_dir,
            quarantine_dir,
            replayed_dir,
        ):
            directory_leases.extend(backend.open_plain_directory_chain(directory))
    except Exception as exc:  # noqa: BLE001 - no WAL has been accepted yet
        for directory_lease in reversed(directory_leases):
            try:
                directory_lease.close()
            except Exception:
                pass
        raise BridgeEventWriteError(
            f"accepted queue directory validation failed before WAL creation: {exc}"
        ) from exc
    leaf = f"bridge-wal-v1-{uuid.uuid4().hex}.jsonl"
    pending_path = pending_dir / leaf
    ready_path = ready_dir / leaf
    row_sha256 = hashlib.sha256(row).hexdigest()
    lease: AppendV1File | None = None
    keep_directory_leases = False
    try:
        lease = backend.create_new(pending_path, hidden=False)
        lease.write_at(0, row)
        lease.flush()
        if lease.size() != len(row) or lease.read_at(0, len(row)) != row:
            raise OSError("durable pending WAL writeback verification mismatch")
        wal = _PendingWal(
            pending_path=pending_path,
            ready_path=ready_path,
            row=row,
            row_sha256=row_sha256,
            lease=lease,
            directory_leases=tuple(directory_leases),
        )
        _ensure_accepted_wal_digest_marker(backend, wal)
        keep_directory_leases = True
        return wal
    except Exception as exc:  # noqa: BLE001
        if lease is not None:
            try:
                lease.close()
            except Exception:
                pass
        excluded_path: Path | None = None
        try:
            if backend.path_exists(pending_path):
                quarantine_dir = accepted_root / "quarantine"
                backend.mkdir(quarantine_dir)
                excluded_path = quarantine_dir / f"unaccepted-{leaf}"
                backend.move(
                    pending_path,
                    excluded_path,
                    replace=False,
                    write_through=True,
                )
        except Exception as quarantine_exc:  # noqa: BLE001
            try:
                if backend.path_exists(pending_path):
                    backend.delete(pending_path)
            except Exception:
                pass
            if backend.path_exists(pending_path):
                raise BridgeEventWriteError(
                    "pending WAL acceptance is unknown and blind retry is unsafe at "
                    f"{pending_path}: {exc}; exclusion failed: {quarantine_exc}",
                    wal_path=pending_path,
                ) from exc
        raise BridgeEventWriteError(
            "could not establish an accepted pending bridge WAL; candidate was "
            f"excluded from automatic replay at {excluded_path}: {exc}",
            wal_path=excluded_path,
        ) from exc
    finally:
        if not keep_directory_leases:
            for directory_lease in reversed(directory_leases):
                try:
                    directory_lease.close()
                except Exception:
                    pass


def _close_wal_lease(wal: _PendingWal) -> None:
    if wal.lease is None:
        return
    lease = wal.lease
    lease.close()
    wal.lease = None


def _read_plain_recovery_path(backend: AppendV1Backend, path: Path) -> bytes:
    if isinstance(backend, WindowsAppendV1Backend):
        return backend.read_plain_single_link_path(path)
    return backend.read_path(path)


def _exact_wal_recovery_candidate(
    backend: AppendV1Backend,
    path: Path,
    wal: _PendingWal,
) -> bool:
    try:
        candidate = _read_plain_recovery_path(backend, path)
    except Exception:  # noqa: BLE001 - a different/unavailable path is not authority
        return False
    return candidate == wal.row and hashlib.sha256(candidate).hexdigest() == wal.row_sha256


def _accepted_wal_digest_marker_path(wal: _PendingWal) -> Path:
    return wal.ready_path.parent / f".{wal.ready_path.name}.pending-recovery-blocked"


def _accepted_wal_digest_marker_bytes(wal: _PendingWal) -> bytes:
    marker = {
        "schema": "waggledance.bridge.accepted-pending-block.v1",
        "wal_leaf": wal.ready_path.name,
        "expected_sha256": wal.row_sha256,
    }
    return (
        json.dumps(marker, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8", errors="strict")


def _accepted_wal_digest_marker_matches(
    backend: AppendV1Backend,
    path: Path,
    wal: _PendingWal,
) -> bool:
    try:
        raw = _read_plain_recovery_path(backend, path)
        marker = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("schema") == "waggledance.bridge.accepted-pending-block.v1"
        and marker.get("wal_leaf") == wal.ready_path.name
        and marker.get("expected_sha256") == wal.row_sha256
    )


def _ensure_accepted_wal_digest_marker(
    backend: AppendV1Backend,
    wal: _PendingWal,
) -> Path:
    marker_path = _accepted_wal_digest_marker_path(wal)
    backend.mkdir(marker_path.parent)
    if backend.path_exists(marker_path):
        if _accepted_wal_digest_marker_matches(backend, marker_path, wal):
            return marker_path
        raise OSError("accepted WAL digest marker collides with different authority")

    quarantine_dir = wal.pending_path.parent.parent / "quarantine"
    backend.mkdir(quarantine_dir)
    temporary_path = quarantine_dir / f"digest-marker-{uuid.uuid4().hex}.tmp"
    marker_bytes = _accepted_wal_digest_marker_bytes(wal)
    marker_file: AppendV1File | None = None
    try:
        marker_file = backend.create_new(temporary_path, hidden=False)
        marker_file.write_at(0, marker_bytes)
        marker_file.flush()
        if (
            marker_file.size() != len(marker_bytes)
            or marker_file.read_at(0, len(marker_bytes)) != marker_bytes
        ):
            raise OSError("durable accepted WAL digest marker verification mismatch")
        marker_file.close()
        marker_file = None
        try:
            backend.move(
                temporary_path,
                marker_path,
                replace=False,
                write_through=True,
            )
        except Exception:  # noqa: BLE001 - accept an exact prior marker only
            if _accepted_wal_digest_marker_matches(backend, marker_path, wal):
                return marker_path
            raise
        return marker_path
    finally:
        if marker_file is not None:
            try:
                marker_file.close()
            except Exception:
                pass
        try:
            if backend.path_exists(temporary_path):
                backend.delete(temporary_path)
        except Exception:
            pass


def _publish_wal_ready(
    backend: AppendV1Backend,
    wal: _PendingWal,
) -> tuple[Path, str]:
    try:
        _close_wal_lease(wal)
    except Exception:
        # Flush plus exact readback already established acceptance. Keep the
        # lease for the outer finally and report pending; the drainer waits for
        # the process to release it before age-gated recovery.
        return wal.pending_path, wal.row_sha256
    try:
        _ensure_accepted_wal_digest_marker(backend, wal)
    except Exception as exc:  # noqa: BLE001 - queued success requires marker authority
        raise BridgeEventWriteError(
            "accepted WAL digest authority is unavailable; queued success is "
            f"unsafe even if pending bytes remain: {exc}",
            wal_path=wal.pending_path,
        ) from exc
    try:
        backend.mkdir(wal.ready_path.parent)
        backend.move(
            wal.pending_path,
            wal.ready_path,
            replace=False,
            write_through=True,
        )
    except Exception as exc:  # noqa: BLE001
        if _exact_wal_recovery_candidate(backend, wal.pending_path, wal):
            return wal.pending_path, wal.row_sha256
        if _exact_wal_recovery_candidate(backend, wal.ready_path, wal):
            return wal.ready_path, wal.row_sha256
        raise BridgeEventWriteError(
            "accepted WAL publication failed and recovery state was retained at "
            f"{wal.pending_path}: {exc}",
            wal_path=wal.pending_path,
        ) from exc
    # Exact bytes were verified through the exclusive pending lease before it
    # closed. The write-through, no-replace rename is the publication point;
    # a drainer is free to archive ready immediately after this succeeds.
    return wal.ready_path, wal.row_sha256


def _retain_wal_best_effort(backend: AppendV1Backend, wal: _PendingWal) -> Path:
    try:
        ready_path, _ = _publish_wal_ready(backend, wal)
        return ready_path
    except BridgeEventWriteError as exc:
        return exc.wal_path or wal.pending_path


def _append_canonical_transactionally(
    *,
    backend: AppendV1Backend,
    path: Path,
    row: bytes,
) -> _CanonicalAppendResult:
    # ``ensure_supported`` already ran before WAL creation.  The caller opens
    # and retains a plain, no-delete directory chain only after acquiring the
    # clean append mutex, before this lexical path can create or open a leaf.
    stream: AppendV1File | None = None
    durable = False
    close_error = ""
    result: _CanonicalAppendResult | None = None
    try:
        stream = backend.open_or_create_shared_read(path)
        pre_length = stream.size()
        file_identity = stream.identity()
        checkpoint_path = Path(os.fspath(path) + CHECKPOINT_SUFFIX)
        if not _checkpoint_matches(
            backend=backend,
            stream=stream,
            checkpoint_path=checkpoint_path,
            file_identity=file_identity,
            length=pre_length,
        ):
            _assert_strict_utf8_target(stream, path, pre_length)
            _write_checkpoint(
                backend=backend,
                stream=stream,
                checkpoint_path=checkpoint_path,
                file_identity=file_identity,
                length=pre_length,
            )

        try:
            stream.write_at(pre_length, row)
            stream.flush()
            durable = True
        except Exception as append_exc:  # noqa: BLE001
            try:
                stream.truncate(pre_length)
                stream.flush()
            except Exception as rollback_exc:  # noqa: BLE001
                raise _CanonicalAppendUncertainError(
                    "transactional bridge append failed "
                    f"({append_exc}); ROLLBACK FAILED: {rollback_exc}"
                ) from rollback_exc
            raise BridgeEventWriteError(
                "transactional bridge append failed and rolled back: "
                f"{append_exc}"
            ) from append_exc

        try:
            _write_checkpoint(
                backend=backend,
                stream=stream,
                checkpoint_path=checkpoint_path,
                file_identity=file_identity,
                length=pre_length + len(row),
            )
            result = _CanonicalAppendResult(durable=True, checkpoint_advanced=True)
        except Exception as exc:  # noqa: BLE001 - bytes are already durable
            result = _CanonicalAppendResult(
                durable=True,
                checkpoint_advanced=False,
                checkpoint_error=str(exc),
            )
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001
                close_error = str(exc)

    if close_error:
        if durable:
            return _CanonicalAppendResult(
                durable=True,
                checkpoint_advanced=False,
                checkpoint_error=f"canonical handle close failed after flush: {close_error}",
            )
        raise BridgeEventWriteError(f"canonical handle close failed: {close_error}")
    if result is None:
        raise BridgeEventWriteError("canonical append ended without a result")
    return result


def _assert_strict_utf8_target(
    stream: AppendV1File,
    path: Path,
    length: int,
) -> None:
    if length == 0:
        return
    tail = stream.read_at(length - 1, 1)
    if tail != b"\n":
        raise BridgeEventWriteError(f"bridge append target has an unterminated row: {path}")
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    offset = 0
    try:
        while offset < length:
            requested = min(8192, length - offset)
            chunk = stream.read_at(offset, requested)
            if len(chunk) != requested:
                raise OSError("canonical bridge full validation read ended early")
            offset += len(chunk)
            decoder.decode(chunk, final=offset == length)
    except UnicodeDecodeError as exc:
        raise BridgeEventWriteError(
            f"bridge append target is not strict UTF-8: {path} ({exc})"
        ) from exc


def _checkpoint_matches(
    *,
    backend: AppendV1Backend,
    stream: AppendV1File,
    checkpoint_path: Path,
    file_identity: str,
    length: int,
) -> bool:
    try:
        if not backend.path_exists(checkpoint_path):
            return False
        size = backend.path_size(checkpoint_path)
        if size <= 0 or size > CHECKPOINT_MAX_BYTES:
            return False
        payload = backend.read_path(checkpoint_path)
        if len(payload) != size or not payload.endswith(b"\n"):
            return False
        if payload.startswith(b"\xef\xbb\xbf") or b"\n" in payload[:-1] or b"\r" in payload[:-1]:
            return False
        text = payload[:-1].decode("utf-8", errors="strict")
        checkpoint = json.loads(text)
        expected_keys = [
            "schema",
            "version",
            "file_identity",
            "validated_length",
            "tail_anchor_length",
            "tail_anchor_sha256",
        ]
        if not isinstance(checkpoint, dict) or list(checkpoint) != expected_keys:
            return False
        if checkpoint["schema"] != CHECKPOINT_SCHEMA:
            return False
        version = checkpoint["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            return False
        if checkpoint["file_identity"] != file_identity:
            return False
        validated_text = checkpoint["validated_length"]
        anchor_length_text = checkpoint["tail_anchor_length"]
        if not isinstance(validated_text, str) or not isinstance(anchor_length_text, str):
            return False
        if not re.fullmatch(r"0|[1-9][0-9]*", validated_text):
            return False
        if not re.fullmatch(r"0|[1-9][0-9]*", anchor_length_text):
            return False
        validated_length = int(validated_text)
        anchor_length = int(anchor_length_text)
        if validated_length != length or anchor_length != min(TAIL_ANCHOR_MAX_BYTES, length):
            return False
        anchor_sha = checkpoint["tail_anchor_sha256"]
        if not isinstance(anchor_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", anchor_sha):
            return False
        canonical = _checkpoint_bytes(
            file_identity=file_identity,
            length=validated_length,
            anchor_length=anchor_length,
            anchor_sha256=anchor_sha,
        )
        if payload != canonical:
            return False
        anchor = _tail_anchor(stream, length)
        return anchor[0] == anchor_length and anchor[1] == anchor_sha
    except Exception:  # noqa: BLE001 - cache miss forces authoritative scan
        return False


def _checkpoint_bytes(
    *,
    file_identity: str,
    length: int,
    anchor_length: int,
    anchor_sha256: str,
) -> bytes:
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "version": 1,
        "file_identity": file_identity,
        "validated_length": str(length),
        "tail_anchor_length": str(anchor_length),
        "tail_anchor_sha256": anchor_sha256,
    }
    return (
        json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        + "\n"
    ).encode("utf-8", errors="strict")


def _tail_anchor(stream: AppendV1File, length: int) -> tuple[int, str]:
    if length < 0 or stream.size() != length:
        raise BridgeEventWriteError("cannot anchor an inexact canonical bridge length")
    anchor_length = min(TAIL_ANCHOR_MAX_BYTES, length)
    anchor = stream.read_at(length - anchor_length, anchor_length) if anchor_length else b""
    if len(anchor) != anchor_length:
        raise BridgeEventWriteError("canonical bridge tail anchor read ended early")
    return anchor_length, hashlib.sha256(anchor).hexdigest()


def _write_checkpoint(
    *,
    backend: AppendV1Backend,
    stream: AppendV1File,
    checkpoint_path: Path,
    file_identity: str,
    length: int,
) -> None:
    if stream.size() != length:
        raise BridgeEventWriteError("canonical bridge length changed before checkpoint advance")
    if stream.identity() != file_identity:
        raise BridgeEventWriteError("canonical bridge file identity changed before checkpoint advance")
    anchor_length, anchor_sha = _tail_anchor(stream, length)
    payload = _checkpoint_bytes(
        file_identity=file_identity,
        length=length,
        anchor_length=anchor_length,
        anchor_sha256=anchor_sha,
    )
    temporary = Path(
        f"{checkpoint_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    handle: AppendV1File | None = None
    try:
        handle = backend.create_new(temporary, hidden=False)
        handle.write_at(0, payload)
        handle.flush()
        handle.close()
        handle = None
        backend.move(
            temporary,
            checkpoint_path,
            replace=True,
            write_through=True,
        )
        published = backend.read_path(checkpoint_path)
        if published != payload:
            raise BridgeEventWriteError("published validation checkpoint verification failed")
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        try:
            temporary_exists = backend.path_exists(temporary)
        except Exception:
            temporary_exists = False
        if temporary_exists:
            try:
                backend.delete(temporary)
            except Exception:
                pass


def _append_auxiliary_best_effort(
    *,
    backend: AppendV1Backend,
    path: Path,
    row: bytes,
    warning_messages: list[str],
) -> bool:
    mutex: AppendV1Mutex | None = None
    try:
        try:
            mutex = backend.acquire_mutex(APPEND_MUTEX_NAME, APPEND_MUTEX_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001
            _record_warning(
                warning_messages,
                f"auxiliary outbox append was skipped: mutex acquisition failed: {exc}",
            )
            return False
        if not mutex.acquired or mutex.abandoned:
            reason = (
                "AppendV1 was abandoned; dirty ownership cannot mutate the outbox"
                if mutex.abandoned
                else "AppendV1 timeout"
            )
            _record_warning(
                warning_messages,
                f"auxiliary outbox append was skipped: {reason}",
            )
            return False
        try:
            _append_auxiliary_transactionally(backend=backend, path=path, row=row)
            return True
        except Exception as exc:  # noqa: BLE001
            _record_warning(
                warning_messages,
                "canonical bridge event is durable; auxiliary outbox append "
                f"was skipped: {exc}",
            )
            return False
    finally:
        if mutex is not None:
            if mutex.acquired:
                try:
                    mutex.release()
                except Exception as exc:  # noqa: BLE001
                    _record_warning(
                        warning_messages,
                        f"auxiliary AppendV1 release failed: {exc}",
                    )
            try:
                mutex.close()
            except Exception as exc:  # noqa: BLE001
                _record_warning(
                    warning_messages,
                    f"auxiliary AppendV1 close failed: {exc}",
                )


def _append_auxiliary_transactionally(
    *,
    backend: AppendV1Backend,
    path: Path,
    row: bytes,
) -> None:
    backend.mkdir(path.parent)
    stream: AppendV1File | None = None
    try:
        stream = backend.open_or_create_shared_read(path)
        pre_length = stream.size()
        if pre_length and stream.read_at(pre_length - 1, 1) != b"\n":
            raise BridgeEventWriteError("auxiliary bridge append target has an unterminated row")
        try:
            stream.write_at(pre_length, row)
            stream.flush()
        except Exception as append_exc:  # noqa: BLE001
            try:
                stream.truncate(pre_length)
                stream.flush()
            except Exception as rollback_exc:  # noqa: BLE001
                raise BridgeEventWriteError(
                    f"auxiliary append failed ({append_exc}); rollback failed: {rollback_exc}"
                ) from rollback_exc
            raise BridgeEventWriteError(
                f"auxiliary append failed and rolled back: {append_exc}"
            ) from append_exc
    finally:
        if stream is not None:
            stream.close()


def _replace_best_effort(
    *,
    backend: AppendV1Backend,
    path: Path,
    payload: bytes,
    warning_messages: list[str],
) -> bool:
    temporary = Path(f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    handle: AppendV1File | None = None
    try:
        backend.mkdir(path.parent)
        handle = backend.create_new(temporary, hidden=False)
        handle.write_at(0, payload)
        handle.flush()
        handle.close()
        handle = None
        backend.move(temporary, path, replace=True, write_through=False)
        return True
    except Exception as exc:  # noqa: BLE001 - canonical is already durable
        _record_warning(
            warning_messages,
            f"could not atomically replace last-event file: {path} ({exc})",
        )
        return False
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        try:
            temporary_exists = backend.path_exists(temporary)
        except Exception:
            temporary_exists = False
        if temporary_exists:
            try:
                backend.delete(temporary)
            except Exception:
                pass


def _record_warning(messages: list[str], message: str) -> None:
    messages.append(message)
    try:
        warnings.warn(message, RuntimeWarning, stacklevel=3)
    except Warning:
        # Warning filters must not convert an accepted canonical or verified
        # queued delivery into a raised failure that invites a duplicate retry.
        pass


class _WindowsFile:
    def __init__(self, backend: "WindowsAppendV1Backend", handle: int, path: Path) -> None:
        self._backend = backend
        self._handle = handle
        self.path = path
        self._closed = False

    def _require_open(self) -> int:
        if self._closed:
            raise OSError(f"file handle is closed: {self.path}")
        return self._handle

    def size(self) -> int:
        value = ctypes.c_longlong()
        if not self._backend._kernel32.GetFileSizeEx(self._require_open(), ctypes.byref(value)):
            self._backend._raise_last_error("GetFileSizeEx", self.path)
        return int(value.value)

    def identity(self) -> str:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not self._backend._kernel32.GetFileInformationByHandle(
            self._require_open(), ctypes.byref(info)
        ):
            self._backend._raise_last_error("GetFileInformationByHandle", self.path)
        return (
            "windows-file-id-v1:"
            f"{int(info.dwVolumeSerialNumber):08x}:"
            f"{int(info.nFileIndexHigh):08x}:"
            f"{int(info.nFileIndexLow):08x}"
        )

    def _seek(self, offset: int) -> None:
        new_position = ctypes.c_longlong()
        if not self._backend._kernel32.SetFilePointerEx(
            self._require_open(),
            ctypes.c_longlong(offset),
            ctypes.byref(new_position),
            0,
        ):
            self._backend._raise_last_error("SetFilePointerEx", self.path)
        if new_position.value != offset:
            raise OSError(f"SetFilePointerEx reached an inexact offset for {self.path}")

    def read_at(self, offset: int, count: int) -> bytes:
        if count < 0:
            raise ValueError("read count must not be negative")
        self._seek(offset)
        chunks: list[bytes] = []
        remaining = count
        while remaining:
            chunk_size = min(remaining, 1 << 20)
            buffer = ctypes.create_string_buffer(chunk_size)
            read = wintypes.DWORD()
            if not self._backend._kernel32.ReadFile(
                self._require_open(), buffer, chunk_size, ctypes.byref(read), None
            ):
                self._backend._raise_last_error("ReadFile", self.path)
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
            remaining -= int(read.value)
        return b"".join(chunks)

    def write_at(self, offset: int, payload: bytes) -> None:
        self._seek(offset)
        view = memoryview(payload)
        written_total = 0
        while written_total < len(payload):
            chunk = bytes(view[written_total : written_total + (1 << 20)])
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(chunk, len(chunk))
            if not self._backend._kernel32.WriteFile(
                self._require_open(),
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            ):
                self._backend._raise_last_error("WriteFile", self.path)
            if written.value <= 0:
                raise OSError(f"WriteFile made no progress for {self.path}")
            written_total += int(written.value)

    def truncate(self, length: int) -> None:
        self._seek(length)
        if not self._backend._kernel32.SetEndOfFile(self._require_open()):
            self._backend._raise_last_error("SetEndOfFile", self.path)

    def flush(self) -> None:
        if not self._backend._kernel32.FlushFileBuffers(self._require_open()):
            self._backend._raise_last_error("FlushFileBuffers", self.path)

    def close(self) -> None:
        if self._closed:
            return
        handle = self._handle
        self._closed = True
        if not self._backend._kernel32.CloseHandle(handle):
            self._backend._raise_last_error("CloseHandle", self.path)


class _WindowsMutex:
    def __init__(
        self,
        backend: "WindowsAppendV1Backend",
        handle: int,
        *,
        acquired: bool,
        abandoned: bool,
    ) -> None:
        self._backend = backend
        self._handle = handle
        self.acquired = acquired
        self.abandoned = abandoned
        self._closed = False
        self._released = False

    def release(self) -> None:
        if self._released or not self.acquired:
            return
        if not self._backend._kernel32.ReleaseMutex(self._handle):
            self._backend._raise_last_error("ReleaseMutex", None)
        self._released = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._backend._kernel32.CloseHandle(self._handle):
            self._backend._raise_last_error("CloseHandle(mutex)", None)


class _WindowsDirectoryLease:
    def __init__(
        self,
        backend: "WindowsAppendV1Backend",
        handle: int,
        path: Path,
    ) -> None:
        self._backend = backend
        self._handle = handle
        self._path = path
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._backend._kernel32.CloseHandle(self._handle):
            self._backend._raise_last_error("CloseHandle(directory)", self._path)


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class WindowsAppendV1Backend:
    """Dependency-free Windows kernel32 backend for the AppendV1 protocol."""

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_HIDDEN = 0x00000002
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    MOVEFILE_REPLACE_EXISTING = 0x00000001
    MOVEFILE_WRITE_THROUGH = 0x00000008
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF

    def __init__(self) -> None:
        self._supported = os.name == "nt"
        self._kernel32: Any | None = None
        if self._supported:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._configure_signatures()

    def ensure_supported(self) -> None:
        if not self._supported or self._kernel32 is None:
            raise BridgeEventWriteError(
                "AppendV1 production writes require Windows file identity, "
                "named mutexes, shared-read handles, and write-through replacement; "
                "refusing an unfenced append"
            )

    def _configure_signatures(self) -> None:
        kernel32 = self._kernel32
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        kernel32.GetFileSizeEx.restype = wintypes.BOOL
        kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        kernel32.SetFilePointerEx.restype = wintypes.BOOL
        kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        kernel32.SetEndOfFile.restype = wintypes.BOOL
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.MoveFileExW.restype = wintypes.BOOL
        kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetFileAttributesW.restype = wintypes.DWORD
        kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.SetFileAttributesW.restype = wintypes.BOOL
        kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
        kernel32.DeleteFileW.restype = wintypes.BOOL

    def _raise_last_error(self, operation: str, path: Path | None) -> None:
        code = ctypes.get_last_error()
        detail = ctypes.FormatError(code).strip()
        suffix = f" for {path}" if path is not None else ""
        raise OSError(code, f"{operation} failed{suffix}: {detail}")

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def open_plain_directory_chain(self, path: Path) -> list[_WindowsDirectoryLease]:
        self.ensure_supported()
        full_path = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        if not full_path.anchor:
            raise OSError(f"accepted queue directory path is not rooted: {path}")
        cursor = Path(full_path.anchor)
        candidates = [cursor]
        for part in full_path.relative_to(full_path.anchor).parts:
            cursor = cursor / part
            candidates.append(cursor)

        leases: list[_WindowsDirectoryLease] = []
        try:
            for candidate in candidates:
                candidate.mkdir(exist_ok=True)
                handle = self._kernel32.CreateFileW(
                    os.fspath(candidate),
                    self.GENERIC_READ,
                    self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
                    None,
                    self.OPEN_EXISTING,
                    self.FILE_FLAG_BACKUP_SEMANTICS
                    | self.FILE_FLAG_OPEN_REPARSE_POINT,
                    None,
                )
                if handle == wintypes.HANDLE(-1).value:
                    self._raise_last_error("CreateFileW(directory)", candidate)
                lease = _WindowsDirectoryLease(self, handle, candidate)
                try:
                    information = _BY_HANDLE_FILE_INFORMATION()
                    if not self._kernel32.GetFileInformationByHandle(
                        handle,
                        ctypes.byref(information),
                    ):
                        self._raise_last_error(
                            "GetFileInformationByHandle(directory)",
                            candidate,
                        )
                    if not (
                        information.dwFileAttributes & self.FILE_ATTRIBUTE_DIRECTORY
                    ) or (
                        information.dwFileAttributes
                        & self.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise OSError(
                            "accepted queue path component must be a plain directory, "
                            f"not a reparse point: {candidate}"
                        )
                except Exception:
                    lease.close()
                    raise
                leases.append(lease)
            return leases
        except Exception:
            for lease in reversed(leases):
                try:
                    lease.close()
                except Exception:
                    pass
            raise

    def _open(
        self,
        path: Path,
        *,
        disposition: int,
        share: int,
        attributes: int,
        access: int | None = None,
    ) -> _WindowsFile:
        self.ensure_supported()
        desired_access = access if access is not None else self.GENERIC_READ | self.GENERIC_WRITE
        handle = self._kernel32.CreateFileW(
            os.fspath(path),
            desired_access,
            share,
            None,
            disposition,
            attributes,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            self._raise_last_error("CreateFileW", path)
        return _WindowsFile(self, handle, path)

    def create_new(self, path: Path, *, hidden: bool) -> _WindowsFile:
        attributes = self.FILE_ATTRIBUTE_HIDDEN if hidden else self.FILE_ATTRIBUTE_NORMAL
        return self._open(path, disposition=self.CREATE_NEW, share=0, attributes=attributes)

    def open_or_create_shared_read(self, path: Path) -> _WindowsFile:
        stream = self._open(
            path,
            disposition=self.OPEN_ALWAYS,
            share=self.FILE_SHARE_READ,
            attributes=self.FILE_ATTRIBUTE_NORMAL | self.FILE_FLAG_OPEN_REPARSE_POINT,
        )
        try:
            information = _BY_HANDLE_FILE_INFORMATION()
            if not self._kernel32.GetFileInformationByHandle(
                stream._require_open(),
                ctypes.byref(information),
            ):
                self._raise_last_error("GetFileInformationByHandle", path)
            if (
                information.nNumberOfLinks != 1
                or information.dwFileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT
                or information.dwFileAttributes & self.FILE_ATTRIBUTE_DIRECTORY
            ):
                raise OSError(f"append target must be a plain single-link file: {path}")
            return stream
        except Exception:
            stream.close()
            raise

    def acquire_mutex(self, name: str, timeout_ms: int) -> _WindowsMutex:
        self.ensure_supported()
        handle = self._kernel32.CreateMutexW(None, False, name)
        if not handle:
            self._raise_last_error("CreateMutexW", None)
        result = int(self._kernel32.WaitForSingleObject(handle, timeout_ms))
        if result == self.WAIT_OBJECT_0:
            return _WindowsMutex(self, handle, acquired=True, abandoned=False)
        if result == self.WAIT_ABANDONED:
            return _WindowsMutex(self, handle, acquired=True, abandoned=True)
        if result == self.WAIT_TIMEOUT:
            return _WindowsMutex(self, handle, acquired=False, abandoned=False)
        try:
            self._kernel32.CloseHandle(handle)
        finally:
            if result == self.WAIT_FAILED:
                self._raise_last_error("WaitForSingleObject", None)
        raise OSError(f"WaitForSingleObject returned unexpected status 0x{result:08x}")

    def read_path(self, path: Path) -> bytes:
        handle = self._open(
            path,
            disposition=self.OPEN_EXISTING,
            share=self.FILE_SHARE_READ,
            attributes=self.FILE_ATTRIBUTE_NORMAL,
            access=self.GENERIC_READ,
        )
        try:
            return handle.read_at(0, handle.size())
        finally:
            handle.close()

    def read_plain_single_link_path(self, path: Path) -> bytes:
        handle = self._open(
            path,
            disposition=self.OPEN_EXISTING,
            share=self.FILE_SHARE_READ,
            attributes=self.FILE_FLAG_OPEN_REPARSE_POINT,
            access=self.GENERIC_READ,
        )
        try:
            information = _BY_HANDLE_FILE_INFORMATION()
            if not self._kernel32.GetFileInformationByHandle(
                handle._handle,  # noqa: SLF001 - same backend owns the handle wrapper
                ctypes.byref(information),
            ):
                self._raise_last_error("GetFileInformationByHandle", path)
            if information.nNumberOfLinks != 1:
                raise OSError(f"accepted recovery candidate has multiple hard links: {path}")
            if information.dwFileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(f"accepted recovery candidate is a reparse point: {path}")
            if information.dwFileAttributes & self.FILE_ATTRIBUTE_DIRECTORY:
                raise OSError(f"accepted recovery candidate is a directory: {path}")
            return handle.read_at(0, handle.size())
        finally:
            handle.close()

    def path_size(self, path: Path) -> int:
        handle = self._open(
            path,
            disposition=self.OPEN_EXISTING,
            share=self.FILE_SHARE_READ,
            attributes=self.FILE_ATTRIBUTE_NORMAL,
            access=self.GENERIC_READ,
        )
        try:
            return handle.size()
        finally:
            handle.close()

    def path_exists(self, path: Path) -> bool:
        attributes = int(self._kernel32.GetFileAttributesW(os.fspath(path)))
        if attributes != self.INVALID_FILE_ATTRIBUTES:
            return True
        return False

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None:
        flags = 0
        if replace:
            flags |= self.MOVEFILE_REPLACE_EXISTING
        if write_through:
            flags |= self.MOVEFILE_WRITE_THROUGH
        if not self._kernel32.MoveFileExW(os.fspath(source), os.fspath(destination), flags):
            self._raise_last_error("MoveFileExW", destination)

    def set_hidden(self, path: Path, hidden: bool) -> None:
        attributes = int(self._kernel32.GetFileAttributesW(os.fspath(path)))
        if attributes == self.INVALID_FILE_ATTRIBUTES:
            self._raise_last_error("GetFileAttributesW", path)
        if hidden:
            updated = attributes | self.FILE_ATTRIBUTE_HIDDEN
        else:
            updated = attributes & ~self.FILE_ATTRIBUTE_HIDDEN
            if updated == 0:
                updated = self.FILE_ATTRIBUTE_NORMAL
        if not self._kernel32.SetFileAttributesW(os.fspath(path), updated):
            self._raise_last_error("SetFileAttributesW", path)

    def delete(self, path: Path) -> None:
        if not self._kernel32.DeleteFileW(os.fspath(path)):
            self._raise_last_error("DeleteFileW", path)


# Explicitly injected portable backend for unit tests.  It is private, is never
# selected by production code, and intentionally cannot be enabled through an
# environment variable or CLI option.
class _PortableFile:
    def __init__(self, backend: "_PortableTestBackend", fd: int, path: Path) -> None:
        self._backend = backend
        self._fd = fd
        self.path = path
        self._closed = False

    def _require_open(self) -> int:
        if self._closed:
            raise OSError(f"file handle is closed: {self.path}")
        return self._fd

    def size(self) -> int:
        return int(os.fstat(self._require_open()).st_size)

    def identity(self) -> str:
        stat = os.fstat(self._require_open())
        return f"test-file-id-v1:{stat.st_dev:x}:{stat.st_ino:x}"

    def read_at(self, offset: int, count: int) -> bytes:
        os.lseek(self._require_open(), offset, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = count
        while remaining:
            chunk = os.read(self._require_open(), remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write_at(self, offset: int, payload: bytes) -> None:
        os.lseek(self._require_open(), offset, os.SEEK_SET)
        written = 0
        while written < len(payload):
            count = os.write(self._require_open(), payload[written:])
            if count <= 0:
                raise OSError(f"portable test write made no progress: {self.path}")
            written += count

    def truncate(self, length: int) -> None:
        os.ftruncate(self._require_open(), length)

    def flush(self) -> None:
        os.fsync(self._require_open())

    def close(self) -> None:
        if not self._closed:
            os.close(self._fd)
            self._closed = True


class _PortableMutex:
    def __init__(
        self,
        lock: threading.Lock | None,
        *,
        acquired: bool,
        abandoned: bool,
        fail_release: bool,
    ) -> None:
        self._lock = lock
        self.acquired = acquired
        self.abandoned = abandoned
        self._fail_release = fail_release
        self._released = False

    def release(self) -> None:
        if self._released or not self.acquired:
            return
        if self._fail_release:
            raise OSError("simulated mutex release failure")
        if self._lock is not None:
            self._lock.release()
        self._released = True

    def close(self) -> None:
        return


class _PortableDirectoryLease:
    def close(self) -> None:
        return


class _PortableTestBackend:
    """Filesystem-backed injected fake used by portable tests only."""

    def __init__(
        self,
        *,
        mutex_outcomes: list[str] | None = None,
        fail_release: bool = False,
    ) -> None:
        self.mutex_outcomes = list(mutex_outcomes or [])
        self.fail_release = fail_release
        self.mutex_acquisitions = 0
        self.mutex_requests: list[tuple[str, int]] = []
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def ensure_supported(self) -> None:
        return

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def open_plain_directory_chain(self, path: Path) -> list[_PortableDirectoryLease]:
        full_path = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        if not full_path.anchor:
            raise OSError(f"accepted queue directory path is not rooted: {path}")
        cursor = Path(full_path.anchor)
        candidates = [cursor]
        for part in full_path.relative_to(full_path.anchor).parts:
            cursor = cursor / part
            candidates.append(cursor)
        leases: list[_PortableDirectoryLease] = []
        for candidate in candidates:
            candidate.mkdir(exist_ok=True)
            if candidate.is_symlink() or not candidate.is_dir():
                raise OSError(
                    "accepted queue path component must be a plain directory, "
                    f"not a symbolic link: {candidate}"
                )
            leases.append(_PortableDirectoryLease())
        return leases

    def create_new(self, path: Path, *, hidden: bool) -> _PortableFile:
        del hidden
        fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        return _PortableFile(self, fd, path)

    def open_or_create_shared_read(self, path: Path) -> _PortableFile:
        fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
            0o600,
        )
        return _PortableFile(self, fd, path)

    def acquire_mutex(self, name: str, timeout_ms: int) -> _PortableMutex:
        self.mutex_acquisitions += 1
        self.mutex_requests.append((name, timeout_ms))
        outcome = self.mutex_outcomes.pop(0) if self.mutex_outcomes else "clean"
        if outcome == "error":
            raise OSError("simulated mutex construction failure")
        if outcome == "timeout":
            return _PortableMutex(None, acquired=False, abandoned=False, fail_release=False)
        if outcome == "abandoned":
            return _PortableMutex(None, acquired=True, abandoned=True, fail_release=False)
        with self._locks_guard:
            lock = self._locks.setdefault(name, threading.Lock())
        acquired = lock.acquire(timeout=max(0.0, timeout_ms / 1000.0))
        return _PortableMutex(
            lock if acquired else None,
            acquired=acquired,
            abandoned=False,
            fail_release=self.fail_release,
        )

    def read_path(self, path: Path) -> bytes:
        return path.read_bytes()

    def path_size(self, path: Path) -> int:
        return path.stat().st_size

    def path_exists(self, path: Path) -> bool:
        return path.is_file()

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        replace: bool,
        write_through: bool,
    ) -> None:
        del write_through
        if replace:
            os.replace(source, destination)
        else:
            os.rename(source, destination)

    def set_hidden(self, path: Path, hidden: bool) -> None:
        del path, hidden

    def delete(self, path: Path) -> None:
        path.unlink()


__all__ = [
    "APPEND_MUTEX_NAME",
    "APPEND_MUTEX_TIMEOUT_MS",
    "QUEUE_PUBLICATION_MUTEX_NAME",
    "QUEUE_PUBLICATION_MUTEX_TIMEOUT_MS",
    "BridgeEventWriteError",
    "BridgeWriteResult",
    "V1_EVENT_TYPES",
    "WindowsAppendV1Backend",
    "validate_v1_replayer_event",
    "write_bridge_event",
]
