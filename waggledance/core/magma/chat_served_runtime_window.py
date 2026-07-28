# SPDX-License-Identifier: BUSL-1.1
"""Process-lifecycle owner for opt-in chat-served measurement windows.

The coordinator is deliberately measurement-only. It can produce a v1 clean
shutdown marker only after bounded intake closure, task drain, durability,
cursor freezing, and independent verification. It never changes ``claim_safe``
and every failure leaves the current window without a valid clean marker.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.chat_served_claim_window_evidence import (
    CLAIM_WINDOW_SIDE_STREAMS,
    MAX_ENABLED_SAMPLES_PER_WINDOW,
    derive_enabled_sample_sequence_digest,
    new_claim_window_final_boundary,
    new_claim_window_start_boundary,
    new_clean_shutdown_marker_v1,
    read_latest_head_anchor,
    verify_claim_window_lifecycle_binding,
    write_head_anchor_checkpoint,
)
from waggledance.core.magma.chat_served_ledger import head_hash, read_entries
from waggledance.core.magma.chat_served_window_registry import (
    ChatServedWindowRegistry,
    RegistryVerificationApproval,
    WindowRegistryBinding,
    derive_window_marker_path_digest,
)

_SOURCE_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_SERVED_DIR_RE = re.compile(r"^served-[0-9a-f]{32}$")
_RECEIPT_INDEX_SCHEMA = "magma.chat_served_receipt_index_entry.v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_JSONL_BYTES = 64 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 64 * 1024
_MAX_BUNDLE_BYTES = 32 * 1024 * 1024
_MAX_BUNDLE_FILES = 32


@dataclass(frozen=True)
class ChatServedRuntimeWindowResult:
    """Path-free lifecycle result suitable for runtime status and logs."""

    status: str
    window_id: str
    reason: str | None
    lifecycle_verified: bool
    clean_marker_written: bool
    measurement_only: bool = True
    claim_safe_count: int = 0
    registry_state: str = "not_reserved"

    def public_summary(self) -> dict[str, object]:
        return {
            "status": self.status,
            "window_id": self.window_id,
            "reason": self.reason,
            "lifecycle_verified": self.lifecycle_verified,
            "clean_marker_written": self.clean_marker_written,
            "measurement_only": self.measurement_only,
            "claim_safe_count": self.claim_safe_count,
            "registry_state": self.registry_state,
        }


@dataclass(frozen=True)
class _ManifestSnapshot:
    manifest_ref: str
    receipt_ref: str
    fingerprint: str
    file_identities: frozenset[tuple[int, int]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("nonfinite_json_number")


def _decode_strict_json(raw: bytes | str) -> object:
    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(
        text,
        object_pairs_hook=_json_object_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _strict_json(path: Path) -> Mapping[str, Any]:
    _guard_no_reparse_components(path)
    raw = path.read_bytes()
    if len(raw) > _MAX_JSON_BYTES:
        raise ValueError("json_file_exceeds_bound")
    value = _decode_strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _strict_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    _guard_no_reparse_components(path)
    if not os.path.lexists(os.fspath(path)):
        return ()
    if _path_is_link_or_reparse(path) or not path.is_file():
        raise ValueError("jsonl_path_invalid")
    if path.stat().st_size > _MAX_JSONL_BYTES:
        raise ValueError("jsonl_file_exceeds_bound")
    records: list[Mapping[str, Any]] = []
    total_bytes = 0
    with open(path, "rb") as handle:
        for line in handle:
            total_bytes += len(line)
            if (
                len(line) > _MAX_JSONL_LINE_BYTES
                or total_bytes > _MAX_JSONL_BYTES
            ):
                raise ValueError("jsonl_line_or_file_exceeds_bound")
            if not line.strip():
                continue
            if len(records) >= MAX_ENABLED_SAMPLES_PER_WINDOW:
                raise ValueError("jsonl_record_bound_exceeded")
            value = _decode_strict_json(line)
            if not isinstance(value, dict):
                raise ValueError("jsonl_object_required")
            records.append(value)
    return tuple(records)


def _path_is_link_or_reparse(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _guard_no_reparse_components(path: Path) -> None:
    """Reject a symlink/junction in every existing lexical path component."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _path_is_link_or_reparse(current):
                raise ValueError("path_reparse_not_allowed")
        except FileNotFoundError:
            break


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create one evidence object without overwriting any prior artifact."""
    _guard_no_reparse_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_no_reparse_components(path.parent)
    _guard_no_reparse_components(path)
    if os.path.lexists(os.fspath(path)):
        raise FileExistsError(path)
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _guard_no_reparse_components(path)


def _write_clean_marker_last(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically publish the marker; the target stays absent on pre-rename errors."""
    _guard_no_reparse_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_no_reparse_components(path.parent)
    _guard_no_reparse_components(path)
    if os.path.lexists(os.fspath(path)):
        raise FileExistsError(path)
    payload = canonical_json_bytes(value) + b"\n"
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{id(value):x}.pending"
    )
    _guard_no_reparse_components(temporary)
    if os.path.lexists(os.fspath(temporary)):
        raise FileExistsError(temporary)
    try:
        with open(temporary, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _guard_no_reparse_components(path.parent)
        _guard_no_reparse_components(path)
        if os.path.lexists(os.fspath(path)):
            raise FileExistsError(path)
        # Deliberately the final operation: a raised pre-rename error leaves no
        # marker, and a successful same-directory rename publishes full bytes.
        os.rename(temporary, path)
    except Exception:
        try:
            if (
                os.path.lexists(os.fspath(temporary))
                and not _path_is_link_or_reparse(temporary)
            ):
                temporary.unlink()
        except Exception:
            pass
        raise


def _append_jsonl(path: Path, values: tuple[Mapping[str, Any], ...]) -> None:
    if not values:
        return
    _guard_no_reparse_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_no_reparse_components(path.parent)
    _guard_no_reparse_components(path)
    lines = tuple(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
        for value in values
    )
    if any(len(line.encode("utf-8")) > _MAX_JSONL_LINE_BYTES for line in lines):
        raise ValueError("jsonl_line_exceeds_bound")
    payload = "".join(lines)
    payload_bytes = len(payload.encode("utf-8"))
    existing_bytes = (
        path.stat().st_size
        if os.path.lexists(os.fspath(path))
        else 0
    )
    if existing_bytes + payload_bytes > _MAX_JSONL_BYTES:
        raise ValueError("jsonl_file_exceeds_bound")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _guard_no_reparse_components(path)


def _safe_posix_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and not path.drive
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _contained_regular_file(root: Path, relative_ref: str) -> Path:
    if not _safe_posix_relative(relative_ref):
        raise ValueError("unsafe_relative_ref")
    _guard_no_reparse_components(root)
    if _path_is_link_or_reparse(root):
        raise ValueError("receipt_root_reparse_not_allowed")
    root_resolved = root.resolve(strict=True)
    candidate = root.joinpath(*PurePosixPath(relative_ref).parts)
    current = root
    for part in PurePosixPath(relative_ref).parts:
        current = current / part
        if _path_is_link_or_reparse(current):
            raise ValueError("reparse_not_allowed")
    if not candidate.is_file():
        raise ValueError("contained_file_missing")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise ValueError("contained_file_escape")
    return candidate


def _bundle_fingerprint(
    bundle_dir: Path,
) -> tuple[str, frozenset[tuple[int, int]]]:
    files: list[tuple[str, bytes]] = []
    file_identities: set[tuple[int, int]] = set()
    total_bytes = 0
    pending = [bundle_dir]
    while pending:
        current_dir = pending.pop()
        _guard_no_reparse_components(current_dir)
        if _path_is_link_or_reparse(current_dir):
            raise ValueError("bundle_reparse_not_allowed")
        with os.scandir(current_dir) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            candidate = Path(entry.path)
            # CPython's Windows DirEntry.stat may report st_dev/st_ino as 0/0;
            # lstat supplies the stable file identity needed for hardlink checks.
            details = os.lstat(candidate)
            attributes = int(getattr(details, "st_file_attributes", 0))
            reparse_flag = int(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
                raise ValueError("bundle_reparse_not_allowed")
            if stat.S_ISDIR(details.st_mode):
                pending.append(candidate)
                continue
            if not stat.S_ISREG(details.st_mode):
                raise ValueError("bundle_nonregular_artifact")
            identity = (int(details.st_dev), int(details.st_ino))
            if identity in file_identities:
                raise ValueError("bundle_hardlink_not_allowed")
            file_identities.add(identity)
            relative = candidate.relative_to(bundle_dir).as_posix()
            if not _safe_posix_relative(relative):
                raise ValueError("bundle_relative_path_invalid")
            _guard_no_reparse_components(candidate)
            raw = candidate.read_bytes()
            total_bytes += len(raw)
            if len(files) >= _MAX_BUNDLE_FILES or total_bytes > _MAX_BUNDLE_BYTES:
                raise ValueError("bundle_snapshot_exceeds_bound")
            files.append((relative, raw))
    files.sort(key=lambda item: item[0])
    material = hashlib.sha256()
    for relative, raw in files:
        material.update(relative.encode("utf-8"))
        material.update(b"\0")
        material.update(len(raw).to_bytes(8, "big"))
        material.update(raw)
    return (
        "sha256:" + material.hexdigest(),
        frozenset(file_identities),
    )


def _scan_receipt_manifests(receipt_root: Path) -> dict[str, _ManifestSnapshot]:
    _guard_no_reparse_components(receipt_root.parent)
    _guard_no_reparse_components(receipt_root)
    receipt_root.mkdir(parents=True, exist_ok=True)
    _guard_no_reparse_components(receipt_root)
    if _path_is_link_or_reparse(receipt_root) or not receipt_root.is_dir():
        raise ValueError("receipt_root_invalid")
    snapshots: dict[str, _ManifestSnapshot] = {}
    namespace_file_identities: set[tuple[int, int]] = set()
    for bundle_dir in sorted(receipt_root.iterdir(), key=lambda path: path.name):
        if not bundle_dir.name.startswith("served-"):
            continue
        if (
            not _SAFE_SERVED_DIR_RE.fullmatch(bundle_dir.name)
            or _path_is_link_or_reparse(bundle_dir)
            or not bundle_dir.is_dir()
        ):
            raise ValueError("receipt_bundle_directory_invalid")
        manifest = _contained_regular_file(
            receipt_root, f"{bundle_dir.name}/manifest.json"
        )
        manifest_value = _strict_json(manifest)
        if set(manifest_value) != {"chain_id", "entries"}:
            raise ValueError("receipt_manifest_shape_invalid")
        entries = manifest_value.get("entries")
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("receipt_manifest_entries_invalid")
        entry = entries[0]
        if not isinstance(entry, dict) or set(entry) != {
            "receipt",
            "payload",
            "evaluation_result",
        }:
            raise ValueError("receipt_manifest_entry_shape_invalid")
        receipt_relative = entry.get("receipt")
        if not _safe_posix_relative(receipt_relative):
            raise ValueError("receipt_manifest_ref_invalid")
        receipt = _contained_regular_file(
            receipt_root,
            f"{bundle_dir.name}/{receipt_relative}",
        )
        receipt_value = _strict_json(receipt)
        manifest_ref = f"{bundle_dir.name}/manifest.json"
        fingerprint, file_identities = _bundle_fingerprint(bundle_dir)
        if namespace_file_identities.intersection(file_identities):
            raise ValueError("receipt_namespace_hardlink_not_allowed")
        namespace_file_identities.update(file_identities)
        snapshots[manifest_ref] = _ManifestSnapshot(
            manifest_ref=manifest_ref,
            receipt_ref=sha256_digest(receipt_value),
            fingerprint=fingerprint,
            file_identities=file_identities,
        )
    return snapshots


class ChatServedRuntimeWindow:
    """Coordinate one fresh process-bound measurement window."""

    def __init__(
        self,
        *,
        emitter: object,
        window_id: str,
        source_head: str,
        ledger_path: str,
        receipt_root: str,
        anchor_store_path: str,
        enabled_samples_path: str,
        served_point_observations_path: str,
        receipt_index_path: str,
        start_boundary_path: str,
        final_boundary_path: str,
        clean_shutdown_marker_path: str,
        verified_window_registry_path: str | None = None,
        enabled_probe: Callable[[], bool],
        sample_interval_seconds: float,
        max_sample_gap_seconds: int,
        drain_timeout_seconds: float,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._emitter = emitter
        self.window_id = window_id
        self._source_head = source_head
        self._ledger_path = Path(ledger_path)
        self._receipt_root = Path(receipt_root)
        self._anchor_store_path = Path(anchor_store_path)
        self._enabled_samples_path = Path(enabled_samples_path)
        self._served_point_observations_path = Path(
            served_point_observations_path
        )
        self._receipt_index_path = Path(receipt_index_path)
        self._start_boundary_path = Path(start_boundary_path)
        self._final_boundary_path = Path(final_boundary_path)
        self._clean_shutdown_marker_path = Path(clean_shutdown_marker_path)
        self._verified_window_registry_path = verified_window_registry_path
        self._window_registry: ChatServedWindowRegistry | None = None
        self._registry_state = "not_reserved"
        self._pending_failures_path = Path(
            str(getattr(emitter, "pending_failure_ledger_path", ""))
        )
        self._enabled_probe = enabled_probe
        self._sample_interval_seconds = sample_interval_seconds
        self._max_sample_gap_seconds = max_sample_gap_seconds
        self._drain_timeout_seconds = drain_timeout_seconds
        self._now_fn = now_fn
        self._last_timestamp: datetime | None = None
        self._last_enabled_state: bool | None = None
        self._sampler_stop = asyncio.Event()
        self._sampler_task: asyncio.Task[None] | None = None
        self._sample_lock = asyncio.Lock()
        self._failure_reason: str | None = None
        self._start_boundary: Mapping[str, Any] | None = None
        self._start_offsets: dict[str, int] | None = None
        self._start_ledger_count = 0
        self._start_manifests: dict[str, _ManifestSnapshot] = {}
        self._start_pending_append_failures = 0
        self._state = "new"
        self._result = ChatServedRuntimeWindowResult(
            status="not_started",
            window_id=window_id,
            reason=None,
            lifecycle_verified=False,
            clean_marker_written=False,
        )

    @property
    def result(self) -> ChatServedRuntimeWindowResult:
        return self._result

    def _next_timestamp(self) -> str:
        raw = self._now_fn()
        if not isinstance(raw, datetime) or raw.tzinfo is None:
            raise ValueError("runtime_window_clock_not_utc")
        value = raw.astimezone(timezone.utc)
        if value.utcoffset() != timedelta(0):
            raise ValueError("runtime_window_clock_not_utc")
        if self._last_timestamp is not None and value <= self._last_timestamp:
            value = self._last_timestamp + timedelta(microseconds=1)
        self._last_timestamp = value
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _ineligible(self, reason: str) -> ChatServedRuntimeWindowResult:
        # A configured evidence emitter must never keep writing after its sole
        # lifecycle owner has failed to establish a valid window. Closing only
        # measurement intake leaves chat serving itself fail-open.
        try:
            close_intake = getattr(self._emitter, "close_intake", None)
            if callable(close_intake):
                close_intake()
        except Exception:  # noqa: BLE001 - ineligibility remains fail-closed
            pass
        self._state = "ineligible"
        self._result = ChatServedRuntimeWindowResult(
            status="ineligible",
            window_id=self.window_id,
            reason=reason,
            lifecycle_verified=False,
            clean_marker_written=False,
            registry_state=self._registry_state,
        )
        return self._result

    def _emitter_head(self) -> str:
        value = getattr(self._emitter, "head")
        if callable(value):
            value = value()
        if not isinstance(value, str):
            raise ValueError("emitter_head_invalid")
        return value

    def _ledger_snapshot(self) -> tuple[tuple[Mapping[str, Any], ...], str]:
        entries, torn_tail = read_entries(str(self._ledger_path))
        if torn_tail:
            raise ValueError("ledger_torn_tail")
        materialized = tuple(entries)
        derived_head = head_hash(list(materialized))
        if self._emitter_head() != derived_head:
            raise ValueError("emitter_ledger_head_mismatch")
        return materialized, derived_head

    def _side_offsets(self) -> dict[str, int]:
        paths = {
            "enabled_state_samples": self._enabled_samples_path,
            "pending_append_failures": self._pending_failures_path,
            "receipt_index": self._receipt_index_path,
            "served_point_observations": self._served_point_observations_path,
        }
        if set(paths) != CLAIM_WINDOW_SIDE_STREAMS:
            raise ValueError("side_stream_set_mismatch")
        return {name: len(_strict_jsonl(path)) for name, path in paths.items()}

    def _reject_receipt_bundle_hardlink_aliases(
        self,
        manifests: Mapping[str, _ManifestSnapshot],
    ) -> None:
        protected_files = (
            self._ledger_path,
            self._anchor_store_path,
            self._enabled_samples_path,
            self._pending_failures_path,
            self._served_point_observations_path,
            self._receipt_index_path,
            self._start_boundary_path,
            self._final_boundary_path,
            self._clean_shutdown_marker_path,
            *self._registry_artifact_paths(),
        )
        evidence_identities = {
            (int(details.st_dev), int(details.st_ino))
            for path in protected_files
            if os.path.lexists(os.fspath(path))
            for details in (os.lstat(path),)
            if stat.S_ISREG(details.st_mode)
        }
        bundle_identities = {
            identity
            for snapshot in manifests.values()
            for identity in snapshot.file_identities
        }
        if evidence_identities.intersection(bundle_identities):
            raise ValueError("runtime_window_receipt_hardlink_overlap")

    def _registry_artifact_paths(self) -> tuple[Path, Path]:
        registry = self._window_registry
        if registry is None:
            raise RuntimeError("window_registry_unavailable")
        return registry.path, registry.lock_path

    def _validate_configured_paths(self) -> None:
        registry_files = self._registry_artifact_paths()
        producer_evidence_files = (
            self._ledger_path,
            self._anchor_store_path,
            self._enabled_samples_path,
            self._pending_failures_path,
            self._served_point_observations_path,
            self._receipt_index_path,
            self._start_boundary_path,
            self._final_boundary_path,
            self._clean_shutdown_marker_path,
        )
        protected_files = (*producer_evidence_files, *registry_files)
        _guard_no_reparse_components(self._receipt_root.parent)
        _guard_no_reparse_components(self._receipt_root)
        for path in protected_files:
            _guard_no_reparse_components(path.parent)
            _guard_no_reparse_components(path)
        lexical = {
            os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
            for path in protected_files
        }
        resolved = {
            os.path.normcase(
                os.path.abspath(os.fspath(path.resolve(strict=False)))
            ).casefold()
            for path in protected_files
        }
        identities = [
            (
                int(details.st_dev),
                int(details.st_ino),
            )
            for path in protected_files
            if os.path.lexists(os.fspath(path))
            for details in (os.lstat(path),)
        ]
        if (
            len(lexical) != len(protected_files)
            or len(resolved) != len(protected_files)
            or len(set(identities)) != len(identities)
        ):
            raise ValueError("runtime_window_paths_overlap")

        def normalized(path: Path, *, resolve: bool) -> Path:
            target = path.resolve(strict=False) if resolve else path
            return Path(
                os.path.normcase(
                    os.path.abspath(os.fspath(target))
                ).casefold()
            )

        for registry_path in registry_files:
            for evidence_path in producer_evidence_files:
                for resolve in (False, True):
                    registry_target = normalized(
                        registry_path,
                        resolve=resolve,
                    )
                    evidence_target = normalized(
                        evidence_path,
                        resolve=resolve,
                    )
                    if (
                        registry_target.is_relative_to(evidence_target.parent)
                        or evidence_target.is_relative_to(
                            registry_target.parent
                        )
                    ):
                        raise ValueError(
                            "runtime_window_registry_evidence_namespace_overlap"
                        )
        receipt_root = Path(
            os.path.abspath(
                os.fspath(self._receipt_root.resolve(strict=False))
            )
        )
        for path in registry_files:
            target = Path(
                os.path.abspath(os.fspath(path.resolve(strict=False)))
            )
            try:
                target.relative_to(receipt_root)
            except ValueError:
                continue
            raise ValueError("runtime_window_registry_receipt_namespace_overlap")
        for path in protected_files:
            target = Path(
                os.path.abspath(os.fspath(path.resolve(strict=False)))
            )
            try:
                relative = target.relative_to(receipt_root)
            except ValueError:
                continue
            if (
                relative.parts
                and relative.parts[0].casefold().startswith("served-")
            ):
                raise ValueError("runtime_window_receipt_namespace_overlap")

    @staticmethod
    def _new_enabled_sample(
        *,
        window_id: str,
        enabled: bool,
        ts_utc: str,
        sample_kind: str,
        previous_enabled: bool | None = None,
    ) -> Mapping[str, Any]:
        # Imported lazily so absence of the production v1 builder fails the
        # opt-in window closed without affecting default-off chat serving.
        from waggledance.core.magma import chat_served_claim_window_evidence as evidence

        builder = getattr(evidence, "new_enabled_state_sample", None)
        if builder is None:
            raise RuntimeError("runtime_enabled_sample_builder_unavailable")
        kwargs: dict[str, object] = {
            "window_id": window_id,
            "enabled": enabled,
            "ts_utc": ts_utc,
            "sample_kind": sample_kind,
        }
        if sample_kind == "transition":
            kwargs["previous_enabled"] = previous_enabled
        return builder(
            **kwargs,
        )

    async def _record_enabled_sample(
        self, *, sample_kind: str, ts_utc: str | None = None
    ) -> Mapping[str, Any]:
        async with self._sample_lock:
            if self._start_offsets is not None:
                current = len(_strict_jsonl(self._enabled_samples_path))
                if (
                    current - self._start_offsets["enabled_state_samples"]
                    >= MAX_ENABLED_SAMPLES_PER_WINDOW
                ):
                    raise ValueError("enabled_sample_bound_exceeded")
            enabled = self._enabled_probe()
            if not isinstance(enabled, bool):
                raise ValueError("enabled_probe_not_boolean")
            effective_kind = sample_kind
            previous_enabled = None
            if (
                sample_kind in {"cadence", "end"}
                and self._last_enabled_state is not None
                and enabled != self._last_enabled_state
            ):
                effective_kind = "transition"
                previous_enabled = self._last_enabled_state
            timestamp = ts_utc if ts_utc is not None else self._next_timestamp()
            sample = self._new_enabled_sample(
                window_id=self.window_id,
                enabled=enabled,
                ts_utc=timestamp,
                sample_kind=effective_kind,
                previous_enabled=previous_enabled,
            )
            await asyncio.to_thread(
                _append_jsonl,
                self._enabled_samples_path,
                (sample,),
            )
            self._last_enabled_state = enabled
            return sample

    async def _sample_periodically(self) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._sampler_stop.wait(),
                        timeout=self._sample_interval_seconds,
                    )
                    return
                except TimeoutError:
                    pass
                sample = await self._record_enabled_sample(
                    sample_kind="cadence",
                )
                if sample.get("enabled") is not True:
                    self._failure_reason = "enabled_transition_false"
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - claim fails closed.
            self._failure_reason = f"enabled_sampler_failed:{type(exc).__name__}"

    async def start(self) -> ChatServedRuntimeWindowResult:
        if self._state != "new":
            return self._result
        try:
            if getattr(self._emitter, "enabled", False) is not True:
                return self._ineligible("emitter_unavailable")
            if _SOURCE_HEAD_RE.fullmatch(self._source_head) is None:
                return self._ineligible("source_head_invalid")
            if (
                not math.isfinite(self._sample_interval_seconds)
                or self._sample_interval_seconds <= 0
                or not isinstance(self._max_sample_gap_seconds, int)
                or isinstance(self._max_sample_gap_seconds, bool)
                or not 0 < self._max_sample_gap_seconds <= 86_400
                or self._sample_interval_seconds > self._max_sample_gap_seconds
                or not math.isfinite(self._drain_timeout_seconds)
                or self._drain_timeout_seconds <= 0
            ):
                return self._ineligible("runtime_window_timing_invalid")
            if (
                not isinstance(self._verified_window_registry_path, str)
                or not self._verified_window_registry_path
                or "\x00" in self._verified_window_registry_path
                or not Path(self._verified_window_registry_path).is_absolute()
            ):
                return self._ineligible("window_registry_path_invalid")
            self._window_registry = ChatServedWindowRegistry(
                self._verified_window_registry_path
            )
            self._validate_configured_paths()
            registry_snapshot = await asyncio.to_thread(
                self._window_registry.snapshot
            )
            self._validate_configured_paths()
            if (
                registry_snapshot.is_consumed(self.window_id)
                or registry_snapshot.is_verified(self.window_id)
            ):
                return self._ineligible("window_registry_window_id_reused")
            for exclusive_path in (
                self._start_boundary_path,
                self._final_boundary_path,
                self._clean_shutdown_marker_path,
            ):
                if os.path.lexists(os.fspath(exclusive_path)):
                    return self._ineligible("preexisting_window_artifact")

            ledger_entries, ledger_head = self._ledger_snapshot()
            pending_append_failures = getattr(
                self._emitter, "pending_append_failures", None
            )
            if (
                not isinstance(pending_append_failures, int)
                or isinstance(pending_append_failures, bool)
                or pending_append_failures != 0
            ):
                return self._ineligible("pending_append_failure_counter_nonzero")
            self._start_pending_append_failures = pending_append_failures
            self._start_ledger_count = len(ledger_entries)
            self._start_offsets = self._side_offsets()
            self._start_manifests = await asyncio.to_thread(
                _scan_receipt_manifests,
                self._receipt_root,
            )
            self._reject_receipt_bundle_hardlink_aliases(
                self._start_manifests
            )
            boundary_timestamp = self._next_timestamp()
            initial_sample = await self._record_enabled_sample(
                sample_kind="start",
                ts_utc=boundary_timestamp,
            )
            if initial_sample.get("enabled") is not True:
                return self._ineligible("initial_enabled_sample_false")
            self._start_boundary = new_claim_window_start_boundary(
                window_id=self.window_id,
                start_ledger_head=ledger_head,
                start_ledger_offset=self._start_ledger_count,
                side_stream_offsets=self._start_offsets,
                source_head=self._source_head,
                start_enabled_sample_digest=str(initial_sample["sample_hash"]),
                max_enabled_sample_gap_seconds=self._max_sample_gap_seconds,
                ts_utc=boundary_timestamp,
            )
            await asyncio.to_thread(
                _write_exclusive_json,
                self._start_boundary_path,
                self._start_boundary,
            )
            self._state = "running"
            self._sampler_task = asyncio.create_task(self._sample_periodically())
            self._result = ChatServedRuntimeWindowResult(
                status="running",
                window_id=self.window_id,
                reason=None,
                lifecycle_verified=False,
                clean_marker_written=False,
            )
            return self._result
        except Exception as exc:  # noqa: BLE001 - serving remains fail-open.
            return self._ineligible(f"runtime_window_start_failed:{type(exc).__name__}")

    async def _stop_sampler(self) -> None:
        self._sampler_stop.set()
        task = self._sampler_task
        if task is None:
            return
        await task

    @staticmethod
    def _drain_succeeded(result: object) -> bool:
        if not isinstance(result, Mapping):
            return False
        return (
            result.get("status") == "drained"
            and result.get("reason") is None
            and result.get("intake_closed") is True
            and result.get("pending") == 0
            and result.get("failed") == 0
            and result.get("cancelled") == 0
            and result.get("post_close_attempts") == 0
            and result.get("schedule_failures") == 0
            and result.get("timed_out") is False
            and result.get("caller_cancelled") is False
        )

    def _receipt_index_entries(
        self,
        *,
        ledger_entries: tuple[Mapping[str, Any], ...],
        final_manifests: Mapping[str, _ManifestSnapshot],
    ) -> tuple[Mapping[str, Any], ...]:
        if set(self._start_manifests) - set(final_manifests):
            raise ValueError("receipt_manifest_removed")
        for manifest_ref, snapshot in self._start_manifests.items():
            if final_manifests[manifest_ref].fingerprint != snapshot.fingerprint:
                raise ValueError("preexisting_receipt_bundle_changed")

        new_manifests = {
            ref: snapshot
            for ref, snapshot in final_manifests.items()
            if ref not in self._start_manifests
        }
        receipt_terminals: dict[str, str] = {}
        for entry in ledger_entries:
            if entry.get("entry_type") != "receipt":
                continue
            receipt_ref = entry.get("receipt_ref")
            served_id = entry.get("served_id")
            if (
                not isinstance(receipt_ref, str)
                or not isinstance(served_id, str)
                or receipt_ref in receipt_terminals
            ):
                raise ValueError("receipt_terminal_index_invalid")
            receipt_terminals[receipt_ref] = served_id
        indexed_refs = [snapshot.receipt_ref for snapshot in new_manifests.values()]
        if (
            len(indexed_refs) != len(set(indexed_refs))
            or set(indexed_refs) != set(receipt_terminals)
        ):
            raise ValueError("receipt_index_not_bijective")

        from waggledance.core.magma import chat_served_claim_window_evidence as evidence

        builder = getattr(evidence, "new_receipt_index_entry", None)
        if builder is None:
            raise RuntimeError("receipt_index_builder_unavailable")
        rows = [
            builder(
                window_id=self.window_id,
                served_id=receipt_terminals[snapshot.receipt_ref],
                receipt_ref=snapshot.receipt_ref,
                manifest_ref=manifest_ref,
            )
            for manifest_ref, snapshot in new_manifests.items()
        ]
        rows.sort(
            key=lambda row: (
                str(row["manifest_ref"]),
                str(row["served_id"]),
                str(row["receipt_ref"]),
            )
        )
        if any(row.get("schema_version") != _RECEIPT_INDEX_SCHEMA for row in rows):
            raise ValueError("receipt_index_schema_invalid")
        return tuple(rows)

    @staticmethod
    def _canonical_verify_callbacks(receipt_root: Path):
        from tools.verify_magma_receipt import verify_manifest
        from waggledance.core.magma.chat_served_receipt import (
            validate_chat_served_payload,
        )

        def resolve_receipt_bundle(manifest_ref: str):
            try:
                manifest_path = _contained_regular_file(receipt_root, manifest_ref)
                report = verify_manifest(manifest_path)
                if not isinstance(report, Mapping) or report.get("ok") is not True:
                    return None
                manifest = _strict_json(manifest_path)
                entries = manifest.get("entries")
                if not isinstance(entries, list) or len(entries) != 1:
                    return None
                entry = entries[0]
                if not isinstance(entry, dict) or set(entry) != {
                    "receipt",
                    "payload",
                    "evaluation_result",
                }:
                    return None
                bundle_ref = PurePosixPath(manifest_ref).parent
                receipt_ref = str(bundle_ref / str(entry["receipt"]))
                payload_ref = str(bundle_ref / str(entry["payload"]))
                return {
                    "receipt": _strict_json(
                        _contained_regular_file(receipt_root, receipt_ref)
                    ),
                    "payload": _strict_json(
                        _contained_regular_file(receipt_root, payload_ref)
                    ),
                }
            except Exception:  # noqa: BLE001 - independent verification fails closed.
                return None

        def verify_receipt_bundle(bundle: Mapping[str, Any]) -> bool:
            if not isinstance(bundle, Mapping) or set(bundle) != {
                "receipt",
                "payload",
            }:
                return False
            receipt = bundle.get("receipt")
            payload = bundle.get("payload")
            if not isinstance(receipt, Mapping) or not isinstance(payload, Mapping):
                return False
            try:
                validate_chat_served_payload(payload)
            except (TypeError, ValueError, OverflowError):
                return False
            return receipt.get("canonical_payload_digest") == sha256_digest(payload)

        def content_address_receipt(receipt: Mapping[str, Any]) -> str:
            return sha256_digest(receipt)

        return (
            resolve_receipt_bundle,
            verify_receipt_bundle,
            content_address_receipt,
        )

    async def shutdown(self) -> ChatServedRuntimeWindowResult:
        if self._state != "running":
            return self._result
        self._state = "stopping"
        try:
            shutdown_failure: str | None = None
            close_intake = getattr(self._emitter, "close_intake", None)
            try:
                intake_closed = (
                    callable(close_intake) and close_intake() is True
                )
            except Exception:
                intake_closed = False
            if not intake_closed:
                shutdown_failure = "intake_close_failed"
            try:
                await self._stop_sampler()
            except Exception:
                shutdown_failure = shutdown_failure or "sampler_stop_failed"
            if self._failure_reason is not None:
                shutdown_failure = shutdown_failure or self._failure_reason

            drain = getattr(self._emitter, "drain", None)
            if not callable(drain):
                shutdown_failure = shutdown_failure or "drain_unavailable"
            else:
                try:
                    drain_result = drain(self._drain_timeout_seconds)
                    if inspect.isawaitable(drain_result):
                        drain_result = await drain_result
                    if not self._drain_succeeded(drain_result):
                        shutdown_failure = shutdown_failure or "drain_incomplete"
                except Exception:
                    shutdown_failure = shutdown_failure or "drain_failed"
            pending_append_failures = getattr(
                self._emitter, "pending_append_failures", None
            )
            if (
                not isinstance(pending_append_failures, int)
                or isinstance(pending_append_failures, bool)
                or pending_append_failures
                != self._start_pending_append_failures
                or pending_append_failures != 0
            ):
                shutdown_failure = (
                    shutdown_failure
                    or "pending_append_failure_counter_changed"
                )

            flush_sink = getattr(self._emitter, "flush_sink", None)
            try:
                if not callable(flush_sink) or flush_sink() is not True:
                    shutdown_failure = shutdown_failure or "sink_flush_failed"
            except Exception:
                shutdown_failure = shutdown_failure or "sink_flush_failed"
            if shutdown_failure is not None:
                raise RuntimeError(shutdown_failure)

            final_timestamp = self._next_timestamp()
            final_sample = await self._record_enabled_sample(
                sample_kind="end",
                ts_utc=final_timestamp,
            )
            if (
                final_sample.get("enabled") is not True
                or final_sample.get("sample_kind") != "end"
            ):
                raise RuntimeError("final_enabled_sample_false")

            all_ledger_entries, final_ledger_head = self._ledger_snapshot()
            ledger_segment = all_ledger_entries[self._start_ledger_count :]
            final_manifests = await asyncio.to_thread(
                _scan_receipt_manifests,
                self._receipt_root,
            )
            self._reject_receipt_bundle_hardlink_aliases(final_manifests)
            index_rows = self._receipt_index_entries(
                ledger_entries=ledger_segment,
                final_manifests=final_manifests,
            )
            await asyncio.to_thread(
                _append_jsonl,
                self._receipt_index_path,
                index_rows,
            )

            if self._start_offsets is None or self._start_boundary is None:
                raise RuntimeError("start_state_missing")
            final_offsets = self._side_offsets()
            side_records = {
                "enabled_state_samples": _strict_jsonl(
                    self._enabled_samples_path
                ),
                "pending_append_failures": _strict_jsonl(
                    self._pending_failures_path
                ),
                "receipt_index": _strict_jsonl(self._receipt_index_path),
                "served_point_observations": _strict_jsonl(
                    self._served_point_observations_path
                ),
            }
            slices = {
                name: records[self._start_offsets[name] : final_offsets[name]]
                for name, records in side_records.items()
            }
            for name in CLAIM_WINDOW_SIDE_STREAMS:
                if len(slices[name]) != (
                    final_offsets[name] - self._start_offsets[name]
                ):
                    raise RuntimeError(f"side_stream_cursor_mismatch:{name}")

            write_head_anchor_checkpoint(
                str(self._anchor_store_path),
                str(self._ledger_path),
                window_id=self.window_id,
                ts_utc=final_timestamp,
                fsync=True,
            )
            anchor = read_latest_head_anchor(
                str(self._anchor_store_path),
                str(self._ledger_path),
                window_id=self.window_id,
            )
            if not anchor.ok or anchor.expected_head != final_ledger_head:
                raise RuntimeError("final_head_checkpoint_invalid")
            anchor_records = _strict_jsonl(self._anchor_store_path)

            enabled_samples = slices["enabled_state_samples"]
            sequence_digest = derive_enabled_sample_sequence_digest(
                enabled_samples,
                window_id=self.window_id,
            )
            if sequence_digest is None:
                raise RuntimeError("enabled_sample_sequence_invalid")
            self._final_boundary = new_claim_window_final_boundary(
                window_id=self.window_id,
                start_boundary_digest=str(
                    self._start_boundary["boundary_hash"]
                ),
                final_ledger_head=final_ledger_head,
                final_ledger_offset=len(all_ledger_entries),
                side_stream_offsets=final_offsets,
                end_enabled_sample_digest=str(final_sample["sample_hash"]),
                enabled_samples_count=len(enabled_samples),
                enabled_sample_sequence_digest=sequence_digest,
                ts_utc=final_timestamp,
            )
            await asyncio.to_thread(
                _write_exclusive_json,
                self._final_boundary_path,
                self._final_boundary,
            )

            from waggledance.core.magma import chat_served_claim_window_evidence as evidence

            marker_timestamp = self._next_timestamp()
            marker = new_clean_shutdown_marker_v1(
                window_id=self.window_id,
                start_boundary_digest=str(
                    self._start_boundary["boundary_hash"]
                ),
                final_boundary_digest=str(
                    self._final_boundary["boundary_hash"]
                ),
                final_ledger_head=final_ledger_head,
                end_enabled_sample_digest=str(final_sample["sample_hash"]),
                ts_utc=marker_timestamp,
            )
            lifecycle = verify_claim_window_lifecycle_binding(
                start_boundary=self._start_boundary,
                final_boundary=self._final_boundary,
                clean_shutdown_marker=marker,
                enabled_samples=enabled_samples,
                window_id=self.window_id,
            )
            if not lifecycle.ok:
                raise RuntimeError(
                    f"lifecycle_binding_invalid:{lifecycle.reason}"
                )
            binding = WindowRegistryBinding(
                window_id=self.window_id,
                source_head=self._source_head,
                binding_digest=(
                    evidence.derive_production_window_registry_binding_digest(
                        expected_window_id=self.window_id,
                        expected_source_head=self._source_head,
                        start_boundary=self._start_boundary,
                        final_boundary=self._final_boundary,
                        clean_shutdown_marker=marker,
                        ledger_entries=ledger_segment,
                        enabled_samples=enabled_samples,
                        pending_failures=slices["pending_append_failures"],
                        receipt_index=slices["receipt_index"],
                        served_point_observations=slices[
                            "served_point_observations"
                        ],
                    )
                ),
                start_boundary_digest=str(
                    self._start_boundary["boundary_hash"]
                ),
                final_boundary_digest=str(
                    self._final_boundary["boundary_hash"]
                ),
                marker_digest=str(marker["marker_hash"]),
                marker_path_digest=derive_window_marker_path_digest(
                    self._clean_shutdown_marker_path
                ),
                final_ledger_head=final_ledger_head,
            )
            verifier = getattr(evidence, "verify_production_window", None)
            if verifier is None:
                raise RuntimeError("production_window_verifier_unavailable")
            callbacks = self._canonical_verify_callbacks(self._receipt_root)

            def verify_before_reservation(
                prior_consumed_window_ids: tuple[str, ...],
            ) -> RegistryVerificationApproval:
                verification = verifier(
                    expected_window_id=self.window_id,
                    expected_source_head=self._source_head,
                    start_boundary=self._start_boundary,
                    final_boundary=self._final_boundary,
                    clean_shutdown_marker=None,
                    ledger_entries=ledger_segment,
                    enabled_samples=enabled_samples,
                    pending_failures=slices["pending_append_failures"],
                    receipt_index=slices["receipt_index"],
                    served_point_observations=slices[
                        "served_point_observations"
                    ],
                    resolve_receipt_bundle=callbacks[0],
                    verify_receipt_bundle=callbacks[1],
                    content_address_receipt=callbacks[2],
                    previously_verified_window_ids=prior_consumed_window_ids,
                )
                return RegistryVerificationApproval(
                    binding=binding,
                    verdict=verification,
                )

            registry = self._window_registry
            if registry is None:
                raise RuntimeError("window_registry_unavailable")
            self._registry_state = "reservation_pending_or_unknown"
            reservation = await asyncio.to_thread(
                registry.reserve_after_verification,
                binding,
                verify_before_reservation,
            )
            self._registry_state = "reserved_pre_marker"

            # The verifier consumes in-memory slices and invokes receipt callbacks
            # while the registry lock is held. The reservation append and fsync
            # happen before this full freeze. Re-check every durable input and the
            # exact reservation so callback-time, append-time, or concurrent local
            # mutation cannot still receive a clean marker. No await follows these
            # reads, so event-loop serving cannot interleave before the marker's
            # atomic rename.
            self._validate_configured_paths()
            frozen_ledger, frozen_head = self._ledger_snapshot()
            if (
                frozen_ledger != all_ledger_entries
                or frozen_head != final_ledger_head
            ):
                raise RuntimeError("ledger_mutated_after_verification")
            frozen_side_records = {
                "enabled_state_samples": _strict_jsonl(
                    self._enabled_samples_path
                ),
                "pending_append_failures": _strict_jsonl(
                    self._pending_failures_path
                ),
                "receipt_index": _strict_jsonl(self._receipt_index_path),
                "served_point_observations": _strict_jsonl(
                    self._served_point_observations_path
                ),
            }
            frozen_offsets = {
                name: len(records)
                for name, records in frozen_side_records.items()
            }
            if frozen_offsets != final_offsets:
                raise RuntimeError("side_stream_mutated_after_verification")
            for name, records in frozen_side_records.items():
                if (
                    records[
                        self._start_offsets[name] : final_offsets[name]
                    ]
                    != slices[name]
                ):
                    raise RuntimeError(
                        f"side_stream_slice_mutated_after_verification:{name}"
                    )
            frozen_manifests = _scan_receipt_manifests(self._receipt_root)
            if frozen_manifests != final_manifests:
                raise RuntimeError("receipt_bundle_mutated_after_verification")
            self._reject_receipt_bundle_hardlink_aliases(frozen_manifests)
            if (
                _strict_json(self._start_boundary_path)
                != self._start_boundary
                or _strict_json(self._final_boundary_path)
                != self._final_boundary
            ):
                raise RuntimeError("boundary_mutated_after_verification")
            if _strict_jsonl(self._anchor_store_path) != anchor_records:
                raise RuntimeError("head_anchor_mutated_after_verification")
            frozen_anchor = read_latest_head_anchor(
                str(self._anchor_store_path),
                str(self._ledger_path),
                window_id=self.window_id,
            )
            if (
                not frozen_anchor.ok
                or frozen_anchor.expected_head != final_ledger_head
            ):
                raise RuntimeError("head_anchor_invalid_after_verification")
            if (
                getattr(self._emitter, "pending_append_failures", None) != 0
                or getattr(self._emitter, "post_close_attempts", None) != 0
            ):
                raise RuntimeError("emitter_latch_changed_after_verification")
            registry_snapshot = registry.snapshot()
            frozen_reservation = registry_snapshot.reservation_for(
                self.window_id
            )
            if (
                frozen_reservation is None
                or dict(frozen_reservation) != dict(reservation)
                or registry_snapshot.finalization_for(self.window_id)
                is not None
            ):
                raise RuntimeError("window_registry_reservation_changed")

            success_result = ChatServedRuntimeWindowResult(
                status="complete",
                window_id=self.window_id,
                reason=None,
                lifecycle_verified=True,
                clean_marker_written=True,
                registry_state="reserved_pre_marker",
            )
            # LAST durable mutation: no success-sensitive operation follows.
            _write_clean_marker_last(self._clean_shutdown_marker_path, marker)
            self._state = "complete"
            self._result = success_result
            return self._result
        except Exception as exc:  # noqa: BLE001 - shutdown and serving stay fail-open.
            self._sampler_stop.set()
            task = self._sampler_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            detail = str(exc)
            if (
                not isinstance(exc, RuntimeError)
                or re.fullmatch(r"[a-z0-9_:]+", detail) is None
            ):
                detail = type(exc).__name__
            return self._ineligible(f"runtime_window_shutdown_failed:{detail}")


__all__ = [
    "ChatServedRuntimeWindow",
    "ChatServedRuntimeWindowResult",
]
