# SPDX-License-Identifier: BUSL-1.1
"""Receiver-owned replay registry for verified chat-served windows.

The registry is deliberately a measurement boundary, not an authority source.
It never changes ``claim_safe``, authorizes runtime work, or treats a producer's
freshness assertion as proof.  A receiver supplies and persists exact source,
window, pre-marker binding, marker-target path, and marker digest pins.

Registration is two-phase:

* ``reserved_pre_marker`` durably consumes a window ID after independent
  pre-marker verification but before the clean marker is published;
* ``final_verified`` binds that reservation to the receiver-verified marker.

The reservation must happen before marker publication.  Consequently two
processes racing with the same window ID cannot both publish a clean marker.
A crash after reservation but before publication leaves the ID consumed, not
falsely verified.  A later receiver may finalize an existing reservation only
after independently verifying the final marker.

Storage is strict, bounded, LF-terminated canonical JSONL.  Every row is
closed-schema and hash-linked.  Reads and appends take both a same-process
mutex and a cross-process advisory lock, reject reparse/symlink paths and
hardlinks, and fail closed on malformed or torn data.  Appends are flushed and
``fsync``-ed before returning.

Receiver timestamps are audit ordering metadata only; sequence/hash order is
authoritative and a wall-clock rollback is advanced by one microsecond instead
of blocking the registry.  Replay detection is scoped to this receiver-owned
registry.  Copying or forking the whole registry is outside this unkeyed local
ledger's threat boundary.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.chat_served_claim_window_evidence import (
    PRODUCTION_WINDOW_VERIFICATION_SCHEMA,
    ProductionWindowVerification,
)

WINDOW_REGISTRY_EVENT_SCHEMA = "magma.chat_served_window_registry_event.v1"
WINDOW_REGISTRY_RECORD_HASH_SCHEMA = (
    "magma.chat_served_window_registry_record_hash.v1"
)
WINDOW_MARKER_PATH_DIGEST_SCHEMA = (
    "magma.chat_served_window_marker_path_digest.v1"
)
RESERVED_PRE_MARKER = "reserved_pre_marker"
FINAL_VERIFIED = "final_verified"
REGISTRY_EVENT_TYPES = frozenset({RESERVED_PRE_MARKER, FINAL_VERIFIED})

GENESIS_PREV_RECORD_HASH = "sha256:" + ("0" * 64)

MAX_REGISTRY_BYTES = 16 * 1024 * 1024
MAX_REGISTRY_LINE_BYTES = 4 * 1024
MAX_REGISTRY_RECORDS = 100_000
MAX_LOCK_TIMEOUT_SECONDS = 30.0

_HASH_FIELD = "record_hash"
_BINDING_FIELDS = (
    "window_id",
    "source_head",
    "binding_digest",
    "start_boundary_digest",
    "final_boundary_digest",
    "marker_path_digest",
    "marker_digest",
    "final_ledger_head",
)
_COMMON_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "event_type",
        "window_id",
        "source_head",
        "binding_digest",
        "start_boundary_digest",
        "final_boundary_digest",
        "marker_path_digest",
        "marker_digest",
        "final_ledger_head",
        "recorded_at_utc",
        "prev_record_hash",
        "measurement_only",
        "claim_safe_count",
        "runtime_authority_granted",
        _HASH_FIELD,
    }
)
_RESERVATION_KEYS = _COMMON_KEYS
_FINALIZATION_KEYS = _COMMON_KEYS | {
    "reservation_hash",
}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SOURCE_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


class WindowRegistryError(ValueError):
    """Base class for fail-closed registry errors."""


class WindowRegistryCorruptionError(WindowRegistryError):
    """The durable registry is malformed, torn, or no longer hash-consistent."""


class WindowRegistryReplayError(WindowRegistryError):
    """A consumed identity or evidence digest was presented again."""


class WindowRegistryStateError(WindowRegistryError):
    """A two-phase state transition or receiver pin is invalid."""


class WindowRegistryVerificationError(WindowRegistryError):
    """The in-lock independent verifier did not approve the exact binding."""


class WindowRegistryLockError(WindowRegistryError):
    """The receiver-owned single-writer lock could not be acquired."""


class WindowRegistryPathError(WindowRegistryError):
    """The registry or lock path is unsafe."""


class RegistryChainResult(NamedTuple):
    """Result of verifying the full registry chain and state machine."""

    ok: bool
    broken_at: int | None
    reason: str | None


@dataclass(frozen=True)
class WindowRegistryBinding:
    """Exact receiver-owned pins for one proposed final evidence snapshot."""

    window_id: str
    source_head: str
    binding_digest: str
    start_boundary_digest: str
    final_boundary_digest: str
    marker_path_digest: str
    marker_digest: str
    final_ledger_head: str


@dataclass(frozen=True)
class RegistryVerificationApproval:
    """Callback return value binding a verifier verdict to exact receiver pins."""

    binding: WindowRegistryBinding
    verdict: object


@dataclass(frozen=True)
class WindowRegistrySnapshot:
    """Immutable read view of consumed and finally verified windows."""

    records: tuple[Mapping[str, Any], ...]
    consumed_window_ids: tuple[str, ...]
    verified_window_ids: tuple[str, ...]
    reservations: Mapping[str, Mapping[str, Any]]
    finalizations: Mapping[str, Mapping[str, Any]]
    head_hash: str
    file_size_bytes: int
    measurement_only: bool = True
    claim_safe_count: int = 0
    runtime_authority_granted: bool = False

    def reservation_for(self, window_id: str) -> Mapping[str, Any] | None:
        """Return the immutable reservation row for ``window_id`` if present."""
        return self.reservations.get(window_id)

    def finalization_for(self, window_id: str) -> Mapping[str, Any] | None:
        """Return the immutable finalization row for ``window_id`` if present."""
        return self.finalizations.get(window_id)

    def is_consumed(self, window_id: str) -> bool:
        return window_id in self.reservations

    def is_verified(self, window_id: str) -> bool:
        return window_id in self.finalizations


@dataclass(frozen=True)
class _RegistryRead:
    records: tuple[dict[str, Any], ...]
    file_size_bytes: int
    identity: tuple[int, int] | None
    mtime_ns: int | None


def is_registry_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def is_window_id(value: object) -> bool:
    return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None


def is_source_head(value: object) -> bool:
    return isinstance(value, str) and _SOURCE_HEAD_RE.fullmatch(value) is not None


def derive_window_marker_path_digest(
    path: str | os.PathLike[str],
) -> str:
    """Bind a marker to one normalized absolute target without storing its path."""
    try:
        raw_path = os.fspath(path)
    except TypeError as exc:
        raise WindowRegistryPathError("marker_path_invalid") from exc
    if not isinstance(raw_path, str):
        raise WindowRegistryPathError("marker_path_invalid")
    if not raw_path:
        raise WindowRegistryPathError("marker_path_empty")
    if "\x00" in raw_path:
        raise WindowRegistryPathError("marker_path_nul_not_allowed")
    requested_path = Path(raw_path)
    if not requested_path.is_absolute():
        raise WindowRegistryPathError("marker_path_must_be_absolute")

    def normalized(value: str | os.PathLike[str]) -> str:
        normalized_value = os.path.normcase(os.path.normpath(os.fspath(value)))
        if os.name == "nt":
            normalized_value = normalized_value.casefold()
        return normalized_value

    try:
        lexical_candidate = _canonicalize_absolute_path(requested_path)
        resolved_candidate = _canonicalize_absolute_path(
            lexical_candidate.resolve(strict=False)
        )
        lexical_path = normalized(lexical_candidate)
        resolved_path = normalized(resolved_candidate)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WindowRegistryPathError("marker_path_resolution_failed") from exc
    return sha256_digest(
        {
            "schema_version": WINDOW_MARKER_PATH_DIGEST_SCHEMA,
            "normalized_lexical_path": lexical_path,
            "resolved_path": resolved_path,
        }
    )


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _receiver_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def compute_registry_record_hash(record: Mapping[str, Any]) -> str:
    """Hash every closed-schema row field except ``record_hash`` itself."""
    material = {
        key: record[key]
        for key in sorted(record)
        if key != _HASH_FIELD
    }
    return sha256_digest(
        {
            "schema_version": WINDOW_REGISTRY_RECORD_HASH_SCHEMA,
            "record": material,
        }
    )


def registry_binding_wellformed_reason(
    binding: object,
) -> str | None:
    """Return ``None`` only for the exact closed receiver-binding type."""
    if type(binding) is not WindowRegistryBinding:
        return "binding_type"
    if not is_window_id(binding.window_id):
        return "window_id_shape"
    if not is_source_head(binding.source_head):
        return "source_head_shape"
    for field in (
        "binding_digest",
        "start_boundary_digest",
        "final_boundary_digest",
        "marker_path_digest",
        "marker_digest",
        "final_ledger_head",
    ):
        if not is_registry_digest(getattr(binding, field)):
            return f"{field}_shape"
    return None


def _binding_record_fields(
    binding: WindowRegistryBinding,
) -> dict[str, str]:
    return {
        field: str(getattr(binding, field))
        for field in _BINDING_FIELDS
    }


def _verdict_field(verdict: object, field: str) -> object:
    if isinstance(verdict, Mapping):
        return verdict.get(field)
    return getattr(verdict, field, None)


def _require_exact_verification_approval(
    approval: object,
    *,
    binding: WindowRegistryBinding,
    expected_phase: str,
    expected_marker_verified: bool,
) -> None:
    if type(approval) is not RegistryVerificationApproval:
        raise WindowRegistryVerificationError(
            "verification_approval_type_invalid"
        )
    if approval.binding != binding:
        raise WindowRegistryVerificationError(
            "verification_approval_binding_mismatch"
        )
    verdict = approval.verdict
    if type(verdict) is not ProductionWindowVerification:
        raise WindowRegistryVerificationError(
            "verification_verdict_type_invalid"
        )
    expected = {
        "ok": True,
        "phase": expected_phase,
        "reason": None,
        "marker_verified": expected_marker_verified,
        "measurement_only": True,
        "claim_safe_count": 0,
        "schema_version": PRODUCTION_WINDOW_VERIFICATION_SCHEMA,
    }
    for field, value in expected.items():
        observed = _verdict_field(verdict, field)
        if field in {"ok", "marker_verified", "measurement_only"}:
            matches = observed is value
        elif field == "claim_safe_count":
            matches = type(observed) is int and observed == 0
        else:
            matches = observed == value
        if not matches:
            raise WindowRegistryVerificationError(
                f"verification_verdict_invalid:{field}"
            )


def registry_record_wellformed_reason(record: object) -> str | None:
    """Return ``None`` only for a fully shaped, self-hash-consistent row."""
    if not isinstance(record, Mapping):
        return "not_a_mapping"
    event_type = record.get("event_type")
    expected_keys = (
        _RESERVATION_KEYS
        if event_type == RESERVED_PRE_MARKER
        else _FINALIZATION_KEYS
        if event_type == FINAL_VERIFIED
        else None
    )
    if expected_keys is None:
        return "event_type"
    if set(record) != expected_keys:
        return "key_set_mismatch"
    if record.get("schema_version") != WINDOW_REGISTRY_EVENT_SCHEMA:
        return "schema_version"
    sequence = record.get("sequence")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or sequence >= MAX_REGISTRY_RECORDS
    ):
        return "sequence_shape"
    if not is_window_id(record.get("window_id")):
        return "window_id_shape"
    if not is_source_head(record.get("source_head")):
        return "source_head_shape"
    if not is_registry_digest(record.get("binding_digest")):
        return "binding_digest_shape"
    for field in (
        "start_boundary_digest",
        "final_boundary_digest",
        "marker_path_digest",
        "marker_digest",
        "final_ledger_head",
    ):
        if not is_registry_digest(record.get(field)):
            return f"{field}_shape"
    if _parse_utc_timestamp(record.get("recorded_at_utc")) is None:
        return "recorded_at_utc_shape"
    if not is_registry_digest(record.get("prev_record_hash")):
        return "prev_record_hash_shape"
    if record.get("measurement_only") is not True:
        return "measurement_only"
    if type(record.get("claim_safe_count")) is not int or record.get(
        "claim_safe_count"
    ) != 0:
        return "claim_safe_count"
    if record.get("runtime_authority_granted") is not False:
        return "runtime_authority_granted"
    if event_type == FINAL_VERIFIED:
        if not is_registry_digest(record.get("reservation_hash")):
            return "reservation_hash_shape"
    if not is_registry_digest(record.get(_HASH_FIELD)):
        return "record_hash_shape"
    try:
        if record.get(_HASH_FIELD) != compute_registry_record_hash(record):
            return "record_hash_mismatch"
    except (KeyError, TypeError, ValueError):
        return "record_hash_not_canonical"
    return None


def _base_record(
    *,
    sequence: int,
    event_type: str,
    window_id: str,
    source_head: str,
    binding_digest: str,
    start_boundary_digest: str,
    final_boundary_digest: str,
    marker_path_digest: str,
    marker_digest: str,
    final_ledger_head: str,
    recorded_at_utc: str,
    prev_record_hash: str,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": WINDOW_REGISTRY_EVENT_SCHEMA,
        "sequence": sequence,
        "event_type": event_type,
        "window_id": window_id,
        "source_head": source_head,
        "binding_digest": binding_digest,
        "start_boundary_digest": start_boundary_digest,
        "final_boundary_digest": final_boundary_digest,
        "marker_path_digest": marker_path_digest,
        "marker_digest": marker_digest,
        "final_ledger_head": final_ledger_head,
        "recorded_at_utc": recorded_at_utc,
        "prev_record_hash": prev_record_hash,
        "measurement_only": True,
        "claim_safe_count": 0,
        "runtime_authority_granted": False,
    }
    return record


def build_window_reservation(
    *,
    sequence: int,
    window_id: str,
    source_head: str,
    binding_digest: str,
    start_boundary_digest: str,
    final_boundary_digest: str,
    marker_path_digest: str,
    marker_digest: str,
    final_ledger_head: str,
    recorded_at_utc: str,
    prev_record_hash: str,
) -> dict[str, Any]:
    """Build one receiver-pinned ``reserved_pre_marker`` row."""
    record = _base_record(
        sequence=sequence,
        event_type=RESERVED_PRE_MARKER,
        window_id=window_id,
        source_head=source_head,
        binding_digest=binding_digest,
        start_boundary_digest=start_boundary_digest,
        final_boundary_digest=final_boundary_digest,
        marker_path_digest=marker_path_digest,
        marker_digest=marker_digest,
        final_ledger_head=final_ledger_head,
        recorded_at_utc=recorded_at_utc,
        prev_record_hash=prev_record_hash,
    )
    record[_HASH_FIELD] = compute_registry_record_hash(record)
    reason = registry_record_wellformed_reason(record)
    if reason is not None:
        raise WindowRegistryError(f"reservation_invalid:{reason}")
    return record


def build_window_finalization(
    *,
    sequence: int,
    window_id: str,
    source_head: str,
    binding_digest: str,
    start_boundary_digest: str,
    final_boundary_digest: str,
    marker_path_digest: str,
    marker_digest: str,
    final_ledger_head: str,
    reservation_hash: str,
    recorded_at_utc: str,
    prev_record_hash: str,
) -> dict[str, Any]:
    """Build one receiver-pinned ``final_verified`` row."""
    record = _base_record(
        sequence=sequence,
        event_type=FINAL_VERIFIED,
        window_id=window_id,
        source_head=source_head,
        binding_digest=binding_digest,
        start_boundary_digest=start_boundary_digest,
        final_boundary_digest=final_boundary_digest,
        marker_path_digest=marker_path_digest,
        marker_digest=marker_digest,
        final_ledger_head=final_ledger_head,
        recorded_at_utc=recorded_at_utc,
        prev_record_hash=prev_record_hash,
    )
    record["reservation_hash"] = reservation_hash
    record[_HASH_FIELD] = compute_registry_record_hash(record)
    reason = registry_record_wellformed_reason(record)
    if reason is not None:
        raise WindowRegistryError(f"finalization_invalid:{reason}")
    return record


def verify_registry_chain(
    records: Sequence[Mapping[str, Any]],
) -> RegistryChainResult:
    """Verify hashes, ordering, uniqueness, and the two-state transition."""
    if len(records) > MAX_REGISTRY_RECORDS:
        return RegistryChainResult(False, MAX_REGISTRY_RECORDS, "record_bound")
    previous_hash = GENESIS_PREV_RECORD_HASH
    previous_timestamp: datetime | None = None
    reservations: dict[str, Mapping[str, Any]] = {}
    finalizations: set[str] = set()
    binding_digests: set[str] = set()
    start_boundary_digests: set[str] = set()
    final_boundary_digests: set[str] = set()
    marker_path_digests: set[str] = set()
    marker_digests: set[str] = set()
    final_ledger_heads: set[str] = set()
    for index, record in enumerate(records):
        reason = registry_record_wellformed_reason(record)
        if reason is not None:
            return RegistryChainResult(False, index, reason)
        if record.get("sequence") != index:
            return RegistryChainResult(False, index, "sequence_broken")
        if record.get("prev_record_hash") != previous_hash:
            return RegistryChainResult(False, index, "prev_record_hash_broken")
        recorded_at = _parse_utc_timestamp(record.get("recorded_at_utc"))
        assert recorded_at is not None
        if previous_timestamp is not None and recorded_at < previous_timestamp:
            return RegistryChainResult(False, index, "timestamp_regressed")
        previous_timestamp = recorded_at
        window_id = str(record["window_id"])
        binding_digest = str(record["binding_digest"])
        if record["event_type"] == RESERVED_PRE_MARKER:
            if window_id in reservations:
                return RegistryChainResult(False, index, "window_id_reused")
            if binding_digest in binding_digests:
                return RegistryChainResult(False, index, "binding_digest_reused")
            start_boundary_digest = str(record["start_boundary_digest"])
            if start_boundary_digest in start_boundary_digests:
                return RegistryChainResult(
                    False,
                    index,
                    "start_boundary_digest_reused",
                )
            final_boundary_digest = str(record["final_boundary_digest"])
            if final_boundary_digest in final_boundary_digests:
                return RegistryChainResult(
                    False,
                    index,
                    "final_boundary_digest_reused",
                )
            marker_path_digest = str(record["marker_path_digest"])
            if marker_path_digest in marker_path_digests:
                return RegistryChainResult(
                    False,
                    index,
                    "marker_path_digest_reused",
                )
            marker_digest = str(record["marker_digest"])
            if marker_digest in marker_digests:
                return RegistryChainResult(False, index, "marker_digest_reused")
            final_ledger_head = str(record["final_ledger_head"])
            if final_ledger_head in final_ledger_heads:
                return RegistryChainResult(
                    False,
                    index,
                    "final_ledger_head_reused",
                )
            reservations[window_id] = record
            binding_digests.add(binding_digest)
            start_boundary_digests.add(start_boundary_digest)
            final_boundary_digests.add(final_boundary_digest)
            marker_path_digests.add(marker_path_digest)
            marker_digests.add(marker_digest)
            final_ledger_heads.add(final_ledger_head)
        else:
            reservation = reservations.get(window_id)
            if reservation is None:
                return RegistryChainResult(False, index, "reservation_missing")
            if window_id in finalizations:
                return RegistryChainResult(False, index, "window_already_finalized")
            for field in _BINDING_FIELDS:
                if record.get(field) != reservation.get(field):
                    return RegistryChainResult(
                        False,
                        index,
                        f"{field}_pin_mismatch",
                    )
            if record.get("reservation_hash") != reservation.get(_HASH_FIELD):
                return RegistryChainResult(False, index, "reservation_hash_mismatch")
            finalizations.add(window_id)
        previous_hash = str(record[_HASH_FIELD])
    return RegistryChainResult(True, None, None)


def registry_head_hash(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return GENESIS_PREV_RECORD_HASH
    return str(records[-1].get(_HASH_FIELD))


def _path_is_link_or_reparse(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _guard_no_reparse_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _path_is_link_or_reparse(current):
                raise WindowRegistryPathError("path_reparse_not_allowed")
        except FileNotFoundError:
            break


def _windows_long_path(path: Path) -> Path:
    """Expand every existing 8.3 component while preserving a missing tail."""
    if os.name != "nt":
        return path
    import ctypes
    from ctypes import wintypes

    absolute = Path(os.path.abspath(os.fspath(path)))
    existing = absolute
    missing: list[str] = []
    while not os.path.lexists(os.fspath(existing)):
        parent = existing.parent
        if parent == existing:
            raise WindowRegistryPathError("path_canonicalization_failed")
        missing.append(existing.name)
        existing = parent

    get_long_path_name = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).GetLongPathNameW
    get_long_path_name.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_long_path_name.restype = wintypes.DWORD
    required = get_long_path_name(os.fspath(existing), None, 0)
    if required == 0:
        raise WindowRegistryPathError("path_canonicalization_failed")
    buffer = ctypes.create_unicode_buffer(required)
    written = get_long_path_name(
        os.fspath(existing),
        buffer,
        required,
    )
    if written == 0 or written >= required:
        raise WindowRegistryPathError("path_canonicalization_failed")
    canonical = Path(buffer.value)
    for part in reversed(missing):
        canonical /= part
    return Path(os.path.normpath(os.fspath(canonical)))


def _canonicalize_absolute_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if os.name == "nt":
        return _windows_long_path(absolute)
    return absolute


def _prepare_parent(path: Path) -> None:
    _guard_no_reparse_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_no_reparse_components(path.parent)
    try:
        details = os.lstat(path.parent)
    except OSError as exc:
        raise WindowRegistryPathError("registry_parent_unreadable") from exc
    if not stat.S_ISDIR(details.st_mode) or _path_is_link_or_reparse(path.parent):
        raise WindowRegistryPathError("registry_parent_invalid")


def _fsync_parent_directory(path: Path) -> None:
    """Persist a new POSIX directory entry after the created file is flushed."""
    if os.name == "nt":
        # Windows ``os.fsync`` maps to a file-handle flush which includes the
        # newly created file's metadata; opening directories with ``os.open``
        # is unsupported there.
        return
    if os.name != "posix":
        raise WindowRegistryError("parent_directory_fsync_unsupported")

    parent = path.parent
    _guard_no_reparse_components(parent)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(parent, flags)
    except OSError as exc:
        raise WindowRegistryError("parent_directory_fsync_failed") from exc
    try:
        before = os.fstat(fd)
        path_before = os.lstat(parent)
        before_identity = (int(before.st_dev), int(before.st_ino))
        if (
            not stat.S_ISDIR(before.st_mode)
            or not stat.S_ISDIR(path_before.st_mode)
            or before_identity
            != (int(path_before.st_dev), int(path_before.st_ino))
            or _path_is_link_or_reparse(parent)
        ):
            raise WindowRegistryPathError("registry_parent_identity_changed")
        os.fsync(fd)
        after = os.fstat(fd)
        path_after = os.lstat(parent)
        if (
            before_identity != (int(after.st_dev), int(after.st_ino))
            or before_identity
            != (int(path_after.st_dev), int(path_after.st_ino))
        ):
            raise WindowRegistryPathError("registry_parent_identity_changed")
    except OSError as exc:
        raise WindowRegistryError("parent_directory_fsync_failed") from exc
    finally:
        os.close(fd)


def _safe_identity(details: os.stat_result, *, label: str) -> tuple[int, int]:
    if not stat.S_ISREG(details.st_mode):
        raise WindowRegistryPathError(f"{label}_not_regular")
    if int(details.st_nlink) != 1:
        raise WindowRegistryPathError(f"{label}_hardlink_not_allowed")
    return (int(details.st_dev), int(details.st_ino))


def _fd_matches_path(
    fd: int,
    path: Path,
    *,
    label: str,
) -> tuple[os.stat_result, tuple[int, int]]:
    try:
        fd_details = os.fstat(fd)
        fd_identity = _safe_identity(fd_details, label=label)
        _guard_no_reparse_components(path)
        if _path_is_link_or_reparse(path):
            raise WindowRegistryPathError(f"{label}_reparse_not_allowed")
        path_details = os.lstat(path)
        path_identity = _safe_identity(path_details, label=label)
    except FileNotFoundError as exc:
        raise WindowRegistryPathError(f"{label}_path_missing") from exc
    if fd_identity != path_identity:
        raise WindowRegistryPathError(f"{label}_identity_changed")
    return fd_details, fd_identity


def _open_flags(base: int) -> int:
    return (
        base
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _thread_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    if os.name == "nt":
        key = key.casefold()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _try_native_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    raise WindowRegistryLockError("process_lock_unsupported")


def _release_native_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    if os.name == "posix":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    raise WindowRegistryLockError("process_lock_unsupported")


def _validate_lock_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise WindowRegistryLockError("lock_timeout_invalid") from exc
    if (
        not math.isfinite(timeout)
        or timeout < 0
        or timeout > MAX_LOCK_TIMEOUT_SECONDS
    ):
        raise WindowRegistryLockError("lock_timeout_invalid")
    return timeout


@contextmanager
def _exclusive_registry_lock(
    registry_path: Path,
    lock_path: Path,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    timeout = _validate_lock_timeout(timeout_seconds)
    started = time.monotonic()
    deadline = started + timeout
    thread_lock = _thread_lock_for(registry_path)
    if not thread_lock.acquire(timeout=timeout):
        raise WindowRegistryLockError("registry_lock_busy")
    fd: int | None = None
    native_locked = False
    try:
        _prepare_parent(lock_path)
        _guard_no_reparse_components(lock_path)
        if os.path.lexists(os.fspath(lock_path)) and _path_is_link_or_reparse(
            lock_path
        ):
            raise WindowRegistryPathError("lock_reparse_not_allowed")
        flags = _open_flags(os.O_RDWR | os.O_CREAT)
        fd = os.open(lock_path, flags, 0o600)
        lock_details, lock_identity = _fd_matches_path(
            fd,
            lock_path,
            label="lock",
        )
        if os.path.lexists(os.fspath(registry_path)):
            registry_details = os.lstat(registry_path)
            registry_identity = _safe_identity(
                registry_details,
                label="registry",
            )
            if registry_identity == lock_identity:
                raise WindowRegistryPathError("registry_lock_hardlink_overlap")
        if lock_details.st_size == 0:
            if os.write(fd, b"\0") != 1:
                raise WindowRegistryLockError("lock_initialization_short_write")
            os.fsync(fd)
        while True:
            if _try_native_lock(fd):
                native_locked = True
                locked_details, locked_identity = _fd_matches_path(
                    fd,
                    lock_path,
                    label="lock",
                )
                if (
                    locked_identity != lock_identity
                    or locked_details.st_size < 1
                ):
                    raise WindowRegistryPathError(
                        "lock_identity_changed_during_acquire"
                    )
                break
            if time.monotonic() >= deadline:
                raise WindowRegistryLockError("registry_lock_busy")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if fd is not None:
            if native_locked:
                try:
                    _release_native_lock(fd)
                except OSError:
                    pass
            os.close(fd)
        thread_lock.release()


def _decode_strict_json(raw: bytes) -> object:
    def object_without_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise WindowRegistryCorruptionError("duplicate_json_key")
            value[key] = item
        return value

    def reject_constant(_value: str) -> None:
        raise WindowRegistryCorruptionError("nonfinite_json_number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise WindowRegistryCorruptionError("nonfinite_json_number")
        return parsed

    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except WindowRegistryCorruptionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise WindowRegistryCorruptionError("jsonl_record_invalid") from exc


def _read_registry_unlocked(
    path: Path,
    *,
    max_file_bytes: int,
    max_line_bytes: int,
    max_records: int,
) -> _RegistryRead:
    _guard_no_reparse_components(path)
    if not os.path.lexists(os.fspath(path)):
        return _RegistryRead((), 0, None, None)
    if _path_is_link_or_reparse(path):
        raise WindowRegistryPathError("registry_reparse_not_allowed")
    fd = os.open(path, _open_flags(os.O_RDONLY))
    try:
        before, identity = _fd_matches_path(fd, path, label="registry")
        if before.st_size > max_file_bytes:
            raise WindowRegistryCorruptionError("registry_file_exceeds_bound")
        raw = bytearray()
        while len(raw) <= max_file_bytes:
            chunk = os.read(fd, min(64 * 1024, max_file_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > max_file_bytes:
            raise WindowRegistryCorruptionError("registry_file_exceeds_bound")
        after, after_identity = _fd_matches_path(fd, path, label="registry")
        if (
            after_identity != identity
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(raw) != before.st_size
        ):
            raise WindowRegistryCorruptionError("registry_changed_during_read")
    finally:
        os.close(fd)
    if not raw:
        return _RegistryRead((), 0, identity, before.st_mtime_ns)
    if raw.startswith(b"\xef\xbb\xbf"):
        raise WindowRegistryCorruptionError("jsonl_bom_not_allowed")
    if not raw.endswith(b"\n"):
        raise WindowRegistryCorruptionError("jsonl_torn_tail")
    byte_lines = bytes(raw).split(b"\n")[:-1]
    if len(byte_lines) > max_records:
        raise WindowRegistryCorruptionError("registry_record_bound_exceeded")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(byte_lines):
        if not line:
            raise WindowRegistryCorruptionError(
                f"jsonl_blank_line:{index + 1}"
            )
        if b"\r" in line:
            raise WindowRegistryCorruptionError(
                f"jsonl_cr_not_allowed:{index + 1}"
            )
        if len(line) + 1 > max_line_bytes:
            raise WindowRegistryCorruptionError(
                f"jsonl_line_exceeds_bound:{index + 1}"
            )
        value = _decode_strict_json(line)
        if type(value) is not dict:
            raise WindowRegistryCorruptionError(
                f"jsonl_object_required:{index + 1}"
            )
        try:
            canonical_line = canonical_json_bytes(value)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise WindowRegistryCorruptionError(
                f"jsonl_record_not_canonicalizable:{index + 1}"
            ) from exc
        if canonical_line != line:
            raise WindowRegistryCorruptionError(
                f"jsonl_noncanonical_record:{index + 1}"
            )
        records.append(value)
    chain = verify_registry_chain(records)
    if not chain.ok:
        raise WindowRegistryCorruptionError(
            f"registry_chain_invalid:{chain.broken_at}:{chain.reason}"
        )
    return _RegistryRead(
        tuple(records),
        len(raw),
        identity,
        before.st_mtime_ns,
    )


def _snapshot_from_read(read: _RegistryRead) -> WindowRegistrySnapshot:
    immutable_records = tuple(
        MappingProxyType(dict(record)) for record in read.records
    )
    reservations: dict[str, Mapping[str, Any]] = {}
    finalizations: dict[str, Mapping[str, Any]] = {}
    for record in immutable_records:
        window_id = str(record["window_id"])
        if record["event_type"] == RESERVED_PRE_MARKER:
            reservations[window_id] = record
        else:
            finalizations[window_id] = record
    return WindowRegistrySnapshot(
        records=immutable_records,
        consumed_window_ids=tuple(reservations),
        verified_window_ids=tuple(finalizations),
        reservations=MappingProxyType(reservations),
        finalizations=MappingProxyType(finalizations),
        head_hash=registry_head_hash(read.records),
        file_size_bytes=read.file_size_bytes,
    )


def _validate_bounds(
    *,
    max_file_bytes: int,
    max_line_bytes: int,
    max_records: int,
) -> tuple[int, int, int]:
    values = (max_file_bytes, max_line_bytes, max_records)
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        for value in values
    ):
        raise WindowRegistryError("registry_bounds_invalid")
    if (
        max_file_bytes > MAX_REGISTRY_BYTES
        or max_line_bytes > MAX_REGISTRY_LINE_BYTES
        or max_records > MAX_REGISTRY_RECORDS
        or max_line_bytes > max_file_bytes
    ):
        raise WindowRegistryError("registry_bounds_invalid")
    return values


def _append_record_unlocked(
    path: Path,
    record: Mapping[str, Any],
    *,
    prior: _RegistryRead,
    max_file_bytes: int,
    max_line_bytes: int,
    max_records: int,
) -> None:
    if len(prior.records) >= max_records:
        raise WindowRegistryError("registry_record_bound_exceeded")
    reason = registry_record_wellformed_reason(record)
    if reason is not None:
        raise WindowRegistryError(f"record_invalid:{reason}")
    line = canonical_json_bytes(record) + b"\n"
    if len(line) > max_line_bytes:
        raise WindowRegistryError("jsonl_line_exceeds_bound")
    if prior.file_size_bytes + len(line) > max_file_bytes:
        raise WindowRegistryError("registry_file_exceeds_bound")

    _prepare_parent(path)
    _guard_no_reparse_components(path)
    existed = prior.identity is not None
    if existed:
        if not os.path.lexists(os.fspath(path)):
            raise WindowRegistryPathError("registry_disappeared_before_append")
        flags = _open_flags(os.O_WRONLY | os.O_APPEND)
    else:
        if os.path.lexists(os.fspath(path)):
            raise WindowRegistryPathError("registry_appeared_before_append")
        flags = _open_flags(os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL)
    fd = os.open(path, flags, 0o600)
    try:
        before, identity = _fd_matches_path(fd, path, label="registry")
        if existed:
            if (
                identity != prior.identity
                or before.st_size != prior.file_size_bytes
                or before.st_mtime_ns != prior.mtime_ns
            ):
                raise WindowRegistryCorruptionError(
                    "registry_changed_before_append"
                )
        elif before.st_size != 0:
            raise WindowRegistryCorruptionError("new_registry_not_empty")
        remaining = line
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise WindowRegistryError("registry_short_write")
            remaining = remaining[written:]
        os.fsync(fd)
        after, after_identity = _fd_matches_path(fd, path, label="registry")
        if (
            after_identity != identity
            or after.st_size != prior.file_size_bytes + len(line)
        ):
            raise WindowRegistryCorruptionError(
                "registry_changed_during_append"
            )
        if not existed:
            _fsync_parent_directory(path)
    finally:
        os.close(fd)
    confirmed = _read_registry_unlocked(
        path,
        max_file_bytes=max_file_bytes,
        max_line_bytes=max_line_bytes,
        max_records=max_records,
    )
    if (
        confirmed.identity != identity
        or confirmed.file_size_bytes != prior.file_size_bytes + len(line)
        or confirmed.records[:-1] != prior.records
        or not confirmed.records
        or confirmed.records[-1] != dict(record)
    ):
        raise WindowRegistryCorruptionError(
            "registry_append_readback_mismatch"
        )


class ChatServedWindowRegistry:
    """Strict two-state receiver registry over one append-only JSONL path."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_file_bytes: int = MAX_REGISTRY_BYTES,
        max_line_bytes: int = MAX_REGISTRY_LINE_BYTES,
        max_records: int = MAX_REGISTRY_RECORDS,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(path, (str, os.PathLike)):
            raise WindowRegistryPathError("registry_path_invalid")
        raw_path = os.fspath(path)
        if not raw_path or "\x00" in raw_path:
            raise WindowRegistryPathError("registry_path_invalid")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise WindowRegistryPathError("registry_path_must_be_absolute")
        requested_path = Path(os.path.abspath(raw_path))
        _guard_no_reparse_components(requested_path)
        self._path = _canonicalize_absolute_path(requested_path)
        _guard_no_reparse_components(self._path)
        self._lock_path = self._path.with_name(f".{self._path.name}.lock")
        if self._path == self._lock_path:
            raise WindowRegistryPathError("registry_lock_path_overlap")
        (
            self._max_file_bytes,
            self._max_line_bytes,
            self._max_records,
        ) = _validate_bounds(
            max_file_bytes=max_file_bytes,
            max_line_bytes=max_line_bytes,
            max_records=max_records,
        )
        self._lock_timeout_seconds = _validate_lock_timeout(lock_timeout_seconds)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @contextmanager
    def _locked(self) -> Iterator[Path]:
        effective_path = _canonicalize_absolute_path(self._path)
        _guard_no_reparse_components(effective_path)
        effective_lock_path = effective_path.with_name(
            f".{effective_path.name}.lock"
        )
        with _exclusive_registry_lock(
            effective_path,
            effective_lock_path,
            timeout_seconds=self._lock_timeout_seconds,
        ):
            confirmed_path = _canonicalize_absolute_path(self._path)
            if confirmed_path != effective_path:
                raise WindowRegistryPathError(
                    "registry_path_identity_changed_during_lock"
                )
            yield effective_path

    def _read_unlocked(self, path: Path | None = None) -> _RegistryRead:
        return _read_registry_unlocked(
            self._path if path is None else path,
            max_file_bytes=self._max_file_bytes,
            max_line_bytes=self._max_line_bytes,
            max_records=self._max_records,
        )

    def snapshot(self) -> WindowRegistrySnapshot:
        """Read and fully verify the registry under the exclusive lock."""
        with self._locked() as effective_path:
            return _snapshot_from_read(self._read_unlocked(effective_path))

    def consumed_window_ids(self) -> tuple[str, ...]:
        return self.snapshot().consumed_window_ids

    def verified_window_ids(self) -> tuple[str, ...]:
        return self.snapshot().verified_window_ids

    @staticmethod
    def _binding_pin_mismatch(
        record: Mapping[str, Any],
        binding: WindowRegistryBinding,
    ) -> str | None:
        for field, value in _binding_record_fields(binding).items():
            if record.get(field) != value:
                return f"{field}_pin_mismatch"
        return None

    @staticmethod
    def _ensure_fresh_reservation_binding(
        snapshot: WindowRegistrySnapshot,
        binding: WindowRegistryBinding,
    ) -> None:
        if snapshot.is_consumed(binding.window_id):
            raise WindowRegistryReplayError("window_id_reused")
        unique_digest_fields = (
            "binding_digest",
            "start_boundary_digest",
            "final_boundary_digest",
            "marker_path_digest",
            "marker_digest",
            "final_ledger_head",
        )
        for row in snapshot.reservations.values():
            for field in unique_digest_fields:
                if row[field] == getattr(binding, field):
                    raise WindowRegistryReplayError(f"{field}_reused")

    @staticmethod
    def _require_unchanged_after_callback(
        before: _RegistryRead,
        after: _RegistryRead,
    ) -> None:
        if after != before:
            raise WindowRegistryCorruptionError(
                "registry_changed_during_verification"
            )

    @staticmethod
    def _invoke_verifier(
        callback: Callable[
            [tuple[str, ...]],
            RegistryVerificationApproval,
        ],
        prior_consumed_window_ids: tuple[str, ...],
    ) -> RegistryVerificationApproval:
        if not callable(callback):
            raise WindowRegistryVerificationError(
                "verification_callback_not_callable"
            )
        try:
            return callback(prior_consumed_window_ids)
        except Exception as exc:
            raise WindowRegistryVerificationError(
                "verification_callback_failed"
            ) from exc

    def reserve_after_verification(
        self,
        binding: WindowRegistryBinding,
        callback: Callable[
            [tuple[str, ...]],
            RegistryVerificationApproval,
        ],
    ) -> Mapping[str, Any]:
        """Verify under lock, then durably consume the ID before marker write.

        ``callback`` receives every previously consumed window ID and must run
        the independent verifier with ``clean_shutdown_marker=None``.  Only an
        exact ``pre_marker_verified`` approval for ``binding`` can be appended.
        """
        reason = registry_binding_wellformed_reason(binding)
        if reason is not None:
            raise WindowRegistryError(f"registry_binding_invalid:{reason}")
        with self._locked() as effective_path:
            prior = self._read_unlocked(effective_path)
            if len(prior.records) >= self._max_records:
                raise WindowRegistryError("registry_record_bound_exceeded")
            snapshot = _snapshot_from_read(prior)
            self._ensure_fresh_reservation_binding(snapshot, binding)
            approval = self._invoke_verifier(
                callback,
                snapshot.consumed_window_ids,
            )
            after_callback = self._read_unlocked(effective_path)
            self._require_unchanged_after_callback(prior, after_callback)
            _require_exact_verification_approval(
                approval,
                binding=binding,
                expected_phase="pre_marker_verified",
                expected_marker_verified=False,
            )
            timestamp = self._next_receiver_timestamp(prior)
            record = build_window_reservation(
                sequence=len(prior.records),
                **_binding_record_fields(binding),
                recorded_at_utc=timestamp,
                prev_record_hash=registry_head_hash(prior.records),
            )
            _append_record_unlocked(
                effective_path,
                record,
                prior=prior,
                max_file_bytes=self._max_file_bytes,
                max_line_bytes=self._max_line_bytes,
                max_records=self._max_records,
            )
            return MappingProxyType(dict(record))

    def finalize_after_verification(
        self,
        binding: WindowRegistryBinding,
        callback: Callable[
            [tuple[str, ...]],
            RegistryVerificationApproval,
        ],
    ) -> Mapping[str, Any]:
        """Finally verify and append under the same exclusive registry lock.

        The callback receives consumed IDs excluding the exact target
        reservation so the core freshness check does not reject the window it
        is finalizing.  It must re-read durable evidence and return an exact
        ``final_verified`` approval for the same binding.
        """
        reason = registry_binding_wellformed_reason(binding)
        if reason is not None:
            raise WindowRegistryError(f"registry_binding_invalid:{reason}")
        with self._locked() as effective_path:
            prior = self._read_unlocked(effective_path)
            if len(prior.records) >= self._max_records:
                raise WindowRegistryError("registry_record_bound_exceeded")
            snapshot = _snapshot_from_read(prior)
            reservation = snapshot.reservation_for(binding.window_id)
            if reservation is None:
                raise WindowRegistryStateError("reservation_missing")
            if snapshot.is_verified(binding.window_id):
                raise WindowRegistryReplayError("window_already_finalized")
            mismatch = self._binding_pin_mismatch(reservation, binding)
            if mismatch is not None:
                raise WindowRegistryStateError(mismatch)
            prior_ids = tuple(
                window_id
                for window_id in snapshot.consumed_window_ids
                if window_id != binding.window_id
            )
            approval = self._invoke_verifier(callback, prior_ids)
            after_callback = self._read_unlocked(effective_path)
            self._require_unchanged_after_callback(prior, after_callback)
            _require_exact_verification_approval(
                approval,
                binding=binding,
                expected_phase=FINAL_VERIFIED,
                expected_marker_verified=True,
            )
            timestamp = self._next_receiver_timestamp(prior)
            record = build_window_finalization(
                sequence=len(prior.records),
                **_binding_record_fields(binding),
                reservation_hash=str(reservation[_HASH_FIELD]),
                recorded_at_utc=timestamp,
                prev_record_hash=registry_head_hash(prior.records),
            )
            _append_record_unlocked(
                effective_path,
                record,
                prior=prior,
                max_file_bytes=self._max_file_bytes,
                max_line_bytes=self._max_line_bytes,
                max_records=self._max_records,
            )
            return MappingProxyType(dict(record))

    @staticmethod
    def _next_receiver_timestamp(
        prior: _RegistryRead,
    ) -> str:
        timestamp = _receiver_timestamp()
        current = _parse_utc_timestamp(timestamp)
        if current is None:
            raise WindowRegistryError("receiver_clock_invalid")
        if prior.records:
            previous = _parse_utc_timestamp(
                prior.records[-1]["recorded_at_utc"]
            )
            assert previous is not None
            if current <= previous:
                current = previous + timedelta(microseconds=1)
                timestamp = (
                    current.isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
        return timestamp


def read_window_registry(
    path: str | os.PathLike[str],
    **kwargs: Any,
) -> WindowRegistrySnapshot:
    """Functional wrapper for a fully verified registry snapshot."""
    return ChatServedWindowRegistry(path, **kwargs).snapshot()


__all__ = [
    "FINAL_VERIFIED",
    "GENESIS_PREV_RECORD_HASH",
    "MAX_LOCK_TIMEOUT_SECONDS",
    "MAX_REGISTRY_BYTES",
    "MAX_REGISTRY_LINE_BYTES",
    "MAX_REGISTRY_RECORDS",
    "REGISTRY_EVENT_TYPES",
    "RESERVED_PRE_MARKER",
    "WINDOW_MARKER_PATH_DIGEST_SCHEMA",
    "WINDOW_REGISTRY_EVENT_SCHEMA",
    "WINDOW_REGISTRY_RECORD_HASH_SCHEMA",
    "ChatServedWindowRegistry",
    "RegistryChainResult",
    "RegistryVerificationApproval",
    "WindowRegistryBinding",
    "WindowRegistryCorruptionError",
    "WindowRegistryError",
    "WindowRegistryLockError",
    "WindowRegistryPathError",
    "WindowRegistryReplayError",
    "WindowRegistrySnapshot",
    "WindowRegistryStateError",
    "WindowRegistryVerificationError",
    "build_window_finalization",
    "build_window_reservation",
    "compute_registry_record_hash",
    "derive_window_marker_path_digest",
    "is_registry_digest",
    "is_source_head",
    "is_window_id",
    "read_window_registry",
    "registry_binding_wellformed_reason",
    "registry_head_hash",
    "registry_record_wellformed_reason",
    "verify_registry_chain",
]
