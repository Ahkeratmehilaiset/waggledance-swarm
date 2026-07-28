# SPDX-License-Identifier: BUSL-1.1
"""Primitive evidence producers for chat-served claim-window gates.

This module is a dormant measurement layer for the outer gates in
``chat_served_accounting``. It produces the inputs that RCO review required to be
derived from concrete signals instead of caller booleans:

* expected ledger head from a separate append-only anchor store;
* enabled-across-window from hashed enabled-state samples;
* clean shutdown from a hashed shutdown marker;
* served-point completeness from hashed instrumentation observations.

It never flips ``claim_safe`` and does not enable runtime collection by itself.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, NamedTuple
from uuid import uuid4

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import NORMALIZATION_VERSION
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
    ClaimWindowReport,
    claim_window_from_ledger,
)
from waggledance.core.magma.chat_served_ledger import (
    GAP_TERMINAL,
    GENESIS_PREV_HASH,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    head_hash,
    is_ledger_hash,
    is_path_safe_token,
    read_entries,
    verify_entry_self,
    wellformed_reason,
)
from waggledance.core.magma.chat_served_receipt import validate_chat_served_payload
from waggledance.core.magma.chat_served_metadata import is_conforming_token

HEAD_ANCHOR_SCHEMA = "magma.chat_served_head_anchor.v0"
CLEAN_SHUTDOWN_MARKER_SCHEMA = "magma.chat_served_clean_shutdown_marker.v0"
ENABLED_STATE_SAMPLE_SCHEMA = "magma.chat_served_enabled_state_sample.v0"
ENABLED_STATE_SAMPLE_SCHEMA_V1 = "magma.chat_served_enabled_state_sample.v1"
SERVED_POINT_OBSERVATION_SCHEMA = "magma.chat_served_point_observation.v0"
CLAIM_WINDOW_START_BOUNDARY_SCHEMA = (
    "magma.chat_served_claim_window_start_boundary.v1"
)
CLAIM_WINDOW_FINAL_BOUNDARY_SCHEMA = (
    "magma.chat_served_claim_window_final_boundary.v1"
)
CLEAN_SHUTDOWN_MARKER_SCHEMA_V1 = "magma.chat_served_clean_shutdown_marker.v1"
SERVED_POINT_OBSERVATION_SCHEMA_V1 = "magma.chat_served_point_observation.v1"
ENABLED_SAMPLE_SEQUENCE_SCHEMA = "magma.chat_served_enabled_sample_sequence.v1"
RECEIPT_INDEX_ENTRY_SCHEMA_V1 = "magma.chat_served_receipt_index_entry.v1"
RECEIPT_INDEX_ENTRY_HASH_DOMAIN = (
    "magma.chat_served_receipt_index_entry_hash.v1"
)
PRODUCTION_WINDOW_VERIFICATION_SCHEMA = (
    "magma.chat_served_production_window_verification.v2"
)
PRODUCTION_WINDOW_COUNTER_AUTHORITY = "measurement_only"
PRODUCTION_WINDOW_REGISTRY_BINDING_SCHEMA = (
    "magma.chat_served_production_window_registry_binding.v1"
)

CLAIM_WINDOW_SIDE_STREAMS = frozenset({
    "enabled_state_samples",
    "pending_append_failures",
    "receipt_index",
    "served_point_observations",
})
MAX_DECLARED_SAMPLE_GAP_SECONDS = 86_400
MAX_ENABLED_SAMPLES_PER_WINDOW = 100_000
MAX_PRODUCTION_WINDOW_RECORDS = 100_000
MAX_PRIVACY_SCAN_DEPTH = 64
MAX_PRIVACY_SCAN_NODES = 1_000_000
ENABLED_SAMPLE_KINDS_V1 = frozenset({"start", "cadence", "transition", "end"})

_ANCHOR_HASH_FIELD = "anchor_hash"
_BOUNDARY_HASH_FIELD = "boundary_hash"
_MARKER_HASH_FIELD = "marker_hash"
_SAMPLE_HASH_FIELD = "sample_hash"
_POINT_HASH_FIELD = "point_hash"
_INDEX_HASH_FIELD = "index_hash"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEDGER_METADATA_REQUIRED = frozenset({
    "source",
    "route_type",
    "language",
    "profile",
    "world_snapshot_ref",
    "query_digest",
    "normalization_version",
})
_LEDGER_METADATA_ALLOWED = _LEDGER_METADATA_REQUIRED | {"agent_id"}
_FORBIDDEN_EVIDENCE_KEYS = frozenset({
    "bound",
    "claim_safe",
    "claim_safe_count",
    "coverage_present",
    "eligible",
    "raw",
    "raw_content",
    "raw_query",
    "raw_query_text",
    "raw_response",
    "raw_response_text",
    "query_text",
    "response_text",
    "prompt",
    "completion",
    "local_path",
    "ok",
    "absolute_path",
    "ledger_path",
    "receipt_root",
})


class HeadAnchorLookup(NamedTuple):
    expected_head: str | None
    ok: bool
    reason: str | None
    torn_tail: bool


class ClaimWindowEvidence(NamedTuple):
    """Sanitized inputs for ``claim_window_from_ledger``.

    ``input_ready`` only means the evidence producers supplied complete inputs. It
    is not claim safety; the ledger gate must still derive the final report.
    """

    expected_head: str | None
    enabled_across_window: bool
    clean_shutdown: bool
    required_served_points: tuple[str, ...]
    instrumented_served_points: tuple[str, ...]
    missing_served_points: tuple[str, ...]
    input_ready: bool
    reason: str | None


class LifecycleBindingVerification(NamedTuple):
    """Verdict for the dormant boundary and enabled-sample sub-contract only.

    Other side-stream contents, source provenance, and ledger receipts still require
    independent re-derivation by the future production wrapper. The wrapper must
    also persist fresh window identity; this structural verifier cannot prove global
    uniqueness by itself.
    """

    ok: bool
    reason: str | None


class ProductionWindowVerification(NamedTuple):
    """Closed, measurement-only verdict for one exact production window."""

    ok: bool
    phase: str
    reason: str | None
    marker_verified: bool
    ledger_entries: int
    enabled_samples: int
    pending_failures: int
    receipt_index_entries: int
    served_point_observations: int
    receipt_terminals: int
    measurement_only: bool = True
    claim_safe_count: int = 0
    schema_version: str = PRODUCTION_WINDOW_VERIFICATION_SCHEMA
    served_total: int = 0
    served_with_receipt_total: int = 0
    served_with_receipt_ratio: float | None = None
    solver_first_served_total: int = 0
    solver_first_served_ratio: float | None = None
    authority: str = PRODUCTION_WINDOW_COUNTER_AUTHORITY


class _ProductionMetadataSnapshot(NamedTuple):
    query_digest: str
    source: str
    route_type: str
    language: str
    profile: str
    world_snapshot_ref: str
    agent_id: str | None


def _canonical_without(entry: Mapping[str, Any], hash_field: str) -> dict[str, Any]:
    return {key: entry[key] for key in sorted(entry) if key != hash_field}


def _entry_hash(entry: Mapping[str, Any], hash_field: str) -> str:
    return sha256_digest(_canonical_without(entry, hash_field))


def _require_token(field: str, value: object) -> str:
    if not is_conforming_token(value):
        raise ValueError(f"{field} is not a conforming token")
    return str(value)


def _is_digest(value: object) -> bool:
    return is_ledger_hash(value)


def _is_source_head(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 40:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _is_nonnegative_offset(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_count(value: object) -> bool:
    return _is_nonnegative_offset(value) and int(value) > 0


def _valid_side_stream_offsets(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != CLAIM_WINDOW_SIDE_STREAMS:
        return False
    return all(_is_nonnegative_offset(value[name]) for name in CLAIM_WINDOW_SIDE_STREAMS)


def _normalized_side_stream_offsets(value: Mapping[str, int]) -> dict[str, int]:
    if not _valid_side_stream_offsets(value):
        raise ValueError("side_stream_offsets are invalid")
    return {name: value[name] for name in sorted(CLAIM_WINDOW_SIDE_STREAMS)}


def _valid_sample_gap(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_DECLARED_SAMPLE_GAP_SECONDS
    )


def new_claim_window_id() -> str:
    """Return a fresh token; the future wrapper must persist its uniqueness."""
    return f"window:{uuid4().hex}"


def _ledger_path_hash(ledger_path: str) -> str:
    resolved = str(Path(ledger_path).resolve())
    return sha256_digest({"ledger_path": resolved})


def _same_resolved_path(left: str, right: str) -> bool:
    return Path(left).resolve() == Path(right).resolve()


def new_head_anchor(
    *,
    window_id: str,
    ledger_path: str,
    expected_head: str,
    ts_utc: str,
    prev_anchor_hash: str,
) -> dict[str, Any]:
    """Build one sanitized head-anchor record for a separate anchor store."""
    if not is_ledger_hash(expected_head):
        raise ValueError("expected_head is not a ledger hash")
    if not is_ledger_hash(prev_anchor_hash):
        raise ValueError("prev_anchor_hash is not a ledger hash")
    entry: dict[str, Any] = {
        "schema_version": HEAD_ANCHOR_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "ledger_path_hash": _ledger_path_hash(ledger_path),
        "expected_head": expected_head,
        "ts_utc": _require_token("ts_utc", ts_utc),
        "prev_anchor_hash": prev_anchor_hash,
    }
    entry[_ANCHOR_HASH_FIELD] = _entry_hash(entry, _ANCHOR_HASH_FIELD)
    if not valid_head_anchor(entry):
        raise ValueError("builder produced invalid head anchor")
    return entry


def valid_head_anchor(entry: object) -> bool:
    if not isinstance(entry, Mapping):
        return False
    keys = set(entry)
    expected = {
        "schema_version",
        "window_id",
        "ledger_path_hash",
        "expected_head",
        "ts_utc",
        "prev_anchor_hash",
        _ANCHOR_HASH_FIELD,
    }
    if keys != expected:
        return False
    if entry.get("schema_version") != HEAD_ANCHOR_SCHEMA:
        return False
    if not is_conforming_token(entry.get("window_id")):
        return False
    if not is_ledger_hash(entry.get("ledger_path_hash")):
        return False
    if not is_ledger_hash(entry.get("expected_head")):
        return False
    if not is_conforming_token(entry.get("ts_utc")):
        return False
    if not is_ledger_hash(entry.get("prev_anchor_hash")):
        return False
    return entry.get(_ANCHOR_HASH_FIELD) == _entry_hash(entry, _ANCHOR_HASH_FIELD)


def _read_head_anchor_entries(anchor_store_path: str) -> tuple[list[Mapping[str, Any]], bool]:
    if not anchor_store_path or not os.path.exists(anchor_store_path):
        return [], False
    entries: list[Mapping[str, Any]] = []
    with open(
        anchor_store_path,
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        if not line.endswith("\n"):
            if index == len(lines) - 1:
                return entries, True
            raise ValueError("anchor_store_unterminated_record")
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                return entries, True
            raise ValueError("anchor_store_corrupt_json") from None
        if not valid_head_anchor(entry):
            raise ValueError("anchor_store_invalid_entry")
        prev = GENESIS_PREV_HASH if not entries else str(entries[-1][_ANCHOR_HASH_FIELD])
        if entry.get("prev_anchor_hash") != prev:
            raise ValueError("anchor_store_chain_broken")
        entries.append(entry)
    return entries, False


def write_head_anchor_checkpoint(
    anchor_store_path: str,
    ledger_path: str,
    *,
    window_id: str,
    ts_utc: str,
    fsync: bool = True,
) -> str:
    """Checkpoint the current ledger head into a separate append-only store."""
    if _same_resolved_path(anchor_store_path, ledger_path):
        raise ValueError("anchor store must be independent from ledger path")
    anchors, torn_tail = _read_head_anchor_entries(anchor_store_path)
    if torn_tail:
        raise ValueError("anchor store has torn tail")
    ledger_entries, ledger_torn_tail = read_entries(ledger_path)
    if ledger_torn_tail:
        raise ValueError("ledger has torn tail")
    if os.path.exists(ledger_path) and os.path.getsize(ledger_path) > 0:
        with open(ledger_path, "rb") as ledger_handle:
            ledger_handle.seek(-1, os.SEEK_END)
            if ledger_handle.read(1) != b"\n":
                raise ValueError("ledger has torn tail")
    prev_anchor_hash = (
        str(anchors[-1][_ANCHOR_HASH_FIELD]) if anchors else GENESIS_PREV_HASH
    )
    entry = new_head_anchor(
        window_id=window_id,
        ledger_path=ledger_path,
        expected_head=head_hash(ledger_entries),
        ts_utc=ts_utc,
        prev_anchor_hash=prev_anchor_hash,
    )
    path = Path(anchor_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())
    return str(entry[_ANCHOR_HASH_FIELD])


def read_latest_head_anchor(
    anchor_store_path: str,
    ledger_path: str,
    *,
    window_id: str | None = None,
) -> HeadAnchorLookup:
    """Read the newest matching expected head from the independent anchor store."""
    try:
        anchors, torn_tail = _read_head_anchor_entries(anchor_store_path)
    except Exception as exc:  # noqa: BLE001 - evidence reads fail closed
        return HeadAnchorLookup(None, False, f"head_anchor_store_invalid:{exc}", False)
    if torn_tail:
        return HeadAnchorLookup(None, False, "head_anchor_store_torn_tail", True)
    if not anchors:
        return HeadAnchorLookup(None, False, "missing_head_anchor_store", False)
    ledger_hash = _ledger_path_hash(ledger_path)
    matches = [
        entry for entry in anchors
        if entry.get("ledger_path_hash") == ledger_hash
        and (window_id is None or entry.get("window_id") == window_id)
    ]
    if not matches:
        return HeadAnchorLookup(None, False, "missing_matching_head_anchor", False)
    return HeadAnchorLookup(str(matches[-1]["expected_head"]), True, None, False)


def new_claim_window_start_boundary(
    *,
    window_id: str,
    start_ledger_head: str,
    start_ledger_offset: int,
    side_stream_offsets: Mapping[str, int],
    source_head: str,
    start_enabled_sample_digest: str,
    max_enabled_sample_gap_seconds: int,
    ts_utc: str,
) -> dict[str, Any]:
    """Build a dormant v1 start boundary with inclusive record cursors."""
    if not _is_digest(start_ledger_head):
        raise ValueError("start_ledger_head is not a ledger hash")
    if not _is_nonnegative_offset(start_ledger_offset):
        raise ValueError("start_ledger_offset is invalid")
    if not _is_source_head(source_head):
        raise ValueError("source_head must be a lowercase 40-hex commit")
    if not _is_digest(start_enabled_sample_digest):
        raise ValueError("start_enabled_sample_digest is invalid")
    if not _valid_sample_gap(max_enabled_sample_gap_seconds):
        raise ValueError("max_enabled_sample_gap_seconds is invalid")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    boundary: dict[str, Any] = {
        "schema_version": CLAIM_WINDOW_START_BOUNDARY_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "start_ledger_head": start_ledger_head,
        "start_ledger_offset": start_ledger_offset,
        "side_stream_offsets": _normalized_side_stream_offsets(side_stream_offsets),
        "source_head": source_head,
        "normalization_version": NORMALIZATION_VERSION,
        "start_enabled_sample_digest": start_enabled_sample_digest,
        "max_enabled_sample_gap_seconds": max_enabled_sample_gap_seconds,
        "ts_utc": ts_utc,
    }
    boundary[_BOUNDARY_HASH_FIELD] = _entry_hash(boundary, _BOUNDARY_HASH_FIELD)
    if not valid_claim_window_start_boundary(boundary, window_id=window_id):
        raise ValueError("builder produced invalid start boundary")
    return boundary


def valid_claim_window_start_boundary(
    boundary: object,
    *,
    window_id: str,
) -> bool:
    if not isinstance(boundary, Mapping):
        return False
    expected = {
        "schema_version",
        "window_id",
        "start_ledger_head",
        "start_ledger_offset",
        "side_stream_offsets",
        "source_head",
        "normalization_version",
        "start_enabled_sample_digest",
        "max_enabled_sample_gap_seconds",
        "ts_utc",
        _BOUNDARY_HASH_FIELD,
    }
    if set(boundary) != expected:
        return False
    if boundary.get("schema_version") != CLAIM_WINDOW_START_BOUNDARY_SCHEMA:
        return False
    if boundary.get("window_id") != window_id or not is_conforming_token(window_id):
        return False
    if not _is_digest(boundary.get("start_ledger_head")):
        return False
    if not _is_nonnegative_offset(boundary.get("start_ledger_offset")):
        return False
    if not _valid_side_stream_offsets(boundary.get("side_stream_offsets")):
        return False
    if not _is_source_head(boundary.get("source_head")):
        return False
    if boundary.get("normalization_version") != NORMALIZATION_VERSION:
        return False
    if not _is_digest(boundary.get("start_enabled_sample_digest")):
        return False
    if not _valid_sample_gap(boundary.get("max_enabled_sample_gap_seconds")):
        return False
    if _parse_utc_timestamp(boundary.get("ts_utc")) is None:
        return False
    return boundary.get(_BOUNDARY_HASH_FIELD) == _entry_hash(
        boundary, _BOUNDARY_HASH_FIELD
    )


def new_claim_window_final_boundary(
    *,
    window_id: str,
    start_boundary_digest: str,
    final_ledger_head: str,
    final_ledger_offset: int,
    side_stream_offsets: Mapping[str, int],
    end_enabled_sample_digest: str,
    enabled_samples_count: int,
    enabled_sample_sequence_digest: str,
    ts_utc: str,
) -> dict[str, Any]:
    """Build a dormant v1 final boundary with exclusive record cursors."""
    for field, value in (
        ("start_boundary_digest", start_boundary_digest),
        ("final_ledger_head", final_ledger_head),
        ("end_enabled_sample_digest", end_enabled_sample_digest),
        ("enabled_sample_sequence_digest", enabled_sample_sequence_digest),
    ):
        if not _is_digest(value):
            raise ValueError(f"{field} is invalid")
    if not _is_nonnegative_offset(final_ledger_offset):
        raise ValueError("final_ledger_offset is invalid")
    if not _is_positive_count(enabled_samples_count):
        raise ValueError("enabled_samples_count is invalid")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    boundary: dict[str, Any] = {
        "schema_version": CLAIM_WINDOW_FINAL_BOUNDARY_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "start_boundary_digest": start_boundary_digest,
        "final_ledger_head": final_ledger_head,
        "final_ledger_offset": final_ledger_offset,
        "side_stream_offsets": _normalized_side_stream_offsets(side_stream_offsets),
        "end_enabled_sample_digest": end_enabled_sample_digest,
        "enabled_samples_count": enabled_samples_count,
        "enabled_sample_sequence_digest": enabled_sample_sequence_digest,
        "ts_utc": ts_utc,
    }
    boundary[_BOUNDARY_HASH_FIELD] = _entry_hash(boundary, _BOUNDARY_HASH_FIELD)
    if not valid_claim_window_final_boundary(boundary, window_id=window_id):
        raise ValueError("builder produced invalid final boundary")
    return boundary


def valid_claim_window_final_boundary(
    boundary: object,
    *,
    window_id: str,
) -> bool:
    if not isinstance(boundary, Mapping):
        return False
    expected = {
        "schema_version",
        "window_id",
        "start_boundary_digest",
        "final_ledger_head",
        "final_ledger_offset",
        "side_stream_offsets",
        "end_enabled_sample_digest",
        "enabled_samples_count",
        "enabled_sample_sequence_digest",
        "ts_utc",
        _BOUNDARY_HASH_FIELD,
    }
    if set(boundary) != expected:
        return False
    if boundary.get("schema_version") != CLAIM_WINDOW_FINAL_BOUNDARY_SCHEMA:
        return False
    if boundary.get("window_id") != window_id or not is_conforming_token(window_id):
        return False
    if not _is_digest(boundary.get("start_boundary_digest")):
        return False
    if not _is_digest(boundary.get("final_ledger_head")):
        return False
    if not _is_nonnegative_offset(boundary.get("final_ledger_offset")):
        return False
    if not _valid_side_stream_offsets(boundary.get("side_stream_offsets")):
        return False
    if not _is_digest(boundary.get("end_enabled_sample_digest")):
        return False
    if not _is_positive_count(boundary.get("enabled_samples_count")):
        return False
    if not _is_digest(boundary.get("enabled_sample_sequence_digest")):
        return False
    if _parse_utc_timestamp(boundary.get("ts_utc")) is None:
        return False
    return boundary.get(_BOUNDARY_HASH_FIELD) == _entry_hash(
        boundary, _BOUNDARY_HASH_FIELD
    )


def new_clean_shutdown_marker_v1(
    *,
    window_id: str,
    start_boundary_digest: str,
    final_boundary_digest: str,
    final_ledger_head: str,
    end_enabled_sample_digest: str,
    ts_utc: str,
) -> dict[str, Any]:
    """Build a marker that is meaningful only after all bound evidence exists."""
    for field, value in (
        ("start_boundary_digest", start_boundary_digest),
        ("final_boundary_digest", final_boundary_digest),
        ("final_ledger_head", final_ledger_head),
        ("end_enabled_sample_digest", end_enabled_sample_digest),
    ):
        if not _is_digest(value):
            raise ValueError(f"{field} is invalid")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    marker: dict[str, Any] = {
        "schema_version": CLEAN_SHUTDOWN_MARKER_SCHEMA_V1,
        "window_id": _require_token("window_id", window_id),
        "status": "clean",
        "start_boundary_digest": start_boundary_digest,
        "final_boundary_digest": final_boundary_digest,
        "final_ledger_head": final_ledger_head,
        "end_enabled_sample_digest": end_enabled_sample_digest,
        "ts_utc": ts_utc,
    }
    marker[_MARKER_HASH_FIELD] = _entry_hash(marker, _MARKER_HASH_FIELD)
    if not valid_clean_shutdown_marker_v1(marker, window_id=window_id):
        raise ValueError("builder produced invalid v1 clean marker")
    return marker


def valid_clean_shutdown_marker_v1(marker: object, *, window_id: str) -> bool:
    if not isinstance(marker, Mapping):
        return False
    expected = {
        "schema_version",
        "window_id",
        "status",
        "start_boundary_digest",
        "final_boundary_digest",
        "final_ledger_head",
        "end_enabled_sample_digest",
        "ts_utc",
        _MARKER_HASH_FIELD,
    }
    if set(marker) != expected:
        return False
    if marker.get("schema_version") != CLEAN_SHUTDOWN_MARKER_SCHEMA_V1:
        return False
    if marker.get("window_id") != window_id or not is_conforming_token(window_id):
        return False
    if marker.get("status") != "clean":
        return False
    for field in (
        "start_boundary_digest",
        "final_boundary_digest",
        "final_ledger_head",
        "end_enabled_sample_digest",
    ):
        if not _is_digest(marker.get(field)):
            return False
    if _parse_utc_timestamp(marker.get("ts_utc")) is None:
        return False
    return marker.get(_MARKER_HASH_FIELD) == _entry_hash(marker, _MARKER_HASH_FIELD)


def new_clean_shutdown_marker(*, window_id: str, ts_utc: str) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "schema_version": CLEAN_SHUTDOWN_MARKER_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "status": "clean",
        "ts_utc": _require_token("ts_utc", ts_utc),
    }
    marker[_MARKER_HASH_FIELD] = _entry_hash(marker, _MARKER_HASH_FIELD)
    return marker


def valid_clean_shutdown_marker(marker: object, *, window_id: str) -> bool:
    if not isinstance(marker, Mapping):
        return False
    keys = {"schema_version", "window_id", "status", "ts_utc", _MARKER_HASH_FIELD}
    if set(marker) != keys:
        return False
    if marker.get("schema_version") != CLEAN_SHUTDOWN_MARKER_SCHEMA:
        return False
    if marker.get("window_id") != window_id:
        return False
    if marker.get("status") != "clean":
        return False
    if not is_conforming_token(marker.get("ts_utc")):
        return False
    return marker.get(_MARKER_HASH_FIELD) == _entry_hash(marker, _MARKER_HASH_FIELD)


def write_clean_shutdown_marker(
    marker_path: str,
    *,
    window_id: str,
    ts_utc: str,
    fsync: bool = True,
) -> None:
    marker = new_clean_shutdown_marker(window_id=window_id, ts_utc=ts_utc)
    path = Path(marker_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())


def _append_jsonl(path: str, entry: Mapping[str, Any], *, fsync: bool) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())


def write_enabled_state_sample(
    sample_path: str,
    *,
    window_id: str,
    enabled: bool,
    ts_utc: str,
    sample_kind: str | None = None,
    previous_enabled: bool | None = None,
    fsync: bool = True,
) -> None:
    """Append one hash-validated enabled-state sample to a durable JSONL store."""
    sample = new_enabled_state_sample(
        window_id=window_id,
        enabled=enabled,
        ts_utc=ts_utc,
        sample_kind=sample_kind,
        previous_enabled=previous_enabled,
    )
    _append_jsonl(sample_path, sample, fsync=fsync)


def write_served_point_observation(
    observation_path: str,
    *,
    point: str,
    wired: bool,
    ts_utc: str,
    window_id: str | None = None,
    fsync: bool = True,
) -> None:
    """Append one hash-validated served-point observation to a JSONL store."""
    observation = new_served_point_observation(
        point=point,
        wired=wired,
        ts_utc=ts_utc,
        window_id=window_id,
    )
    _append_jsonl(observation_path, observation, fsync=fsync)


def read_clean_shutdown_marker(marker_path: str, *, window_id: str) -> bool:
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
    except Exception:  # noqa: BLE001 - absent/corrupt marker fails closed
        return False
    return valid_clean_shutdown_marker(marker, window_id=window_id)


def new_enabled_state_sample(
    *,
    window_id: str,
    enabled: bool,
    ts_utc: str,
    sample_kind: str | None = None,
    previous_enabled: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    if sample_kind is None and previous_enabled is not None:
        raise ValueError("previous_enabled requires a transition sample")
    if sample_kind is not None and sample_kind not in ENABLED_SAMPLE_KINDS_V1:
        raise ValueError("sample_kind is invalid")
    if sample_kind == "transition":
        if not isinstance(previous_enabled, bool) or previous_enabled == enabled:
            raise ValueError("transition previous_enabled is invalid")
    elif sample_kind is not None and previous_enabled is not None:
        raise ValueError("previous_enabled is only valid for a transition")
    sample: dict[str, Any] = {
        "schema_version": (
            ENABLED_STATE_SAMPLE_SCHEMA
            if sample_kind is None
            else ENABLED_STATE_SAMPLE_SCHEMA_V1
        ),
        "window_id": _require_token("window_id", window_id),
        "enabled": enabled,
        "ts_utc": _require_token("ts_utc", ts_utc),
    }
    if sample_kind is not None:
        sample["sample_kind"] = sample_kind
    if sample_kind == "transition":
        sample["previous_enabled"] = previous_enabled
    sample[_SAMPLE_HASH_FIELD] = _entry_hash(sample, _SAMPLE_HASH_FIELD)
    return sample


def valid_enabled_state_sample(sample: object, *, window_id: str) -> bool:
    if not isinstance(sample, Mapping):
        return False
    schema = sample.get("schema_version")
    if schema == ENABLED_STATE_SAMPLE_SCHEMA:
        expected = {
            "schema_version",
            "window_id",
            "enabled",
            "ts_utc",
            _SAMPLE_HASH_FIELD,
        }
    elif schema == ENABLED_STATE_SAMPLE_SCHEMA_V1:
        expected = {
            "schema_version",
            "window_id",
            "enabled",
            "sample_kind",
            "ts_utc",
            _SAMPLE_HASH_FIELD,
        }
        if sample.get("sample_kind") == "transition":
            expected.add("previous_enabled")
    else:
        return False
    if set(sample) != expected:
        return False
    if sample.get("window_id") != window_id:
        return False
    if not isinstance(sample.get("enabled"), bool):
        return False
    if schema == ENABLED_STATE_SAMPLE_SCHEMA_V1:
        kind = sample.get("sample_kind")
        if kind not in ENABLED_SAMPLE_KINDS_V1:
            return False
        if kind == "transition":
            previous = sample.get("previous_enabled")
            if not isinstance(previous, bool) or previous == sample.get("enabled"):
                return False
    if _parse_utc_timestamp(sample.get("ts_utc")) is None:
        return False
    return sample.get(_SAMPLE_HASH_FIELD) == _entry_hash(sample, _SAMPLE_HASH_FIELD)


def derive_enabled_across_window(
    samples: Iterable[Mapping[str, Any]],
    *,
    window_id: str,
    max_gap_seconds: int | None = None,
) -> bool:
    if max_gap_seconds is not None and not _valid_sample_gap(max_gap_seconds):
        return False
    seen = False
    previous_ts: datetime | None = None
    for sample in samples:
        if not valid_enabled_state_sample(sample, window_id=window_id):
            return False
        sample_ts = _parse_utc_timestamp(sample.get("ts_utc"))
        if sample_ts is None:
            return False
        if previous_ts is not None:
            if sample_ts <= previous_ts:
                return False
            if (
                max_gap_seconds is not None
                and (sample_ts - previous_ts).total_seconds() > max_gap_seconds
            ):
                return False
        previous_ts = sample_ts
        seen = True
        if sample.get("enabled") is not True:
            return False
    return seen


def derive_enabled_sample_sequence_digest(
    samples: Iterable[Mapping[str, Any]],
    *,
    window_id: str,
) -> str | None:
    """Hash one bounded, validated enabled-sample sequence in order."""
    try:
        materialized = _bounded_enabled_samples(samples)
    except Exception:  # noqa: BLE001 - an unreadable evidence stream fails closed
        return None
    if not materialized:
        return None
    sample_hashes: list[str] = []
    for sample in materialized:
        if not valid_enabled_state_sample(sample, window_id=window_id):
            return None
        sample_hashes.append(str(sample[_SAMPLE_HASH_FIELD]))
    return _enabled_sample_sequence_digest(sample_hashes, window_id=window_id)


def _enabled_sample_sequence_digest(
    sample_hashes: Iterable[str],
    *,
    window_id: str,
) -> str:
    return sha256_digest(
        {
            "schema_version": ENABLED_SAMPLE_SEQUENCE_SCHEMA,
            "window_id": window_id,
            "sample_hashes": list(sample_hashes),
        }
    )


def _bounded_enabled_samples(
    samples: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...] | None:
    iterator = iter(samples)
    materialized: list[Mapping[str, Any]] = []
    for _ in range(MAX_ENABLED_SAMPLES_PER_WINDOW + 1):
        try:
            materialized.append(next(iterator))
        except StopIteration:
            break
    if len(materialized) > MAX_ENABLED_SAMPLES_PER_WINDOW:
        return None
    return tuple(materialized)


def _safe_manifest_ref(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or "\\" in value
        or "\x00" in value
    ):
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _receipt_index_hash(entry: Mapping[str, Any]) -> str:
    return sha256_digest(
        {
            "domain": RECEIPT_INDEX_ENTRY_HASH_DOMAIN,
            "entry": {
                key: entry[key]
                for key in sorted(entry)
                if key != _INDEX_HASH_FIELD
            },
        }
    )


def new_receipt_index_entry(
    *,
    window_id: str,
    served_id: str,
    receipt_ref: str,
    manifest_ref: str,
) -> dict[str, Any]:
    """Build one window-bound, path-safe receipt-manifest index row."""
    if not is_path_safe_token(served_id):
        raise ValueError("served_id is invalid")
    if not _is_digest(receipt_ref):
        raise ValueError("receipt_ref is invalid")
    if not _safe_manifest_ref(manifest_ref):
        raise ValueError("manifest_ref is invalid")
    entry: dict[str, Any] = {
        "schema_version": RECEIPT_INDEX_ENTRY_SCHEMA_V1,
        "window_id": _require_token("window_id", window_id),
        "served_id": served_id,
        "receipt_ref": receipt_ref,
        "manifest_ref": manifest_ref,
    }
    entry[_INDEX_HASH_FIELD] = _receipt_index_hash(entry)
    if not valid_receipt_index_entry(entry, window_id=window_id):
        raise ValueError("builder produced invalid receipt index entry")
    return entry


def valid_receipt_index_entry(entry: object, *, window_id: str) -> bool:
    if not isinstance(entry, Mapping):
        return False
    expected = {
        "schema_version",
        "window_id",
        "served_id",
        "receipt_ref",
        "manifest_ref",
        _INDEX_HASH_FIELD,
    }
    if set(entry) != expected:
        return False
    if entry.get("schema_version") != RECEIPT_INDEX_ENTRY_SCHEMA_V1:
        return False
    if entry.get("window_id") != window_id or not is_conforming_token(window_id):
        return False
    if not is_path_safe_token(entry.get("served_id")):
        return False
    if not _is_digest(entry.get("receipt_ref")):
        return False
    if not _safe_manifest_ref(entry.get("manifest_ref")):
        return False
    return entry.get(_INDEX_HASH_FIELD) == _receipt_index_hash(entry)


def _bounded_records(value: object) -> tuple[Mapping[str, Any], ...] | None:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    try:
        iterator = iter(value)  # type: ignore[arg-type]
        records: list[Mapping[str, Any]] = []
        for _ in range(MAX_PRODUCTION_WINDOW_RECORDS + 1):
            try:
                record = next(iterator)
            except StopIteration:
                break
            if type(record) is not dict:
                return None
            records.append(record)
    except Exception:  # noqa: BLE001 - hostile evidence iterables fail closed
        return None
    if len(records) > MAX_PRODUCTION_WINDOW_RECORDS:
        return None
    return tuple(records)


def _is_local_absolute_path(value: str) -> bool:
    try:
        return PurePosixPath(value).is_absolute() or bool(
            PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive
        )
    except (TypeError, ValueError):
        return True


def _privacy_safe(value: object) -> bool:
    """Reject producer verdicts, raw-content channels, and local absolute paths."""
    active: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_PRIVACY_SCAN_NODES or depth > MAX_PRIVACY_SCAN_DEPTH:
            return False
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                return False
            active.add(identity)
            try:
                for key, nested in item.items():
                    if not isinstance(key, str):
                        return False
                    lowered = key.casefold()
                    if (
                        lowered in _FORBIDDEN_EVIDENCE_KEYS
                        or lowered.startswith("raw_")
                        or lowered.endswith("_raw")
                    ):
                        return False
                    if not visit(nested, depth + 1):
                        return False
                return True
            finally:
                active.remove(identity)
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in active:
                return False
            active.add(identity)
            try:
                return all(visit(nested, depth + 1) for nested in item)
            finally:
                active.remove(identity)
        if isinstance(item, str):
            return not _is_local_absolute_path(item)
        if item is None or isinstance(item, (bool, int)):
            return True
        if isinstance(item, float):
            return math.isfinite(item)
        return False

    try:
        return visit(value, 0)
    except Exception:  # noqa: BLE001 - hostile/cyclic evidence fails closed
        return False


def _bounded_window_ids(value: object) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    try:
        iterator = iter(value)  # type: ignore[arg-type]
        identifiers: list[str] = []
        for _ in range(MAX_PRODUCTION_WINDOW_RECORDS + 1):
            try:
                identifier = next(iterator)
            except StopIteration:
                break
            if not isinstance(identifier, str):
                return None
            identifiers.append(identifier)
    except Exception:  # noqa: BLE001 - a registry read failure fails closed
        return None
    if len(identifiers) > MAX_PRODUCTION_WINDOW_RECORDS:
        return None
    return tuple(identifiers)


def _production_verdict(
    *,
    ok: bool,
    phase: str,
    reason: str | None,
    marker_verified: bool,
    ledger_entries: int,
    enabled_samples: int,
    pending_failures: int,
    receipt_index_entries: int,
    served_point_observations: int,
    receipt_terminals: int,
    served_total: int = 0,
    served_with_receipt_total: int = 0,
    served_with_receipt_ratio: float | None = None,
    solver_first_served_total: int = 0,
    solver_first_served_ratio: float | None = None,
) -> ProductionWindowVerification:
    return ProductionWindowVerification(
        ok=ok,
        phase=phase,
        reason=reason,
        marker_verified=marker_verified,
        ledger_entries=ledger_entries,
        enabled_samples=enabled_samples,
        pending_failures=pending_failures,
        receipt_index_entries=receipt_index_entries,
        served_point_observations=served_point_observations,
        receipt_terminals=receipt_terminals,
        served_total=served_total,
        served_with_receipt_total=served_with_receipt_total,
        served_with_receipt_ratio=served_with_receipt_ratio,
        solver_first_served_total=solver_first_served_total,
        solver_first_served_ratio=solver_first_served_ratio,
    )


def _production_failure(
    reason: str,
    *,
    marker_present: bool,
    ledger_entries: int = 0,
    enabled_samples: int = 0,
    pending_failures: int = 0,
    receipt_index_entries: int = 0,
    served_point_observations: int = 0,
    receipt_terminals: int = 0,
) -> ProductionWindowVerification:
    return _production_verdict(
        ok=False,
        phase="final_rejected" if marker_present else "pre_marker_rejected",
        reason=reason,
        marker_verified=False,
        ledger_entries=ledger_entries,
        enabled_samples=enabled_samples,
        pending_failures=pending_failures,
        receipt_index_entries=receipt_index_entries,
        served_point_observations=served_point_observations,
        receipt_terminals=receipt_terminals,
    )


def _utc_after(value: object) -> str | None:
    parsed = _parse_utc_timestamp(value)
    if parsed is None:
        return None
    return (parsed + timedelta(microseconds=1)).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _closed_pending_failure(entry: Mapping[str, Any]) -> bool:
    expected = {
        "schema_version",
        "served_id_hash",
        "ts_utc",
        "reason",
        "metadata",
    }
    if set(entry) != expected:
        return False
    if entry.get("schema_version") != "magma.chat_served_pending_append_failure.v0":
        return False
    if not _is_digest(entry.get("served_id_hash")):
        return False
    if entry.get("reason") not in {"metadata_rejected", "sink_write_failed"}:
        return False
    if _parse_utc_timestamp(entry.get("ts_utc")) is None:
        return False
    metadata = entry.get("metadata")
    return bool(
        isinstance(metadata, Mapping)
        and all(
            is_conforming_token(key) and is_conforming_token(item)
            for key, item in metadata.items()
        )
    )


def _valid_production_metadata(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = set(value)
    if not _LEDGER_METADATA_REQUIRED <= keys <= _LEDGER_METADATA_ALLOWED:
        return False
    if not all(is_conforming_token(key) and is_conforming_token(item) for key, item in value.items()):
        return False
    if value.get("normalization_version") != NORMALIZATION_VERSION:
        return False
    return bool(_SHA256_RE.fullmatch(str(value.get("query_digest"))))


def _production_metadata_snapshot(
    metadata: Mapping[str, Any],
) -> _ProductionMetadataSnapshot | None:
    """Freeze exact builtin scalars before any receipt callback can run."""
    values = tuple(
        metadata.get(field)
        for field in (
            "query_digest",
            "source",
            "route_type",
            "language",
            "profile",
            "world_snapshot_ref",
        )
    )
    agent_id = metadata.get("agent_id")
    if (
        any(type(value) is not str for value in values)
        or (agent_id is not None and type(agent_id) is not str)
    ):
        return None
    return _ProductionMetadataSnapshot(*values, agent_id)


def _receipt_payload_matches_pending_metadata(
    payload: Mapping[str, Any],
    metadata: _ProductionMetadataSnapshot,
) -> bool:
    """Bind claim-facing receipt fields to the synchronous served event."""
    observed = tuple(
        payload.get(field)
        for field in (
            "query_digest",
            "source",
            "route_type",
            "language",
            "profile",
            "world_snapshot_ref",
        )
    )
    agent_id = payload.get("agent_id")
    if (
        any(type(value) is not str for value in observed)
        or (agent_id is not None and type(agent_id) is not str)
    ):
        return False
    return (*observed, agent_id) == metadata


def _receipt_proves_solver_first(payload: Mapping[str, Any]) -> bool:
    """Conservatively derive solver-first from the receipt's route trace."""
    if (
        type(payload.get("route_type")) is not str
        or payload["route_type"] != "solver"
        or type(payload.get("source")) is not str
        or payload["source"] != "solver"
    ):
        return False
    trace = payload.get("route_stage_trace")
    if type(trace) is not list:
        return False

    route_selection_intent: str | None = None
    deterministic_intent: str | None = None
    first_serving_stage: str | None = None
    later_serving_stage = False
    for event in trace:
        if type(event) is not dict or type(event.get("stage")) is not str:
            return False
        stage = event["stage"]
        if stage == "route_selection":
            route_type = event.get("route_type")
            solver_intent = event.get("solver_intent")
            if (
                type(route_type) is not str
                or route_type != "solver"
                or type(solver_intent) is not str
            ):
                return False
            route_selection_intent = solver_intent
        elif stage == "deterministic_solver":
            intent = event.get("intent")
            if type(intent) is not str:
                return False
            deterministic_intent = intent

        serves = (
            (stage == "hot_cache" and event.get("hit") is True)
            or (
                stage
                in {
                    "deterministic_solver",
                    "hybrid_retrieval_8_cell",
                    "hex_neighbor_assist_7_cell",
                }
                and event.get("answered") is True
            )
            or stage == "orchestrator_llm_fallback"
        )
        if serves:
            if first_serving_stage is None:
                first_serving_stage = stage
            else:
                later_serving_stage = True

    return bool(
        first_serving_stage == "deterministic_solver"
        and not later_serving_stage
        and route_selection_intent is not None
        and route_selection_intent == deterministic_intent
    )


def _valid_enabled_transition_sequence(
    samples: tuple[Mapping[str, Any], ...],
) -> str | None:
    if len(samples) < 2:
        return "enabled_boundary_samples_missing"
    if any(
        sample.get("schema_version") != ENABLED_STATE_SAMPLE_SCHEMA_V1
        for sample in samples
    ):
        return "enabled_sample_not_v1"
    if samples[0].get("sample_kind") != "start":
        return "enabled_start_sample_missing"
    if samples[-1].get("sample_kind") != "end":
        return "enabled_end_sample_missing"
    if any(
        sample.get("sample_kind") in {"start", "end"}
        for sample in samples[1:-1]
    ):
        return "enabled_boundary_sample_repeated"
    previous = samples[0].get("enabled")
    for sample in samples[1:]:
        current = sample.get("enabled")
        kind = sample.get("sample_kind")
        if current != previous:
            if kind != "transition" or sample.get("previous_enabled") != previous:
                return "enabled_transition_missing"
        elif kind == "transition":
            return "enabled_transition_spurious"
        previous = current
    if any(sample.get("enabled") is not True for sample in samples):
        return "not_enabled_across_window"
    return None


def verify_claim_window_lifecycle_binding(
    *,
    start_boundary: Mapping[str, Any],
    final_boundary: Mapping[str, Any],
    clean_shutdown_marker: Mapping[str, Any],
    enabled_samples: Iterable[Mapping[str, Any]],
    window_id: str,
) -> LifecycleBindingVerification:
    """Verify dormant boundary/sample links without granting eligibility."""
    if not valid_claim_window_start_boundary(start_boundary, window_id=window_id):
        return LifecycleBindingVerification(False, "start_boundary_invalid")
    if not valid_claim_window_final_boundary(final_boundary, window_id=window_id):
        return LifecycleBindingVerification(False, "final_boundary_invalid")
    if not valid_clean_shutdown_marker_v1(
        clean_shutdown_marker, window_id=window_id
    ):
        return LifecycleBindingVerification(False, "clean_marker_invalid")

    start_digest = str(start_boundary[_BOUNDARY_HASH_FIELD])
    final_digest = str(final_boundary[_BOUNDARY_HASH_FIELD])
    if final_boundary.get("start_boundary_digest") != start_digest:
        return LifecycleBindingVerification(False, "start_boundary_digest_mismatch")
    if clean_shutdown_marker.get("start_boundary_digest") != start_digest:
        return LifecycleBindingVerification(False, "marker_start_digest_mismatch")
    if clean_shutdown_marker.get("final_boundary_digest") != final_digest:
        return LifecycleBindingVerification(False, "marker_final_digest_mismatch")
    if clean_shutdown_marker.get("final_ledger_head") != final_boundary.get(
        "final_ledger_head"
    ):
        return LifecycleBindingVerification(False, "marker_final_head_mismatch")
    if clean_shutdown_marker.get("end_enabled_sample_digest") != final_boundary.get(
        "end_enabled_sample_digest"
    ):
        return LifecycleBindingVerification(False, "marker_end_sample_mismatch")

    if int(final_boundary["final_ledger_offset"]) < int(
        start_boundary["start_ledger_offset"]
    ):
        return LifecycleBindingVerification(False, "ledger_offset_regressed")
    start_offsets = start_boundary["side_stream_offsets"]
    final_offsets = final_boundary["side_stream_offsets"]
    for stream in sorted(CLAIM_WINDOW_SIDE_STREAMS):
        if int(final_offsets[stream]) < int(start_offsets[stream]):
            return LifecycleBindingVerification(
                False, f"side_stream_offset_regressed:{stream}"
            )

    start_ts = _parse_utc_timestamp(start_boundary.get("ts_utc"))
    final_ts = _parse_utc_timestamp(final_boundary.get("ts_utc"))
    marker_ts = _parse_utc_timestamp(clean_shutdown_marker.get("ts_utc"))
    if start_ts is None or final_ts is None or marker_ts is None:
        return LifecycleBindingVerification(False, "lifecycle_timestamp_invalid")
    if not start_ts < final_ts < marker_ts:
        return LifecycleBindingVerification(False, "lifecycle_timestamp_not_monotonic")

    try:
        samples = _bounded_enabled_samples(enabled_samples)
    except Exception:  # noqa: BLE001 - an unreadable evidence stream fails closed
        return LifecycleBindingVerification(False, "enabled_samples_read_failed")
    if samples is None:
        return LifecycleBindingVerification(False, "enabled_samples_exceed_bound")
    if len(samples) < 2:
        return LifecycleBindingVerification(False, "enabled_boundary_samples_missing")
    cursor_sample_count = int(final_offsets["enabled_state_samples"]) - int(
        start_offsets["enabled_state_samples"]
    )
    declared_sample_count = int(final_boundary["enabled_samples_count"])
    if cursor_sample_count != declared_sample_count:
        return LifecycleBindingVerification(False, "enabled_sample_count_cursor_mismatch")
    if declared_sample_count != len(samples):
        return LifecycleBindingVerification(False, "enabled_sample_offset_span_mismatch")
    max_gap = int(start_boundary["max_enabled_sample_gap_seconds"])
    if not derive_enabled_across_window(
        samples,
        window_id=window_id,
        max_gap_seconds=max_gap,
    ):
        return LifecycleBindingVerification(False, "enabled_timeline_invalid")
    first_ts = _parse_utc_timestamp(samples[0].get("ts_utc"))
    last_ts = _parse_utc_timestamp(samples[-1].get("ts_utc"))
    if first_ts is None or last_ts is None or first_ts < start_ts or last_ts > final_ts:
        return LifecycleBindingVerification(False, "enabled_timeline_outside_boundary")
    if (
        (first_ts - start_ts).total_seconds() > max_gap
        or (final_ts - last_ts).total_seconds() > max_gap
    ):
        return LifecycleBindingVerification(False, "enabled_boundary_cadence_gap")
    if samples[0].get(_SAMPLE_HASH_FIELD) != start_boundary.get(
        "start_enabled_sample_digest"
    ):
        return LifecycleBindingVerification(False, "start_enabled_sample_mismatch")
    if samples[-1].get(_SAMPLE_HASH_FIELD) != final_boundary.get(
        "end_enabled_sample_digest"
    ):
        return LifecycleBindingVerification(False, "end_enabled_sample_mismatch")
    sequence_digest = _enabled_sample_sequence_digest(
        (str(sample[_SAMPLE_HASH_FIELD]) for sample in samples),
        window_id=window_id,
    )
    if sequence_digest != final_boundary.get("enabled_sample_sequence_digest"):
        return LifecycleBindingVerification(False, "enabled_sample_sequence_mismatch")
    return LifecycleBindingVerification(True, None)


def verify_production_window(
    *,
    expected_window_id: str,
    expected_source_head: str,
    start_boundary: Mapping[str, Any],
    final_boundary: Mapping[str, Any],
    clean_shutdown_marker: Mapping[str, Any] | None,
    ledger_entries: Iterable[Mapping[str, Any]],
    enabled_samples: Iterable[Mapping[str, Any]],
    pending_failures: Iterable[Mapping[str, Any]],
    receipt_index: Iterable[Mapping[str, Any]],
    served_point_observations: Iterable[Mapping[str, Any]],
    resolve_receipt_bundle: Callable[[str], Mapping[str, Any] | None],
    verify_receipt_bundle: Callable[[Mapping[str, Any]], bool],
    content_address_receipt: Callable[[Mapping[str, Any]], str],
    previously_verified_window_ids: Iterable[str] = (),
) -> ProductionWindowVerification:
    """Independently verify one exact, record-cursor-bound production window.

    ``clean_shutdown_marker=None`` is the coordinator's pre-marker mode. A passing
    result is then explicitly ``pre_marker_verified``: it permits only the final
    marker write. Supplying the marker performs the final verification. Neither
    mode grants eligibility, runtime authority, or a claim-safety transition.

    The four evidence iterables and the ledger iterable must be the *exact* slices
    selected by the start/final record cursors. Whole-file projections, omitted
    rows, historical rows, cross-window rows, and extra receipt manifests therefore
    fail their cursor/count or closed-schema checks.

    Freshness is proven only against ``previously_verified_window_ids`` supplied by
    a durable coordinator registry. An empty/process-local iterable cannot establish
    global uniqueness; source-head and boundary binding still reject stale inputs.
    """
    marker_present = clean_shutdown_marker is not None
    ledger = _bounded_records(ledger_entries)
    samples = _bounded_records(enabled_samples)
    failures = _bounded_records(pending_failures)
    index = _bounded_records(receipt_index)
    observations = _bounded_records(served_point_observations)
    if any(value is None for value in (ledger, samples, failures, index, observations)):
        return _production_failure("evidence_read_failed", marker_present=marker_present)
    assert ledger is not None
    assert samples is not None
    assert failures is not None
    assert index is not None
    assert observations is not None
    counts = {
        "ledger_entries": len(ledger),
        "enabled_samples": len(samples),
        "pending_failures": len(failures),
        "receipt_index_entries": len(index),
        "served_point_observations": len(observations),
    }

    prior_ids = _bounded_window_ids(previously_verified_window_ids)
    if prior_ids is None:
        return _production_failure(
            "window_registry_read_failed",
            marker_present=marker_present,
            **counts,
        )
    if (
        not is_conforming_token(expected_window_id)
        or not _is_source_head(expected_source_head)
    ):
        return _production_failure(
            "expected_context_invalid",
            marker_present=marker_present,
            **counts,
        )
    if (
        any(not is_conforming_token(item) for item in prior_ids)
        or len(set(prior_ids)) != len(prior_ids)
    ):
        return _production_failure(
            "window_registry_invalid",
            marker_present=marker_present,
            **counts,
        )
    if expected_window_id in prior_ids:
        return _production_failure(
            "window_id_reused",
            marker_present=marker_present,
            **counts,
        )

    all_evidence = {
        "start_boundary": start_boundary,
        "final_boundary": final_boundary,
        "clean_shutdown_marker": clean_shutdown_marker,
        "ledger_entries": ledger,
        "enabled_samples": samples,
        "pending_failures": failures,
        "receipt_index": index,
        "served_point_observations": observations,
    }
    if not _privacy_safe(all_evidence):
        return _production_failure(
            "privacy_or_producer_flag_violation",
            marker_present=marker_present,
            **counts,
        )
    try:
        evidence_digest = sha256_digest(all_evidence)
    except Exception:  # noqa: BLE001 - non-canonical evidence fails closed
        return _production_failure(
            "evidence_not_canonical",
            marker_present=marker_present,
            **counts,
        )

    if not valid_claim_window_start_boundary(
        start_boundary,
        window_id=expected_window_id,
    ):
        return _production_failure(
            "start_boundary_invalid",
            marker_present=marker_present,
            **counts,
        )
    if start_boundary.get("source_head") != expected_source_head:
        return _production_failure(
            "source_head_mismatch",
            marker_present=marker_present,
            **counts,
        )
    if not valid_claim_window_final_boundary(
        final_boundary,
        window_id=expected_window_id,
    ):
        return _production_failure(
            "final_boundary_invalid",
            marker_present=marker_present,
            **counts,
        )

    marker_for_lifecycle: Mapping[str, Any]
    if clean_shutdown_marker is None:
        synthetic_ts = _utc_after(final_boundary.get("ts_utc"))
        if synthetic_ts is None:
            return _production_failure(
                "final_boundary_invalid",
                marker_present=False,
                **counts,
            )
        try:
            marker_for_lifecycle = new_clean_shutdown_marker_v1(
                window_id=expected_window_id,
                start_boundary_digest=str(start_boundary[_BOUNDARY_HASH_FIELD]),
                final_boundary_digest=str(final_boundary[_BOUNDARY_HASH_FIELD]),
                final_ledger_head=str(final_boundary["final_ledger_head"]),
                end_enabled_sample_digest=str(
                    final_boundary["end_enabled_sample_digest"]
                ),
                ts_utc=synthetic_ts,
            )
        except Exception:  # noqa: BLE001 - malformed boundary inputs fail closed
            return _production_failure(
                "pre_marker_link_invalid",
                marker_present=False,
                **counts,
            )
    else:
        marker_for_lifecycle = clean_shutdown_marker
    lifecycle = verify_claim_window_lifecycle_binding(
        start_boundary=start_boundary,
        final_boundary=final_boundary,
        clean_shutdown_marker=marker_for_lifecycle,
        enabled_samples=samples,
        window_id=expected_window_id,
    )
    if not lifecycle.ok:
        return _production_failure(
            lifecycle.reason or "lifecycle_binding_invalid",
            marker_present=marker_present,
            **counts,
        )

    start_ledger_offset = int(start_boundary["start_ledger_offset"])
    final_ledger_offset = int(final_boundary["final_ledger_offset"])
    if final_ledger_offset - start_ledger_offset != len(ledger):
        return _production_failure(
            "ledger_cursor_slice_mismatch",
            marker_present=marker_present,
            **counts,
        )
    if not ledger:
        return _production_failure(
            "empty_ledger_window",
            marker_present=marker_present,
            **counts,
        )
    start_offsets = start_boundary["side_stream_offsets"]
    final_offsets = final_boundary["side_stream_offsets"]
    supplied_streams = {
        "enabled_state_samples": samples,
        "pending_append_failures": failures,
        "receipt_index": index,
        "served_point_observations": observations,
    }
    for stream, records in supplied_streams.items():
        if int(final_offsets[stream]) - int(start_offsets[stream]) != len(records):
            return _production_failure(
                f"side_stream_cursor_slice_mismatch:{stream}",
                marker_present=marker_present,
                **counts,
            )

    transition_reason = _valid_enabled_transition_sequence(samples)
    if transition_reason is not None:
        return _production_failure(
            transition_reason,
            marker_present=marker_present,
            **counts,
        )

    start_ts = _parse_utc_timestamp(start_boundary.get("ts_utc"))
    final_ts = _parse_utc_timestamp(final_boundary.get("ts_utc"))
    if start_ts is None or final_ts is None:
        return _production_failure(
            "lifecycle_timestamp_invalid",
            marker_present=marker_present,
            **counts,
        )

    previous_hash = str(start_boundary["start_ledger_head"])
    previous_ts = start_ts
    metadata_by_served: dict[str, _ProductionMetadataSnapshot] = {}
    state: dict[str, str] = {}
    receipt_terminals: list[tuple[str, str]] = []
    for entry in ledger:
        if wellformed_reason(entry) is not None or not verify_entry_self(entry):
            return _production_failure(
                "ledger_entry_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        if entry.get("prev_ledger_hash") != previous_hash:
            return _production_failure(
                "ledger_segment_chain_broken",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        entry_ts = _parse_utc_timestamp(entry.get("ts_utc"))
        if (
            entry_ts is None
            or entry_ts < start_ts
            or entry_ts > final_ts
            or entry_ts < previous_ts
        ):
            return _production_failure(
                "ledger_timestamp_outside_window",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        previous_ts = entry_ts
        previous_hash = str(entry["entry_hash"])
        served_id = str(entry["served_id"])
        entry_type = entry["entry_type"]
        if entry_type == SERVED_PENDING:
            metadata = entry.get("metadata")
            if (
                served_id in state
                or not _valid_production_metadata(metadata)
                or not isinstance(metadata, Mapping)
            ):
                return _production_failure(
                    "ledger_pending_invalid",
                    marker_present=marker_present,
                    receipt_terminals=len(receipt_terminals),
                    **counts,
                )
            metadata_snapshot = _production_metadata_snapshot(metadata)
            if metadata_snapshot is None:
                return _production_failure(
                    "ledger_pending_invalid",
                    marker_present=marker_present,
                    receipt_terminals=len(receipt_terminals),
                    **counts,
                )
            state[served_id] = "pending"
            metadata_by_served[served_id] = metadata_snapshot
        elif entry_type in {RECEIPT_TERMINAL, GAP_TERMINAL}:
            if state.get(served_id) != "pending":
                return _production_failure(
                    "ledger_terminal_lifecycle_invalid",
                    marker_present=marker_present,
                    receipt_terminals=len(receipt_terminals),
                    **counts,
                )
            if entry_type == GAP_TERMINAL:
                state[served_id] = "gap"
            else:
                state[served_id] = "receipt"
                receipt_terminals.append((served_id, str(entry["receipt_ref"])))
        else:  # defensive: wellformed_reason already pins the set
            return _production_failure(
                "ledger_entry_type_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
    if previous_hash != final_boundary.get("final_ledger_head"):
        return _production_failure(
            "final_ledger_head_mismatch",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )
    if not state or any(
        value not in {"receipt", "gap"} for value in state.values()
    ):
        return _production_failure(
            "ledger_unresolved_pending",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    for failure in failures:
        if not _closed_pending_failure(failure):
            return _production_failure(
                "pending_failure_record_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
    if failures:
        return _production_failure(
            "pending_append_failures_present",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    observed_points: set[str] = set()
    for observation in observations:
        if not valid_served_point_observation(
            observation,
            window_id=expected_window_id,
        ):
            return _production_failure(
                "served_point_observation_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        observation_ts = _parse_utc_timestamp(observation.get("ts_utc"))
        point = str(observation["point"])
        if (
            observation_ts is None
            or observation_ts < start_ts
            or observation_ts > final_ts
            or observation.get("wired") is not True
            or point in observed_points
        ):
            return _production_failure(
                "served_point_observation_timeline_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        observed_points.add(point)
    if observed_points != set(REQUIRED_CHAT_SERVED_POINTS):
        return _production_failure(
            "served_point_instrumentation_incomplete",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    for row in index:
        if not valid_receipt_index_entry(row, window_id=expected_window_id):
            return _production_failure(
                "receipt_index_entry_invalid",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
    index_pins = tuple(
        (
            str(row["manifest_ref"]),
            str(row["served_id"]),
            str(row["receipt_ref"]),
        )
        for row in index
    )
    if index_pins != tuple(sorted(index_pins)):
        return _production_failure(
            "receipt_index_not_sorted",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )
    indexed_terminals = [
        (served_id, receipt_ref)
        for _manifest_ref, served_id, receipt_ref in index_pins
    ]
    manifest_refs = [
        manifest_ref
        for manifest_ref, _served_id, _receipt_ref in index_pins
    ]
    indexed_receipt_refs = [
        receipt_ref
        for _manifest_ref, _served_id, receipt_ref in index_pins
    ]
    if (
        len(set(indexed_terminals)) != len(indexed_terminals)
        or len(set(manifest_refs)) != len(manifest_refs)
        or len(set(indexed_receipt_refs)) != len(indexed_receipt_refs)
        or sorted(indexed_terminals) != sorted(receipt_terminals)
    ):
        return _production_failure(
            "receipt_index_not_exact",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    bundle_pins: list[
        tuple[str, str, str, _ProductionMetadataSnapshot]
    ] = []
    for manifest_ref, served_id, receipt_ref in index_pins:
        pending_metadata = metadata_by_served.get(served_id)
        if pending_metadata is None:
            return _production_failure(
                "receipt_index_not_exact",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        bundle_pins.append(
            (manifest_ref, served_id, receipt_ref, pending_metadata)
        )
    frozen_bundle_pins = tuple(bundle_pins)

    def verify_resolved_bundle(
        pin: tuple[str, str, str, _ProductionMetadataSnapshot],
    ) -> tuple[str, bool] | None:
        try:
            manifest_ref, _served_id, receipt_ref, pending_metadata = pin
            bundle = resolve_receipt_bundle(manifest_ref)
            if type(bundle) is not dict or set(bundle) != {"receipt", "payload"}:
                return None
            receipt = bundle.get("receipt")
            payload = bundle.get("payload")
            if type(receipt) is not dict or type(payload) is not dict:
                return None
            if not _privacy_safe(bundle):
                return None
            before = sha256_digest(bundle)
            validate_chat_served_payload(payload)
            if not _receipt_payload_matches_pending_metadata(
                payload,
                pending_metadata,
            ):
                return None
            solver_first = _receipt_proves_solver_first(payload)
            if receipt.get("canonical_payload_digest") != sha256_digest(payload):
                return None
            canonical_ref = sha256_digest(receipt)
            if canonical_ref != receipt_ref:
                return None
            if content_address_receipt(receipt) != canonical_ref:
                return None
            if verify_receipt_bundle(bundle) is not True:
                return None
            if sha256_digest(bundle) != before:
                return None
            return before, solver_first
        except Exception:  # noqa: BLE001 - resolver/verifier failures fail closed
            return None

    served_with_receipt_total = 0
    solver_first_served_total = 0
    for pin in frozen_bundle_pins:
        first_observation = verify_resolved_bundle(pin)
        second_observation = verify_resolved_bundle(pin)
        if first_observation is None or second_observation != first_observation:
            return _production_failure(
                "receipt_bundle_verification_failed",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
        served_with_receipt_total += 1
        solver_first_served_total += int(first_observation[1])

    try:
        if sha256_digest(all_evidence) != evidence_digest:
            return _production_failure(
                "evidence_mutated_during_verification",
                marker_present=marker_present,
                receipt_terminals=len(receipt_terminals),
                **counts,
            )
    except Exception:  # noqa: BLE001 - post-callback evidence failure is closed
        return _production_failure(
            "evidence_mutated_during_verification",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    served_total = len(state)
    if not (
        0
        <= solver_first_served_total
        <= served_with_receipt_total
        <= served_total
    ):
        return _production_failure(
            "receipt_counter_invariant_failed",
            marker_present=marker_present,
            receipt_terminals=len(receipt_terminals),
            **counts,
        )

    return _production_verdict(
        ok=True,
        phase="final_verified" if marker_present else "pre_marker_verified",
        reason=None,
        marker_verified=marker_present,
        receipt_terminals=len(receipt_terminals),
        served_total=served_total,
        served_with_receipt_total=served_with_receipt_total,
        served_with_receipt_ratio=served_with_receipt_total / served_total,
        solver_first_served_total=solver_first_served_total,
        solver_first_served_ratio=solver_first_served_total / served_total,
        **counts,
    )


def derive_production_window_registry_binding_digest(
    *,
    expected_window_id: str,
    expected_source_head: str,
    start_boundary: Mapping[str, Any],
    final_boundary: Mapping[str, Any],
    clean_shutdown_marker: Mapping[str, Any],
    ledger_entries: Iterable[Mapping[str, Any]],
    enabled_samples: Iterable[Mapping[str, Any]],
    pending_failures: Iterable[Mapping[str, Any]],
    receipt_index: Iterable[Mapping[str, Any]],
    served_point_observations: Iterable[Mapping[str, Any]],
) -> str:
    """Bind receiver pins to one exact, closed-window evidence snapshot.

    This digest is only a replay-registry comparison key. It does not verify the
    evidence, grant authority, or replace ``verify_production_window``.
    """
    records = {
        "ledger_entries": _bounded_records(ledger_entries),
        "enabled_samples": _bounded_records(enabled_samples),
        "pending_failures": _bounded_records(pending_failures),
        "receipt_index": _bounded_records(receipt_index),
        "served_point_observations": _bounded_records(
            served_point_observations
        ),
    }
    if any(value is None for value in records.values()):
        raise ValueError("registry_binding_evidence_exceeds_bound")
    if (
        not is_conforming_token(expected_window_id)
        or not _is_source_head(expected_source_head)
        or not isinstance(start_boundary, Mapping)
        or not isinstance(final_boundary, Mapping)
        or not isinstance(clean_shutdown_marker, Mapping)
    ):
        raise ValueError("registry_binding_context_invalid")
    binding = {
        "schema_version": PRODUCTION_WINDOW_REGISTRY_BINDING_SCHEMA,
        "expected_window_id": expected_window_id,
        "expected_source_head": expected_source_head,
        "start_boundary": start_boundary,
        "final_boundary": final_boundary,
        "clean_shutdown_marker": clean_shutdown_marker,
        **records,
    }
    if not _privacy_safe(binding):
        raise ValueError("registry_binding_privacy_violation")
    try:
        return sha256_digest(binding)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("registry_binding_not_canonical") from exc


def new_served_point_observation(
    *,
    point: str,
    wired: bool,
    ts_utc: str,
    window_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(wired, bool):
        raise ValueError("wired must be a boolean")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    observation: dict[str, Any] = {
        "schema_version": (
            SERVED_POINT_OBSERVATION_SCHEMA_V1
            if window_id is not None
            else SERVED_POINT_OBSERVATION_SCHEMA
        ),
        "point": _require_token("point", point),
        "wired": wired,
        "ts_utc": _require_token("ts_utc", ts_utc),
    }
    if window_id is not None:
        observation["window_id"] = _require_token("window_id", window_id)
    observation[_POINT_HASH_FIELD] = _entry_hash(observation, _POINT_HASH_FIELD)
    return observation


def valid_served_point_observation(
    observation: object,
    *,
    window_id: str | None = None,
) -> bool:
    if not isinstance(observation, Mapping):
        return False
    schema = observation.get("schema_version")
    legacy = schema == SERVED_POINT_OBSERVATION_SCHEMA
    bound = schema == SERVED_POINT_OBSERVATION_SCHEMA_V1
    if not legacy and not bound:
        return False
    expected = {"schema_version", "point", "wired", "ts_utc", _POINT_HASH_FIELD}
    if bound:
        expected.add("window_id")
    if set(observation) != expected:
        return False
    if bound:
        observed_window = observation.get("window_id")
        if not is_conforming_token(observed_window):
            return False
        if window_id is None or observed_window != window_id:
            return False
    elif window_id is not None:
        return False
    if observation.get("point") not in REQUIRED_CHAT_SERVED_POINTS:
        return False
    if not isinstance(observation.get("wired"), bool):
        return False
    if _parse_utc_timestamp(observation.get("ts_utc")) is None:
        return False
    return observation.get(_POINT_HASH_FIELD) == _entry_hash(observation, _POINT_HASH_FIELD)


def derive_instrumented_served_points(
    observations: Iterable[Mapping[str, Any]],
    *,
    window_id: str | None = None,
) -> tuple[str, ...]:
    """Return wired points for diagnostics or for one explicitly bound window.

    With no ``window_id``, legacy callers may inspect either v0 rows or each v1
    row against its own declared window. That mode is diagnostics-only and does
    not establish same-window attribution. ``verify_production_window`` instead
    validates every v1 row against the independently expected window.
    """
    points: set[str] = set()
    for observation in observations:
        observed_window = (
            observation.get("window_id")
            if window_id is None
            and observation.get("schema_version")
            == SERVED_POINT_OBSERVATION_SCHEMA_V1
            else window_id
        )
        if valid_served_point_observation(
            observation,
            window_id=observed_window,
        ) and observation.get("wired"):
            points.add(str(observation["point"]))
    return tuple(sorted(points))


def build_claim_window_evidence(
    *,
    anchor_store_path: str,
    ledger_path: str,
    window_id: str,
    enabled_samples: Iterable[Mapping[str, Any]],
    clean_shutdown_marker_path: str,
    served_point_observations: Iterable[Mapping[str, Any]],
) -> ClaimWindowEvidence:
    required = tuple(sorted(REQUIRED_CHAT_SERVED_POINTS))
    expected_head: str | None = None
    anchor_reason: str | None = None
    if _same_resolved_path(anchor_store_path, ledger_path):
        anchor_reason = "head_anchor_not_independent"
    else:
        anchor = read_latest_head_anchor(
            anchor_store_path,
            ledger_path,
            window_id=window_id,
        )
        expected_head = anchor.expected_head
        anchor_reason = anchor.reason

    enabled = derive_enabled_across_window(enabled_samples, window_id=window_id)
    clean = read_clean_shutdown_marker(
        clean_shutdown_marker_path,
        window_id=window_id,
    )
    instrumented = derive_instrumented_served_points(served_point_observations)
    instrumented_set = set(instrumented)
    missing = tuple(point for point in required if point not in instrumented_set)

    reason: str | None = None
    if anchor_reason is not None:
        reason = anchor_reason
    else:
        # The live emitter still writes legacy v0 marker/observation evidence.
        # Preserve it for diagnostics, but never let it make a window eligible.
        reason = "lifecycle_binding_missing"

    return ClaimWindowEvidence(
        expected_head=expected_head,
        enabled_across_window=enabled,
        clean_shutdown=clean,
        required_served_points=required,
        instrumented_served_points=instrumented,
        missing_served_points=missing,
        input_ready=reason is None,
        reason=reason,
    )


def claim_window_from_evidence(
    ledger_path: str,
    evidence: ClaimWindowEvidence,
    *,
    pending_failure_ledger_path: str | None = None,
) -> ClaimWindowReport:
    """Evaluate #1503 gates using only the sanitized evidence snapshot."""
    report = claim_window_from_ledger(
        ledger_path,
        expected_head=evidence.expected_head,
        enabled_across_window=evidence.enabled_across_window,
        # This legacy v0 adapter is diagnostic-only. A future production wrapper
        # must re-derive the complete v1 segment before it can pass this gate.
        clean_shutdown=False,
        instrumented_served_points=evidence.instrumented_served_points,
        required_served_points=evidence.required_served_points,
        pending_failure_ledger_path=pending_failure_ledger_path,
    )
    if evidence.reason is not None and not report.eligible:
        return report._replace(reason=evidence.reason)
    return report


__all__ = [
    "CLAIM_WINDOW_FINAL_BOUNDARY_SCHEMA",
    "CLAIM_WINDOW_SIDE_STREAMS",
    "CLAIM_WINDOW_START_BOUNDARY_SCHEMA",
    "CLEAN_SHUTDOWN_MARKER_SCHEMA",
    "CLEAN_SHUTDOWN_MARKER_SCHEMA_V1",
    "ENABLED_SAMPLE_KINDS_V1",
    "ENABLED_STATE_SAMPLE_SCHEMA",
    "ENABLED_STATE_SAMPLE_SCHEMA_V1",
    "ENABLED_SAMPLE_SEQUENCE_SCHEMA",
    "HEAD_ANCHOR_SCHEMA",
    "MAX_DECLARED_SAMPLE_GAP_SECONDS",
    "MAX_ENABLED_SAMPLES_PER_WINDOW",
    "MAX_PRIVACY_SCAN_DEPTH",
    "MAX_PRIVACY_SCAN_NODES",
    "MAX_PRODUCTION_WINDOW_RECORDS",
    "PRODUCTION_WINDOW_COUNTER_AUTHORITY",
    "PRODUCTION_WINDOW_REGISTRY_BINDING_SCHEMA",
    "PRODUCTION_WINDOW_VERIFICATION_SCHEMA",
    "RECEIPT_INDEX_ENTRY_HASH_DOMAIN",
    "RECEIPT_INDEX_ENTRY_SCHEMA_V1",
    "SERVED_POINT_OBSERVATION_SCHEMA",
    "SERVED_POINT_OBSERVATION_SCHEMA_V1",
    "ClaimWindowEvidence",
    "HeadAnchorLookup",
    "LifecycleBindingVerification",
    "ProductionWindowVerification",
    "build_claim_window_evidence",
    "claim_window_from_evidence",
    "derive_enabled_across_window",
    "derive_enabled_sample_sequence_digest",
    "derive_instrumented_served_points",
    "derive_production_window_registry_binding_digest",
    "new_claim_window_final_boundary",
    "new_claim_window_id",
    "new_claim_window_start_boundary",
    "new_clean_shutdown_marker",
    "new_clean_shutdown_marker_v1",
    "new_enabled_state_sample",
    "new_head_anchor",
    "new_receipt_index_entry",
    "new_served_point_observation",
    "read_clean_shutdown_marker",
    "read_latest_head_anchor",
    "valid_clean_shutdown_marker",
    "valid_clean_shutdown_marker_v1",
    "valid_claim_window_final_boundary",
    "valid_claim_window_start_boundary",
    "valid_enabled_state_sample",
    "valid_head_anchor",
    "valid_receipt_index_entry",
    "valid_served_point_observation",
    "verify_claim_window_lifecycle_binding",
    "verify_production_window",
    "write_clean_shutdown_marker",
    "write_enabled_state_sample",
    "write_head_anchor_checkpoint",
    "write_served_point_observation",
]
