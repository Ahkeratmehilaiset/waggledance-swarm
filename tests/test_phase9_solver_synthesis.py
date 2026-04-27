# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §U1 Solver Families + Declarative Bootstrap."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.solver_synthesis import (
    HEX_CELLS,
    SOLVER_FAMILY_KINDS,
    U1_HIGH_CONFIDENCE_THRESHOLD,
    U1_LOW_CONFIDENCE_THRESHOLD,
    bulk_rule_extractor as bre,
    declarative_solver_spec as ds,
    deterministic_solver_compiler as dsc,
    solver_family_registry as sfr,
)


# ═══════════════════ schema enums match constants ══════════════════

def test_solver_family_kinds_match_schema():
    schema = json.loads((ROOT / "schemas" / "solver_family.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["kind"]["enum"]) == SOLVER_FAMILY_KINDS


def test_solver_spec_cell_id_enum_matches():
    schema = json.loads((ROOT / "schemas" / "solver_spec.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["cell_id"]["enum"]) == HEX_CELLS


# ═══════════════════ family registry ══════════════════════════════

def test_default_families_includes_all_10():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    assert set(reg.list_kinds()) == set(SOLVER_FAMILY_KINDS)
    assert len(reg.list_kinds()) == 10


def test_solver_family_rejects_unknown_kind():
    with pytest.raises(ValueError):
        sfr.SolverFamily(
            schema_version=1, family_id="x", name="x",
            kind="bogus_kind", required_spec_keys=(),
            compiler_module="x",
        )


def test_registry_get_returns_default():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    fam = reg.get("scalar_unit_conversion")
    assert fam is not None
    assert "factor" in fam.required_spec_keys


# ═══════════════════ declarative_solver_spec ══════════════════════

def test_make_spec_validates_required_keys():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    with pytest.raises(ds.SpecValidationError, match="missing required keys"):
        ds.make_spec(
            family_kind="scalar_unit_conversion",
            solver_name="bad_solver_x",
            cell_id="thermal",
            spec={"from_unit": "c"},   # missing to_unit + factor
            source="t", source_kind="table",
            registry=reg,
        )


def test_make_spec_rejects_unknown_family():
    reg = sfr.SolverFamilyRegistry()
    with pytest.raises(ds.SpecValidationError, match="not in registry"):
        ds.make_spec(
            family_kind="scalar_unit_conversion",
            solver_name="x_solver",
            cell_id="thermal",
            spec={"from_unit": "c", "to_unit": "f", "factor": 1.8},
            source="t", source_kind="table",
            registry=reg,
        )


def test_make_spec_rejects_invalid_solver_name():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    with pytest.raises(ValueError):
        ds.make_spec(
            family_kind="scalar_unit_conversion",
            solver_name="X",   # too short, capital
            cell_id="thermal",
            spec={"from_unit": "c", "to_unit": "f", "factor": 1.8},
            source="t", source_kind="table",
            registry=reg,
        )


def test_make_spec_rejects_unknown_cell():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    with pytest.raises(ValueError):
        ds.make_spec(
            family_kind="scalar_unit_conversion",
            solver_name="solver_x",
            cell_id="bogus_cell",
            spec={"from_unit": "c", "to_unit": "f", "factor": 1.8},
            source="t", source_kind="table",
            registry=reg,
        )


def test_spec_id_deterministic():
    reg = sfr.SolverFamilyRegistry().register_defaults()
    spec_a = ds.make_spec(
        family_kind="scalar_unit_conversion", solver_name="solver_x",
        cell_id="thermal",
        spec={"from_unit": "c", "to_unit": "f", "factor": 1.8},
        source="t", source_kind="table", registry=reg,
    )
    spec_b = ds.make_spec(
        family_kind="scalar_unit_conversion", solver_name="solver_x",
        cell_id="thermal",
        spec={"from_unit": "c", "to_unit": "f", "factor": 1.8},
        source="different_source",   # different source — id excludes
        source_kind="document", registry=reg,
    )
    assert spec_a.spec_id == spec_b.spec_id


# ═══════════════════ deterministic_solver_compiler ════════════════

def _spec(family_kind: str, spec: dict,
            solver_name: str = "auto_solver_x") -> ds.SolverSpec:
    reg = sfr.SolverFamilyRegistry().register_defaults()
    return ds.make_spec(
        family_kind=family_kind, solver_name=solver_name,
        cell_id="thermal", spec=spec,
        source="t", source_kind="table", registry=reg,
    )


def test_all_10_families_compile_deterministically():
    """Every default family compiles deterministically."""
    spec_inputs = {
        "scalar_unit_conversion": {"from_unit": "c", "to_unit": "f",
                                       "factor": 1.8, "offset": 32},
        "lookup_table": {"table": {"a": 1, "b": 2}},
        "threshold_rule": {"threshold": 0.5, "operator": ">",
                            "true_label": "high", "false_label": "low"},
        "interval_bucket_classifier": {"intervals": [
            {"min": 0, "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "high"},
        ]},
        "linear_arithmetic": {"coefficients": [1.5, 2.0],
                               "intercept": 0.5},
        "weighted_aggregation": {"weights": {"a": 1, "b": 2}},
        "temporal_window_rule": {"window_seconds": 300,
                                   "aggregator": "mean",
                                   "threshold": 100, "operator": ">"},
        "bounded_interpolation": {
            "knots": [{"x": 0, "y": 0}, {"x": 10, "y": 100}],
            "method": "linear", "min_x": 0, "max_x": 10,
        },
        "structured_field_extractor": {
            "source_field": "raw_text",
            "extract_pattern": r"(\d+)", "extract_kind": "regex",
        },
        "deterministic_composition_wrapper": {
            "steps": ["solver_a", "solver_b"],
        },
    }
    for kind, spec_in in spec_inputs.items():
        s = _spec(kind, spec_in, solver_name=f"u1_{kind}_x")
        c1 = dsc.compile_spec(s)
        c2 = dsc.compile_spec(s)
        assert c1.canonical_json == c2.canonical_json, kind
        assert c1.artifact_id == c2.artifact_id, kind


def test_compiler_rejects_unknown_threshold_operator():
    s = _spec("threshold_rule", {
        "threshold": 0.5, "operator": "@@", "true_label": "h",
        "false_label": "l",
    })
    with pytest.raises(ValueError, match="unknown operator"):
        dsc.compile_spec(s)


def test_compiler_rejects_invalid_interval():
    s = _spec("interval_bucket_classifier", {
        "intervals": [{"min": 10, "max": 0, "label": "bad"}],
    })
    with pytest.raises(ValueError, match="interval min > max"):
        dsc.compile_spec(s)


def test_compiler_rejects_unknown_aggregator():
    s = _spec("temporal_window_rule", {
        "window_seconds": 60, "aggregator": "bogus_agg",
        "threshold": 0, "operator": ">",
    })
    with pytest.raises(ValueError, match="unknown aggregator"):
        dsc.compile_spec(s)


def test_compiler_rejects_unknown_interpolation_method():
    s = _spec("bounded_interpolation", {
        "knots": [{"x": 0, "y": 0}], "method": "bogus_method",
        "min_x": 0, "max_x": 10,
    })
    with pytest.raises(ValueError, match="unknown method"):
        dsc.compile_spec(s)


def test_compiler_rejects_empty_composition_steps():
    s = _spec("deterministic_composition_wrapper", {"steps": []})
    with pytest.raises(ValueError, match="steps must be non-empty"):
        dsc.compile_spec(s)


def test_lookup_table_artifact_sorted_keys():
    s = _spec("lookup_table", {"table": {"z": 1, "a": 2, "m": 3}})
    c = dsc.compile_spec(s)
    keys = list(c.artifact["table"].keys())
    assert keys == sorted(keys)


def test_compile_byte_identical_two_specs():
    """Two specs with identical content + name + cell produce
    byte-identical canonical_json."""
    s1 = _spec("linear_arithmetic",
                 {"coefficients": [1, 2], "intercept": 0})
    s2 = _spec("linear_arithmetic",
                 {"coefficients": [1, 2], "intercept": 0})
    c1 = dsc.compile_spec(s1)
    c2 = dsc.compile_spec(s2)
    assert c1.canonical_json == c2.canonical_json


def test_all_compiler_kinds_match_default_families():
    assert set(dsc.all_compiler_kinds()) == set(SOLVER_FAMILY_KINDS)


# ═══════════════════ bulk_rule_extractor ══════════════════════════

def test_extract_from_unit_conversion_table():
    table = {
        "columns": ["celsius", "fahrenheit"],
        "rows": [[5, 9], [10, 18], [20, 36]],
    }
    matches = bre.extract_from_table(table)
    kinds = {m.family_kind for m in matches}
    assert "scalar_unit_conversion" in kinds


def test_extract_from_linear_table():
    table = {
        "columns": ["x", "y"],
        "rows": [[0, 1], [1, 3], [2, 5], [3, 7]],
    }
    matches = bre.extract_from_table(table)
    kinds = {m.family_kind for m in matches}
    # Linear y = 2x + 1 should be detected
    assert "linear_arithmetic" in kinds or "scalar_unit_conversion" in kinds


def test_extract_from_explicit_intervals():
    table = {"intervals": [
        {"min": 0, "max": 10, "label": "low"},
        {"min": 10, "max": 20, "label": "high"},
    ]}
    matches = bre.extract_from_table(table)
    assert any(m.family_kind == "interval_bucket_classifier"
                and m.match_confidence >= 0.9 for m in matches)


def test_extract_from_explicit_mapping():
    table = {"mapping": {"a": 1, "b": 2}}
    matches = bre.extract_from_table(table)
    assert any(m.family_kind == "lookup_table"
                and m.match_confidence >= 0.9 for m in matches)


def test_extract_from_threshold_shape():
    table = {"threshold": 0.5, "operator": ">",
              "true_label": "high", "false_label": "low"}
    matches = bre.extract_from_table(table)
    assert any(m.family_kind == "threshold_rule"
                and m.match_confidence >= 0.9 for m in matches)


def test_extract_unrecognized_falls_through():
    table = {"some_unrelated_key": 42}
    matches = bre.extract_from_table(table)
    # Falls through with low confidence → routes to U3
    assert any(m.match_confidence < U1_LOW_CONFIDENCE_THRESHOLD
                for m in matches)


# ═══════════════════ U1→U3 routing rules ══════════════════════════

def test_match_route_high_confidence_picks_u1():
    m = bre.FamilyMatch(family_kind="scalar_unit_conversion",
                            match_confidence=0.95,
                            extracted_spec={}, rationale="r")
    assert bre.match_route(m) == "U1_compile"


def test_match_route_low_confidence_picks_u3():
    m = bre.FamilyMatch(family_kind="scalar_unit_conversion",
                            match_confidence=0.30,
                            extracted_spec={}, rationale="r")
    assert bre.match_route(m) == "U3_residual"


def test_match_route_mid_confidence_tries_u1_then_u3():
    m = bre.FamilyMatch(family_kind="scalar_unit_conversion",
                            match_confidence=0.65,
                            extracted_spec={}, rationale="r")
    assert bre.match_route(m) == "U1_with_U3_fallback"


# ═══════════════════ no free-form code path for known families ════

def test_compiler_uses_no_exec_or_eval():
    """Critical: U1 compilers must NEVER use exec/eval/compile to
    generate solver code. Free-form code is reserved for U3."""
    src = (ROOT / "waggledance" / "core" / "solver_synthesis"
            / "deterministic_solver_compiler.py").read_text(encoding="utf-8")
    forbidden = ["exec(", "eval(", "compile(", "globals()[",
                  "__import__("]
    for pat in forbidden:
        assert pat not in src, (
            f"deterministic_solver_compiler.py uses forbidden "
            f"free-form code generator: {pat}"
        )


# ═══════════════════ end-to-end bootstrap ═════════════════════════

def test_end_to_end_table_to_artifact():
    """Full pipeline: table → matches → spec → compiled artifact."""
    table = {
        "columns": ["celsius", "fahrenheit"],
        "rows": [[5, 9], [10, 18]],
    }
    matches = bre.extract_from_table(table)
    promising = [m for m in matches
                  if m.match_confidence >= U1_HIGH_CONFIDENCE_THRESHOLD]
    assert promising, "expected at least one high-confidence match"

    reg = sfr.SolverFamilyRegistry().register_defaults()
    artifacts = []
    for m in promising:
        spec = ds.make_spec(
            family_kind=m.family_kind,
            solver_name=f"u1_{m.family_kind}_001",
            cell_id="general",
            spec=m.extracted_spec,
            source="test_table", source_kind="table",
            registry=reg,
        )
        artifacts.append(dsc.compile_spec(spec))
    assert len(artifacts) >= 1
    assert artifacts[0].artifact["kind"] == promising[0].family_kind


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_bootstrap_solvers.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--tables", "--cell-id", "--apply", "--json"):
        assert flag in r.stdout


def test_cli_dry_run_with_no_inputs():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_bootstrap_solvers.py"), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["tables_seen"] == 0
    assert "registered_families" in out
    assert len(out["registered_families"]) == 10


def test_cli_runs_on_real_tables(tmp_path):
    tables_path = tmp_path / "tables.json"
    tables_path.write_text(json.dumps([
        {"columns": ["c", "f"],
         "rows": [[5, 9], [10, 18], [20, 36]]},
        {"intervals": [
            {"min": 0, "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "high"},
        ]},
    ]), encoding="utf-8")
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_bootstrap_solvers.py"),
         "--tables", str(tables_path), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["tables_seen"] == 2
    assert out["specs_compiled"] >= 2


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_u1_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "core" / "solver_synthesis"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
