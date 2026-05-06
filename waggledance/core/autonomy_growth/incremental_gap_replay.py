# SPDX-License-Identifier: BUSL-1.1
"""Phase 18F - Incremental runtime gap replay.

Cursor-based, lock-protected, fail-closed incremental replay over the
existing Phase 18E persisted runtime gap events. Reuses verbatim:

* `runtime_gap_signals` table (Phase 12 schema v3, no schema change);
* `kind = phase18e.runtime_gap_event.v1` discriminator (Phase 18E);
* `mine_runtime_gaps` (Phase 18B);
* `register_mined_solver_specs` (Phase 18C);
* `LowRiskSolverDispatcher.dispatch_by_features` (Phase 17A).

What's new in Phase 18F:

* Cursor in `schema_meta` under key
  ``phase18f.replay_cursor.v1`` — durable, monotonic, gap-free.
* Replay lock in `schema_meta` under key
  ``phase18f.replay_lock.v1`` — concurrent-attempt protection.
* `load_runtime_gap_events_after_id()` — strict + counted-skip loader
  (rejects malformed JSON, type-confused JSON, forbidden fields,
  non-Mapping feature_dict; counts and skips rather than raising so a
  single corrupted historical row cannot brick replay).
* `bridge_detector_signal_to_phase18e_event()` — adapter from the
  Phase 12 ``GapSignal`` shape into the canonical Phase 18E event
  mapping. The detector write path (`RuntimeGapDetector.record`) is
  untouched.
* `run_incremental_gap_replay_once()` — the production-shaped
  primitive: acquire lock, read cursor, load new rows, mine, register,
  advance cursor on success, release lock.

No allowlist widening. No new pip dependency. No model pull / cloud
call / live builder execution / Stage-2 flip / HUMAN_APPROVAL.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from waggledance.core.autonomy_growth.gap_candidate import (
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_intake import GapSignal
from waggledance.core.autonomy_growth.gap_mining import (
    GapMiningConfig,
    mine_runtime_gaps,
)
from waggledance.core.autonomy_growth.mined_solver_runtime import (
    RegistrationSummary,
    register_mined_solver_specs,
)
from waggledance.core.autonomy_growth.runtime_gap_replay import (
    PHASE18E_RUNTIME_GAP_EVENT_KIND,
    GapEventSchemaError,
    PersistedGapEvent,
    normalize_runtime_gap_event,
    persist_runtime_gap_events,
)
from waggledance.core.storage.control_plane import (
    ControlPlaneDB,
    RuntimeGapSignalRecord,
)


REPLAY_CURSOR_KEY = "phase18f.replay_cursor.v1"
REPLAY_LOCK_KEY = "phase18f.replay_lock.v1"
DEFAULT_LOCK_TTL_SECONDS = 30


class BridgeRejectionError(ValueError):
    """Raised when a Phase 12 detector signal cannot be bridged to a
    Phase 18E event. Fail-closed: bridge does not coerce malformed
    detector rows."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayCursor:
    last_processed_id: int
    advanced_at_utc: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "last_processed_id": int(self.last_processed_id),
                "advanced_at_utc": str(self.advanced_at_utc),
            },
            sort_keys=True, separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "ReplayCursor":
        data = json.loads(text)
        return cls(
            last_processed_id=int(data.get("last_processed_id", 0)),
            advanced_at_utc=str(data.get("advanced_at_utc", "")),
        )


@dataclass(frozen=True)
class ReplayLock:
    acquired_at_utc: str
    owner: str
    ttl_seconds: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "acquired_at_utc": str(self.acquired_at_utc),
                "owner": str(self.owner),
                "ttl_seconds": int(self.ttl_seconds),
            },
            sort_keys=True, separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "ReplayLock":
        data = json.loads(text)
        return cls(
            acquired_at_utc=str(data.get("acquired_at_utc", "")),
            owner=str(data.get("owner", "")),
            ttl_seconds=int(data.get("ttl_seconds", DEFAULT_LOCK_TTL_SECONDS)),
        )


@dataclass(frozen=True)
class IncrementalLoadResult:
    """Outcome of ``load_runtime_gap_events_after_id`` — strict +
    counted-skip behavior over the ``runtime_gap_signals`` rows after
    the cursor."""

    events: tuple[PersistedGapEvent, ...]
    max_id_seen: int
    rows_examined: int
    malformed_event_rejection_count: int
    type_confusion_rejection_count: int
    forbidden_field_rejections: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [ev.to_dict() for ev in self.events],
            "max_id_seen": self.max_id_seen,
            "rows_examined": self.rows_examined,
            "malformed_event_rejection_count": (
                self.malformed_event_rejection_count
            ),
            "type_confusion_rejection_count": (
                self.type_confusion_rejection_count
            ),
            "forbidden_field_rejections": self.forbidden_field_rejections,
        }


@dataclass(frozen=True)
class IncrementalReplayResult:
    """Outcome of one ``run_incremental_gap_replay_once`` call."""

    status: str
    loaded_event_count: int
    new_max_id: int
    cursor_before: int
    cursor_after: int
    cursor_advanced: bool
    registered_solver_count: int
    non_allowlisted_rejected_count: int
    families_covered: int
    malformed_event_rejection_count: int
    type_confusion_rejection_count: int
    forbidden_field_rejections: int
    error_message: Optional[str] = None
    registration_summary: Optional[RegistrationSummary] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "loaded_event_count": self.loaded_event_count,
            "new_max_id": self.new_max_id,
            "cursor_before": self.cursor_before,
            "cursor_after": self.cursor_after,
            "cursor_advanced": self.cursor_advanced,
            "registered_solver_count": self.registered_solver_count,
            "non_allowlisted_rejected_count": (
                self.non_allowlisted_rejected_count
            ),
            "families_covered": self.families_covered,
            "malformed_event_rejection_count": (
                self.malformed_event_rejection_count
            ),
            "type_confusion_rejection_count": (
                self.type_confusion_rejection_count
            ),
            "forbidden_field_rejections": self.forbidden_field_rejections,
            "error_message": self.error_message,
            "registration_summary": (
                self.registration_summary.to_dict()
                if self.registration_summary is not None else None
            ),
        }


@dataclass(frozen=True)
class DetectorBridgeResult:
    """Outcome of ``persist_detector_gap_signals_as_replay_events``."""

    persisted_event_ids: tuple[str, ...]
    skipped_existing_event_ids: tuple[str, ...]
    bridge_rejected_count: int
    persist_rejected_count: int
    forbidden_field_rejections: int
    malformed_event_rejection_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "persisted_event_ids": list(self.persisted_event_ids),
            "skipped_existing_event_ids": list(
                self.skipped_existing_event_ids,
            ),
            "bridge_rejected_count": self.bridge_rejected_count,
            "persist_rejected_count": self.persist_rejected_count,
            "forbidden_field_rejections": self.forbidden_field_rejections,
            "malformed_event_rejection_count": (
                self.malformed_event_rejection_count
            ),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        # Accept both with and without trailing Z.
        clean = ts[:-1] if ts.endswith("Z") else ts
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _process_owner() -> str:
    return f"{os.getpid()}:{platform.node()}"


# ---------------------------------------------------------------------------
# Cursor + lock state
# ---------------------------------------------------------------------------


def read_replay_cursor(cp: ControlPlaneDB) -> ReplayCursor:
    raw = cp.get_meta(REPLAY_CURSOR_KEY)
    if raw is None:
        return ReplayCursor(last_processed_id=0, advanced_at_utc="")
    try:
        return ReplayCursor.from_json(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return ReplayCursor(last_processed_id=0, advanced_at_utc="")


def write_replay_cursor(
    cp: ControlPlaneDB,
    *,
    last_processed_id: int,
    advanced_at_utc: Optional[str] = None,
) -> ReplayCursor:
    cursor = ReplayCursor(
        last_processed_id=int(last_processed_id),
        advanced_at_utc=advanced_at_utc or _utcnow_iso(),
    )
    cp.set_meta(REPLAY_CURSOR_KEY, cursor.to_json())
    return cursor


def acquire_replay_lock(
    cp: ControlPlaneDB,
    *,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    owner: Optional[str] = None,
    now_utc: Optional[str] = None,
) -> Optional[ReplayLock]:
    """Try to acquire the Phase 18F replay lock. Returns the held
    ``ReplayLock`` on success or None if a non-stale lock is already
    held."""
    now_iso = now_utc or _utcnow_iso()
    raw = cp.get_meta(REPLAY_LOCK_KEY)
    if raw is not None:
        try:
            existing = ReplayLock.from_json(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            existing = None
        if existing is not None:
            acquired = _parse_iso(existing.acquired_at_utc)
            now_dt = _parse_iso(now_iso)
            if acquired is not None and now_dt is not None:
                if (now_dt - acquired) < timedelta(seconds=existing.ttl_seconds):
                    return None  # non-stale lock present
    lock = ReplayLock(
        acquired_at_utc=now_iso,
        owner=owner or _process_owner(),
        ttl_seconds=int(ttl_seconds),
    )
    cp.set_meta(REPLAY_LOCK_KEY, lock.to_json())
    return lock


def release_replay_lock(cp: ControlPlaneDB) -> bool:
    return cp.delete_meta(REPLAY_LOCK_KEY)


# ---------------------------------------------------------------------------
# Strict + counted-skip loader
# ---------------------------------------------------------------------------


def load_runtime_gap_events_after_id(
    cp: ControlPlaneDB,
    *,
    after_id: int,
    limit: Optional[int] = None,
) -> IncrementalLoadResult:
    """Load Phase 18E events with id > after_id. Strict: malformed
    JSON / type-confused JSON / forbidden-field events are rejected
    (counted, NOT raised). Successful events return as
    :class:`PersistedGapEvent`."""
    rows: list[RuntimeGapSignalRecord] = cp.list_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
        after_id=int(after_id),
        limit=limit,
    )
    events: list[PersistedGapEvent] = []
    malformed = 0
    type_conf = 0
    forbidden = 0
    max_id_seen = int(after_id)
    for row in rows:
        max_id_seen = max(max_id_seen, int(row.id))
        if not row.signal_payload:
            malformed += 1
            continue
        try:
            data = json.loads(row.signal_payload)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(data, Mapping):
            type_conf += 1
            continue
        try:
            ev = normalize_runtime_gap_event(data)
        except GapEventSchemaError as exc:
            if "forbidden" in str(exc):
                forbidden += 1
            else:
                malformed += 1
            continue
        events.append(ev)
    return IncrementalLoadResult(
        events=tuple(events),
        max_id_seen=max_id_seen,
        rows_examined=len(rows),
        malformed_event_rejection_count=malformed,
        type_confusion_rejection_count=type_conf,
        forbidden_field_rejections=forbidden,
    )


# ---------------------------------------------------------------------------
# RuntimeGapDetector bridge
# ---------------------------------------------------------------------------


def bridge_detector_signal_to_phase18e_event(
    signal: GapSignal,
    *,
    raw_query: str,
    miss_reason: str,
    confidence_hint: float,
    risk_label: str,
    evidence_ref: str,
    cluster_window: str = "",
    occurred_at_utc: Optional[str] = None,
    source: str = "phase18f_detector_bridge",
) -> dict[str, Any]:
    """Adapt a Phase 12 ``GapSignal`` into a Phase 18E event mapping
    suitable for ``persist_runtime_gap_events``. Fail-closed: missing
    or wrong-typed payload, missing family_kind, or out-of-range
    confidence raise :class:`BridgeRejectionError`."""
    if signal is None:
        raise BridgeRejectionError("signal is None")
    if not isinstance(signal.family_kind, str) or not signal.family_kind:
        raise BridgeRejectionError(
            "signal.family_kind must be a non-empty string"
        )
    if signal.payload is None:
        raise BridgeRejectionError(
            "signal.payload is required and must be a Mapping"
        )
    if not isinstance(signal.payload, Mapping):
        raise BridgeRejectionError(
            f"signal.payload must be Mapping, got "
            f"{type(signal.payload).__name__}"
        )
    feature_dict = signal.payload.get("feature_dict")
    if not isinstance(feature_dict, Mapping):
        raise BridgeRejectionError(
            "signal.payload['feature_dict'] must be a Mapping"
        )
    try:
        conf = float(confidence_hint)
    except (TypeError, ValueError) as exc:
        raise BridgeRejectionError(
            f"confidence_hint must be a number: {exc}"
        ) from exc
    if not (0.0 <= conf <= 1.0):
        raise BridgeRejectionError(
            f"confidence_hint out of [0,1]: {conf}"
        )
    return {
        "schema_version": "phase18e.runtime_gap_event.v1",
        "occurred_at_utc": occurred_at_utc or _utcnow_iso(),
        "source": source,
        "family_kind": signal.family_kind,
        "feature_dict": dict(feature_dict),
        "raw_query": str(raw_query),
        "miss_reason": str(miss_reason),
        "confidence_hint": conf,
        "risk_label": str(risk_label),
        "evidence_ref": str(evidence_ref),
        "cluster_window": str(cluster_window or ""),
        "signal_id": (
            f"phase18f_detector:{signal.family_kind}:{signal.kind}"
        ),
    }


def persist_detector_gap_signals_as_replay_events(
    cp: ControlPlaneDB,
    items: Sequence[tuple[GapSignal, Mapping[str, Any]]],
) -> DetectorBridgeResult:
    """Bridge each (signal, kwargs) pair into a Phase 18E event and
    persist via ``persist_runtime_gap_events``. Counts bridge
    rejections separately from persist rejections so callers can tell
    how many detector rows were rejected vs how many persisted-but-
    forbidden events were caught at normalization."""
    bridged_events: list[dict[str, Any]] = []
    bridge_rejected = 0
    for signal, kwargs in items:
        try:
            ev = bridge_detector_signal_to_phase18e_event(signal, **kwargs)
        except BridgeRejectionError:
            bridge_rejected += 1
            continue
        bridged_events.append(ev)
    persist_result = persist_runtime_gap_events(cp, bridged_events)
    return DetectorBridgeResult(
        persisted_event_ids=persist_result.inserted_event_ids,
        skipped_existing_event_ids=(
            persist_result.skipped_existing_event_ids
        ),
        bridge_rejected_count=bridge_rejected,
        persist_rejected_count=persist_result.rejected_event_count,
        forbidden_field_rejections=(
            persist_result.forbidden_field_rejections
        ),
        malformed_event_rejection_count=(
            persist_result.malformed_event_rejection_count
        ),
    )


# ---------------------------------------------------------------------------
# Run-once orchestrator
# ---------------------------------------------------------------------------


def run_incremental_gap_replay_once(
    cp: ControlPlaneDB,
    *,
    config: Optional[GapMiningConfig] = None,
    lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    skip_lock: bool = False,
) -> IncrementalReplayResult:
    """Run one incremental replay tick.

    * Acquires the Phase 18F replay lock unless ``skip_lock=True``;
      returns LOCKED_NOT_RUN if a non-stale lock is already held.
    * Loads phase18e events with id > cursor.last_processed_id.
    * Calls ``mine_runtime_gaps`` and ``register_mined_solver_specs``.
    * Advances the cursor to ``max_id_seen`` only on success.
    * Releases the lock on the way out (also on FAILED_NO_ADVANCE).
    """
    cursor_before = read_replay_cursor(cp)

    if not skip_lock:
        acquired = acquire_replay_lock(cp, ttl_seconds=lock_ttl_seconds)
        if acquired is None:
            return IncrementalReplayResult(
                status="LOCKED_NOT_RUN",
                loaded_event_count=0,
                new_max_id=cursor_before.last_processed_id,
                cursor_before=cursor_before.last_processed_id,
                cursor_after=cursor_before.last_processed_id,
                cursor_advanced=False,
                registered_solver_count=0,
                non_allowlisted_rejected_count=0,
                families_covered=0,
                malformed_event_rejection_count=0,
                type_confusion_rejection_count=0,
                forbidden_field_rejections=0,
                error_message=None,
                registration_summary=None,
            )

    try:
        load = load_runtime_gap_events_after_id(
            cp, after_id=cursor_before.last_processed_id,
        )
        events = list(load.events)
        if not events:
            cursor_after = cursor_before.last_processed_id
            return IncrementalReplayResult(
                status="OK",
                loaded_event_count=0,
                new_max_id=load.max_id_seen,
                cursor_before=cursor_before.last_processed_id,
                cursor_after=cursor_after,
                cursor_advanced=False,
                registered_solver_count=0,
                non_allowlisted_rejected_count=0,
                families_covered=0,
                malformed_event_rejection_count=(
                    load.malformed_event_rejection_count
                ),
                type_confusion_rejection_count=(
                    load.type_confusion_rejection_count
                ),
                forbidden_field_rejections=load.forbidden_field_rejections,
                error_message=None,
                registration_summary=None,
            )

        signals = [ev.to_phase18b_signal() for ev in events]
        try:
            mining = mine_runtime_gaps(signals, config=config)
            registration = register_mined_solver_specs(
                candidates=mining.candidates,
                control_plane=cp,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return IncrementalReplayResult(
                status="FAILED_NO_ADVANCE",
                loaded_event_count=len(events),
                new_max_id=load.max_id_seen,
                cursor_before=cursor_before.last_processed_id,
                cursor_after=cursor_before.last_processed_id,
                cursor_advanced=False,
                registered_solver_count=0,
                non_allowlisted_rejected_count=0,
                families_covered=0,
                malformed_event_rejection_count=(
                    load.malformed_event_rejection_count
                ),
                type_confusion_rejection_count=(
                    load.type_confusion_rejection_count
                ),
                forbidden_field_rejections=load.forbidden_field_rejections,
                error_message=str(exc),
                registration_summary=None,
            )

        # Advance cursor to the highest id we actually saw.
        new_cursor_id = load.max_id_seen
        write_replay_cursor(cp, last_processed_id=new_cursor_id)

        # Families_covered counts unique allowlisted-registered families.
        registered_family_kinds: set[str] = set()
        cand_by_id = {c.candidate_id: c for c in mining.candidates}
        for cid in registration.registered_candidate_ids:
            cand = cand_by_id.get(cid)
            if cand is not None:
                registered_family_kinds.add(cand.family_kind)

        return IncrementalReplayResult(
            status="OK",
            loaded_event_count=len(events),
            new_max_id=load.max_id_seen,
            cursor_before=cursor_before.last_processed_id,
            cursor_after=new_cursor_id,
            cursor_advanced=new_cursor_id > cursor_before.last_processed_id,
            registered_solver_count=registration.registered_count,
            non_allowlisted_rejected_count=registration.rejected_count,
            families_covered=len(registered_family_kinds),
            malformed_event_rejection_count=(
                load.malformed_event_rejection_count
            ),
            type_confusion_rejection_count=(
                load.type_confusion_rejection_count
            ),
            forbidden_field_rejections=load.forbidden_field_rejections,
            error_message=None,
            registration_summary=registration,
        )
    finally:
        if not skip_lock:
            release_replay_lock(cp)


__all__ = [
    "REPLAY_CURSOR_KEY",
    "REPLAY_LOCK_KEY",
    "DEFAULT_LOCK_TTL_SECONDS",
    "BridgeRejectionError",
    "ReplayCursor",
    "ReplayLock",
    "IncrementalLoadResult",
    "IncrementalReplayResult",
    "DetectorBridgeResult",
    "read_replay_cursor",
    "write_replay_cursor",
    "acquire_replay_lock",
    "release_replay_lock",
    "load_runtime_gap_events_after_id",
    "bridge_detector_signal_to_phase18e_event",
    "persist_detector_gap_signals_as_replay_events",
    "run_incremental_gap_replay_once",
]
