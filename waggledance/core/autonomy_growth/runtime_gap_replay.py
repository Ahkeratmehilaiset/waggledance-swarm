# SPDX-License-Identifier: BUSL-1.1
"""Phase 18E - Persisted runtime gap event replay.

Persists Phase 18B-shaped runtime gap events as durable rows in the
existing ``runtime_gap_signals`` table (Phase 12 schema v3), then
replays them through the existing Phase 18B miner and Phase 18C
runtime registration / dispatch path.

Design choice (documented in
``docs/runs/phase18e_runtime_gap_replay_2026_05_06/runtime_gap_replay_design.md``):
this module REUSES the existing ``runtime_gap_signals`` table with a
phase18e-specific ``kind`` discriminator
(``phase18e.runtime_gap_event.v1``). It does NOT add a new table, does
NOT alter the schema, and does NOT touch the Phase 12 detector write
path.

Contract:

* Persisted events are content-keyed by a deterministic ``event_id``
  (16-hex SHA-256 prefix); persisting the same fixture twice is an
  idempotent no-op at the persistence layer.
* The replay path calls :func:`mine_runtime_gaps` (Phase 18B) verbatim
  and :func:`register_mined_solver_specs` (Phase 18C) verbatim. No
  fork, no parallel implementation.
* Forbidden-field events (any payload key matching ``token``,
  ``password``, ``Authorization``, ``secret``, ``api_key``,
  ``private_key`` case-insensitive, OR any value matching common
  cloud / GitHub token regex) fail closed at normalization with
  :class:`GapEventSchemaError`. They are never persisted.
* Out-of-family / high-risk / builder-handoff / insufficient /
  duplicate / malformed events are preserved in the audit trail
  where appropriate but never become executable runtime solvers.
* No provider call. No builder call. No cloud API. No model pull.
* No Stage-2 atomic flip. No HUMAN_APPROVAL collected.
* No allowlist widening.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from waggledance.core.autonomy_growth.gap_candidate import (
    GapCandidate,
    GapMiningResult,
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (
    GapMiningConfig,
    mine_runtime_gaps,
)
from waggledance.core.autonomy_growth.mined_solver_runtime import (
    RegistrationSummary,
    register_mined_solver_specs,
)
from waggledance.core.storage.control_plane import (
    ControlPlaneDB,
    RuntimeGapSignalRecord,
)


PHASE_TAG = "phase18e"
SCHEMA_VERSION = "phase18e.runtime_gap_event.v1"
PHASE18E_RUNTIME_GAP_EVENT_KIND = SCHEMA_VERSION

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "occurred_at_utc",
    "source",
    "family_kind",
    "feature_dict",
    "raw_query",
    "miss_reason",
    "confidence_hint",
    "risk_label",
    "evidence_ref",
    "cluster_window",
)

_FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "token",
    "password",
    "authorization",
    "secret",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
)

_FORBIDDEN_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gho_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"https://x-access-token:[^@\s]+@"),
    re.compile(r"Authorization: Bearer [A-Za-z0-9_.\-]{20,}"),
    re.compile(r"BEGIN (RSA )?PRIVATE KEY"),
)


class GapEventSchemaError(ValueError):
    """Raised when a runtime gap event does not satisfy the
    phase18e.runtime_gap_event.v1 schema."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedGapEvent:
    """One persisted runtime gap event in the canonical Phase 18E shape."""

    event_id: str
    schema_version: str
    occurred_at_utc: str
    source: str
    family_kind: str
    feature_dict: Mapping[str, Any]
    raw_query: str
    miss_reason: str
    confidence_hint: float
    risk_label: str
    evidence_ref: str
    cluster_window: str
    provenance_hash: str
    signal_id: str
    notes: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "event_id": self.event_id,
            "schema_version": self.schema_version,
            "occurred_at_utc": self.occurred_at_utc,
            "source": self.source,
            "family_kind": self.family_kind,
            "feature_dict": dict(self.feature_dict),
            "raw_query": self.raw_query,
            "miss_reason": self.miss_reason,
            "confidence_hint": self.confidence_hint,
            "risk_label": self.risk_label,
            "evidence_ref": self.evidence_ref,
            "cluster_window": self.cluster_window,
            "provenance_hash": self.provenance_hash,
            "signal_id": self.signal_id,
        }
        if self.notes is not None:
            out["notes"] = self.notes
        return out

    def to_phase18b_signal(self) -> dict[str, Any]:
        """Convert to the mapping shape ``mine_runtime_gaps`` consumes."""
        return {
            "signal_id": self.signal_id,
            "family_kind": self.family_kind,
            "feature_dict": dict(self.feature_dict),
            "cluster_window": self.cluster_window,
            "confidence_hint": self.confidence_hint,
            "risk_label": self.risk_label,
            "evidence_ref": self.evidence_ref,
            "raw_query": self.raw_query,
            "miss_reason": self.miss_reason,
        }


@dataclass(frozen=True)
class GapPersistResult:
    inserted_event_ids: tuple[str, ...]
    skipped_existing_event_ids: tuple[str, ...]
    rejected_event_count: int
    malformed_event_rejection_count: int
    forbidden_field_rejections: int
    rejected_reasons: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted_event_ids": list(self.inserted_event_ids),
            "skipped_existing_event_ids": list(self.skipped_existing_event_ids),
            "rejected_event_count": self.rejected_event_count,
            "malformed_event_rejection_count": (
                self.malformed_event_rejection_count
            ),
            "forbidden_field_rejections": self.forbidden_field_rejections,
            "rejected_reasons": dict(self.rejected_reasons),
        }


@dataclass(frozen=True)
class GapReplayResult:
    loaded_event_count: int
    rejected_at_normalize: int
    forbidden_field_rejections: int
    mining_result: GapMiningResult
    registration_summary: RegistrationSummary
    counters: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded_event_count": self.loaded_event_count,
            "rejected_at_normalize": self.rejected_at_normalize,
            "forbidden_field_rejections": self.forbidden_field_rejections,
            "mining_result": self.mining_result.to_dict(),
            "registration_summary": self.registration_summary.to_dict(),
            "counters": dict(self.counters),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        default=str, ensure_ascii=True,
    )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_forbidden_fields(payload: Mapping[str, Any]) -> Optional[str]:
    """Return a redacted reason if the payload contains a forbidden
    key name or token-shaped value; else None.

    The reason string never contains the matched secret value.
    """
    for k, v in payload.items():
        kl = str(k).lower()
        for forbidden in _FORBIDDEN_KEY_SUBSTRINGS:
            if forbidden in kl:
                return f"forbidden_field_key:{forbidden}"
    blob = _canonical_json(dict(payload))
    for pat in _FORBIDDEN_VALUE_PATTERNS:
        if pat.search(blob):
            return f"forbidden_value_pattern:{pat.pattern[:30]}"
    return None


def _compute_event_id(family_kind: str,
                       feature_dict: Mapping[str, Any],
                       cluster_window: str,
                       evidence_ref: str) -> str:
    payload = "|".join([
        family_kind,
        _canonical_json(dict(feature_dict)),
        cluster_window or "",
        evidence_ref or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _compute_provenance_hash(d: Mapping[str, Any]) -> str:
    """Stable SHA-256 hex over the canonical dict minus
    ``provenance_hash`` itself."""
    sub = {k: v for k, v in d.items() if k != "provenance_hash"}
    return hashlib.sha256(_canonical_json(sub).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_runtime_gap_event(
    raw: Mapping[str, Any],
) -> PersistedGapEvent:
    """Validate + canonicalize a raw event mapping into a frozen
    :class:`PersistedGapEvent`. Raises :class:`GapEventSchemaError`
    on schema violation."""
    if not isinstance(raw, Mapping):
        raise GapEventSchemaError(
            f"event must be a mapping, got {type(raw).__name__}"
        )

    forbidden = _check_forbidden_fields(raw)
    if forbidden is not None:
        raise GapEventSchemaError(f"forbidden field in event: {forbidden}")

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise GapEventSchemaError(
            f"unsupported schema_version={schema_version!r}; "
            f"expected {SCHEMA_VERSION!r}"
        )

    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise GapEventSchemaError(
            f"missing required field(s): {missing}"
        )

    family_kind = str(raw["family_kind"])
    feature_dict_raw = raw["feature_dict"]
    if not isinstance(feature_dict_raw, Mapping):
        raise GapEventSchemaError(
            "feature_dict must be a mapping"
        )
    feature_dict = dict(feature_dict_raw)

    cluster_window = str(raw.get("cluster_window") or "")
    evidence_ref = str(raw.get("evidence_ref") or "")
    raw_query = str(raw.get("raw_query") or "")
    miss_reason = str(raw.get("miss_reason") or "")
    risk_label = str(raw.get("risk_label") or "low_risk")

    try:
        confidence_hint = float(raw["confidence_hint"])
    except (TypeError, ValueError) as exc:
        raise GapEventSchemaError(
            f"confidence_hint must be a number: {exc}"
        ) from exc
    if not (0.0 <= confidence_hint <= 1.0):
        raise GapEventSchemaError(
            f"confidence_hint out of [0,1]: {confidence_hint}"
        )

    occurred_at_utc = str(raw["occurred_at_utc"])
    source = str(raw["source"])

    event_id = str(
        raw.get("event_id")
        or _compute_event_id(
            family_kind, feature_dict, cluster_window, evidence_ref,
        )
    )

    signal_id = str(raw.get("signal_id") or f"phase18e:{event_id}")
    notes_raw = raw.get("notes")
    notes = str(notes_raw) if notes_raw is not None else None

    canonical_for_hash = {
        "event_id": event_id,
        "schema_version": schema_version,
        "occurred_at_utc": occurred_at_utc,
        "source": source,
        "family_kind": family_kind,
        "feature_dict": feature_dict,
        "raw_query": raw_query,
        "miss_reason": miss_reason,
        "confidence_hint": confidence_hint,
        "risk_label": risk_label,
        "evidence_ref": evidence_ref,
        "cluster_window": cluster_window,
        "signal_id": signal_id,
    }
    provenance_hash = _compute_provenance_hash(canonical_for_hash)

    return PersistedGapEvent(
        event_id=event_id,
        schema_version=schema_version,
        occurred_at_utc=occurred_at_utc,
        source=source,
        family_kind=family_kind,
        feature_dict=feature_dict,
        raw_query=raw_query,
        miss_reason=miss_reason,
        confidence_hint=confidence_hint,
        risk_label=risk_label,
        evidence_ref=evidence_ref,
        cluster_window=cluster_window,
        provenance_hash=provenance_hash,
        signal_id=signal_id,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_runtime_gap_events(
    control_plane: ControlPlaneDB,
    events: Sequence[Mapping[str, Any]],
) -> GapPersistResult:
    """Persist a batch of raw runtime gap events into
    ``runtime_gap_signals`` under
    ``kind = phase18e.runtime_gap_event.v1``.

    Idempotent: events whose ``event_id`` is already present are
    skipped, not re-inserted. Malformed / forbidden-field events are
    rejected with their reason counted (no values printed)."""
    if control_plane is None:
        raise ValueError("control_plane is required")

    rejected_reasons: dict[str, int] = {}
    forbidden_field_rejections = 0
    malformed_count = 0
    inserted: list[str] = []
    skipped: list[str] = []

    existing_records = control_plane.list_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
    )
    existing_ids: set[str] = set()
    for rec in existing_records:
        try:
            payload = json.loads(rec.signal_payload or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, Mapping):
            # Type-confused or non-object signal_payload (e.g. JSON
            # string / array / null). Cannot extract event_id; treat
            # as opaque and skip rather than raise.
            continue
        ev_id = payload.get("event_id")
        if isinstance(ev_id, str) and ev_id:
            existing_ids.add(ev_id)

    for raw in events:
        try:
            ev = normalize_runtime_gap_event(raw)
        except GapEventSchemaError as exc:
            reason = str(exc)
            if "forbidden field" in reason:
                forbidden_field_rejections += 1
            else:
                malformed_count += 1
            key = reason.split(":", 1)[0] if ":" in reason else reason
            rejected_reasons[key] = rejected_reasons.get(key, 0) + 1
            continue

        if ev.event_id in existing_ids:
            skipped.append(ev.event_id)
            continue

        signal_payload = _canonical_json(ev.to_dict())
        control_plane.record_runtime_gap_signal(
            kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
            family_kind=ev.family_kind,
            cell_coord=None,
            signal_payload=signal_payload,
            weight=float(ev.confidence_hint),
            observed_at=ev.occurred_at_utc,
        )
        existing_ids.add(ev.event_id)
        inserted.append(ev.event_id)

    rejected_total = malformed_count + forbidden_field_rejections
    return GapPersistResult(
        inserted_event_ids=tuple(inserted),
        skipped_existing_event_ids=tuple(skipped),
        rejected_event_count=rejected_total,
        malformed_event_rejection_count=malformed_count,
        forbidden_field_rejections=forbidden_field_rejections,
        rejected_reasons=dict(rejected_reasons),
    )


def load_runtime_gap_events(
    control_plane: ControlPlaneDB,
    *,
    source: Optional[str] = None,
) -> list[PersistedGapEvent]:
    """Load all phase18e events from ``runtime_gap_signals``.

    ``source`` filters in-memory by the event's ``source`` field after
    deserialization. Malformed payloads (non-JSON, schema mismatch)
    raise :class:`GapEventSchemaError` and are NOT silently dropped."""
    rows = control_plane.list_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
    )
    out: list[PersistedGapEvent] = []
    for row in rows:
        if not row.signal_payload:
            raise GapEventSchemaError(
                f"row id={row.id} has empty signal_payload"
            )
        try:
            data = json.loads(row.signal_payload)
        except json.JSONDecodeError as exc:
            raise GapEventSchemaError(
                f"row id={row.id} signal_payload is not valid JSON: {exc}"
            ) from exc
        ev = normalize_runtime_gap_event(data)
        if source is not None and ev.source != source:
            continue
        out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay_persisted_gap_events(
    control_plane: ControlPlaneDB,
    *,
    config: Optional[GapMiningConfig] = None,
    source: Optional[str] = None,
) -> GapReplayResult:
    """Load all phase18e persisted events, run them through the
    Phase 18B miner, then through the Phase 18C registration path.

    Returns a :class:`GapReplayResult` bundling counts that the proof
    harness uses to compute its release-gate verdict.
    """
    if control_plane is None:
        raise ValueError("control_plane is required")

    rows = control_plane.list_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
    )

    events: list[PersistedGapEvent] = []
    rejected_at_normalize = 0
    forbidden_field_rejections = 0
    for row in rows:
        if not row.signal_payload:
            rejected_at_normalize += 1
            continue
        try:
            data = json.loads(row.signal_payload)
        except json.JSONDecodeError:
            rejected_at_normalize += 1
            continue
        try:
            ev = normalize_runtime_gap_event(data)
        except GapEventSchemaError as exc:
            if "forbidden" in str(exc):
                forbidden_field_rejections += 1
            else:
                rejected_at_normalize += 1
            continue
        if source is not None and ev.source != source:
            continue
        events.append(ev)

    signals = [ev.to_phase18b_signal() for ev in events]
    mining_result = mine_runtime_gaps(signals, config=config)

    registration_summary = register_mined_solver_specs(
        candidates=mining_result.candidates,
        control_plane=control_plane,
    )

    counters: dict[str, int] = {}
    counters.update(dict(mining_result.counters))
    counters["loaded_event_count"] = len(events)
    counters["rejected_at_normalize"] = rejected_at_normalize
    counters["forbidden_field_rejections"] = forbidden_field_rejections
    counters["registered_solver_count"] = registration_summary.registered_count
    counters["non_allowlisted_rejected_count"] = (
        registration_summary.rejected_count
    )

    return GapReplayResult(
        loaded_event_count=len(events),
        rejected_at_normalize=rejected_at_normalize,
        forbidden_field_rejections=forbidden_field_rejections,
        mining_result=mining_result,
        registration_summary=registration_summary,
        counters=counters,
    )


__all__ = [
    "PHASE_TAG",
    "PHASE18E_RUNTIME_GAP_EVENT_KIND",
    "SCHEMA_VERSION",
    "REQUIRED_FIELDS",
    "GapEventSchemaError",
    "PersistedGapEvent",
    "GapPersistResult",
    "GapReplayResult",
    "normalize_runtime_gap_event",
    "persist_runtime_gap_events",
    "load_runtime_gap_events",
    "replay_persisted_gap_events",
]
