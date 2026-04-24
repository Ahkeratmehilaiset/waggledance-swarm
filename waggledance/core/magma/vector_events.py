"""MAGMA vector events — contract for the FAISS storage layer.

These are the four event types the Phase-8 MAGMA/FAISS scaling plan
commits to (see `docs/architecture/MAGMA_FAISS_SCALING.md`). They are
defined here so code paths that write to FAISS today can already emit
them; consumers (vector-indexer, cold-archiver) arrive in Stage 2.

Today these events have no effect on the runtime — there is no active
consumer. Defining them now lets us:
- record them in MAGMA alongside the existing 28 autonomy events
- migrate the FAISS write path to an event-sourced projection without
  a schema break later
- keep the audit trail self-consistent even before JetStream lands

Nothing in this module imports from a runtime bus. If you need to emit
an event into MAGMA today, construct the dataclass and hand it to
`core.audit_log.append()` via the usual path — or log it as JSON for
the migration tool to consume later.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# Event name constants. Kept as module-level strings so consumers can
# compare identities without importing enum.
EVT_SOLVER_UPSERTED = "solver.upserted"
EVT_VECTOR_UPSERT_REQUESTED = "vector.upsert_requested"
EVT_VECTOR_DELETE_REQUESTED = "vector.delete_requested"
EVT_VECTOR_COMMIT_APPLIED = "vector.commit_applied"

ALL_VECTOR_EVENT_NAMES: tuple[str, ...] = (
    EVT_SOLVER_UPSERTED,
    EVT_VECTOR_UPSERT_REQUESTED,
    EVT_VECTOR_DELETE_REQUESTED,
    EVT_VECTOR_COMMIT_APPLIED,
)

# Schema version of the event payload envelope below. Increment on any
# breaking change to the field set; consumers should refuse unknown
# versions.
VECTOR_EVENT_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class VectorEvent:
    """One MAGMA vector event.

    All four event types share this envelope; `event` tags which one
    and `payload` carries type-specific fields.

    The dataclass is frozen so one event cannot be mutated after
    construction — matching the append-only nature of the audit log.
    """
    event: str
    cell_id: str
    solver_id: str | None = None
    ts: str = field(default_factory=_utc_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = VECTOR_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.event not in ALL_VECTOR_EVENT_NAMES:
            raise ValueError(
                f"unknown vector event: {self.event!r}. "
                f"Valid: {ALL_VECTOR_EVENT_NAMES}"
            )
        # Enforce payload shape per event type (below).
        validate_payload(self.event, self.payload)

    def to_dict(self) -> dict[str, Any]:
        """Stable dict form for JSON / JSONL persistence."""
        return asdict(self)

    def to_json(self) -> str:
        """Canonical JSON line (sorted keys, compact separators) so two
        runs with the same content produce byte-identical bytes."""
        return json.dumps(self.to_dict(), sort_keys=True,
                           separators=(",", ":"), default=str)

    def event_id(self) -> str:
        """Idempotent id: sha256 of the canonical JSON excluding ts.
        Two identical events (same cell + solver + event + payload)
        always produce the same id regardless of when they were emitted,
        so replay-time dedup is cheap."""
        d = {k: v for k, v in self.to_dict().items() if k != "ts"}
        blob = json.dumps(d, sort_keys=True, separators=(",", ":"),
                           default=str).encode("utf-8")
        return "evt_" + hashlib.sha256(blob).hexdigest()[:16]


# ── Per-event payload validation ──────────────────────────────────

def validate_payload(event: str, payload: dict[str, Any]) -> None:
    """Raise ValueError if payload is missing required keys for event."""
    required = _REQUIRED_PAYLOAD_KEYS.get(event, ())
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(
            f"event {event!r} payload missing keys: {missing}"
        )


_REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    # A solver's YAML was added or updated on disk. Does not itself
    # touch FAISS; the vector indexer is expected to emit a follow-up
    # vector.upsert_requested event.
    EVT_SOLVER_UPSERTED: ("model_id", "signature", "source_path"),

    # Please (re-)embed this solver and upsert its vector. Payload
    # carries enough context for the indexer to decide whether to
    # recompute the embedding (signature change) or reuse cached one.
    EVT_VECTOR_UPSERT_REQUESTED: ("model_id", "signature"),

    # Please delete this solver's vector.
    EVT_VECTOR_DELETE_REQUESTED: ("model_id",),

    # An indexer has finished a commit. Carries enough evidence to
    # verify the on-disk state matches what MAGMA recorded.
    EVT_VECTOR_COMMIT_APPLIED: (
        "faiss_commit_id", "artifact_path",
        "vector_count", "checksum",
    ),
}


# ── Convenience constructors ──────────────────────────────────────

def solver_upserted(cell_id: str, model_id: str, signature: str,
                    source_path: str) -> VectorEvent:
    return VectorEvent(
        event=EVT_SOLVER_UPSERTED,
        cell_id=cell_id,
        solver_id=model_id,
        payload={
            "model_id": model_id,
            "signature": signature,
            "source_path": source_path,
        },
    )


def vector_upsert_requested(cell_id: str, model_id: str,
                             signature: str,
                             reason: str | None = None) -> VectorEvent:
    payload: dict[str, Any] = {"model_id": model_id, "signature": signature}
    if reason:
        payload["reason"] = reason
    return VectorEvent(
        event=EVT_VECTOR_UPSERT_REQUESTED,
        cell_id=cell_id,
        solver_id=model_id,
        payload=payload,
    )


def vector_delete_requested(cell_id: str, model_id: str,
                             reason: str | None = None) -> VectorEvent:
    payload: dict[str, Any] = {"model_id": model_id}
    if reason:
        payload["reason"] = reason
    return VectorEvent(
        event=EVT_VECTOR_DELETE_REQUESTED,
        cell_id=cell_id,
        solver_id=model_id,
        payload=payload,
    )


def vector_commit_applied(cell_id: str, faiss_commit_id: str,
                           artifact_path: str, vector_count: int,
                           checksum: str,
                           source_events: list[str] | None = None) -> VectorEvent:
    payload: dict[str, Any] = {
        "faiss_commit_id": faiss_commit_id,
        "artifact_path": artifact_path,
        "vector_count": vector_count,
        "checksum": checksum,
    }
    if source_events is not None:
        payload["source_events"] = list(source_events)
    return VectorEvent(
        event=EVT_VECTOR_COMMIT_APPLIED,
        cell_id=cell_id,
        payload=payload,
    )
