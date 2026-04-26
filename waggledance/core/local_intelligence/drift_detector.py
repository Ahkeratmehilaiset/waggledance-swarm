# SPDX-License-Identifier: BUSL-1.1
"""Drift detector — Phase 9 §N.

Scaffolding for detecting drift between a candidate local model and a
reference baseline. The detector compares evaluation report pairs and
emits a deterministic DriftReport. Drift severity uses fixed
thresholds; the report is advisory and never auto-gates a promotion.

Severity ladder (advisory):
- nominal       — accuracy delta within ±0.02
- watch         — |delta| in (0.02, 0.05]
- elevated      — |delta| in (0.05, 0.10]
- critical      — |delta| > 0.10
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal


DriftSeverity = Literal["nominal", "watch", "elevated", "critical"]


_THRESHOLDS = (
    (0.02, "nominal"),
    (0.05, "watch"),
    (0.10, "elevated"),
)


class _DriftDetectorError(ValueError):
    """Internal sentinel — surfaced as ValueError to callers."""


def _classify(delta_abs: float) -> str:
    for upper, label in _THRESHOLDS:
        if delta_abs <= upper:
            return label
    return "critical"


@dataclass(frozen=True)
class DriftReport:
    candidate_eval_set_id: str
    baseline_eval_set_id: str
    candidate_accuracy: float
    baseline_accuracy: float
    accuracy_delta: float
    severity: str
    advisory_only: bool
    no_runtime_auto_action: bool
    rationale: str

    def __post_init__(self) -> None:
        if self.severity not in ("nominal", "watch", "elevated", "critical"):
            raise _DriftDetectorError(
                f"unknown severity {self.severity!r}"
            )
        if self.advisory_only is not True:
            raise _DriftDetectorError("advisory_only must be True")
        if self.no_runtime_auto_action is not True:
            raise _DriftDetectorError(
                "no_runtime_auto_action must be True"
            )

    def to_dict(self) -> dict:
        return {
            "candidate_eval_set_id": self.candidate_eval_set_id,
            "baseline_eval_set_id": self.baseline_eval_set_id,
            "candidate_accuracy": self.candidate_accuracy,
            "baseline_accuracy": self.baseline_accuracy,
            "accuracy_delta": self.accuracy_delta,
            "severity": self.severity,
            "advisory_only": self.advisory_only,
            "no_runtime_auto_action": self.no_runtime_auto_action,
            "rationale": self.rationale,
        }


@dataclass
class DriftDetector:
    def compare(self, *,
                 candidate_eval_set_id: str,
                 candidate_accuracy: float,
                 baseline_eval_set_id: str,
                 baseline_accuracy: float) -> DriftReport:
        for v, name in (
            (candidate_accuracy, "candidate_accuracy"),
            (baseline_accuracy, "baseline_accuracy"),
        ):
            if not (0.0 <= v <= 1.0):
                raise _DriftDetectorError(
                    f"{name} must be in [0,1], got {v!r}"
                )
        delta = candidate_accuracy - baseline_accuracy
        severity = _classify(abs(delta))
        rationale = (
            f"|delta|={abs(delta):.4f} -> severity={severity}; "
            f"advisory only, never auto-promotes or auto-rolls back"
        )
        return DriftReport(
            candidate_eval_set_id=candidate_eval_set_id,
            baseline_eval_set_id=baseline_eval_set_id,
            candidate_accuracy=candidate_accuracy,
            baseline_accuracy=baseline_accuracy,
            accuracy_delta=delta,
            severity=severity,
            advisory_only=True,
            no_runtime_auto_action=True,
            rationale=rationale,
        )
