# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Family-keyed reference oracles for the low-risk autogrowth lane.

Oracles are independent Python implementations of each family's
mathematical contract. They run *outside* the deterministic compiler
and the executor, so a 1.0 agreement rate during shadow evaluation is
real evidence that the candidate behaves correctly under the family's
intended semantics.

These oracles are the lane's authoritative reference for the six
allowlisted families. They are pure functions of (inputs, artifact);
they perform no I/O.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping


def _scalar_unit_conversion_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _lookup_table_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    table = artifact["table"]
    key = inputs["key"]
    if key in table:
        return table[key]
    sk = str(key)
    if sk in table:
        return table[sk]
    return artifact.get("default")


def _threshold_rule_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    op = artifact["operator"]
    x = float(inputs["x"])
    th = float(artifact["threshold"])
    fired = {
        ">":  x > th,  ">=": x >= th,
        "<":  x < th,  "<=": x <= th,
        "==": x == th, "!=": x != th,
    }[op]
    return artifact["true_label"] if fired else artifact["false_label"]


def _interval_bucket_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    x = float(inputs["x"])
    for iv in artifact.get("intervals", []):
        mn = float(iv["min"])
        mx = float(iv["max"])
        if mn <= x < mx:
            return iv.get("label")
    return artifact.get("out_of_range_label")


def _linear_arithmetic_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> float:
    coeffs = [float(c) for c in artifact["coefficients"]]
    intercept = float(artifact["intercept"])
    cols = artifact.get("input_columns") or []
    if cols:
        x_vec = [float(inputs[c]) for c in cols]
    else:
        x_vec = [float(inputs[f"x_{i + 1}"]) for i in range(len(coeffs))]
    return sum(c * x for c, x in zip(coeffs, x_vec)) + intercept


def _bounded_interpolation_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    x = float(inputs["x"])
    knots = list(artifact.get("knots") or [])
    method = artifact.get("method", "linear")
    min_x = float(artifact["min_x"])
    max_x = float(artifact["max_x"])
    policy = artifact.get("out_of_range_policy", "clip")
    if x < min_x or x > max_x:
        if policy == "clip":
            x = max(min_x, min(max_x, x))
        elif policy == "null":
            return None
        else:
            raise ValueError(f"oracle: x={x} outside [{min_x}, {max_x}]")
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
        return float(knots[-1]["y"])
    raise ValueError(f"oracle: unsupported method {method!r}")


OracleFn = Callable[[Mapping[str, Any], Mapping[str, Any]], Any]


FAMILY_ORACLES: dict[str, OracleFn] = {
    "scalar_unit_conversion": _scalar_unit_conversion_oracle,
    "lookup_table": _lookup_table_oracle,
    "threshold_rule": _threshold_rule_oracle,
    "interval_bucket_classifier": _interval_bucket_oracle,
    "linear_arithmetic": _linear_arithmetic_oracle,
    "bounded_interpolation": _bounded_interpolation_oracle,
}


def get_oracle(family_kind: str) -> OracleFn:
    """Return the registered oracle for a low-risk family.

    Raises :class:`KeyError` when no oracle is registered. The
    autogrowth scheduler treats this as a "rejected:no_oracle" outcome.
    """

    return FAMILY_ORACLES[family_kind]


__all__ = ["FAMILY_ORACLES", "OracleFn", "get_oracle"]
