"""Calibration drift detector — Phase 9 §I.

Identifies SYSTEMIC shifts in calibration over time, not just local
errors. Drift detection compares the current calibration record per
dimension against a moving baseline; alerts trigger when the
prior-score → evidence-implied-score gap widens consistently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import DEFAULT_DRIFT_THRESHOLD, DEFAULT_DRIFT_WINDOW
from .prediction_calibrator import CalibrationRecord


@dataclass(frozen=True)
class DriftAlert:
    dimension: str
    direction: str          # "drift_low_confidence" | "drift_overconfident"
    magnitude: float
    window_size: int
    rationale: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "direction": self.direction,
            "magnitude": self.magnitude,
            "window_size": self.window_size,
            "rationale": self.rationale,
        }


def detect_drift(history_per_dimension: dict[str, list[CalibrationRecord]],
                    *,
                    threshold: float = DEFAULT_DRIFT_THRESHOLD,
                    window: int = DEFAULT_DRIFT_WINDOW,
                    ) -> list[DriftAlert]:
    """Per-dimension drift detection.

    A `drift_overconfident` alert means abs(prior - evidence_implied)
    persistently shows the system OVER-estimated confidence.
    A `drift_low_confidence` alert means the system UNDER-estimated.

    Trigger rule: at least `window` records present for the dimension
    AND mean signed gap (prior - evidence_implied) over the last
    `window` records exceeds threshold in absolute value.
    """
    alerts: list[DriftAlert] = []
    for dim, records in sorted(history_per_dimension.items()):
        if len(records) < window:
            continue
        recent = records[-window:]
        signed_gaps = [r.prior_score - r.evidence_implied_score
                        for r in recent]
        mean_gap = sum(signed_gaps) / len(signed_gaps)
        if abs(mean_gap) < threshold:
            continue
        direction = ("drift_overconfident" if mean_gap > 0
                       else "drift_low_confidence")
        alerts.append(DriftAlert(
            dimension=dim, direction=direction,
            magnitude=round(abs(mean_gap), 6),
            window_size=len(recent),
            rationale=(
                f"mean(prior - evidence_implied) over last "
                f"{len(recent)} records = {mean_gap:+.3f}; "
                f"|magnitude| >= threshold {threshold}"
            ),
        ))
    return alerts
