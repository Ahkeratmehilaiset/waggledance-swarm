# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.solver_family_registry.

The family registry is the static source of truth for which solver
families exist, what spec keys they require, and which compiler runs
them. The deterministic_solver_compiler tests already pin per-family
compile output; this test pins the registry → compiler mapping itself.

A regression in the registry can let a candidate pass through
synthesis with the wrong required_spec_keys (so a malformed spec
silently survives to runtime) or point to a non-existent compiler.
Direct test coverage on this file was zero before this PR.

Pinned invariants:

- `SolverFamily.__post_init__` rejects unknown family kind.
- `default_families()` returns exactly 10 families, one per
  SOLVER_FAMILY_KINDS member, with non-empty required_spec_keys.
- Every default family points at the deterministic_solver_compiler
  module — no LLM compilers in the default set.
- `SolverFamilyRegistry.register_defaults()` populates by `kind`
  with all 10 families.
- `register` upserts by kind.
- `to_dict` produces a sorted, JSON-serialisable shape.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis import (
    SOLVER_FAMILY_KINDS,
    SOLVER_SYNTHESIS_SCHEMA_VERSION,
)
from waggledance.core.solver_synthesis.solver_family_registry import (
    SolverFamily,
    SolverFamilyRegistry,
    default_families,
)


# --- SolverFamily dataclass ----------------------------------------

def test_solver_family_post_init_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown family kind"):
        SolverFamily(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            family_id="bogus",
            name="Bogus family",
            kind="not_a_family",
            required_spec_keys=("x",),
            compiler_module="deterministic_solver_compiler",
        )


def test_solver_family_to_dict_round_trips_fields():
    fam = SolverFamily(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        family_id="threshold_rule",
        name="Threshold rule classifier",
        kind="threshold_rule",
        required_spec_keys=("threshold", "operator"),
        compiler_module="deterministic_solver_compiler",
        description="x op threshold → label",
    )
    d = fam.to_dict()
    assert d["family_id"] == "threshold_rule"
    assert d["kind"] == "threshold_rule"
    assert d["required_spec_keys"] == ["threshold", "operator"]
    assert d["compiler_module"] == "deterministic_solver_compiler"
    assert d["description"] == "x op threshold → label"


# --- default_families -----------------------------------------------

def test_default_families_returns_exactly_one_per_solver_family_kind():
    fams = default_families()
    assert len(fams) == len(SOLVER_FAMILY_KINDS)
    kinds = {f.kind for f in fams}
    assert kinds == set(SOLVER_FAMILY_KINDS)


def test_default_families_all_have_non_empty_required_spec_keys():
    """If required_spec_keys is empty for any family, malformed specs
    pass straight through synthesis with no validation. Every family
    must declare at least one required key."""
    for fam in default_families():
        assert fam.required_spec_keys, f"{fam.kind} has no required_spec_keys"


def test_default_families_all_point_at_deterministic_compiler():
    """No LLM compilers in the default set. Every family routes to
    `deterministic_solver_compiler` per Phase 9 §U1."""
    for fam in default_families():
        assert fam.compiler_module == "deterministic_solver_compiler", (
            f"{fam.kind} routes to non-deterministic compiler "
            f"{fam.compiler_module!r}"
        )


def test_default_families_all_have_schema_version_set():
    for fam in default_families():
        assert fam.schema_version == SOLVER_SYNTHESIS_SCHEMA_VERSION


def test_default_families_all_have_family_id_matching_kind():
    """Convention: family_id and kind are the same string in the
    default set. A future addition might break this, in which case
    the registry's `get()` would still work but downstream key-by-id
    consumers may not. Pin the convention so divergence shows up here."""
    for fam in default_families():
        assert fam.family_id == fam.kind, (
            f"family_id {fam.family_id!r} != kind {fam.kind!r}"
        )


def test_default_families_required_spec_keys_match_compiler_expectations():
    """Quick smoke check that required_spec_keys roughly match what
    the deterministic compiler reads (sampled, not exhaustive)."""
    by_kind = {f.kind: f for f in default_families()}
    assert "factor" in by_kind["scalar_unit_conversion"].required_spec_keys
    assert "table" in by_kind["lookup_table"].required_spec_keys
    assert "threshold" in by_kind["threshold_rule"].required_spec_keys
    assert "intervals" in by_kind["interval_bucket_classifier"].required_spec_keys
    assert "coefficients" in by_kind["linear_arithmetic"].required_spec_keys
    assert "weights" in by_kind["weighted_aggregation"].required_spec_keys
    assert "knots" in by_kind["bounded_interpolation"].required_spec_keys
    assert "steps" in by_kind["deterministic_composition_wrapper"].required_spec_keys


# --- SolverFamilyRegistry ------------------------------------------

def test_registry_register_defaults_populates_all_kinds():
    reg = SolverFamilyRegistry().register_defaults()
    kinds = reg.list_kinds()
    assert set(kinds) == set(SOLVER_FAMILY_KINDS)


def test_registry_get_returns_family_by_kind():
    reg = SolverFamilyRegistry().register_defaults()
    fam = reg.get("threshold_rule")
    assert fam is not None
    assert fam.kind == "threshold_rule"
    assert "threshold" in fam.required_spec_keys


def test_registry_get_returns_none_for_unknown_kind():
    reg = SolverFamilyRegistry().register_defaults()
    assert reg.get("not_a_real_kind") is None


def test_registry_register_upserts_by_kind():
    reg = SolverFamilyRegistry().register_defaults()
    custom = SolverFamily(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        family_id="threshold_rule_v2",
        name="Threshold rule v2",
        kind="threshold_rule",  # same kind ⇒ overwrite
        required_spec_keys=("threshold", "operator", "tolerance"),
        compiler_module="deterministic_solver_compiler",
    )
    reg.register(custom)
    fam = reg.get("threshold_rule")
    assert fam.family_id == "threshold_rule_v2"
    assert "tolerance" in fam.required_spec_keys


def test_registry_to_dict_is_sorted_by_kind():
    reg = SolverFamilyRegistry().register_defaults()
    d = reg.to_dict()
    keys = list(d["families"].keys())
    assert keys == sorted(keys)
    assert d["schema_version"] == SOLVER_SYNTHESIS_SCHEMA_VERSION


def test_registry_list_kinds_is_sorted():
    reg = SolverFamilyRegistry().register_defaults()
    kinds = reg.list_kinds()
    assert kinds == sorted(kinds)


def test_registry_empty_by_default_before_register_defaults():
    reg = SolverFamilyRegistry()
    assert reg.list_kinds() == []
    assert reg.get("threshold_rule") is None
