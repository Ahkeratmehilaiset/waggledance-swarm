# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.declarative_solver_spec.

A SolverSpec is the declarative description of a concrete solver
instance. The spec passes through validation (family registry lookup,
required spec keys present, name pattern, cell allowlist) before the
deterministic compiler runs. A regression in this validation can let
an unauthorized cell or malformed spec reach the compiler.

Direct test coverage on the module's `make_spec` validator and
`compute_spec_id` determinism was zero before this PR.

Pinned invariants:

- `_SOLVER_NAME_PATTERN`: enforced via __post_init__. solver_name
  must be `^[a-z][a-z0-9_]{2,63}$`.
- `__post_init__` rejects unknown family_kind and unknown cell_id.
- `compute_spec_id` is deterministic on identical input; differs on
  changed input; truncated to 12 chars.
- `make_spec` looks up the family in the registry; raises
  SpecValidationError when the family is missing or required spec
  keys are missing.
- The returned SolverSpec carries the computed spec_id and copies the
  spec dict (so subsequent mutations of the input do NOT mutate the
  stored spec).
- `to_dict` emits the provenance sub-dict with the four provenance
  fields.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis import (
    SOLVER_FAMILY_KINDS,
    SOLVER_SYNTHESIS_SCHEMA_VERSION,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import (
    SolverSpec,
    SpecValidationError,
    compute_spec_id,
    make_spec,
)
from waggledance.core.solver_synthesis.solver_family_registry import (
    SolverFamilyRegistry,
)


# --- helpers --------------------------------------------------------

def _registry() -> SolverFamilyRegistry:
    return SolverFamilyRegistry().register_defaults()


def _good_kwargs() -> dict:
    return {
        "family_kind": "scalar_unit_conversion",
        "solver_name": "celsius_to_kelvin",
        "cell_id": "general",
        "spec": {"from_unit": "C", "to_unit": "K", "factor": 1.0,
                 "offset": 273.15},
        "source": "manual",
        "source_kind": "manual",
        "registry": _registry(),
    }


# --- SolverSpec __post_init__ rejection --------------------------

def test_solver_spec_rejects_unknown_family_kind():
    with pytest.raises(ValueError, match="unknown family_kind"):
        SolverSpec(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            spec_id="x", family_kind="not_a_family",
            solver_name="abc_def",
            cell_id="general", spec={}, source="s", source_kind="manual",
        )


def test_solver_spec_rejects_unknown_cell_id():
    with pytest.raises(ValueError, match="unknown cell_id"):
        SolverSpec(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            spec_id="x", family_kind="scalar_unit_conversion",
            solver_name="abc_def",
            cell_id="not_a_cell", spec={}, source="s", source_kind="manual",
        )


@pytest.mark.parametrize("bad_name", [
    "Abc",          # uppercase forbidden
    "ab",           # too short (min 3 chars total → pattern needs 3-64)
    "9abc",         # leading digit forbidden
    "ab-cd",        # hyphen forbidden
    "ab.cd",        # dot forbidden
    "",             # empty
])
def test_solver_spec_rejects_solver_name_not_matching_pattern(bad_name):
    with pytest.raises(ValueError, match="solver_name must match"):
        SolverSpec(
            schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
            spec_id="x", family_kind="scalar_unit_conversion",
            solver_name=bad_name,
            cell_id="general", spec={}, source="s", source_kind="manual",
        )


@pytest.mark.parametrize("good_name", [
    "abc",                  # exactly minimum length 3
    "abc_def",
    "celsius_to_kelvin",
    "a1b2c3",
    "a" * 64,               # exactly maximum length 64
])
def test_solver_spec_accepts_well_formed_solver_names(good_name):
    spec = SolverSpec(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        spec_id="x", family_kind="scalar_unit_conversion",
        solver_name=good_name,
        cell_id="general", spec={}, source="s", source_kind="manual",
    )
    assert spec.solver_name == good_name


def test_solver_spec_to_dict_emits_provenance_block():
    spec = SolverSpec(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        spec_id="abc", family_kind="scalar_unit_conversion",
        solver_name="celsius_to_kelvin",
        cell_id="general",
        spec={"factor": 1.0},
        source="manual_audit",
        source_kind="manual",
        branch_name="main",
        base_commit_hash="deadbeef",
        pinned_input_manifest_sha256="f" * 64,
    )
    d = spec.to_dict()
    assert d["spec_id"] == "abc"
    assert d["spec"] == {"factor": 1.0}
    assert d["provenance"] == {
        "source": "manual_audit",
        "source_kind": "manual",
        "branch_name": "main",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
    }


# --- compute_spec_id determinism ---------------------------------

def test_compute_spec_id_deterministic_on_identical_input():
    a = compute_spec_id(family_kind="scalar_unit_conversion",
                        solver_name="celsius_to_kelvin",
                        cell_id="general",
                        spec={"factor": 1.0, "offset": 273.15})
    b = compute_spec_id(family_kind="scalar_unit_conversion",
                        solver_name="celsius_to_kelvin",
                        cell_id="general",
                        spec={"factor": 1.0, "offset": 273.15})
    assert a == b
    assert len(a) == 12


def test_compute_spec_id_changes_on_input_change():
    a = compute_spec_id(family_kind="scalar_unit_conversion",
                        solver_name="celsius_to_kelvin",
                        cell_id="general",
                        spec={"factor": 1.0})
    b = compute_spec_id(family_kind="scalar_unit_conversion",
                        solver_name="celsius_to_fahrenheit",
                        cell_id="general",
                        spec={"factor": 1.8})
    assert a != b


def test_compute_spec_id_handles_unhashable_via_default_str():
    """default=str in the JSON serialiser lets compute_spec_id work
    on specs that contain non-JSON-native values (e.g. tuples)."""
    sid = compute_spec_id(
        family_kind="scalar_unit_conversion",
        solver_name="abc_def",
        cell_id="general",
        spec={"factor": 1.0, "metadata": {"complex": (1, 2)}},
    )
    assert len(sid) == 12


# --- make_spec validator ------------------------------------------

def test_make_spec_returns_solver_spec_with_computed_id():
    spec = make_spec(**_good_kwargs())
    assert isinstance(spec, SolverSpec)
    expected_id = compute_spec_id(
        family_kind="scalar_unit_conversion",
        solver_name="celsius_to_kelvin",
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K", "factor": 1.0,
              "offset": 273.15},
    )
    assert spec.spec_id == expected_id


def test_make_spec_raises_when_family_not_in_registry():
    """An empty registry has no families; lookup must fail explicitly."""
    kwargs = _good_kwargs()
    kwargs["registry"] = SolverFamilyRegistry()  # empty
    with pytest.raises(SpecValidationError, match="not in registry"):
        make_spec(**kwargs)


def test_make_spec_raises_when_required_spec_keys_missing():
    kwargs = _good_kwargs()
    kwargs["spec"] = {"to_unit": "K"}  # missing from_unit + factor
    with pytest.raises(SpecValidationError, match="missing required keys"):
        make_spec(**kwargs)


def test_make_spec_copies_spec_dict_so_post_mutations_do_not_leak():
    """make_spec stores `dict(spec)`, not the input by reference. A
    later mutation of the caller's dict must NOT change the stored
    SolverSpec.spec."""
    spec_in = {"from_unit": "C", "to_unit": "K", "factor": 1.0,
               "offset": 273.15}
    kwargs = _good_kwargs()
    kwargs["spec"] = spec_in
    out = make_spec(**kwargs)
    spec_in["factor"] = 999.0
    assert out.spec["factor"] == 1.0


def test_make_spec_carries_provenance_through():
    kwargs = _good_kwargs()
    kwargs["branch_name"] = "test/branch"
    kwargs["base_commit_hash"] = "deadbeef"
    kwargs["pinned_input_manifest_sha256"] = "f" * 64
    out = make_spec(**kwargs)
    assert out.branch_name == "test/branch"
    assert out.base_commit_hash == "deadbeef"
    assert out.pinned_input_manifest_sha256 == "f" * 64
