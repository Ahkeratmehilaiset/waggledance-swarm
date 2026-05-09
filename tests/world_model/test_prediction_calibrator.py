# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.prediction_calibrator.

Iteration N+6 second scout (Claude solo). prediction_calibrator
aggregates prediction errors per dimension into a CalibrationRecord
that the drift detector then evaluates over time (Phase 9 §I).
Drift here would either mis-aggregate evidence (corrupt drift
input) or silently include un-evaluated predictions
(self_model contamination of EXTERNAL prediction calibration).

Pinned invariants:

- Predictions with calibration_error is None OR actual_value is
  None are SILENTLY SKIPPED — never raise. Calibration is per
  EVALUATED prediction only.
- Grouping key default is "horizon"; missing/None grouping
  attribute falls back to "unknown" sentinel.
- Custom dimension_key parameter respected.
- Empty input -> empty dict.
- All-unevaluated input -> empty dict.
- Per-dimension stats:
  - prior_score = mean(confidence) rounded to 6 decimals.
  - abs_error = mean(calibration_error) rounded to 6 decimals.
  - evidence_implied_score = clamp(1.0 - mean_err, 0, 1) rounded
    to 6 decimals (the "what data says we should have predicted"
    proxy; clamping avoids negative scores when error > 1.0).
  - n_observations = number of evaluated predictions in dimension.
- Output dict order is sorted by dimension (audit determinism via
  `sorted(grouped.items())`).
- calibration_to_snapshot_dict round-trips each record's to_dict
  in the same sorted order.
- CalibrationRecord is frozen.
"""
from __future__ import annotations

import dataclasses

import pytest

from waggledance.core.world_model.prediction_calibrator import (
    CalibrationRecord,
    calibrate_per_dimension,
    calibration_to_snapshot_dict,
)
from waggledance.core.world_model.world_model_snapshot import Prediction


# --- helpers -------------------------------------------------------

def _pred(
    pid: str = "p1",
    horizon: str = "short_term",
    confidence: float = 0.5,
    calibration_error: float | None = 0.1,
    actual_value: object = "actual",
) -> Prediction:
    return Prediction(
        prediction_id=pid,
        claim="claim",
        predicted_value="pred",
        confidence=confidence,
        horizon=horizon,
        actual_value=actual_value,
        calibration_error=calibration_error,
    )


# --- evaluation gating: skip un-evaluated --------------------------

def test_calibrate_skips_predictions_with_none_calibration_error():
    """A prediction with calibration_error=None has not been
    evaluated yet; it MUST NOT contribute to the calibration
    aggregate."""
    preds = [
        _pred("p1", "short_term", calibration_error=None),
        _pred("p2", "short_term", calibration_error=0.2, confidence=0.5),
    ]
    out = calibrate_per_dimension(preds)
    assert "short_term" in out
    rec = out["short_term"]
    assert rec.n_observations == 1  # only p2 counted


def test_calibrate_skips_predictions_with_none_actual_value():
    """actual_value=None means the prediction was not yet observed;
    skipped."""
    preds = [
        _pred("p1", "short_term", actual_value=None, calibration_error=0.5),
        _pred("p2", "short_term", actual_value="x", calibration_error=0.1,
                 confidence=0.4),
    ]
    out = calibrate_per_dimension(preds)
    assert out["short_term"].n_observations == 1


def test_calibrate_empty_input_returns_empty_dict():
    assert calibrate_per_dimension([]) == {}


def test_calibrate_all_unevaluated_returns_empty_dict():
    """If EVERY prediction is unevaluated (None calibration_error
    OR None actual), no records emit."""
    preds = [
        _pred("p1", "short_term", calibration_error=None),
        _pred("p2", "short_term", actual_value=None),
    ]
    assert calibrate_per_dimension(preds) == {}


# --- grouping key ------------------------------------------------

def test_calibrate_groups_by_horizon_by_default():
    preds = [
        _pred("p1", horizon="short_term", confidence=0.6,
                 calibration_error=0.1),
        _pred("p2", horizon="short_term", confidence=0.4,
                 calibration_error=0.3),
        _pred("p3", horizon="medium_term", confidence=0.5,
                 calibration_error=0.2),
    ]
    out = calibrate_per_dimension(preds)
    assert set(out.keys()) == {"short_term", "medium_term"}
    assert out["short_term"].n_observations == 2
    assert out["medium_term"].n_observations == 1


def test_calibrate_custom_dimension_key():
    """Caller may group by a different attribute. claim is a
    string field on Prediction so it works for testing."""
    preds = [
        _pred("p1", horizon="short_term", confidence=0.5,
                 calibration_error=0.1),
        _pred("p2", horizon="short_term", confidence=0.7,
                 calibration_error=0.2),
    ]
    # Override "claim" attr per pred via dataclass replace
    p1 = dataclasses.replace(preds[0], claim="claim_a")
    p2 = dataclasses.replace(preds[1], claim="claim_b")
    out = calibrate_per_dimension([p1, p2], dimension_key="claim")
    assert set(out.keys()) == {"claim_a", "claim_b"}


def test_calibrate_missing_attribute_falls_back_to_unknown():
    """If the dimension_key attribute is missing from a Prediction,
    grouping uses 'unknown' sentinel — never raises AttributeError."""
    preds = [_pred("p1", horizon="short_term", calibration_error=0.1)]
    # Group by an attribute that doesn't exist on Prediction
    out = calibrate_per_dimension(preds, dimension_key="nonexistent")
    assert "unknown" in out
    assert out["unknown"].n_observations == 1


def test_calibrate_empty_dimension_key_value_falls_back_to_unknown():
    """If the grouping attribute exists but is an empty string
    (truthy-fallback test), grouping uses 'unknown' sentinel.
    `getattr(...) or "unknown"` catches both missing AND falsy
    values. Use predicted_unit as the dimension_key since
    Prediction validates `horizon` to a fixed set, but
    predicted_unit defaults to ''."""
    # Default predicted_unit is '' (empty string), which is falsy
    pred = _pred("p1", "short_term", calibration_error=0.1)
    # predicted_unit defaults to "" -> falsy -> "unknown"
    out = calibrate_per_dimension(
        [pred], dimension_key="predicted_unit",
    )
    assert "unknown" in out


# --- per-dimension stats: mean + clamp + rounding ----------------

def test_calibrate_prior_score_is_mean_confidence():
    """prior_score = mean(confidence) rounded to 6 decimals.
    Two preds with conf 0.4 and 0.6 -> mean 0.5."""
    preds = [
        _pred("p1", "short_term", confidence=0.4, calibration_error=0.1),
        _pred("p2", "short_term", confidence=0.6, calibration_error=0.1),
    ]
    out = calibrate_per_dimension(preds)
    assert out["short_term"].prior_score == 0.5


def test_calibrate_abs_error_is_mean_calibration_error():
    """abs_error = mean(calibration_error) rounded to 6 decimals."""
    preds = [
        _pred("p1", "short_term", confidence=0.5, calibration_error=0.2),
        _pred("p2", "short_term", confidence=0.5, calibration_error=0.4),
    ]
    out = calibrate_per_dimension(preds)
    # mean = 0.3 (exactly representable)
    assert out["short_term"].abs_error == 0.3


def test_calibrate_evidence_implied_is_one_minus_mean_error():
    """evidence_implied_score = clamp(1.0 - mean_err, 0, 1).
    With mean_err=0.3, evidence_implied=0.7."""
    preds = [
        _pred("p1", "short_term", confidence=0.5, calibration_error=0.2),
        _pred("p2", "short_term", confidence=0.5, calibration_error=0.4),
    ]
    out = calibrate_per_dimension(preds)
    assert out["short_term"].evidence_implied_score == 0.7


def test_calibrate_evidence_implied_clamped_at_zero_for_high_error():
    """When mean_err > 1.0, evidence_implied clamps to 0.0
    (must never go negative — the score is in [0, 1])."""
    preds = [_pred("p1", "short_term", confidence=0.5, calibration_error=2.0)]
    out = calibrate_per_dimension(preds)
    # 1.0 - 2.0 = -1.0 -> clamp to 0.0
    assert out["short_term"].evidence_implied_score == 0.0


def test_calibrate_evidence_implied_clamped_at_one_for_negative_error():
    """When mean_err < 0 (negative — better than perfect, weird but
    not impossible if calibration_error is signed), 1.0 - (-x) > 1.0
    must clamp to 1.0."""
    preds = [_pred("p1", "short_term", confidence=0.5, calibration_error=-0.5)]
    out = calibrate_per_dimension(preds)
    # 1.0 - (-0.5) = 1.5 -> clamp to 1.0
    assert out["short_term"].evidence_implied_score == 1.0


def test_calibrate_n_observations_equals_evaluated_count():
    """n_observations counts ONLY evaluated predictions; not
    skipped ones."""
    preds = [
        _pred("p1", "short_term", calibration_error=None),       # skipped
        _pred("p2", "short_term", actual_value=None,
                 calibration_error=0.5),                    # skipped
        _pred("p3", "short_term", calibration_error=0.1),         # counted
        _pred("p4", "short_term", calibration_error=0.2),         # counted
    ]
    out = calibrate_per_dimension(preds)
    assert out["short_term"].n_observations == 2


# --- output dict ordering ----------------------------------------

def test_calibrate_output_sorted_by_dimension():
    """Output dict iteration order is sorted by dimension name
    (via `sorted(grouped.items())`). Audit byte-stability for
    snapshot consumers."""
    preds = [
        _pred("p1", horizon="long_term", calibration_error=0.1),
        _pred("p2", horizon="immediate", calibration_error=0.1),
        _pred("p3", horizon="medium_term", calibration_error=0.1),
    ]
    out = calibrate_per_dimension(preds)
    keys = list(out.keys())
    # sorted alphabetically: immediate, long_term, medium_term
    assert keys == sorted(keys)
    assert keys == ["immediate", "long_term", "medium_term"]


# --- rounding to 6 decimals --------------------------------------

def test_calibrate_stats_rounded_to_six_decimals():
    """Long-tail floats truncated to 6 decimals — audit
    consumers see byte-stable values."""
    one_third = 1 / 3
    preds = [
        _pred(f"p{i}", "short_term", confidence=one_third, calibration_error=one_third)
        for i in range(3)
    ]
    out = calibrate_per_dimension(preds)
    rec = out["short_term"]
    assert rec.prior_score == round(one_third, 6)
    assert rec.abs_error == round(one_third, 6)


# --- calibration_to_snapshot_dict --------------------------------

def test_calibration_to_snapshot_dict_round_trips_records():
    preds = [
        _pred("p1", "long_term", confidence=0.5, calibration_error=0.1),
        _pred("p2", "immediate", confidence=0.6, calibration_error=0.2),
    ]
    records = calibrate_per_dimension(preds)
    snapshot = calibration_to_snapshot_dict(records)
    # sorted alphabetically: immediate, long_term
    keys = list(snapshot.keys())
    assert keys == sorted(keys)
    assert keys == ["immediate", "long_term"]
    # round-trips per-record fields
    rec = records["immediate"]
    assert snapshot["immediate"] == {
        "prior_score": rec.prior_score,
        "evidence_implied_score": rec.evidence_implied_score,
        "abs_error": rec.abs_error,
        "n_observations": rec.n_observations,
    }


def test_calibration_to_snapshot_dict_empty_input():
    assert calibration_to_snapshot_dict({}) == {}


# --- CalibrationRecord dataclass contract ------------------------

def test_calibration_record_is_frozen():
    rec = CalibrationRecord(
        dimension="h", prior_score=0.5,
        evidence_implied_score=0.6, abs_error=0.1,
        n_observations=5,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.prior_score = 0.0  # type: ignore[misc]


def test_calibration_record_to_dict_omits_dimension_field():
    """to_dict carries the 4 numeric fields but NOT 'dimension' —
    that's the dict KEY in calibration_to_snapshot_dict, not a
    field. Pinned to guard against accidental schema drift."""
    rec = CalibrationRecord(
        dimension="h", prior_score=0.5,
        evidence_implied_score=0.6, abs_error=0.1,
        n_observations=5,
    )
    d = rec.to_dict()
    assert d == {
        "prior_score": 0.5,
        "evidence_implied_score": 0.6,
        "abs_error": 0.1,
        "n_observations": 5,
    }
    assert "dimension" not in d
