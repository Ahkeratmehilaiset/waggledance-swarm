"""Prediction calibrator — Phase 9 §I.

Aggregates prediction errors per dimension into a calibration record
mirroring Session B's calibration corrections style. Strictly per
EXTERNAL prediction; never modifies self_model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .world_model_snapshot import Prediction


@dataclass(frozen=True)
class CalibrationRecord:
    dimension: str
    prior_score: float
    evidence_implied_score: float
    abs_error: float
    n_observations: int

    def to_dict(self) -> dict:
        return {
            "prior_score": self.prior_score,
            "evidence_implied_score": self.evidence_implied_score,
            "abs_error": self.abs_error,
            "n_observations": self.n_observations,
        }


def calibrate_per_dimension(predictions: Iterable[Prediction],
                                 dimension_key: str = "horizon"
                                 ) -> dict[str, CalibrationRecord]:
    """Group evaluated predictions by horizon (default) or by another
    grouping key, then compute mean abs_error vs mean confidence."""
    grouped: dict[str, list[Prediction]] = {}
    for p in predictions:
        if p.calibration_error is None or p.actual_value is None:
            continue
        key = getattr(p, dimension_key, None) or "unknown"
        grouped.setdefault(key, []).append(p)
    out: dict[str, CalibrationRecord] = {}
    for dim, preds in sorted(grouped.items()):
        n = len(preds)
        if n == 0:
            continue
        mean_conf = sum(p.confidence for p in preds) / n
        mean_err = sum(p.calibration_error or 0.0 for p in preds) / n
        # evidence_implied_score is "what the data says we should
        # have predicted", proxy = 1.0 - mean_err clamped
        evidence_implied = max(0.0, min(1.0, 1.0 - mean_err))
        out[dim] = CalibrationRecord(
            dimension=dim,
            prior_score=round(mean_conf, 6),
            evidence_implied_score=round(evidence_implied, 6),
            abs_error=round(mean_err, 6),
            n_observations=n,
        )
    return out


def calibration_to_snapshot_dict(records: dict[str, CalibrationRecord]
                                      ) -> dict:
    return {dim: r.to_dict() for dim, r in sorted(records.items())}
