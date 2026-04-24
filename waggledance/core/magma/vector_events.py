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
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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

    Schema evolution:
    - v1 (Stage 1): event, cell_id, solver_id, ts, payload, schema_version
    - v1 + source (Stage 2, additive): `source` identifies the emitting
      component ("backfill", "indexer", etc). Old v1 events without
      source still parse — the field defaults to None and consumers
      tolerate its absence.
    - commit_ref linkage for vector.commit_applied lives in the event's
      payload as `faiss_commit_id` (see validate_payload below). Other
      events may carry payload["commit_ref"] when they relate to a
      specific commit; this is informational and not enforced.
    """
    event: str
    cell_id: str
    solver_id: str | None = None
    ts: str = field(default_factory=_utc_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = VECTOR_EVENT_SCHEMA_VERSION
    # Optional provenance: which producer emitted this event. Backward-
    # compatible — old v1 events without this field read back with
    # source=None.
    source: str | None = None

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
                           source_events: list[str] | None = None,
                           source: str | None = "indexer",
                           input_event_range: tuple[str, str] | None = None,
                           ) -> VectorEvent:
    """Build a vector.commit_applied event.

    Per R6 §5 the event must carry enough to audit the apply. Fields:
    - faiss_commit_id: deterministic id of the applied projection
    - artifact_path: where the committed files live (relative)
    - vector_count: number of vectors in the committed set
    - checksum: manifest checksum for integrity
    - source_events: optional list of event_ids consumed to produce
      this commit (idempotency audit)
    - input_event_range: optional [first_event_id, last_event_id]
      tuple describing the apply window
    - source: component that emitted this event (default "indexer")
    """
    payload: dict[str, Any] = {
        "faiss_commit_id": faiss_commit_id,
        "artifact_path": artifact_path,
        "vector_count": vector_count,
        "checksum": checksum,
    }
    if source_events is not None:
        payload["source_events"] = list(source_events)
    if input_event_range is not None:
        payload["input_event_range"] = list(input_event_range)
    return VectorEvent(
        event=EVT_VECTOR_COMMIT_APPLIED,
        cell_id=cell_id,
        payload=payload,
        source=source,
    )


# ── Event log writer + reader ─────────────────────────────────────
#
# Until JetStream lands in Stage 2, the event log is an append-only
# JSONL file. Producers call `emit(event)`; consumers iterate with
# `read_events(path)`. Both functions are module-level helpers so
# existing tools can import them without pulling in the whole MAGMA
# runtime.

DEFAULT_EVENT_LOG = Path("data") / "vector" / "events.jsonl"


def _resolve_event_log(path: Path | str | None) -> Path:
    """Resolve the target path. Explicit argument wins; env var
    `WAGGLE_VECTOR_EVENT_LOG` is next (useful for tests); default is
    `data/vector/events.jsonl` relative to the current working dir."""
    if path is not None:
        return Path(path)
    env = os.environ.get("WAGGLE_VECTOR_EVENT_LOG")
    if env:
        return Path(env)
    return DEFAULT_EVENT_LOG


def emit(event: VectorEvent, path: Path | str | None = None) -> Path:
    """Append `event` to the event log as a single canonical JSON line.
    Returns the resolved path (for logging / test assertions).

    The writer creates parent directories on first use so Stage 1 tools
    can emit before the data/vector/ directory exists."""
    target = _resolve_event_log(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(event.to_json())
        f.write("\n")
    return target


def emit_many(events: list[VectorEvent],
              path: Path | str | None = None) -> Path:
    """Append a batch of events atomically-per-line. Not a true
    multi-event atomic transaction (Stage 2 gets that via JetStream);
    this just saves the open/close overhead when a producer writes
    many events in one pass."""
    target = _resolve_event_log(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        for event in events:
            f.write(event.to_json())
            f.write("\n")
    return target


def read_events(path: Path | str | None = None) -> Iterator[VectorEvent]:
    """Yield every VectorEvent from the log file in write order. Skips
    unparseable lines silently (they'll show up as a warning once a
    real consumer lands in Stage 2)."""
    target = _resolve_event_log(path)
    if not target.exists():
        return
    with open(target, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                yield VectorEvent(
                    event=d["event"],
                    cell_id=d["cell_id"],
                    solver_id=d.get("solver_id"),
                    ts=d.get("ts") or _utc_now_iso(),
                    payload=d.get("payload") or {},
                    schema_version=d.get("schema_version", VECTOR_EVENT_SCHEMA_VERSION),
                    # source is optional, defaults to None for old v1
                    # events that predate the field.
                    source=d.get("source"),
                )
            except (KeyError, ValueError):
                # Unknown / malformed event — skip
                continue
