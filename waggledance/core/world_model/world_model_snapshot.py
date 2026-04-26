"""World model snapshot — Phase 9 §I.

Frozen, deterministic snapshot of external knowledge at a point in
time. Strictly NOT self-model: facts here describe the world, not WD
itself.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import FACT_KINDS, PREDICTION_HORIZONS, WORLD_MODEL_SCHEMA_VERSION


@dataclass(frozen=True)
class ExternalFact:
    fact_id: str
    kind: str
    claim: str
    confidence: float
    source_refs: tuple[str, ...]
    ts_iso: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in FACT_KINDS:
            raise ValueError(
                f"unknown fact kind {self.kind!r}; allowed: {FACT_KINDS}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1], got {self.confidence}"
            )

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "kind": self.kind,
            "claim": self.claim, "confidence": self.confidence,
            "source_refs": list(self.source_refs),
            "ts_iso": self.ts_iso,
        }


@dataclass(frozen=True)
class CausalRelation:
    cause_fact_id: str
    effect_fact_id: str
    strength: float
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(
                f"strength must be in [0,1], got {self.strength}"
            )
        if self.cause_fact_id == self.effect_fact_id:
            raise ValueError(
                "cause_fact_id and effect_fact_id must differ"
            )

    def to_dict(self) -> dict:
        return {
            "cause_fact_id": self.cause_fact_id,
            "effect_fact_id": self.effect_fact_id,
            "strength": self.strength,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    claim: str
    predicted_value: Any
    confidence: float
    horizon: str
    predicted_unit: str = ""
    based_on_facts: tuple[str, ...] = ()
    evaluated_at_iso: str | None = None
    actual_value: Any = None
    calibration_error: float | None = None

    def __post_init__(self) -> None:
        if self.horizon not in PREDICTION_HORIZONS:
            raise ValueError(
                f"unknown horizon {self.horizon!r}; allowed: {PREDICTION_HORIZONS}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1], got {self.confidence}"
            )

    def to_dict(self) -> dict:
        return {
            "prediction_id": self.prediction_id,
            "claim": self.claim,
            "predicted_value": self.predicted_value,
            "predicted_unit": self.predicted_unit,
            "confidence": self.confidence,
            "horizon": self.horizon,
            "based_on_facts": list(self.based_on_facts),
            "evaluated_at_iso": self.evaluated_at_iso,
            "actual_value": self.actual_value,
            "calibration_error": self.calibration_error,
        }


@dataclass(frozen=True)
class WorldModelSnapshot:
    schema_version: int
    snapshot_id: str
    produced_at_iso: str
    external_facts: tuple[ExternalFact, ...]
    causal_relations: tuple[CausalRelation, ...]
    predictions: tuple[Prediction, ...]
    calibration_per_dimension: dict[str, dict[str, float | int]]
    uncertainty_summary: dict[str, int]
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    produced_by: str
    fixture_fallback_used: bool = False

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "produced_at_iso": self.produced_at_iso,
            "external_facts": [f.to_dict() for f in self.external_facts],
            "causal_relations": [r.to_dict() for r in self.causal_relations],
            "predictions": [p.to_dict() for p in self.predictions],
            "calibration_state": {
                "per_dimension": {
                    k: dict(v) for k, v in
                    sorted(self.calibration_per_dimension.items())
                },
            },
            "uncertainty_summary": dict(sorted(
                self.uncertainty_summary.items()
            )),
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
                "produced_by": self.produced_by,
                "fixture_fallback_used": self.fixture_fallback_used,
            },
        }


def compute_snapshot_id(*,
                              external_facts: Iterable[ExternalFact],
                              causal_relations: Iterable[CausalRelation],
                              predictions: Iterable[Prediction],
                              ) -> str:
    """Structural id excludes ts and confidence drift."""
    canonical = json.dumps({
        "facts": sorted(f.fact_id for f in external_facts),
        "causes": sorted(
            f"{r.cause_fact_id}->{r.effect_fact_id}"
            for r in causal_relations
        ),
        "predictions": sorted(p.prediction_id for p in predictions),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_snapshot(*,
                       produced_at_iso: str,
                       external_facts: Iterable[ExternalFact],
                       causal_relations: Iterable[CausalRelation],
                       predictions: Iterable[Prediction],
                       calibration_per_dimension: dict | None = None,
                       branch_name: str,
                       base_commit_hash: str,
                       pinned_input_manifest_sha256: str,
                       produced_by: str = "build_world_model_snapshot",
                       fixture_fallback_used: bool = False,
                       ) -> WorldModelSnapshot:
    facts_t = tuple(external_facts)
    causes_t = tuple(causal_relations)
    preds_t = tuple(predictions)
    sid = compute_snapshot_id(
        external_facts=facts_t, causal_relations=causes_t,
        predictions=preds_t,
    )
    summary = {
        "facts_with_low_confidence": sum(
            1 for f in facts_t if f.confidence < 0.5
        ),
        "predictions_pending_eval": sum(
            1 for p in preds_t if p.actual_value is None
        ),
        "drift_alerts_active": 0,
    }
    return WorldModelSnapshot(
        schema_version=WORLD_MODEL_SCHEMA_VERSION,
        snapshot_id=sid,
        produced_at_iso=produced_at_iso,
        external_facts=facts_t,
        causal_relations=causes_t,
        predictions=preds_t,
        calibration_per_dimension=dict(calibration_per_dimension or {}),
        uncertainty_summary=summary,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        produced_by=produced_by,
        fixture_fallback_used=fixture_fallback_used,
    )


def to_canonical_json(s: WorldModelSnapshot) -> str:
    return json.dumps(s.to_dict(), indent=2, sort_keys=True)
