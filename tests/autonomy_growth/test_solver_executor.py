# SPDX-License-Identifier: Apache-2.0
"""Per-family executor tests for the low-risk runtime lane."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.solver_executor import (
    ExecutorError,
    UnsupportedFamilyError,
    execute_artifact,
)


def test_scalar_unit_conversion_celsius_to_kelvin() -> None:
    artifact = {
        "kind": "scalar_unit_conversion",
        "from_unit": "C", "to_unit": "K",
        "factor": 1.0, "offset": 273.15,
    }
    assert execute_artifact(artifact, {"x": 0.0}) == pytest.approx(273.15)
    assert execute_artifact(artifact, {"x": 100.0}) == pytest.approx(373.15)


def test_scalar_unit_conversion_default_offset() -> None:
    artifact = {
        "kind": "scalar_unit_conversion",
        "from_unit": "m", "to_unit": "km",
        "factor": 0.001,
    }
    assert execute_artifact(artifact, {"x": 5000}) == pytest.approx(5.0)


def test_scalar_unit_conversion_missing_input_raises() -> None:
    artifact = {
        "kind": "scalar_unit_conversion",
        "from_unit": "m", "to_unit": "km",
        "factor": 0.001,
    }
    with pytest.raises(ExecutorError):
        execute_artifact(artifact, {"y": 1})


def test_lookup_table_hit_and_default() -> None:
    artifact = {
        "kind": "lookup_table",
        "table": {"red": "stop", "green": "go", "yellow": "slow"},
        "default": "unknown",
    }
    assert execute_artifact(artifact, {"key": "red"}) == "stop"
    assert execute_artifact(artifact, {"key": "blue"}) == "unknown"


def test_lookup_table_numeric_key_is_string_normalized_at_compile() -> None:
    artifact = {
        "kind": "lookup_table",
        "table": {"1": "a", "2": "b"},
        "default": None,
    }
    assert execute_artifact(artifact, {"key": 1}) == "a"
    assert execute_artifact(artifact, {"key": "2"}) == "b"


def test_threshold_rule_gt() -> None:
    artifact = {
        "kind": "threshold_rule",
        "threshold": 30.0, "operator": ">",
        "true_label": "hot", "false_label": "cool",
    }
    assert execute_artifact(artifact, {"x": 35.0}) == "hot"
    assert execute_artifact(artifact, {"x": 30.0}) == "cool"
    assert execute_artifact(artifact, {"x": 25.0}) == "cool"


def test_threshold_rule_unknown_operator_raises() -> None:
    artifact = {
        "kind": "threshold_rule",
        "threshold": 0.0, "operator": "??",
        "true_label": "a", "false_label": "b",
    }
    with pytest.raises(ExecutorError):
        execute_artifact(artifact, {"x": 1})


def test_interval_bucket_classifier_inside_and_out() -> None:
    artifact = {
        "kind": "interval_bucket_classifier",
        "intervals": [
            {"min": 0, "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "mid"},
            {"min": 20, "max": 100, "label": "high"},
        ],
        "out_of_range_label": "out",
    }
    assert execute_artifact(artifact, {"x": 5}) == "low"
    assert execute_artifact(artifact, {"x": 10}) == "mid"  # half-open
    assert execute_artifact(artifact, {"x": 50}) == "high"
    assert execute_artifact(artifact, {"x": 200}) == "out"


def test_linear_arithmetic_with_input_columns() -> None:
    artifact = {
        "kind": "linear_arithmetic",
        "coefficients": [2.0, -1.0, 0.5],
        "intercept": 10.0,
        "input_columns": ["a", "b", "c"],
    }
    out = execute_artifact(artifact, {"a": 1.0, "b": 2.0, "c": 4.0})
    # 2*1 + -1*2 + 0.5*4 + 10 = 2 - 2 + 2 + 10 = 12
    assert out == pytest.approx(12.0)


def test_linear_arithmetic_with_positional_x_keys() -> None:
    artifact = {
        "kind": "linear_arithmetic",
        "coefficients": [1.0, 2.0],
        "intercept": 0.0,
        "input_columns": [],
    }
    out = execute_artifact(artifact, {"x_1": 3.0, "x_2": 5.0})
    assert out == pytest.approx(13.0)


def test_bounded_interpolation_linear_inside_range() -> None:
    artifact = {
        "kind": "bounded_interpolation",
        "knots": [
            {"x": 0.0, "y": 0.0},
            {"x": 10.0, "y": 100.0},
            {"x": 20.0, "y": 50.0},
        ],
        "method": "linear",
        "min_x": 0.0,
        "max_x": 20.0,
        "out_of_range_policy": "clip",
    }
    assert execute_artifact(artifact, {"x": 5.0}) == pytest.approx(50.0)
    assert execute_artifact(artifact, {"x": 15.0}) == pytest.approx(75.0)
    assert execute_artifact(artifact, {"x": 0.0}) == pytest.approx(0.0)
    assert execute_artifact(artifact, {"x": 20.0}) == pytest.approx(50.0)


def test_bounded_interpolation_clip_out_of_range() -> None:
    artifact = {
        "kind": "bounded_interpolation",
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 100.0}],
        "method": "linear",
        "min_x": 0.0,
        "max_x": 10.0,
        "out_of_range_policy": "clip",
    }
    assert execute_artifact(artifact, {"x": -1.0}) == pytest.approx(0.0)
    assert execute_artifact(artifact, {"x": 100.0}) == pytest.approx(100.0)


def test_bounded_interpolation_null_policy() -> None:
    artifact = {
        "kind": "bounded_interpolation",
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 100.0}],
        "method": "linear",
        "min_x": 0.0,
        "max_x": 10.0,
        "out_of_range_policy": "null",
    }
    assert execute_artifact(artifact, {"x": -1.0}) is None


def test_unsupported_family_raises() -> None:
    artifact = {"kind": "temporal_window_rule"}
    with pytest.raises(UnsupportedFamilyError):
        execute_artifact(artifact, {"x": 1})


def test_artifact_missing_kind_raises() -> None:
    with pytest.raises(ExecutorError):
        execute_artifact({}, {"x": 1})


def test_executor_is_pure_no_artifact_mutation() -> None:
    """Executor must not mutate the artifact dict."""

    artifact = {
        "kind": "lookup_table",
        "table": {"a": 1, "b": 2},
        "default": 0,
    }
    snapshot = {"kind": artifact["kind"], "table": dict(artifact["table"]),
                "default": artifact["default"]}
    for _ in range(3):
        execute_artifact(artifact, {"key": "a"})
    assert artifact["kind"] == snapshot["kind"]
    assert artifact["table"] == snapshot["table"]
    assert artifact["default"] == snapshot["default"]


def test_executor_is_deterministic_repeated_calls() -> None:
    artifact = {
        "kind": "linear_arithmetic",
        "coefficients": [3.14, -2.71],
        "intercept": 1.0,
        "input_columns": ["a", "b"],
    }
    inputs = {"a": 7.0, "b": 11.0}
    first = execute_artifact(artifact, inputs)
    for _ in range(5):
        assert execute_artifact(artifact, inputs) == first
