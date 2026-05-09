# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.deterministic_solver_compiler.

Phase 9 §U1 deterministic compiler: every known family has a
hand-rolled compiler that turns a SolverSpec into a byte-identical
artifact dict. There is no free-form code generation here — these
artifacts go through the autogrowth gate without LLM round-tripping.

A regression in any compiler can produce structurally-valid artifacts
that classify, interpolate, threshold, or compose incorrectly while
remaining schema-valid. Direct test coverage on this file was zero
before this PR (R7 Candidate 1, surfaced by Claude's parallel scout
during R6).

Pinned invariants:

- `compile_spec` raises ValueError on unknown family_kind.
- `all_compiler_kinds()` matches `SOLVER_FAMILY_KINDS` constant.
- `CompiledSolver.canonical_json` is byte-identical given identical
  input → `artifact_id` is deterministic.
- Each family compiler:
  - normalises numeric fields to float;
  - sorts dict keys / interval lists deterministically;
  - rejects illegal operators / aggregators / interpolation methods.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis import (
    SOLVER_FAMILY_KINDS,
    SOLVER_SYNTHESIS_SCHEMA_VERSION,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    CompiledSolver,
    all_compiler_kinds,
    compile_spec,
)


# --- helpers --------------------------------------------------------

def _spec(family_kind: str, spec: dict, *, solver_name: str = "test_solver") -> SolverSpec:
    return SolverSpec(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        spec_id=f"spec_{family_kind}",
        family_kind=family_kind,
        solver_name=solver_name,
        cell_id="general",
        spec=spec,
        source="test",
        source_kind="manual",
    )


# --- registry surface ----------------------------------------------

def test_all_compiler_kinds_matches_solver_family_kinds_constant():
    assert set(all_compiler_kinds()) == set(SOLVER_FAMILY_KINDS)


def test_compile_spec_raises_on_unknown_family_kind():
    """Unknown family_kind must raise at compile time, not produce a
    bogus artifact. We bypass __post_init__ via object.__setattr__
    because SolverSpec validates family_kind on construction."""
    spec = _spec("scalar_unit_conversion",
                 {"from_unit": "C", "to_unit": "K",
                  "factor": 1.0, "offset": 273.15})
    object.__setattr__(spec, "family_kind", "not_a_family")
    with pytest.raises(ValueError, match="no compiler for"):
        compile_spec(spec)


# --- determinism: byte-identical artifact_id -----------------------

def test_compile_spec_byte_identical_canonical_json_for_same_input():
    s1 = _spec("scalar_unit_conversion",
               {"from_unit": "C", "to_unit": "K",
                "factor": 1.0, "offset": 273.15})
    s2 = _spec("scalar_unit_conversion",
               {"from_unit": "C", "to_unit": "K",
                "factor": 1.0, "offset": 273.15})
    a = compile_spec(s1)
    b = compile_spec(s2)
    assert a.canonical_json == b.canonical_json
    assert a.artifact_id == b.artifact_id


def test_compile_spec_artifact_id_changes_when_input_changes():
    s_a = _spec("scalar_unit_conversion",
                {"from_unit": "C", "to_unit": "K",
                 "factor": 1.0, "offset": 273.15})
    s_b = _spec("scalar_unit_conversion",
                {"from_unit": "C", "to_unit": "F",
                 "factor": 1.8, "offset": 32.0})
    a = compile_spec(s_a)
    b = compile_spec(s_b)
    assert a.artifact_id != b.artifact_id


# --- per-family compilers ------------------------------------------

def test_scalar_unit_conversion_normalises_numeric_fields_to_float():
    s = _spec("scalar_unit_conversion", {
        "from_unit": "C", "to_unit": "K",
        "factor": "1.0", "offset": 273,  # str / int — must be coerced
    })
    out = compile_spec(s).artifact
    assert out["kind"] == "scalar_unit_conversion"
    assert out["factor"] == 1.0
    assert out["offset"] == 273.0
    assert isinstance(out["factor"], float)
    assert "y = x *" in out["formula"]


def test_lookup_table_sorts_keys_and_records_size():
    s = _spec("lookup_table", {
        "table": {"z": "last", "a": "first", "m": "middle"},
        "default": "FALLBACK",
    })
    out = compile_spec(s).artifact
    keys_in_order = list(out["table"].keys())
    assert keys_in_order == sorted(keys_in_order)
    assert out["default"] == "FALLBACK"
    assert out["size"] == 3


def test_lookup_table_raises_when_table_not_dict():
    s = _spec("lookup_table", {"table": [("a", 1)]})
    with pytest.raises(ValueError, match="table.* must be a dict"):
        compile_spec(s)


@pytest.mark.parametrize("op", [">", ">=", "<", "<=", "==", "!="])
def test_threshold_rule_accepts_all_six_operators(op):
    s = _spec("threshold_rule", {
        "threshold": "5", "operator": op,
        "true_label": "hi", "false_label": "lo",
    })
    out = compile_spec(s).artifact
    assert out["operator"] == op
    assert out["threshold"] == 5.0
    assert isinstance(out["threshold"], float)


def test_threshold_rule_rejects_unknown_operator():
    s = _spec("threshold_rule", {
        "threshold": 1, "operator": "≈",
        "true_label": "hi", "false_label": "lo",
    })
    with pytest.raises(ValueError, match="unknown operator"):
        compile_spec(s)


def test_interval_bucket_sorts_intervals_by_min():
    s = _spec("interval_bucket_classifier", {
        "intervals": [
            {"min": 20, "max": 30, "label": "high"},
            {"min": 0,  "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "mid"},
        ],
        "out_of_range_label": "unknown",
    })
    out = compile_spec(s).artifact
    mins = [i["min"] for i in out["intervals"]]
    assert mins == sorted(mins)
    assert out["out_of_range_label"] == "unknown"


def test_interval_bucket_raises_when_min_greater_than_max():
    s = _spec("interval_bucket_classifier", {
        "intervals": [{"min": 30, "max": 10, "label": "bad"}],
    })
    with pytest.raises(ValueError, match="interval min"):
        compile_spec(s)


def test_linear_arithmetic_normalises_coefficients_and_intercept():
    s = _spec("linear_arithmetic", {
        "coefficients": ["2.0", 3, 0.5],
        "intercept": 1,
        "input_columns": ["a", "b", "c"],
    })
    out = compile_spec(s).artifact
    assert out["coefficients"] == [2.0, 3.0, 0.5]
    assert all(isinstance(c, float) for c in out["coefficients"])
    assert out["intercept"] == 1.0
    assert out["input_columns"] == ["a", "b", "c"]


def test_weighted_aggregation_sorts_dict_weights():
    s = _spec("weighted_aggregation", {
        "weights": {"z": 0.1, "a": 0.5, "m": 0.4},
        "missing_policy": "drop",
    })
    out = compile_spec(s).artifact
    keys_in_order = list(out["weights"].keys())
    assert keys_in_order == sorted(keys_in_order)
    assert out["missing_policy"] == "drop"


def test_weighted_aggregation_default_missing_policy_drop():
    s = _spec("weighted_aggregation", {"weights": {"a": 1.0}})
    out = compile_spec(s).artifact
    assert out["missing_policy"] == "drop"


def test_temporal_window_rejects_unknown_aggregator():
    s = _spec("temporal_window_rule", {
        "window_seconds": 60, "aggregator": "stddev",
        "threshold": 10, "operator": ">",
    })
    with pytest.raises(ValueError, match="unknown aggregator"):
        compile_spec(s)


def test_temporal_window_normalises_window_seconds_to_int():
    s = _spec("temporal_window_rule", {
        "window_seconds": "120", "aggregator": "mean",
        "threshold": 5, "operator": ">",
    })
    out = compile_spec(s).artifact
    assert out["window_seconds"] == 120
    assert isinstance(out["window_seconds"], int)
    assert out["threshold"] == 5.0


def test_bounded_interpolation_sorts_knots_by_x():
    s = _spec("bounded_interpolation", {
        "knots": [{"x": 10, "y": 5}, {"x": 0, "y": 0}, {"x": 20, "y": 10}],
        "method": "linear",
        "min_x": 0, "max_x": 20,
    })
    out = compile_spec(s).artifact
    xs = [k["x"] for k in out["knots"]]
    assert xs == sorted(xs)
    assert out["min_x"] == 0.0
    assert out["max_x"] == 20.0
    assert out["out_of_range_policy"] == "clip"  # default


def test_bounded_interpolation_rejects_unknown_method():
    s = _spec("bounded_interpolation", {
        "knots": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
        "method": "spline_5th_degree",
        "min_x": 0, "max_x": 1,
    })
    with pytest.raises(ValueError, match="unknown method"):
        compile_spec(s)


def test_structured_field_extractor_default_extract_kind_regex():
    s = _spec("structured_field_extractor", {
        "source_field": "raw_text",
        "extract_pattern": r"(\d+)\s*°C",
    })
    out = compile_spec(s).artifact
    assert out["extract_kind"] == "regex"
    assert out["source_field"] == "raw_text"


def test_composition_wrapper_records_step_count():
    s = _spec("deterministic_composition_wrapper", {
        "steps": ["step_a", "step_b", "step_c"],
    })
    out = compile_spec(s).artifact
    assert out["steps"] == ["step_a", "step_b", "step_c"]
    assert out["step_count"] == 3


def test_composition_wrapper_rejects_empty_steps():
    s = _spec("deterministic_composition_wrapper", {"steps": []})
    with pytest.raises(ValueError, match="non-empty"):
        compile_spec(s)


# --- to_dict serialization -----------------------------------------

def test_compiled_solver_to_dict_preserves_artifact_and_omits_canonical_json():
    s = _spec("scalar_unit_conversion", {
        "from_unit": "C", "to_unit": "K",
        "factor": 1.0, "offset": 273.15,
    })
    cs = compile_spec(s)
    d = cs.to_dict()
    assert d["schema_version"] == SOLVER_SYNTHESIS_SCHEMA_VERSION
    assert d["family_kind"] == "scalar_unit_conversion"
    assert d["artifact_id"] == cs.artifact_id
    assert d["artifact"] == cs.artifact
    # to_dict omits canonical_json and is JSON-serializable.
    assert "canonical_json" not in d
