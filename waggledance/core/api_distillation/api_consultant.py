"""API consultant — Phase 9 §J.

Drives the 6-layer trust gate and produces ConsultationRecord entries.
NEVER directly mutates self_model / world_model — only emits records
that downstream phases (promotion ladder, ingestion) consume.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import API_DISTILLATION_SCHEMA_VERSION, TRUST_GATE_LAYERS
from .knowledge_extractor import (
    ExtractedFact,
    ExtractedLesson,
    ExtractedSolverSpec,
    extract,
)
from ..provider_plane.response_normalizer import ProviderResponse


@dataclass(frozen=True)
class TrustGateResult:
    layer_reached: str
    rationale: str
    blocked_at: str | None    # the layer where it failed, if any

    def to_dict(self) -> dict:
        return {
            "layer_reached": self.layer_reached,
            "rationale": self.rationale,
            "blocked_at": self.blocked_at,
        }


def evaluate_trust_gate(*,
                              response: ProviderResponse,
                              extracted: dict,
                              corroborating_responses: list[ProviderResponse] | None = None,
                              calibration_threshold: float = 0.6,
                              ) -> TrustGateResult:
    """Walk the 6 layers in order; stop at the first failure.

    Layer 1 (raw_quarantine) — every response starts here; pass when
      response is non-empty.
    Layer 2 (internal_consistency) — extracted items have plausible
      structure (non-empty claims, valid confidence values).
    Layer 3 (existing_knowledge_cross_check) — at least one fact has
      claim text length ≥ 10 (proxy for "concrete enough to compare").
    Layer 4 (multi_source_corroboration) — corroborating_responses
      contains ≥ 1 other provider; pass otherwise gracefully (we
      record that we did not reach this layer).
    Layer 5 (calibration_threshold) — extracted facts mean confidence
      ≥ calibration_threshold.
    Layer 6 (human_gated) — never auto-passed; reserved for explicit
      human approval artifact (out of scope for J).
    """
    facts = extracted.get("facts") or []
    solvers = extracted.get("solver_specs") or []
    lessons = extracted.get("lessons") or []

    if not response.raw_payload and not (facts or solvers or lessons):
        return TrustGateResult(
            layer_reached="raw_quarantine",
            rationale="raw payload empty and nothing extracted",
            blocked_at="internal_consistency",
        )

    # Layer 2
    for f in facts:
        if not (0.0 <= f.confidence <= 1.0):
            return TrustGateResult(
                layer_reached="raw_quarantine",
                rationale=f"fact confidence out of range: {f.confidence}",
                blocked_at="internal_consistency",
            )
        if not f.claim:
            return TrustGateResult(
                layer_reached="raw_quarantine",
                rationale="fact missing claim",
                blocked_at="internal_consistency",
            )

    # Layer 3
    if facts and not any(len(f.claim) >= 10 for f in facts):
        return TrustGateResult(
            layer_reached="internal_consistency",
            rationale="no fact concrete enough for cross-check",
            blocked_at="existing_knowledge_cross_check",
        )

    # Layer 4
    corrob = corroborating_responses or []
    has_corrob = any(r.provider_used != response.provider_used
                       for r in corrob)
    if not has_corrob:
        return TrustGateResult(
            layer_reached="existing_knowledge_cross_check",
            rationale=("no corroborating provider response supplied; "
                        "stopping at single-source layer"),
            blocked_at="multi_source_corroboration",
        )

    # Layer 5
    if facts:
        mean_conf = sum(f.confidence for f in facts) / len(facts)
        if mean_conf < calibration_threshold:
            return TrustGateResult(
                layer_reached="multi_source_corroboration",
                rationale=(
                    f"mean fact confidence {mean_conf:.3f} < "
                    f"threshold {calibration_threshold}"
                ),
                blocked_at="calibration_threshold",
            )

    # Layer 6 — never auto-passed
    return TrustGateResult(
        layer_reached="calibration_threshold",
        rationale=(
            "all auto-checkable layers passed; human_gated layer "
            "remains as the final approval boundary"
        ),
        blocked_at="human_gated",
    )


@dataclass(frozen=True)
class ConsultationRecord:
    schema_version: int
    consultation_id: str
    request_id: str
    response_id: str
    trust_layer_reached: str
    extracted_facts: tuple[dict, ...]
    extracted_solver_specs: tuple[dict, ...]
    extracted_lessons: tuple[dict, ...]
    ts_iso: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "consultation_id": self.consultation_id,
            "request_id": self.request_id,
            "response_id": self.response_id,
            "trust_layer_reached": self.trust_layer_reached,
            "extracted_facts": [dict(f) for f in self.extracted_facts],
            "extracted_solver_specs":
                [dict(s) for s in self.extracted_solver_specs],
            "extracted_lessons":
                [dict(l) for l in self.extracted_lessons],
            "ts_iso": self.ts_iso,
        }


def consult(*,
                response: ProviderResponse,
                corroborating_responses: list[ProviderResponse] | None = None,
                calibration_threshold: float = 0.6,
                ts_iso: str = "",
                ) -> ConsultationRecord:
    """End-to-end: extract → trust gate → ConsultationRecord."""
    extracted = extract(response)
    gate = evaluate_trust_gate(
        response=response, extracted=extracted,
        corroborating_responses=corroborating_responses,
        calibration_threshold=calibration_threshold,
    )
    canonical = json.dumps({
        "request_id": response.request_id,
        "response_id": response.response_id,
        "trust": gate.layer_reached,
    }, sort_keys=True, separators=(",", ":"))
    cid = "consult_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return ConsultationRecord(
        schema_version=API_DISTILLATION_SCHEMA_VERSION,
        consultation_id=cid,
        request_id=response.request_id,
        response_id=response.response_id,
        trust_layer_reached=gate.layer_reached,
        extracted_facts=tuple(f.to_dict() for f in extracted["facts"]),
        extracted_solver_specs=tuple(
            s.to_dict() for s in extracted["solver_specs"]
        ),
        extracted_lessons=tuple(
            l.to_dict() for l in extracted["lessons"]
        ),
        ts_iso=ts_iso,
    )
