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
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import NORMALIZATION_VERSION
from waggledance.core.magma.chat_served_accounting import (
    REQUIRED_CHAT_SERVED_POINTS,
    ClaimWindowReport,
    claim_window_from_ledger,
)
from waggledance.core.magma.chat_served_ledger import (
    GENESIS_PREV_HASH,
    head_hash,
    is_ledger_hash,
    read_entries,
)
from waggledance.core.magma.chat_served_metadata import is_conforming_token

HEAD_ANCHOR_SCHEMA = "magma.chat_served_head_anchor.v0"
CLEAN_SHUTDOWN_MARKER_SCHEMA = "magma.chat_served_clean_shutdown_marker.v0"
ENABLED_STATE_SAMPLE_SCHEMA = "magma.chat_served_enabled_state_sample.v0"
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

CLAIM_WINDOW_SIDE_STREAMS = frozenset({
    "enabled_state_samples",
    "pending_append_failures",
    "receipt_index",
    "served_point_observations",
})
MAX_DECLARED_SAMPLE_GAP_SECONDS = 86_400
MAX_ENABLED_SAMPLES_PER_WINDOW = 100_000

_ANCHOR_HASH_FIELD = "anchor_hash"
_BOUNDARY_HASH_FIELD = "boundary_hash"
_MARKER_HASH_FIELD = "marker_hash"
_SAMPLE_HASH_FIELD = "sample_hash"
_POINT_HASH_FIELD = "point_hash"


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
    with open(anchor_store_path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
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
    ledger_entries, _ledger_torn_tail = read_entries(ledger_path)
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
    fsync: bool = True,
) -> None:
    """Append one hash-validated enabled-state sample to a durable JSONL store."""
    sample = new_enabled_state_sample(
        window_id=window_id,
        enabled=enabled,
        ts_utc=ts_utc,
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
) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if _parse_utc_timestamp(ts_utc) is None:
        raise ValueError("ts_utc is not a UTC timestamp")
    sample: dict[str, Any] = {
        "schema_version": ENABLED_STATE_SAMPLE_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "enabled": enabled,
        "ts_utc": _require_token("ts_utc", ts_utc),
    }
    sample[_SAMPLE_HASH_FIELD] = _entry_hash(sample, _SAMPLE_HASH_FIELD)
    return sample


def valid_enabled_state_sample(sample: object, *, window_id: str) -> bool:
    if not isinstance(sample, Mapping):
        return False
    keys = {"schema_version", "window_id", "enabled", "ts_utc", _SAMPLE_HASH_FIELD}
    if set(sample) != keys:
        return False
    if sample.get("schema_version") != ENABLED_STATE_SAMPLE_SCHEMA:
        return False
    if sample.get("window_id") != window_id:
        return False
    if not isinstance(sample.get("enabled"), bool):
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
    points: set[str] = set()
    for observation in observations:
        if valid_served_point_observation(
            observation,
            window_id=window_id,
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
    "ENABLED_STATE_SAMPLE_SCHEMA",
    "ENABLED_SAMPLE_SEQUENCE_SCHEMA",
    "HEAD_ANCHOR_SCHEMA",
    "MAX_DECLARED_SAMPLE_GAP_SECONDS",
    "MAX_ENABLED_SAMPLES_PER_WINDOW",
    "SERVED_POINT_OBSERVATION_SCHEMA",
    "SERVED_POINT_OBSERVATION_SCHEMA_V1",
    "ClaimWindowEvidence",
    "HeadAnchorLookup",
    "LifecycleBindingVerification",
    "build_claim_window_evidence",
    "claim_window_from_evidence",
    "derive_enabled_across_window",
    "derive_enabled_sample_sequence_digest",
    "derive_instrumented_served_points",
    "new_claim_window_final_boundary",
    "new_claim_window_id",
    "new_claim_window_start_boundary",
    "new_clean_shutdown_marker",
    "new_clean_shutdown_marker_v1",
    "new_enabled_state_sample",
    "new_head_anchor",
    "new_served_point_observation",
    "read_clean_shutdown_marker",
    "read_latest_head_anchor",
    "valid_clean_shutdown_marker",
    "valid_clean_shutdown_marker_v1",
    "valid_claim_window_final_boundary",
    "valid_claim_window_start_boundary",
    "valid_enabled_state_sample",
    "valid_head_anchor",
    "valid_served_point_observation",
    "verify_claim_window_lifecycle_binding",
    "write_clean_shutdown_marker",
    "write_enabled_state_sample",
    "write_head_anchor_checkpoint",
    "write_served_point_observation",
]
