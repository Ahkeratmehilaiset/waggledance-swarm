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
from pathlib import Path
from typing import Any, NamedTuple

from waggledance.core.magma.canonical import sha256_digest
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

_ANCHOR_HASH_FIELD = "anchor_hash"
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


def _canonical_without(entry: Mapping[str, Any], hash_field: str) -> dict[str, Any]:
    return {key: entry[key] for key in sorted(entry) if key != hash_field}


def _entry_hash(entry: Mapping[str, Any], hash_field: str) -> str:
    return sha256_digest(_canonical_without(entry, hash_field))


def _require_token(field: str, value: object) -> str:
    if not is_conforming_token(value):
        raise ValueError(f"{field} is not a conforming token")
    return str(value)


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
    fsync: bool = True,
) -> None:
    """Append one hash-validated served-point observation to a JSONL store."""
    observation = new_served_point_observation(
        point=point,
        wired=wired,
        ts_utc=ts_utc,
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
    sample: dict[str, Any] = {
        "schema_version": ENABLED_STATE_SAMPLE_SCHEMA,
        "window_id": _require_token("window_id", window_id),
        "enabled": bool(enabled),
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
    if not is_conforming_token(sample.get("ts_utc")):
        return False
    return sample.get(_SAMPLE_HASH_FIELD) == _entry_hash(sample, _SAMPLE_HASH_FIELD)


def derive_enabled_across_window(
    samples: Iterable[Mapping[str, Any]],
    *,
    window_id: str,
) -> bool:
    seen = False
    for sample in samples:
        if not valid_enabled_state_sample(sample, window_id=window_id):
            return False
        seen = True
        if sample.get("enabled") is not True:
            return False
    return seen


def new_served_point_observation(
    *,
    point: str,
    wired: bool,
    ts_utc: str,
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "schema_version": SERVED_POINT_OBSERVATION_SCHEMA,
        "point": _require_token("point", point),
        "wired": bool(wired),
        "ts_utc": _require_token("ts_utc", ts_utc),
    }
    observation[_POINT_HASH_FIELD] = _entry_hash(observation, _POINT_HASH_FIELD)
    return observation


def valid_served_point_observation(observation: object) -> bool:
    if not isinstance(observation, Mapping):
        return False
    keys = {"schema_version", "point", "wired", "ts_utc", _POINT_HASH_FIELD}
    if set(observation) != keys:
        return False
    if observation.get("schema_version") != SERVED_POINT_OBSERVATION_SCHEMA:
        return False
    if observation.get("point") not in REQUIRED_CHAT_SERVED_POINTS:
        return False
    if not isinstance(observation.get("wired"), bool):
        return False
    if not is_conforming_token(observation.get("ts_utc")):
        return False
    return observation.get(_POINT_HASH_FIELD) == _entry_hash(observation, _POINT_HASH_FIELD)


def derive_instrumented_served_points(
    observations: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    points: set[str] = set()
    for observation in observations:
        if valid_served_point_observation(observation) and observation.get("wired"):
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
    elif not enabled:
        reason = "enabled_window_not_proven"
    elif not clean:
        reason = "clean_shutdown_not_proven"
    elif missing:
        reason = "served_point_instrumentation_not_proven"

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
    return claim_window_from_ledger(
        ledger_path,
        expected_head=evidence.expected_head,
        enabled_across_window=evidence.enabled_across_window,
        clean_shutdown=evidence.clean_shutdown,
        instrumented_served_points=evidence.instrumented_served_points,
        required_served_points=evidence.required_served_points,
        pending_failure_ledger_path=pending_failure_ledger_path,
    )


__all__ = [
    "CLEAN_SHUTDOWN_MARKER_SCHEMA",
    "ENABLED_STATE_SAMPLE_SCHEMA",
    "HEAD_ANCHOR_SCHEMA",
    "SERVED_POINT_OBSERVATION_SCHEMA",
    "ClaimWindowEvidence",
    "HeadAnchorLookup",
    "build_claim_window_evidence",
    "claim_window_from_evidence",
    "derive_enabled_across_window",
    "derive_instrumented_served_points",
    "new_clean_shutdown_marker",
    "new_enabled_state_sample",
    "new_head_anchor",
    "new_served_point_observation",
    "read_clean_shutdown_marker",
    "read_latest_head_anchor",
    "valid_clean_shutdown_marker",
    "valid_enabled_state_sample",
    "valid_head_anchor",
    "valid_served_point_observation",
    "write_clean_shutdown_marker",
    "write_enabled_state_sample",
    "write_head_anchor_checkpoint",
    "write_served_point_observation",
]
