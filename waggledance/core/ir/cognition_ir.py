"""Cognition IR core — Phase 9 §G.

Defines the unified IRObject envelope for all 14 IR types
(13 from Prompt_1_Master §G plus learning_suggestion from §U2). The
envelope is a frozen dataclass with type-specific data carried in
`payload`. Structural identity (ir_id) is sha256[:12] of canonical
(ir_type, payload structural keys, capsule_context).

Crown-jewel area waggledance/core/ir/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import (
    DEPENDENCY_RELATIONS,
    IR_COMPAT_VERSION,
    IR_SCHEMA_VERSION,
    IR_TYPES,
    LIFECYCLE_STATUSES,
    PROMOTION_STATES,
    SOURCE_SESSIONS,
)


# Per-type identity-key whitelist: which payload keys participate in
# the structural ir_id hash. Volatile fields (ts, rationale text,
# scoring numbers) are excluded.
_IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "curiosity": ("candidate_cell", "suspected_gap_type", "curiosity_id"),
    "tension": ("tension_id", "type", "claim"),
    "blind_spot": ("domain", "severity"),
    "dream_target": ("source_tension_id", "candidate_cell"),
    "shadow_candidate": ("solver_name", "cell_id"),
    "meta_proposal": ("proposal_type", "scope_class", "canonical_target"),
    "capability": ("capability_name", "cell_id"),
    "review_candidate": ("meta_proposal_id", "canonical_target"),
    "approved_change": ("change_target", "change_kind"),
    "repair_request": ("affected_file", "defect_kind"),
    "solver_candidate": ("solver_name", "cell_id"),
    "builder_request": ("intent", "lane"),
    "builder_result": ("request_id",),
    "learning_suggestion": ("about_topic",),
}


@dataclass(frozen=True)
class Dependency:
    depends_on_ir_id: str
    relation: str

    def __post_init__(self) -> None:
        if self.relation not in DEPENDENCY_RELATIONS:
            raise ValueError(
                f"unknown dependency relation: {self.relation!r}; "
                f"allowed: {DEPENDENCY_RELATIONS}"
            )

    def to_dict(self) -> dict:
        return {"depends_on_ir_id": self.depends_on_ir_id,
                "relation": self.relation}


@dataclass(frozen=True)
class Provenance:
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    produced_by: str
    source_session: str = "external"
    fixture_fallback_used: bool = False

    def __post_init__(self) -> None:
        if self.source_session not in SOURCE_SESSIONS:
            raise ValueError(
                f"unknown source_session: {self.source_session!r}; "
                f"allowed: {SOURCE_SESSIONS}"
            )

    def to_dict(self) -> dict:
        return {
            "branch_name": self.branch_name,
            "base_commit_hash": self.base_commit_hash,
            "pinned_input_manifest_sha256": self.pinned_input_manifest_sha256,
            "produced_by": self.produced_by,
            "source_session": self.source_session,
            "fixture_fallback_used": self.fixture_fallback_used,
        }


@dataclass(frozen=True)
class IRObject:
    schema_version: int
    ir_id: str
    ir_type: str
    ir_compat_version: int
    lifecycle_status: str
    risk: str
    promotion_state: str
    evidence_refs: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    provenance: Provenance
    capsule_context: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.ir_type not in IR_TYPES:
            raise ValueError(
                f"unknown ir_type: {self.ir_type!r}; allowed: {IR_TYPES}"
            )
        if self.lifecycle_status not in LIFECYCLE_STATUSES:
            raise ValueError(
                f"unknown lifecycle_status: {self.lifecycle_status!r}"
            )
        if self.promotion_state not in PROMOTION_STATES:
            raise ValueError(
                f"unknown promotion_state: {self.promotion_state!r}"
            )
        if self.risk not in ("low", "medium", "high"):
            raise ValueError(f"unknown risk: {self.risk!r}")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "ir_id": self.ir_id,
            "ir_type": self.ir_type,
            "ir_compat_version": self.ir_compat_version,
            "lifecycle_status": self.lifecycle_status,
            "risk": self.risk,
            "promotion_state": self.promotion_state,
            "evidence_refs": list(self.evidence_refs),
            "dependencies": [d.to_dict() for d in self.dependencies],
            "provenance": self.provenance.to_dict(),
            "capsule_context": self.capsule_context,
            "payload": dict(self.payload),
        }


# ── Structural identity ──────────────────────────────────────────-

def compute_ir_id(*, ir_type: str, payload: dict,
                       capsule_context: str) -> str:
    """sha256[:12] of canonical (ir_type, identity-keys-only payload,
    capsule_context). Identity-key whitelist per type ensures volatile
    fields (rationale text, scores) don't shift the id."""
    keys = _IDENTITY_KEYS.get(ir_type, tuple(sorted(payload.keys())))
    identity_payload = {k: payload.get(k) for k in keys}
    canonical = json.dumps({
        "ir_type": ir_type,
        "identity_payload": identity_payload,
        "capsule_context": capsule_context,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Construction ─────────────────────────────────────────────────-

def make_ir(*,
                ir_type: str,
                payload: dict,
                provenance: Provenance,
                capsule_context: str = "neutral_v1",
                lifecycle_status: str = "new",
                risk: str = "low",
                promotion_state: str = "candidate",
                evidence_refs: Iterable[str] = (),
                dependencies: Iterable[Dependency] = (),
                ) -> IRObject:
    if ir_type not in IR_TYPES:
        raise ValueError(
            f"unknown ir_type: {ir_type!r}; allowed: {IR_TYPES}"
        )
    ir_id = compute_ir_id(ir_type=ir_type, payload=payload,
                              capsule_context=capsule_context)
    return IRObject(
        schema_version=IR_SCHEMA_VERSION,
        ir_id=ir_id,
        ir_type=ir_type,
        ir_compat_version=IR_COMPAT_VERSION,
        lifecycle_status=lifecycle_status,
        risk=risk,
        promotion_state=promotion_state,
        evidence_refs=tuple(evidence_refs),
        dependencies=tuple(dependencies),
        provenance=provenance,
        capsule_context=capsule_context,
        payload=dict(payload),
    )


# ── Lifecycle / promotion transitions (pure) ─────────────────────-

def with_lifecycle(obj: IRObject, status: str) -> IRObject:
    if status not in LIFECYCLE_STATUSES:
        raise ValueError(f"unknown lifecycle_status: {status!r}")
    return IRObject(
        schema_version=obj.schema_version, ir_id=obj.ir_id,
        ir_type=obj.ir_type, ir_compat_version=obj.ir_compat_version,
        lifecycle_status=status, risk=obj.risk,
        promotion_state=obj.promotion_state,
        evidence_refs=obj.evidence_refs, dependencies=obj.dependencies,
        provenance=obj.provenance, capsule_context=obj.capsule_context,
        payload=obj.payload,
    )


def with_promotion(obj: IRObject, state: str) -> IRObject:
    if state not in PROMOTION_STATES:
        raise ValueError(f"unknown promotion_state: {state!r}")
    return IRObject(
        schema_version=obj.schema_version, ir_id=obj.ir_id,
        ir_type=obj.ir_type, ir_compat_version=obj.ir_compat_version,
        lifecycle_status=obj.lifecycle_status, risk=obj.risk,
        promotion_state=state, evidence_refs=obj.evidence_refs,
        dependencies=obj.dependencies, provenance=obj.provenance,
        capsule_context=obj.capsule_context, payload=obj.payload,
    )
