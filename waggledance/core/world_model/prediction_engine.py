# SPDX-License-Identifier: BUSL-1.1
"""Prediction engine — Phase 9 §I.

Constructs Prediction objects from causal relations + observed
external facts. Pure: no live execution, no LLM calls.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from . import PREDICTION_HORIZONS
from .world_model_snapshot import (
    CausalRelation,
    ExternalFact,
    Prediction,
)


def _prediction_id(*, claim: str, horizon: str,
                       based_on_facts: Iterable[str]) -> str:
    canonical = json.dumps({
        "claim": claim, "horizon": horizon,
        "based_on": sorted(based_on_facts),
    }, sort_keys=True, separators=(",", ":"))
    return "pred_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def make_prediction(*, claim: str, predicted_value, horizon: str,
                          based_on_facts: Iterable[str],
                          confidence: float,
                          predicted_unit: str = "") -> Prediction:
    if horizon not in PREDICTION_HORIZONS:
        raise ValueError(
            f"unknown horizon {horizon!r}; allowed: {PREDICTION_HORIZONS}"
        )
    pid = _prediction_id(claim=claim, horizon=horizon,
                              based_on_facts=based_on_facts)
    return Prediction(
        prediction_id=pid, claim=claim,
        predicted_value=predicted_value,
        predicted_unit=predicted_unit,
        confidence=float(confidence),
        horizon=horizon,
        based_on_facts=tuple(based_on_facts),
    )


def evaluate_prediction(p: Prediction, *,
                              actual_value,
                              evaluated_at_iso: str) -> Prediction:
    """Return a new Prediction with actual_value + calibration_error."""
    err: float | None = None
    try:
        err = abs(float(actual_value) - float(p.predicted_value))
    except (TypeError, ValueError):
        # Non-numeric prediction; calibration_error stays None
        err = None
    return Prediction(
        prediction_id=p.prediction_id, claim=p.claim,
        predicted_value=p.predicted_value,
        predicted_unit=p.predicted_unit,
        confidence=p.confidence, horizon=p.horizon,
        based_on_facts=p.based_on_facts,
        evaluated_at_iso=evaluated_at_iso,
        actual_value=actual_value,
        calibration_error=err,
    )
