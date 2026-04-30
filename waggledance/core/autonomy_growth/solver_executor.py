# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Pure executors for the low-risk allowlisted family kinds.

Each executor takes the deterministic compiled artifact (a dict
produced by
``waggledance.core.solver_synthesis.deterministic_solver_compiler``)
and an input record, and returns the result. Executors are pure
functions; they perform no I/O, spawn no subprocess, and never mutate
the artifact.

The artifact dicts already enforce shape — they were produced by a
deterministic compiler that validated required keys. Executors here
trust that shape and fail fast (``ExecutorError``) if it is violated
at execution time, which would indicate either an unsupported family
or a corrupted artifact.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .low_risk_policy import LOW_RISK_FAMILY_KINDS


class ExecutorError(RuntimeError):
    """Raised when an executor cannot evaluate the artifact."""


class UnsupportedFamilyError(ExecutorError):
    """Family is outside the low-risk allowlist or has no executor."""


def _input_scalar(inputs: Mapping[str, Any], key: str = "x") -> float:
    if key not in inputs:
        raise ExecutorError(f"missing input key {key!r}")
    try:
        return float(inputs[key])
    except (TypeError, ValueError) as exc:
        raise ExecutorError(
            f"input {key!r}={inputs[key]!r} is not numeric"
        ) from exc


def _exec_scalar_unit_conversion(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> float:
    x = _input_scalar(inputs)
    if "factor" not in artifact:
        raise ExecutorError("scalar_unit_conversion: artifact missing 'factor'")
    factor = float(artifact["factor"])
    offset = float(artifact.get("offset", 0.0))
    return x * factor + offset


def _exec_lookup_table(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Any:
    if "key" not in inputs:
        raise ExecutorError("lookup_table: missing input key 'key'")
    table = artifact.get("table")
    if not isinstance(table, dict):
        raise ExecutorError("lookup_table: artifact has no 'table' dict")
    key = inputs["key"]
    # Spec keys are sorted by string repr at compile time; lookup matches
    # raw key first, then string-keyed form.
    if key in table:
        return table[key]
    str_key = str(key)
    if str_key in table:
        return table[str_key]
    return artifact.get("default")


_THRESHOLD_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _exec_threshold_rule(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Any:
    x = _input_scalar(inputs)
    for required in ("operator", "threshold", "true_label", "false_label"):
        if required not in artifact:
            raise ExecutorError(
                f"threshold_rule: artifact missing {required!r}"
            )
    op_name = artifact["operator"]
    op = _THRESHOLD_OPS.get(op_name)
    if op is None:
        raise ExecutorError(f"threshold_rule: unknown operator {op_name!r}")
    threshold = float(artifact["threshold"])
    return artifact["true_label"] if op(x, threshold) else artifact["false_label"]


def _exec_interval_bucket(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Any:
    x = _input_scalar(inputs)
    intervals = artifact.get("intervals", [])
    for iv in intervals:
        try:
            mn = float(iv["min"])
            mx = float(iv["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutorError(
                f"interval_bucket: malformed interval {iv!r}"
            ) from exc
        if mn <= x < mx:
            return iv.get("label")
    return artifact.get("out_of_range_label")


def _exec_linear_arithmetic(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> float:
    if "coefficients" not in artifact or "intercept" not in artifact:
        raise ExecutorError(
            "linear_arithmetic: artifact missing 'coefficients' or 'intercept'"
        )
    coeffs = [float(c) for c in artifact["coefficients"]]
    intercept = float(artifact["intercept"])
    cols = artifact.get("input_columns") or []
    if cols:
        if len(cols) != len(coeffs):
            raise ExecutorError(
                "linear_arithmetic: input_columns length mismatch"
            )
        try:
            x_vec = [float(inputs[c]) for c in cols]
        except KeyError as exc:
            raise ExecutorError(
                f"linear_arithmetic: missing input column {exc!s}"
            ) from exc
    else:
        # Fall back to ordered keys x_1..x_n
        try:
            x_vec = [float(inputs[f"x_{i + 1}"]) for i in range(len(coeffs))]
        except KeyError as exc:
            raise ExecutorError(
                f"linear_arithmetic: missing positional input {exc!s} "
                "(provide input_columns in the spec or x_1..x_n in the call)"
            ) from exc
    return sum(c * x for c, x in zip(coeffs, x_vec)) + intercept


def _exec_bounded_interpolation(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Any:
    x = _input_scalar(inputs)
    if "min_x" not in artifact or "max_x" not in artifact:
        raise ExecutorError(
            "bounded_interpolation: artifact missing 'min_x' or 'max_x'"
        )
    knots = list(artifact.get("knots") or [])
    if len(knots) < 2:
        raise ExecutorError(
            "bounded_interpolation: at least 2 knots required"
        )
    method = artifact.get("method", "linear")
    min_x = float(artifact["min_x"])
    max_x = float(artifact["max_x"])
    policy = artifact.get("out_of_range_policy", "clip")

    if x < min_x or x > max_x:
        if policy == "clip":
            x = max(min_x, min(max_x, x))
        elif policy == "null":
            return None
        elif policy == "raise":
            raise ExecutorError(f"bounded_interpolation: x={x} out of range")
        else:
            raise ExecutorError(
                f"bounded_interpolation: unknown out_of_range_policy {policy!r}"
            )

    # Knots are pre-sorted by x at compile time; do a linear-time scan.
    # This is bounded by len(knots), which stays small in practice.
    if method == "linear":
        for i in range(len(knots) - 1):
            x0 = float(knots[i]["x"])
            x1 = float(knots[i + 1]["x"])
            if x0 <= x <= x1:
                if x1 == x0:
                    return float(knots[i]["y"])
                y0 = float(knots[i]["y"])
                y1 = float(knots[i + 1]["y"])
                t = (x - x0) / (x1 - x0)
                return y0 + (y1 - y0) * t
        # If we got here, x equals exactly the last knot
        return float(knots[-1]["y"])
    raise ExecutorError(
        f"bounded_interpolation: method {method!r} not implemented in low-risk lane"
    )


_EXECUTORS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], Any]] = {
    "scalar_unit_conversion": _exec_scalar_unit_conversion,
    "lookup_table": _exec_lookup_table,
    "threshold_rule": _exec_threshold_rule,
    "interval_bucket_classifier": _exec_interval_bucket,
    "linear_arithmetic": _exec_linear_arithmetic,
    "bounded_interpolation": _exec_bounded_interpolation,
}


def supported_executor_kinds() -> tuple[str, ...]:
    """Return the family kinds for which we have a runtime executor."""

    return tuple(sorted(_EXECUTORS.keys()))


def execute_artifact(
    artifact: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Any:
    """Execute a compiled artifact against an input record.

    The artifact's ``kind`` field selects the executor. The family must
    be in the low-risk allowlist; otherwise
    :class:`UnsupportedFamilyError` is raised.
    """

    kind = artifact.get("kind") if isinstance(artifact, dict) else None
    if kind is None:
        raise ExecutorError("artifact missing 'kind'")
    if kind not in LOW_RISK_FAMILY_KINDS:
        raise UnsupportedFamilyError(
            f"family {kind!r} is not in the low-risk allowlist"
        )
    executor = _EXECUTORS.get(kind)
    if executor is None:
        raise UnsupportedFamilyError(
            f"no runtime executor for family {kind!r}"
        )
    return executor(artifact, inputs)
