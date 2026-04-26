"""Deterministic solver compiler — Phase 9 §U1.

Compiles SolverSpec → solver artifact (dict) byte-identically. No
free-form code generation. Every known family has a deterministic
compiler.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import SOLVER_FAMILY_KINDS, SOLVER_SYNTHESIS_SCHEMA_VERSION
from .declarative_solver_spec import SolverSpec


@dataclass(frozen=True)
class CompiledSolver:
    schema_version: int
    artifact_id: str
    spec_id: str
    family_kind: str
    solver_name: str
    cell_id: str
    artifact: dict        # deterministic compiled form
    canonical_json: str   # for byte-identical determinism tests

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "spec_id": self.spec_id,
            "family_kind": self.family_kind,
            "solver_name": self.solver_name,
            "cell_id": self.cell_id,
            "artifact": dict(self.artifact),
        }


def _compile_scalar_unit_conversion(spec: dict) -> dict:
    return {
        "kind": "scalar_unit_conversion",
        "from_unit": spec["from_unit"],
        "to_unit": spec["to_unit"],
        "factor": float(spec["factor"]),
        "offset": float(spec.get("offset", 0.0)),
        "formula": (
            f"y = x * {float(spec['factor'])} "
            f"+ {float(spec.get('offset', 0.0))}"
        ),
    }


def _compile_lookup_table(spec: dict) -> dict:
    table = spec["table"]
    if not isinstance(table, dict):
        raise ValueError("lookup_table: 'table' must be a dict")
    return {
        "kind": "lookup_table",
        "table": dict(sorted(table.items(), key=lambda x: str(x[0]))),
        "default": spec.get("default"),
        "size": len(table),
    }


def _compile_threshold_rule(spec: dict) -> dict:
    op = spec["operator"]
    if op not in (">", ">=", "<", "<=", "==", "!="):
        raise ValueError(f"threshold_rule: unknown operator {op!r}")
    return {
        "kind": "threshold_rule",
        "threshold": float(spec["threshold"]),
        "operator": op,
        "true_label": spec["true_label"],
        "false_label": spec["false_label"],
    }


def _compile_interval_bucket(spec: dict) -> dict:
    intervals = list(spec["intervals"])
    intervals.sort(key=lambda i: float(i["min"]))
    for i in intervals:
        if float(i["min"]) > float(i["max"]):
            raise ValueError(
                f"interval min > max: {i!r}"
            )
    return {
        "kind": "interval_bucket_classifier",
        "intervals": intervals,
        "out_of_range_label": spec.get("out_of_range_label"),
    }


def _compile_linear_arithmetic(spec: dict) -> dict:
    return {
        "kind": "linear_arithmetic",
        "coefficients": [float(c) for c in spec["coefficients"]],
        "intercept": float(spec["intercept"]),
        "input_columns": list(spec.get("input_columns") or ()),
    }


def _compile_weighted_aggregation(spec: dict) -> dict:
    weights = spec["weights"]
    if isinstance(weights, dict):
        weights = dict(sorted(weights.items()))
    return {
        "kind": "weighted_aggregation",
        "weights": weights,
        "missing_policy": spec.get("missing_policy", "drop"),
    }


def _compile_temporal_window(spec: dict) -> dict:
    agg = spec["aggregator"]
    if agg not in ("mean", "max", "min", "sum", "median",
                    "count_above", "count_below"):
        raise ValueError(f"temporal_window: unknown aggregator {agg!r}")
    return {
        "kind": "temporal_window_rule",
        "window_seconds": int(spec["window_seconds"]),
        "aggregator": agg,
        "threshold": float(spec["threshold"]),
        "operator": spec["operator"],
    }


def _compile_bounded_interpolation(spec: dict) -> dict:
    knots = sorted(spec["knots"], key=lambda k: float(k.get("x", 0)))
    method = spec.get("method", "linear")
    if method not in ("linear", "cubic"):
        raise ValueError(f"bounded_interpolation: unknown method {method!r}")
    return {
        "kind": "bounded_interpolation",
        "knots": knots,
        "method": method,
        "min_x": float(spec["min_x"]),
        "max_x": float(spec["max_x"]),
        "out_of_range_policy": spec.get("out_of_range_policy", "clip"),
    }


def _compile_field_extractor(spec: dict) -> dict:
    return {
        "kind": "structured_field_extractor",
        "source_field": spec["source_field"],
        "extract_pattern": spec["extract_pattern"],
        "extract_kind": spec.get("extract_kind", "regex"),
    }


def _compile_composition_wrapper(spec: dict) -> dict:
    steps = list(spec["steps"])
    if not steps:
        raise ValueError(
            "deterministic_composition_wrapper: steps must be non-empty"
        )
    return {
        "kind": "deterministic_composition_wrapper",
        "steps": steps,
        "step_count": len(steps),
    }


_COMPILERS = {
    "scalar_unit_conversion": _compile_scalar_unit_conversion,
    "lookup_table": _compile_lookup_table,
    "threshold_rule": _compile_threshold_rule,
    "interval_bucket_classifier": _compile_interval_bucket,
    "linear_arithmetic": _compile_linear_arithmetic,
    "weighted_aggregation": _compile_weighted_aggregation,
    "temporal_window_rule": _compile_temporal_window,
    "bounded_interpolation": _compile_bounded_interpolation,
    "structured_field_extractor": _compile_field_extractor,
    "deterministic_composition_wrapper": _compile_composition_wrapper,
}


def compile_spec(spec: SolverSpec) -> CompiledSolver:
    """Run the family-specific compiler. Output is byte-identical
    given identical input."""
    compiler = _COMPILERS.get(spec.family_kind)
    if compiler is None:
        raise ValueError(
            f"no compiler for family_kind {spec.family_kind!r}"
        )
    artifact = compiler(spec.spec)
    canonical = json.dumps(
        {"spec_id": spec.spec_id,
         "solver_name": spec.solver_name,
         "cell_id": spec.cell_id,
         "artifact": artifact},
        sort_keys=True, separators=(",", ":"),
    )
    aid = "art_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return CompiledSolver(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        artifact_id=aid,
        spec_id=spec.spec_id,
        family_kind=spec.family_kind,
        solver_name=spec.solver_name,
        cell_id=spec.cell_id,
        artifact=artifact,
        canonical_json=canonical,
    )


def all_compiler_kinds() -> list[str]:
    return sorted(_COMPILERS.keys())
