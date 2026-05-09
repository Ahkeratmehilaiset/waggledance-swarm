# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.prediction_engine.

prediction_engine constructs Prediction objects from causal relations +
observed external facts (Phase 9 §I). Pure: no live execution, no LLM
calls. Drift here would either:
- emit non-deterministic prediction_ids (audit-trail breaks across
  runs, downstream dedup defeated), or
- silently accept invalid horizons (drift from PREDICTION_HORIZONS),
  or
- compute calibration_error wrong / not at all (calibration drift
  detector then operates on bad input).

ZERO direct test coverage on main as of 2026-05-09.

Pinned invariants:

- _prediction_id is deterministic on (claim, horizon,
  sorted(based_on_facts)). The fact list is sorted before hashing
  so caller order doesn't change the id.
- _prediction_id format: "pred_" + 10-char hex prefix.
- make_prediction:
  - raises ValueError if horizon not in PREDICTION_HORIZONS.
  - confidence is float()-cast (so int input becomes float).
  - based_on_facts is tuple()-cast (idempotent if already tuple,
    fixes if list/iterable).
  - predicted_unit defaults to "".
- evaluate_prediction:
  - returns a NEW Prediction (frozen dataclass; original
    untouched).
  - calibration_error = abs(float(actual) - float(predicted))
    when both numeric.
  - calibration_error = None when prediction is non-numeric (the
    abs/float chain raises TypeError/ValueError and we swallow).
  - prediction_id, claim, predicted_value/unit, confidence,
    horizon, based_on_facts ALL preserved verbatim.
  - actual_value carried verbatim into the new Prediction.
  - evaluated_at_iso carried verbatim into the new Prediction.
"""
from __future__ import annotations

import pytest

from waggledance.core.world_model import PREDICTION_HORIZONS
from waggledance.core.world_model.prediction_engine import (
    _prediction_id,
    evaluate_prediction,
    make_prediction,
)


# --- _prediction_id determinism -----------------------------------

def test_prediction_id_deterministic_on_same_inputs():
    a = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f1", "f2"))
    b = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f1", "f2"))
    assert a == b


def test_prediction_id_changes_when_claim_changes():
    a = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f1",))
    b = _prediction_id(claim="Y", horizon="short_term",
                            based_on_facts=("f1",))
    assert a != b


def test_prediction_id_changes_when_horizon_changes():
    a = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f1",))
    b = _prediction_id(claim="X", horizon="long_term",
                            based_on_facts=("f1",))
    assert a != b


def test_prediction_id_invariant_to_fact_order():
    """The fact list is sorted before hashing, so caller order
    must NOT affect the id. A drifted impl that hashes raw order
    would break dedup whenever upstream code reordered facts."""
    a = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f1", "f2", "f3"))
    b = _prediction_id(claim="X", horizon="short_term",
                            based_on_facts=("f3", "f1", "f2"))
    assert a == b


def test_prediction_id_format_is_pred_prefix_plus_short_hash():
    pid = _prediction_id(claim="X", horizon="short_term",
                              based_on_facts=())
    assert pid.startswith("pred_")
    assert len(pid) == len("pred_") + 10


# --- make_prediction: horizon validation -------------------------

def test_make_prediction_rejects_unknown_horizon():
    with pytest.raises(ValueError) as ei:
        make_prediction(
            claim="X", predicted_value=1.0, horizon="not_a_horizon",
            based_on_facts=(), confidence=0.5,
        )
    assert "unknown horizon" in str(ei.value)


@pytest.mark.parametrize("horizon", list(PREDICTION_HORIZONS))
def test_make_prediction_accepts_each_documented_horizon(horizon):
    """Every horizon in PREDICTION_HORIZONS must construct a
    valid Prediction. Pinned so a future shrink of the enum is
    caught loudly."""
    p = make_prediction(
        claim=f"test_{horizon}", predicted_value=0.0,
        horizon=horizon, based_on_facts=("f1",), confidence=0.5,
    )
    assert p.horizon == horizon


# --- make_prediction: shape -------------------------------------

def test_make_prediction_returns_prediction_with_id_and_fields():
    p = make_prediction(
        claim="thermal cell will warm up",
        predicted_value=21.5, horizon="short_term",
        based_on_facts=("f1", "f2"),
        confidence=0.7, predicted_unit="C",
    )
    assert p.prediction_id.startswith("pred_")
    assert p.claim == "thermal cell will warm up"
    assert p.predicted_value == 21.5
    assert p.predicted_unit == "C"
    assert p.confidence == 0.7
    assert p.horizon == "short_term"
    assert p.based_on_facts == ("f1", "f2")
    # Not yet evaluated
    assert p.actual_value is None
    assert p.calibration_error is None
    assert p.evaluated_at_iso is None


def test_make_prediction_confidence_float_cast():
    """Int confidence is coerced to float. A drifted impl that
    keeps int would break downstream comparisons that assume
    float."""
    p = make_prediction(
        claim="X", predicted_value=0.0, horizon="short_term",
        based_on_facts=(), confidence=1,
    )
    assert isinstance(p.confidence, float)
    assert p.confidence == 1.0


def test_make_prediction_based_on_facts_tuple_cast():
    """based_on_facts is tuple-cast for hashability + frozen
    dataclass. List input must work."""
    p = make_prediction(
        claim="X", predicted_value=0.0, horizon="short_term",
        based_on_facts=["f1", "f2"], confidence=0.5,
    )
    assert isinstance(p.based_on_facts, tuple)
    assert p.based_on_facts == ("f1", "f2")


def test_make_prediction_default_predicted_unit_is_empty_string():
    p = make_prediction(
        claim="X", predicted_value=0.0, horizon="short_term",
        based_on_facts=(), confidence=0.5,
    )
    assert p.predicted_unit == ""


def test_make_prediction_id_consistency_across_calls():
    """make_prediction with identical args must produce
    identical prediction_id (round-trip the id determinism)."""
    args = dict(
        claim="X", predicted_value=0.0, horizon="short_term",
        based_on_facts=("f1",), confidence=0.5,
    )
    p1 = make_prediction(**args)
    p2 = make_prediction(**args)
    assert p1.prediction_id == p2.prediction_id


# --- evaluate_prediction: numeric calibration_error --------------

def test_evaluate_prediction_numeric_calibration_error():
    p = make_prediction(
        claim="temp will be 20C", predicted_value=20.0,
        horizon="short_term", based_on_facts=(),
        confidence=0.5,
    )
    evaluated = evaluate_prediction(
        p, actual_value=22.5, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert evaluated.actual_value == 22.5
    assert evaluated.calibration_error == 2.5
    assert evaluated.evaluated_at_iso == "2026-05-09T00:00:00Z"


def test_evaluate_prediction_calibration_error_is_absolute():
    """abs(actual - predicted): under-prediction and
    over-prediction both yield positive error."""
    p = make_prediction(
        claim="X", predicted_value=10.0,
        horizon="short_term", based_on_facts=(),
        confidence=0.5,
    )
    over = evaluate_prediction(
        p, actual_value=15.0, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    under = evaluate_prediction(
        p, actual_value=5.0, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert over.calibration_error == 5.0
    assert under.calibration_error == 5.0  # NOT -5.0


def test_evaluate_prediction_int_actual_against_float_predicted():
    """Numeric coercion via float() on both sides — int actual vs
    float predicted should still produce numeric error."""
    p = make_prediction(
        claim="X", predicted_value=10.5,
        horizon="short_term", based_on_facts=(),
        confidence=0.5,
    )
    e = evaluate_prediction(
        p, actual_value=12, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert e.calibration_error == 1.5


def test_evaluate_prediction_string_predicted_yields_none_error():
    """Non-numeric predicted_value: float() chain raises;
    calibration_error stays None — that's the documented escape
    hatch for categorical predictions."""
    p = make_prediction(
        claim="next color", predicted_value="red",
        horizon="short_term", based_on_facts=(),
        confidence=0.5,
    )
    e = evaluate_prediction(
        p, actual_value="blue", evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert e.actual_value == "blue"
    assert e.calibration_error is None


def test_evaluate_prediction_dict_predicted_yields_none_error():
    """A dict predicted_value (e.g. structured forecast) would
    hit TypeError on float() — calibration_error stays None
    instead of crashing."""
    p = make_prediction(
        claim="X", predicted_value={"nested": 1.0},
        horizon="short_term", based_on_facts=(),
        confidence=0.5,
    )
    e = evaluate_prediction(
        p, actual_value={"nested": 1.0},
        evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert e.calibration_error is None


# --- evaluate_prediction: original is untouched -------------------

def test_evaluate_prediction_returns_new_object_original_untouched():
    """Prediction is a frozen dataclass; evaluate_prediction
    returns a NEW Prediction. The original must remain
    unevaluated (defensive against accidental mutation)."""
    p = make_prediction(
        claim="X", predicted_value=10.0, horizon="short_term",
        based_on_facts=("f1",), confidence=0.5,
    )
    original_pid = p.prediction_id
    e = evaluate_prediction(
        p, actual_value=12.0, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    # Original untouched
    assert p.actual_value is None
    assert p.calibration_error is None
    assert p.evaluated_at_iso is None
    # Evaluated copy carries new fields
    assert e.actual_value == 12.0
    assert e.calibration_error == 2.0
    assert e.evaluated_at_iso == "2026-05-09T00:00:00Z"
    # Identity-preserving fields are equal in both
    assert e.prediction_id == original_pid
    assert e.claim == p.claim
    assert e.predicted_value == p.predicted_value


def test_evaluate_prediction_preserves_all_identity_fields():
    """The evaluated Prediction must keep prediction_id, claim,
    predicted_value, predicted_unit, confidence, horizon,
    based_on_facts verbatim. Drift here would break dedup or
    audit-trail across the evaluate step."""
    p = make_prediction(
        claim="thermal cell will warm up",
        predicted_value=21.5, horizon="medium_term",
        based_on_facts=("f1", "f2"),
        confidence=0.7, predicted_unit="C",
    )
    e = evaluate_prediction(
        p, actual_value=20.0,
        evaluated_at_iso="2026-05-09T01:00:00Z",
    )
    assert e.prediction_id == p.prediction_id
    assert e.claim == p.claim
    assert e.predicted_value == p.predicted_value
    assert e.predicted_unit == p.predicted_unit
    assert e.confidence == p.confidence
    assert e.horizon == p.horizon
    assert e.based_on_facts == p.based_on_facts


def test_evaluate_prediction_zero_error_when_actual_equals_predicted():
    p = make_prediction(
        claim="X", predicted_value=10.0, horizon="short_term",
        based_on_facts=(), confidence=1.0,
    )
    e = evaluate_prediction(
        p, actual_value=10.0, evaluated_at_iso="2026-05-09T00:00:00Z",
    )
    assert e.calibration_error == 0.0
