"""Tests for canonical solver hash (deduplication of Claude proposals).

Covers both the legacy `canonical_hash` and the Phase 8 strict
`solver_hash` per x.txt Phase 3 requirements (changed output unit,
tags, and invariants must all change the hash; reordered keys must
not).
"""
from __future__ import annotations

from waggledance.core.learning.solver_hash import (
    canonical_hash, short_hash, HashRegistry,
    solver_hash, canonicalize_solver_spec,
    normalize_formula, normalize_variables,
)


def _proposal(formulas, variables=None, conditions=None, model_id="test",
              description="irrelevant"):
    return {
        "model_id": model_id,
        "model_name": "whatever",
        "description": description,
        "formulas": formulas,
        "variables": variables or {},
        "conditions": conditions,
    }


# ── Identity ──────────────────────────────────────────────────────

def test_identical_proposals_have_identical_hashes():
    p1 = _proposal([{"name": "f", "formula": "a + b"}], {"a": {"unit": "m"}, "b": {"unit": "m"}})
    p2 = _proposal([{"name": "f", "formula": "a + b"}], {"a": {"unit": "m"}, "b": {"unit": "m"}})
    assert canonical_hash(p1) == canonical_hash(p2)


def test_hash_is_64_hex_chars():
    h = canonical_hash(_proposal([{"name": "f", "formula": "x"}]))
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ── Cosmetic insensitivity ────────────────────────────────────────

def test_model_id_does_not_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}], model_id="one")
    p2 = _proposal([{"name": "f", "formula": "a"}], model_id="two")
    assert canonical_hash(p1) == canonical_hash(p2)


def test_description_does_not_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}], description="A")
    p2 = _proposal([{"name": "f", "formula": "a"}], description="B")
    assert canonical_hash(p1) == canonical_hash(p2)


def test_whitespace_in_formula_does_not_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a+b"}])
    p2 = _proposal([{"name": "f", "formula": "  a + b  "}])
    p3 = _proposal([{"name": "f", "formula": "a\t+\nb"}])
    assert canonical_hash(p1) == canonical_hash(p2) == canonical_hash(p3)


def test_formula_order_does_not_affect_hash():
    p1 = _proposal([
        {"name": "f1", "formula": "a"},
        {"name": "f2", "formula": "b"},
    ])
    p2 = _proposal([
        {"name": "f2", "formula": "b"},
        {"name": "f1", "formula": "a"},
    ])
    assert canonical_hash(p1) == canonical_hash(p2)


def test_variable_order_does_not_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}],
                    {"a": {"unit": "m"}, "b": {"unit": "s"}})
    p2 = _proposal([{"name": "f", "formula": "a"}],
                    {"b": {"unit": "s"}, "a": {"unit": "m"}})
    assert canonical_hash(p1) == canonical_hash(p2)


# ── Structural sensitivity ────────────────────────────────────────

def test_different_formula_changes_hash():
    p1 = _proposal([{"name": "f", "formula": "a + b"}])
    p2 = _proposal([{"name": "f", "formula": "a - b"}])
    assert canonical_hash(p1) != canonical_hash(p2)


def test_different_formula_name_changes_hash():
    p1 = _proposal([{"name": "f1", "formula": "a"}])
    p2 = _proposal([{"name": "f2", "formula": "a"}])
    assert canonical_hash(p1) != canonical_hash(p2)


def test_different_variable_unit_changes_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}], {"a": {"unit": "m"}})
    p2 = _proposal([{"name": "f", "formula": "a"}], {"a": {"unit": "s"}})
    assert canonical_hash(p1) != canonical_hash(p2)


def test_conditions_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}])
    p2 = _proposal([{"name": "f", "formula": "a"}], conditions=["a > 0"])
    assert canonical_hash(p1) != canonical_hash(p2)


def test_condition_order_does_not_affect_hash():
    p1 = _proposal([{"name": "f", "formula": "a"}], conditions=["a > 0", "b < 1"])
    p2 = _proposal([{"name": "f", "formula": "a"}], conditions=["b < 1", "a > 0"])
    assert canonical_hash(p1) == canonical_hash(p2)


# ── short_hash ────────────────────────────────────────────────────

def test_short_hash_is_prefix_of_canonical():
    p = _proposal([{"name": "f", "formula": "a"}])
    assert canonical_hash(p).startswith(short_hash(p))
    assert len(short_hash(p)) == 12


# ── HashRegistry ──────────────────────────────────────────────────

def test_registry_detects_duplicates():
    reg = HashRegistry()
    p = _proposal([{"name": "f", "formula": "a"}])
    h = canonical_hash(p)
    assert not reg.seen(h)
    reg.add(h)
    assert reg.seen(h)
    assert h in reg
    assert len(reg) == 1


def test_registry_from_existing_axioms(tmp_path):
    """Pre-populating from an axioms dir should ingest all valid YAML files."""
    import yaml
    axioms = tmp_path / "axioms" / "domain"
    axioms.mkdir(parents=True)
    (axioms / "a.yaml").write_text(yaml.safe_dump(_proposal(
        [{"name": "honey", "formula": "colony * nectar"}])), encoding="utf-8")
    (axioms / "b.yaml").write_text(yaml.safe_dump(_proposal(
        [{"name": "thermal", "formula": "heat_loss * time"}])), encoding="utf-8")

    reg = HashRegistry.from_axioms_dir(tmp_path / "axioms")
    assert len(reg) == 2


def test_registry_from_axioms_dir_skips_unparseable(tmp_path):
    axioms = tmp_path / "axioms"
    axioms.mkdir()
    (axioms / "good.yaml").write_text(
        "model_id: good\nformulas:\n  - name: x\n    formula: a\n",
        encoding="utf-8",
    )
    (axioms / "broken.yaml").write_text(": : garbage\n:\n:::", encoding="utf-8")
    reg = HashRegistry.from_axioms_dir(axioms)
    # good.yaml should be in; broken.yaml should be silently skipped
    assert len(reg) == 1


# ── Real-world shape ──────────────────────────────────────────────

def test_realistic_proposal_hashes():
    """Honey-yield and heating-cost style proposals get distinct hashes."""
    honey = _proposal(
        [{"name": "daily_foragers", "formula": "colony_strength * forager_ratio"},
         {"name": "daily_honey_kg", "formula": "daily_foragers * nectar_load_mg * 0.0001"}],
        {"colony_strength": {"unit": "count"}, "forager_ratio": {"unit": "ratio"},
         "nectar_load_mg": {"unit": "mg"}},
    )
    heating = _proposal(
        [{"name": "heat_loss_rate", "formula": "area * (T_indoor - T_outdoor) / R_value"},
         {"name": "daily_cost", "formula": "heat_loss_rate * 24 / 1000 * kwh_price"}],
        {"area": {"unit": "m2"}, "T_indoor": {"unit": "C"}, "T_outdoor": {"unit": "C"},
         "R_value": {"unit": "m2K/W"}, "kwh_price": {"unit": "eur/kWh"}},
    )
    assert canonical_hash(honey) != canonical_hash(heating)
    # Self-consistency
    assert canonical_hash(honey) == canonical_hash(honey)


# ── Phase 8 strict solver_hash ────────────────────────────────────

def _strict_spec(formulas, variables=None, conditions=None,
                 outputs=None, validation=None, tags=None,
                 cell_id=None, domain=None, description="x"):
    spec = {
        "model_id": "t",
        "model_name": "whatever",
        "description": description,
        "formulas": formulas,
        "variables": variables or {},
    }
    if conditions is not None:
        spec["conditions"] = conditions
    if outputs is not None:
        spec["solver_output_schema"] = {"primary_value": outputs}
    if validation is not None:
        spec["validation"] = validation
    if tags is not None:
        spec["tags"] = tags
    if cell_id is not None:
        spec["cell_id"] = cell_id
    if domain is not None:
        spec["domain"] = domain
    return spec


def test_solver_hash_is_64_hex():
    h = solver_hash(_strict_spec([{"name": "f", "formula": "a"}]))
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_solver_hash_identical_for_reordered_yaml_keys():
    """x.txt Phase 3: same solver with reordered keys hashes the same."""
    a = {
        "model_id": "m",
        "description": "d",
        "formulas": [{"name": "f", "formula": "a + b"}],
        "variables": {"a": {"unit": "m"}, "b": {"unit": "m"}},
        "solver_output_schema": {"primary_value": {"name": "out", "unit": "m"}},
        "tags": ["thermal", "indoor"],
        "validation": [{"check": "a > 0"}],
    }
    b = {
        "tags": ["indoor", "thermal"],
        "validation": [{"check": "a > 0"}],
        "solver_output_schema": {"primary_value": {"unit": "m", "name": "out"}},
        "variables": {"b": {"unit": "m"}, "a": {"unit": "m"}},
        "formulas": [{"formula": "a + b", "name": "f"}],
        "description": "d",
        "model_id": "m",
    }
    assert solver_hash(a) == solver_hash(b)


def test_solver_hash_changed_formula_differs():
    """x.txt Phase 3: changed formula hashes differently."""
    a = _strict_spec([{"name": "f", "formula": "a + b"}])
    b = _strict_spec([{"name": "f", "formula": "a - b"}])
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_changed_output_unit_differs():
    """x.txt Phase 3: changed output unit hashes differently."""
    a = _strict_spec(
        [{"name": "f", "formula": "a"}],
        outputs={"name": "out", "unit": "m"},
    )
    b = _strict_spec(
        [{"name": "f", "formula": "a"}],
        outputs={"name": "out", "unit": "cm"},
    )
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_formula_output_unit_differs():
    """Changing a per-formula output_unit must also move the hash."""
    a = _strict_spec([{"name": "f", "formula": "a", "output_unit": "W"}])
    b = _strict_spec([{"name": "f", "formula": "a", "output_unit": "kW"}])
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_changed_tag_differs():
    a = _strict_spec([{"name": "f", "formula": "a"}], tags=["thermal"])
    b = _strict_spec([{"name": "f", "formula": "a"}], tags=["electrical"])
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_cell_id_contributes():
    a = _strict_spec([{"name": "f", "formula": "a"}], cell_id="thermal")
    b = _strict_spec([{"name": "f", "formula": "a"}], cell_id="energy")
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_changed_invariant_differs():
    """x.txt Phase 3: invariants included in the hash."""
    a = _strict_spec([{"name": "f", "formula": "a"}],
                     validation=[{"check": "a > 0"}])
    b = _strict_spec([{"name": "f", "formula": "a"}],
                     validation=[{"check": "a >= 0"}])
    assert solver_hash(a) != solver_hash(b)


def test_solver_hash_invariant_order_does_not_matter():
    a = _strict_spec([{"name": "f", "formula": "a"}],
                     validation=[{"check": "a > 0"}, {"check": "b < 1"}])
    b = _strict_spec([{"name": "f", "formula": "a"}],
                     validation=[{"check": "b < 1"}, {"check": "a > 0"}])
    assert solver_hash(a) == solver_hash(b)


def test_canonicalize_solver_spec_is_json_serializable():
    import json
    spec = _strict_spec(
        [{"name": "f", "formula": "a"}],
        variables={"a": {"unit": "m"}},
        outputs={"name": "out", "unit": "m"},
        validation=[{"check": "a > 0"}],
        tags=["thermal"],
    )
    canon = canonicalize_solver_spec(spec)
    # Must round-trip through JSON — guarantees portability
    dumped = json.dumps(canon, sort_keys=True)
    assert json.loads(dumped) == canon


def test_normalize_helpers_public():
    assert normalize_formula("  a +  b  ") == "a+b"
    assert normalize_variables({"a": {"unit": "m"}, "b": {"unit": "s"}}) == [["a", "m"], ["b", "s"]]


def test_registry_strict_mode_detects_output_unit_change(tmp_path):
    """from_axioms_dir(use_strict=True) should separate two axioms that
    differ only in output unit (which canonical_hash would miss)."""
    import yaml
    d = tmp_path / "axioms"
    d.mkdir()
    a = _strict_spec([{"name": "f", "formula": "a", "output_unit": "W"}])
    b = _strict_spec([{"name": "f", "formula": "a", "output_unit": "kW"}])
    (d / "a.yaml").write_text(yaml.safe_dump(a), encoding="utf-8")
    (d / "b.yaml").write_text(yaml.safe_dump(b), encoding="utf-8")

    reg_strict = HashRegistry.from_axioms_dir(d, use_strict=True)
    reg_loose = HashRegistry.from_axioms_dir(d, use_strict=False)
    assert len(reg_strict) == 2, "strict mode should see two distinct solvers"
    assert len(reg_loose) == 1, "legacy mode collapses by formula shape only"
