# SPDX-License-Identifier: BUSL-1.1
"""Solver family registry — Phase 9 §U1.

Catalog of the 10 known solver families plus their required spec
keys. Every entry maps to a deterministic compiler.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import SOLVER_FAMILY_KINDS, SOLVER_SYNTHESIS_SCHEMA_VERSION


@dataclass(frozen=True)
class SolverFamily:
    schema_version: int
    family_id: str
    name: str
    kind: str
    required_spec_keys: tuple[str, ...]
    compiler_module: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SOLVER_FAMILY_KINDS:
            raise ValueError(
                f"unknown family kind: {self.kind!r}; "
                f"allowed: {SOLVER_FAMILY_KINDS}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "family_id": self.family_id,
            "name": self.name,
            "kind": self.kind,
            "required_spec_keys": list(self.required_spec_keys),
            "compiler_module": self.compiler_module,
            "description": self.description,
        }


# ── 10 default families ───────────────────────────────────────────

def default_families() -> tuple[SolverFamily, ...]:
    return (
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="scalar_unit_conversion",
            name="Scalar unit conversion",
            kind="scalar_unit_conversion",
            required_spec_keys=("from_unit", "to_unit", "factor"),
            compiler_module="deterministic_solver_compiler",
            description="y = x * factor (+ optional offset)",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="lookup_table",
            name="Lookup table",
            kind="lookup_table",
            required_spec_keys=("table",),
            compiler_module="deterministic_solver_compiler",
            description="Mapping key → value",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="threshold_rule",
            name="Threshold rule classifier",
            kind="threshold_rule",
            required_spec_keys=("threshold", "operator", "true_label",
                                 "false_label"),
            compiler_module="deterministic_solver_compiler",
            description="x op threshold → label",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="interval_bucket_classifier",
            name="Interval bucket classifier",
            kind="interval_bucket_classifier",
            required_spec_keys=("intervals",),
            compiler_module="deterministic_solver_compiler",
            description="x falls into bucket [a, b) → label",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="linear_arithmetic",
            name="Linear arithmetic",
            kind="linear_arithmetic",
            required_spec_keys=("coefficients", "intercept"),
            compiler_module="deterministic_solver_compiler",
            description="y = sum(c_i * x_i) + b",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="weighted_aggregation",
            name="Weighted aggregation",
            kind="weighted_aggregation",
            required_spec_keys=("weights",),
            compiler_module="deterministic_solver_compiler",
            description="y = sum(w_i * x_i) / sum(w_i)",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="temporal_window_rule",
            name="Temporal window rule",
            kind="temporal_window_rule",
            required_spec_keys=("window_seconds", "aggregator",
                                 "threshold", "operator"),
            compiler_module="deterministic_solver_compiler",
            description="aggregator(over window) op threshold",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="bounded_interpolation",
            name="Bounded interpolation/extrapolation",
            kind="bounded_interpolation",
            required_spec_keys=("knots", "method", "min_x", "max_x"),
            compiler_module="deterministic_solver_compiler",
            description="Piecewise linear/cubic with bounds",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="structured_field_extractor",
            name="Structured field extractor",
            kind="structured_field_extractor",
            required_spec_keys=("source_field", "extract_pattern"),
            compiler_module="deterministic_solver_compiler",
            description="regex / json-path extraction",
        ),
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="deterministic_composition_wrapper",
            name="Deterministic composition wrapper",
            kind="deterministic_composition_wrapper",
            required_spec_keys=("steps",),
            compiler_module="deterministic_solver_compiler",
            description="Pipeline of registered solver_specs",
        ),
    )


@dataclass
class SolverFamilyRegistry:
    families: dict[str, SolverFamily] = field(default_factory=dict)

    def register_defaults(self) -> "SolverFamilyRegistry":
        for fam in default_families():
            self.families[fam.kind] = fam
        return self

    def register(self, fam: SolverFamily) -> "SolverFamilyRegistry":
        self.families[fam.kind] = fam
        return self

    def get(self, kind: str) -> SolverFamily | None:
        return self.families.get(kind)

    def list_kinds(self) -> list[str]:
        return sorted(self.families.keys())

    def to_dict(self) -> dict:
        return {
            "schema_version": SOLVER_SYNTHESIS_SCHEMA_VERSION,
            "families": {k: f.to_dict()
                          for k, f in sorted(self.families.items())},
        }
