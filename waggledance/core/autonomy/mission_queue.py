"""Autonomy mission queue — Phase 9 §F.mission_queue.

Persistent, append-only queue of work units the kernel schedules
across ticks. Missions are deterministic (structural mission_id), and
ordering is stable: by priority desc, then mission_id ascending.

CRITICAL SCOPE RULE (Prompt_1_Master §F):
- missions never auto-execute side effects
- the queue itself is a recommender; action_gate is the only exit
- circuit breaker state must be consulted before retries (handoff
  done in F.background_scheduler / F.circuit_breaker — this module
  exposes the consultation API)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

MISSION_SCHEMA_VERSION = 1

ALLOWED_KINDS = (
    "ingest_request",
    "consultation_request",
    "builder_request",
    "solver_synthesis_request",
    "shadow_replay_request",
    "promotion_review_request",
    "memory_tier_move",
    "calibration_check",
    "self_inspection",
    "noop",
)
ALLOWED_LANES = (
    "provider_plane",
    "builder_lane",
    "solver_synthesis",
    "ingestion",
    "memory_tiers",
    "promotion",
    "self_inspection",
    "wait",
)
ALLOWED_LIFECYCLE = ("queued", "scheduled", "completed", "failed", "cancelled")


@dataclass(frozen=True)
class Mission:
    schema_version: int
    mission_id: str
    kind: str
    lane: str
    priority: float
    intent: str
    rationale: str
    lifecycle_status: str
    no_runtime_mutation: bool
    created_tick_id: int
    capsule_context: str
    evidence_refs: tuple[str, ...] = ()
    completed_tick_id: int | None = None
    max_retries: int = 3
    retry_count: int = 0
    circuit_breaker_lane: str | None = None

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "kind": self.kind,
            "lane": self.lane,
            "priority": self.priority,
            "intent": self.intent,
            "rationale": self.rationale,
            "lifecycle_status": self.lifecycle_status,
            "no_runtime_mutation": self.no_runtime_mutation,
            "created_tick_id": self.created_tick_id,
            "completed_tick_id": self.completed_tick_id,
            "evidence_refs": list(self.evidence_refs),
            "capsule_context": self.capsule_context,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
        }
        if self.circuit_breaker_lane is not None:
            d["circuit_breaker_lane"] = self.circuit_breaker_lane
        return d


# ── Identity ──────────────────────────────────────────────────────

def compute_mission_id(*, kind: str, lane: str, intent: str,
                            capsule_context: str) -> str:
    """Deterministic structural id; excludes priority, rationale, ts.
    Two missions with the same (kind, lane, intent, capsule_context)
    produce the same id and are deduped on enqueue."""
    canonical = json.dumps({
        "kind": kind, "lane": lane, "intent": intent,
        "capsule_context": capsule_context,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Construction ──────────────────────────────────────────────────

def make_mission(*, kind: str, lane: str, priority: float,
                      intent: str, rationale: str,
                      created_tick_id: int,
                      capsule_context: str = "neutral_v1",
                      evidence_refs: tuple[str, ...] = (),
                      max_retries: int = 3,
                      circuit_breaker_lane: str | None = None,
                      ) -> Mission:
    if kind not in ALLOWED_KINDS:
        raise ValueError(
            f"unknown mission kind: {kind!r}; allowed: {ALLOWED_KINDS}"
        )
    if lane not in ALLOWED_LANES:
        raise ValueError(
            f"unknown mission lane: {lane!r}; allowed: {ALLOWED_LANES}"
        )
    if priority < 0:
        raise ValueError(f"priority must be >= 0, got {priority}")
    if max_retries < 0 or max_retries > 10:
        raise ValueError(f"max_retries must be in [0, 10], got {max_retries}")
    return Mission(
        schema_version=MISSION_SCHEMA_VERSION,
        mission_id=compute_mission_id(kind=kind, lane=lane, intent=intent,
                                          capsule_context=capsule_context),
        kind=kind, lane=lane, priority=float(priority),
        intent=intent, rationale=rationale,
        lifecycle_status="queued",
        no_runtime_mutation=True,
        created_tick_id=created_tick_id,
        capsule_context=capsule_context,
        evidence_refs=tuple(evidence_refs),
        max_retries=max_retries,
        circuit_breaker_lane=circuit_breaker_lane,
    )


# ── Queue operations (pure; never mutate input list) ─────────────

def enqueue(missions: list[Mission], new_mission: Mission) -> list[Mission]:
    """Append new_mission iff its mission_id is not already present."""
    if any(m.mission_id == new_mission.mission_id for m in missions):
        return list(missions)
    return list(missions) + [new_mission]


def deterministic_order(missions: Iterable[Mission]) -> list[Mission]:
    """Stable ordering: priority desc, then mission_id ascending."""
    return sorted(missions, key=lambda m: (-m.priority, m.mission_id))


def open_missions(missions: Iterable[Mission]) -> list[Mission]:
    """Return only missions whose lifecycle_status is queued or scheduled."""
    return [m for m in missions if m.lifecycle_status in ("queued", "scheduled")]


def filter_by_breakers(missions: Iterable[Mission],
                            breaker_states: dict[str, str]) -> tuple[list[Mission], list[Mission]]:
    """Split missions into (admissible, blocked).

    A mission is blocked if its circuit_breaker_lane (or its lane) is
    in 'open' or 'quarantined' state. The kernel may still emit such
    missions to the audit log but action_gate must reject them.
    """
    admissible: list[Mission] = []
    blocked: list[Mission] = []
    for m in missions:
        check_lane = m.circuit_breaker_lane or m.lane
        state = breaker_states.get(check_lane, "closed")
        if state in ("open", "quarantined"):
            blocked.append(m)
        else:
            admissible.append(m)
    return admissible, blocked


def with_lifecycle(mission: Mission, *,
                       status: str,
                       completed_tick_id: int | None = None,
                       retry_count: int | None = None) -> Mission:
    """Return a new Mission with updated lifecycle fields."""
    if status not in ALLOWED_LIFECYCLE:
        raise ValueError(
            f"unknown lifecycle status: {status!r}; "
            f"allowed: {ALLOWED_LIFECYCLE}"
        )
    return Mission(
        schema_version=mission.schema_version,
        mission_id=mission.mission_id,
        kind=mission.kind, lane=mission.lane,
        priority=mission.priority,
        intent=mission.intent, rationale=mission.rationale,
        lifecycle_status=status,
        no_runtime_mutation=mission.no_runtime_mutation,
        created_tick_id=mission.created_tick_id,
        capsule_context=mission.capsule_context,
        evidence_refs=mission.evidence_refs,
        completed_tick_id=(completed_tick_id if completed_tick_id is not None
                            else mission.completed_tick_id),
        max_retries=mission.max_retries,
        retry_count=(retry_count if retry_count is not None
                      else mission.retry_count),
        circuit_breaker_lane=mission.circuit_breaker_lane,
    )


# ── Atomic persistence ────────────────────────────────────────────

def to_canonical_jsonl(missions: Iterable[Mission]) -> str:
    lines = [
        json.dumps(m.to_dict(), sort_keys=True, separators=(",", ":"))
        for m in deterministic_order(missions)
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def save_missions(missions: list[Mission], path: Path | str) -> Path:
    """Atomic save via tmp + os.replace. The file is JSONL but the
    full snapshot is rewritten each save (compaction/replace), not
    appended. The kernel's append-only audit trail is separate."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = to_canonical_jsonl(missions)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".missions.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def load_missions(path: Path | str) -> list[Mission]:
    """Read missions JSONL. Missing file → []. Malformed lines are
    silently skipped (consistent with R7.5 vector_events policy)."""
    target = Path(path)
    if not target.exists():
        return []
    out: list[Mission] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(Mission(
                schema_version=int(d.get("schema_version") or 1),
                mission_id=str(d["mission_id"]),
                kind=str(d["kind"]),
                lane=str(d["lane"]),
                priority=float(d.get("priority") or 0.0),
                intent=str(d["intent"]),
                rationale=str(d["rationale"]),
                lifecycle_status=str(d.get("lifecycle_status") or "queued"),
                no_runtime_mutation=bool(d.get("no_runtime_mutation", True)),
                created_tick_id=int(d.get("created_tick_id") or 0),
                capsule_context=str(d.get("capsule_context") or "neutral_v1"),
                evidence_refs=tuple(d.get("evidence_refs") or ()),
                completed_tick_id=(int(d["completed_tick_id"])
                                     if d.get("completed_tick_id") is not None
                                     else None),
                max_retries=int(d.get("max_retries") or 3),
                retry_count=int(d.get("retry_count") or 0),
                circuit_breaker_lane=d.get("circuit_breaker_lane"),
            ))
        except (KeyError, ValueError):
            continue
    return out
