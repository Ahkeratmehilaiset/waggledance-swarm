# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for the six allowlisted family-keyed reference oracles.

These oracles are the autogrowth lane's authoritative reference for the
six low-risk families. ``AutogrowthScheduler.tick()`` looks them up via
``get_oracle`` before calling ``LowRiskGrower.grow_from_gap`` for shadow
evaluation; a wrong oracle silently auto-promotes bad solvers or rejects
good ones. The scheduler tests reach the registry only through happy-path
import; this file pins the family-by-family contract directly.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.family_oracles import (
    FAMILY_ORACLES,
    get_oracle,
)


# --- registry surface --------------------------------------------------

def test_registry_lists_six_allowlisted_families():
    assert set(FAMILY_ORACLES.keys()) == {
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    }


def test_get_oracle_returns_registered_callable():
    assert get_oracle("scalar_unit_conversion") is FAMILY_ORACLES["scalar_unit_conversion"]


def test_get_oracle_raises_keyerror_for_unknown_family():
    with pytest.raises(KeyError):
        get_oracle("not_a_real_family")


# --- scalar_unit_conversion -------------------------------------------

def test_scalar_unit_conversion_with_offset():
    oracle = get_oracle("scalar_unit_conversion")
    # Celsius -> Kelvin: K = C * 1 + 273.15
    assert oracle({"x": 0}, {"factor": 1.0, "offset": 273.15}) == pytest.approx(273.15)
    assert oracle({"x": 100}, {"factor": 1.0, "offset": 273.15}) == pytest.approx(373.15)


def test_scalar_unit_conversion_offset_defaults_to_zero():
    oracle = get_oracle("scalar_unit_conversion")
    # No offset key => default 0.0 => pure scaling.
    assert oracle({"x": 5}, {"factor": 2.5}) == pytest.approx(12.5)


# --- lookup_table -----------------------------------------------------

def test_lookup_table_int_key_match():
    oracle = get_oracle("lookup_table")
    artifact = {"table": {1: "one", 2: "two"}}
    assert oracle({"key": 1}, artifact) == "one"


def test_lookup_table_string_key_fallback():
    """Key 1 is not in table directly, but '1' is — coerce-to-str fallback fires."""
    oracle = get_oracle("lookup_table")
    artifact = {"table": {"1": "one", "2": "two"}}
    assert oracle({"key": 1}, artifact) == "one"


def test_lookup_table_default_when_missing():
    oracle = get_oracle("lookup_table")
    artifact = {"table": {"a": "alpha"}, "default": "FALLBACK"}
    assert oracle({"key": "z"}, artifact) == "FALLBACK"
    # No default => returns None.
    assert oracle({"key": "z"}, {"table": {"a": "alpha"}}) is None


# --- threshold_rule ---------------------------------------------------

@pytest.mark.parametrize(
    "operator,x,th,expected",
    [
        (">",  5.0, 4.0, "fired"),
        (">",  4.0, 4.0, "rest"),
        (">=", 4.0, 4.0, "fired"),
        ("<",  3.0, 4.0, "fired"),
        ("<",  4.0, 4.0, "rest"),
        ("<=", 4.0, 4.0, "fired"),
        ("==", 4.0, 4.0, "fired"),
        ("==", 4.0, 4.5, "rest"),
        ("!=", 4.0, 4.5, "fired"),
        ("!=", 4.0, 4.0, "rest"),
    ],
)
def test_threshold_rule_all_six_operators_at_boundary(operator, x, th, expected):
    oracle = get_oracle("threshold_rule")
    artifact = {
        "operator": operator,
        "threshold": th,
        "true_label": "fired",
        "false_label": "rest",
    }
    assert oracle({"x": x}, artifact) == expected


# --- interval_bucket_classifier ---------------------------------------

def test_interval_bucket_inclusive_lower_exclusive_upper():
    oracle = get_oracle("interval_bucket_classifier")
    artifact = {
        "intervals": [
            {"min": 0,  "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "mid"},
            {"min": 20, "max": 30, "label": "high"},
        ],
        "out_of_range_label": "unknown",
    }
    assert oracle({"x": 0},  artifact) == "low"   # min boundary inclusive
    assert oracle({"x": 9.999}, artifact) == "low"
    assert oracle({"x": 10}, artifact) == "mid"   # exact upper of low IS lower of mid
    assert oracle({"x": 29.999}, artifact) == "high"
    assert oracle({"x": 30}, artifact) == "unknown"  # max excluded
    assert oracle({"x": -1}, artifact) == "unknown"


# --- linear_arithmetic -----------------------------------------------

def test_linear_arithmetic_with_input_columns():
    oracle = get_oracle("linear_arithmetic")
    artifact = {
        "coefficients": [2.0, -1.0, 0.5],
        "intercept": 1.0,
        "input_columns": ["a", "b", "c"],
    }
    # 2*1 - 1*4 + 0.5*10 + 1 = 2 - 4 + 5 + 1 = 4.0
    assert oracle({"a": 1, "b": 4, "c": 10}, artifact) == pytest.approx(4.0)


def test_linear_arithmetic_falls_back_to_x_1_indexing_when_no_input_columns():
    oracle = get_oracle("linear_arithmetic")
    artifact = {"coefficients": [3.0, 4.0], "intercept": 1.0}
    # Without input_columns: x_1=2, x_2=5 => 3*2 + 4*5 + 1 = 27.0
    assert oracle({"x_1": 2, "x_2": 5}, artifact) == pytest.approx(27.0)


# --- bounded_interpolation -------------------------------------------

def _piecewise_artifact(policy: str = "clip") -> dict:
    return {
        "knots": [
            {"x": 0,  "y": 0},
            {"x": 10, "y": 100},
            {"x": 20, "y": 50},
        ],
        "method": "linear",
        "min_x": 0,
        "max_x": 20,
        "out_of_range_policy": policy,
    }


def test_bounded_interpolation_exact_knot_returns_y():
    oracle = get_oracle("bounded_interpolation")
    artifact = _piecewise_artifact()
    assert oracle({"x": 0},  artifact) == pytest.approx(0.0)
    assert oracle({"x": 10}, artifact) == pytest.approx(100.0)
    assert oracle({"x": 20}, artifact) == pytest.approx(50.0)


def test_bounded_interpolation_between_knots_linear():
    oracle = get_oracle("bounded_interpolation")
    artifact = _piecewise_artifact()
    # x=5 is halfway in [0,10] segment with y running 0->100 => 50
    assert oracle({"x": 5},  artifact) == pytest.approx(50.0)
    # x=15 is halfway in [10,20] segment with y running 100->50 => 75
    assert oracle({"x": 15}, artifact) == pytest.approx(75.0)


def test_bounded_interpolation_clip_policy():
    oracle = get_oracle("bounded_interpolation")
    artifact = _piecewise_artifact("clip")
    assert oracle({"x": -5}, artifact) == pytest.approx(0.0)   # clipped to min_x=0
    assert oracle({"x": 99}, artifact) == pytest.approx(50.0)  # clipped to max_x=20


def test_bounded_interpolation_null_policy_returns_none_out_of_range():
    oracle = get_oracle("bounded_interpolation")
    artifact = _piecewise_artifact("null")
    assert oracle({"x": -5}, artifact) is None
    assert oracle({"x": 99}, artifact) is None


def test_bounded_interpolation_error_policy_raises_value_error():
    oracle = get_oracle("bounded_interpolation")
    artifact = _piecewise_artifact("raise")
    with pytest.raises(ValueError):
        oracle({"x": -5}, artifact)


def test_bounded_interpolation_unsupported_method_raises_value_error():
    oracle = get_oracle("bounded_interpolation")
    artifact = {
        "knots": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
        "method": "cubic_spline",
        "min_x": 0,
        "max_x": 1,
    }
    with pytest.raises(ValueError):
        oracle({"x": 0.5}, artifact)
