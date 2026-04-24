"""Tests for tools/propose_solver.py.

Covers all 12 gates plus verdict composition and the CLI-shape `run()`
entry point. No network, no port 8002, no campaign disruption.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = ROOT / "tools" / "propose_solver.py"
    spec = importlib.util.spec_from_file_location("propose_solver", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["propose_solver"] = mod
    spec.loader.exec_module(mod)
    return mod


# Import once at module level; tests re-use
mod = _load_module()


def _proposal() -> dict:
    return {
        "proposal_id": "test-proposal-1",
        "cell_id": "thermal",
        "solver_name": "new_thermal_estimator",
        "purpose": "Estimate thermal output from inputs A and B.",
        "inputs": [
            {"name": "a", "unit": "m", "description": "first input",
             "range": [0, 100], "type": "number"},
            {"name": "b", "unit": "m", "description": "second input",
             "range": [0, 100], "type": "number"},
        ],
        "outputs": [
            {"name": "out", "unit": "m", "description": "primary output",
             "type": "number", "primary": True}
        ],
        "formula_or_algorithm": {
            "kind": "formula_chain",
            "steps": [
                {"name": "out", "formula": "a + b", "output_unit": "m",
                 "description": "sum"},
            ],
        },
        "assumptions": ["inputs measured in meters"],
        "invariants": ["out >= 0"],
        "units": {"system": "SI"},
        "tests": [
            {"name": "basic", "inputs": {"a": 1, "b": 2}, "expected": 3,
             "tolerance": 1e-6},
        ],
        "expected_failure_modes": [
            {"condition": "a < 0", "behavior": "returns negative"},
        ],
        "examples": [
            {"description": "simple", "inputs": {"a": 1, "b": 2}, "expected": 3},
        ],
        "provenance_note": "test fixture",
        "estimated_latency_ms": 0.1,
        "expected_coverage_lift": {"value": 0.10, "uncertainty": "low",
                                   "rationale": "covers common thermal question"},
        "risk_level": "low",
        "uncertainty_declaration": "small sample of validation cases",
    }


# ── Gate 1: schema ────────────────────────────────────────────────

def test_good_proposal_passes_schema():
    assert mod.gate_schema(_proposal())["ok"]


def test_schema_rejects_missing_required_field():
    p = _proposal(); p.pop("inputs")
    r = mod.gate_schema(p)
    assert not r["ok"]
    assert r["errors"]


# ── Gate 2: cell exists ───────────────────────────────────────────

def test_cell_exists_passes_for_valid():
    assert mod.gate_cell_exists(_proposal())["ok"]


def test_cell_exists_fails_for_unknown():
    p = _proposal(); p["cell_id"] = "nonexistent"
    assert not mod.gate_cell_exists(p)["ok"]


# ── Gate 3: hash duplicate ────────────────────────────────────────

def test_hash_duplicate_new_proposal_passes(tmp_path):
    # Empty axioms dir → nothing to collide with
    axioms = tmp_path / "axioms"
    axioms.mkdir()
    r = mod.gate_hash_duplicate(_proposal(), axioms)
    assert r["ok"]


def test_hash_duplicate_detects_existing(tmp_path):
    axioms = tmp_path / "axioms" / "x"
    axioms.mkdir(parents=True)
    existing = mod._proposal_to_axiom_shape(_proposal())
    (axioms / "exists.yaml").write_text(yaml.safe_dump(existing), encoding="utf-8")
    r = mod.gate_hash_duplicate(_proposal(), tmp_path / "axioms")
    assert not r["ok"]


# ── Gate 4: io types ──────────────────────────────────────────────

def test_io_types_rejects_multiple_primary():
    p = _proposal()
    p["outputs"].append({"name": "other", "unit": "m",
                          "description": "also primary",
                          "type": "number", "primary": True})
    r = mod.gate_io_types(p)
    assert not r["ok"]


def test_io_types_rejects_test_input_not_declared():
    p = _proposal()
    p["tests"][0]["inputs"] = {"undeclared_var": 1}
    r = mod.gate_io_types(p)
    assert not r["ok"]


# ── Gate 5: deterministic replay ──────────────────────────────────

def test_replay_passes_on_correct_formula():
    r = mod.gate_deterministic_replay(_proposal())
    assert r["ok"]
    assert r["tests_ran"] == 1


def test_replay_fails_on_wrong_expected():
    p = _proposal()
    p["tests"][0]["expected"] = 999
    r = mod.gate_deterministic_replay(p)
    assert not r["ok"]


def test_replay_skipped_for_algorithm_kind():
    p = _proposal()
    p["formula_or_algorithm"] = {
        "kind": "algorithm",
        "description": "opaque for now"
    }
    r = mod.gate_deterministic_replay(p)
    assert r["ok"] and r.get("skipped")


def test_replay_refuses_unsafe_formula():
    p = _proposal()
    # __import__ is not on the allowed identifier list
    p["formula_or_algorithm"]["steps"][0]["formula"] = "__import__('os').system('x')"
    r = mod.gate_deterministic_replay(p)
    assert not r["ok"]


# ── Gate 6: unit consistency ──────────────────────────────────────

def test_unit_consistency_passes_for_declared_vars():
    assert mod.gate_unit_consistency(_proposal())["ok"]


def test_unit_consistency_fails_for_undeclared_var():
    p = _proposal()
    p["formula_or_algorithm"]["steps"][0]["formula"] = "a + c"
    r = mod.gate_unit_consistency(p)
    assert not r["ok"]


def test_unit_consistency_fails_when_step_missing_output_unit():
    p = _proposal()
    p["formula_or_algorithm"]["steps"][0]["output_unit"] = ""
    r = mod.gate_unit_consistency(p)
    assert not r["ok"]


# ── Gate 7: contradiction ─────────────────────────────────────────

def test_contradiction_none_by_default(tmp_path):
    axioms = tmp_path / "axioms"
    axioms.mkdir()
    assert mod.gate_contradiction(_proposal(), axioms)["ok"]


def test_contradiction_detects_opposite_invariant(tmp_path):
    axioms = tmp_path / "axioms" / "x"
    axioms.mkdir(parents=True)
    existing = {
        "model_id": "existing",
        "cell_id": "thermal",
        "formulas": [{"name": "f", "formula": "x", "output_unit": "m"}],
        "variables": {"out": {"unit": "m"}},
        "validation": [{"check": "out < 0"}],
    }
    (axioms / "existing.yaml").write_text(yaml.safe_dump(existing), encoding="utf-8")

    p = _proposal()
    # Our proposal asserts out >= 0; existing says out < 0 → contradiction
    r = mod.gate_contradiction(p, tmp_path / "axioms")
    assert not r["ok"], r


# ── Gate 8: invariants present ────────────────────────────────────

def test_invariants_present_passes():
    assert mod.gate_invariants_present(_proposal())["ok"]


def test_invariants_present_fails_empty():
    p = _proposal(); p["invariants"] = []
    assert not mod.gate_invariants_present(p)["ok"]


# ── Gate 9: tests present ─────────────────────────────────────────

def test_tests_present_passes():
    assert mod.gate_tests_present(_proposal())["ok"]


def test_tests_present_fails_empty():
    p = _proposal(); p["tests"] = []
    assert not mod.gate_tests_present(p)["ok"]


# ── Gate 10: latency budget ───────────────────────────────────────

def test_latency_under_budget_passes():
    assert mod.gate_latency_budget(_proposal(), 100)["ok"]


def test_latency_over_budget_fails():
    p = _proposal(); p["estimated_latency_ms"] = 500
    assert not mod.gate_latency_budget(p, 100)["ok"]


# ── Gate 11: no secrets / no absolute paths ───────────────────────

def test_no_secrets_passes_clean():
    assert mod.gate_no_secrets_no_paths(_proposal())["ok"]


def test_no_secrets_fails_on_absolute_windows_path():
    p = _proposal()
    p["provenance_note"] = "data from C:\\Users\\jani\\Documents\\secret.yaml"
    assert not mod.gate_no_secrets_no_paths(p)["ok"]


def test_no_secrets_fails_on_api_key_token():
    p = _proposal()
    p["provenance_note"] = "auth Bearer abcd1234"
    assert not mod.gate_no_secrets_no_paths(p)["ok"]


# ── Gate 12: LLM dependency ───────────────────────────────────────

def test_llm_declared_required_passes():
    p = _proposal()
    p["llm_dependency"] = {"required": True, "reason": "needs summary",
                            "fallback_only": True}
    assert mod.gate_llm_dependency(p)["ok"]


def test_llm_undeclared_token_in_formula_fails():
    p = _proposal()
    p["formula_or_algorithm"] = {
        "kind": "algorithm",
        "description": "call an LLM with a prompt to summarize inputs",
    }
    assert not mod.gate_llm_dependency(p)["ok"]


def test_llm_provenance_note_does_not_false_positive():
    """provenance_note names the teacher; must not trigger detection."""
    p = _proposal()
    p["provenance_note"] = "teacher: claude-opus-4.7; manifest sha256:abc"
    assert mod.gate_llm_dependency(p)["ok"]


# ── Verdict composition ───────────────────────────────────────────

def test_verdict_accept_candidate_for_high_lift(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "AXIOMS_DIR", tmp_path / "axioms")
    (tmp_path / "axioms").mkdir()
    result = mod.evaluate_proposal(_proposal(), axioms_dir=tmp_path / "axioms")
    assert result["verdict"] == mod.V_ACCEPT_CANDIDATE


def test_verdict_accept_shadow_for_mid_lift(tmp_path):
    # Between reject threshold (0.005) and accept threshold (0.02)
    p = _proposal()
    p["expected_coverage_lift"]["value"] = 0.01
    axioms = tmp_path / "axioms"; axioms.mkdir()
    result = mod.evaluate_proposal(p, axioms_dir=axioms)
    assert result["verdict"] == mod.V_ACCEPT_SHADOW_ONLY


def test_verdict_reject_low_value_for_tiny_lift(tmp_path):
    # Below reject threshold (0.005) → REJECT_LOW_VALUE per GPT R4 fix
    p = _proposal()
    p["expected_coverage_lift"]["value"] = 0.001
    axioms = tmp_path / "axioms"; axioms.mkdir()
    result = mod.evaluate_proposal(p, axioms_dir=axioms)
    assert result["verdict"] == mod.V_REJECT_LOW_VALUE


def test_verdict_reject_schema_when_missing_required(tmp_path):
    p = _proposal(); p.pop("inputs")
    axioms = tmp_path / "axioms"; axioms.mkdir()
    result = mod.evaluate_proposal(p, axioms_dir=axioms)
    assert result["verdict"] == mod.V_REJECT_SCHEMA


def test_verdict_reject_duplicate(tmp_path):
    axioms = tmp_path / "axioms" / "x"; axioms.mkdir(parents=True)
    (axioms / "exists.yaml").write_text(
        yaml.safe_dump(mod._proposal_to_axiom_shape(_proposal())),
        encoding="utf-8",
    )
    result = mod.evaluate_proposal(_proposal(), axioms_dir=tmp_path / "axioms")
    assert result["verdict"] == mod.V_REJECT_DUPLICATE


def test_run_writes_report_file(tmp_path):
    axioms = tmp_path / "axioms"; axioms.mkdir()
    p_path = tmp_path / "proposal.yaml"
    p_path.write_text(yaml.safe_dump(_proposal()), encoding="utf-8")

    import unittest.mock as um
    with um.patch.object(mod, "AXIOMS_DIR", axioms):
        out = mod.run(p_path, tmp_path / "reports")
    assert Path(out["report_path"]).exists()
    text = Path(out["report_path"]).read_text("utf-8")
    assert "Proposal gate report" in text
    assert "Verdict" in text
    # No secrets / absolute paths leaked
    assert "C:\\Users" not in text


def test_all_14_gates_run_and_reported(tmp_path):
    axioms = tmp_path / "axioms"; axioms.mkdir()
    result = mod.evaluate_proposal(_proposal(), axioms_dir=axioms)
    gate_names = {g["gate"] for g in result["gates"]}
    expected = {
        "schema", "cell_exists", "hash_duplicate", "io_types",
        "deterministic_replay", "unit_consistency", "contradiction",
        "invariants_present", "tests_present", "latency_budget",
        "no_secrets_no_paths", "llm_dependency", "closed_world",
        "machine_invariants_shape",
    }
    assert gate_names == expected, f"missing: {expected - gate_names}"


def test_auto_merge_is_not_performed(tmp_path):
    """Whatever the verdict, evaluate_proposal must never mutate
    configs/axioms or the registry. Verify file tree unchanged."""
    axioms = tmp_path / "axioms"; axioms.mkdir()
    before = sorted(p.name for p in axioms.rglob("*"))
    mod.evaluate_proposal(_proposal(), axioms_dir=axioms)
    after = sorted(p.name for p in axioms.rglob("*"))
    assert before == after


# ── GPT R4: uncertainty_declaration enforced as schema-required ───

def test_missing_uncertainty_declaration_rejected_as_schema(tmp_path):
    p = _proposal(); p.pop("uncertainty_declaration")
    axioms = tmp_path / "axioms"; axioms.mkdir()
    result = mod.evaluate_proposal(p, axioms_dir=axioms)
    assert result["verdict"] == mod.V_REJECT_SCHEMA


# ── GPT R4: Gate 13 closed-world ──────────────────────────────────

def _algorithm_proposal(description: str) -> dict:
    p = _proposal()
    p["formula_or_algorithm"] = {
        "kind": "algorithm",
        "description": description,
    }
    return p


def test_closed_world_formula_chain_is_skipped():
    r = mod.gate_closed_world(_proposal())
    assert r["ok"] and r.get("skipped")


def test_closed_world_rejects_network_token():
    p = _algorithm_proposal("fetch via https: then parse")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_rejects_subprocess():
    p = _algorithm_proposal("call subprocess.Popen on an external binary")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_rejects_filesystem():
    p = _algorithm_proposal("read_text from the config file on disk")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_rejects_env_read():
    p = _algorithm_proposal("read os.environ to get the region setting")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_rejects_wall_clock():
    p = _algorithm_proposal("use time.time to branch on current epoch")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_rejects_randomness():
    p = _algorithm_proposal("generate a uuid.uuid4 for the lookup key")
    assert not mod.gate_closed_world(p)["ok"]


def test_closed_world_allows_declared_llm_but_still_blocks_fs():
    p = _algorithm_proposal("call openai.chat completion then open( local cache")
    p["llm_dependency"] = {"required": True, "reason": "test"}
    r = mod.gate_closed_world(p)
    # openai./completion allowed (LLM declared) but open( still blocks
    assert not r["ok"]
    tokens = {e["token"] for e in r["errors"]}
    assert "open(" in tokens


def test_closed_world_passes_clean_algorithm():
    p = _algorithm_proposal(
        "multiply input_a by scalar k and threshold at value T, "
        "constants embedded."
    )
    assert mod.gate_closed_world(p)["ok"]


# ── GPT R4: wider secret scan covers dict keys ────────────────────

def test_secrets_gate_finds_in_dict_keys():
    p = _proposal()
    p["tags"] = ["normal-tag"]
    # Inject a dangerous path as a dict KEY in examples[].inputs
    p["examples"][0]["inputs"] = {"C:\\Users\\x": 1}
    assert not mod.gate_no_secrets_no_paths(p)["ok"]


# ── GPT R4: wider LLM scan covers inputs.description, outputs.description,
#           expected_failure_modes.behavior, examples.source ────────

def test_llm_gate_finds_hidden_token_in_input_description():
    p = _proposal()
    p["inputs"][0]["description"] = "use an openai. completion then parse"
    assert not mod.gate_llm_dependency(p)["ok"]


def test_llm_gate_finds_hidden_token_in_output_description():
    p = _proposal()
    p["outputs"][0]["description"] = "anthropic. result after summarization"
    assert not mod.gate_llm_dependency(p)["ok"]


def test_llm_gate_finds_hidden_token_in_failure_modes():
    p = _proposal()
    p["expected_failure_modes"][0]["behavior"] = "fall back to chatgpt"
    assert not mod.gate_llm_dependency(p)["ok"]


def test_llm_gate_provenance_note_still_exempt():
    """provenance_note legitimately names the teacher; must NOT flag."""
    p = _proposal()
    p["provenance_note"] = "teacher: claude-opus-4.7 with chat completion"
    assert mod.gate_llm_dependency(p)["ok"]


# ── GPT R4: hash projection includes domain, all outputs, failure modes ──

def test_hash_projection_moves_with_domain_change(tmp_path):
    a = _proposal()
    b = _proposal()
    b["domain"] = "cottage"
    a["domain"] = "greenhouse"
    import hashlib as _h
    from waggledance.core.learning.solver_hash import solver_hash
    ha = solver_hash(mod._proposal_to_axiom_shape(a))
    hb = solver_hash(mod._proposal_to_axiom_shape(b))
    assert ha != hb


def test_hash_projection_moves_with_secondary_output_change():
    a = _proposal()
    b = _proposal()
    b["outputs"].append({
        "name": "secondary_metric", "unit": "ratio",
        "description": "extra", "type": "ratio",
    })
    from waggledance.core.learning.solver_hash import solver_hash
    ha = solver_hash(mod._proposal_to_axiom_shape(a))
    hb = solver_hash(mod._proposal_to_axiom_shape(b))
    assert ha != hb


def test_hash_projection_moves_with_failure_mode_change():
    a = _proposal()
    b = _proposal()
    b["expected_failure_modes"] = [
        {"condition": "input > 9999", "behavior": "raises overflow"},
    ]
    from waggledance.core.learning.solver_hash import solver_hash
    ha = solver_hash(mod._proposal_to_axiom_shape(a))
    hb = solver_hash(mod._proposal_to_axiom_shape(b))
    assert ha != hb


# ── GPT R4 advisory: machine_invariants shape gate ────────────────

def test_machine_invariants_absent_is_noop():
    p = _proposal()
    r = mod.gate_machine_invariants_shape(p)
    assert r["ok"] and r.get("skipped")


def test_machine_invariants_valid_shape_passes():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "nonneg_out", "expr": "out >= 0"},
        {"id": "bounded", "expr": "a + b <= 1000"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert r["ok"], r


def test_machine_invariants_rejects_function_call():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "bad", "expr": "abs(out) < 10"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert not r["ok"]


def test_machine_invariants_rejects_attribute():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "bad", "expr": "os.environ >= 0"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert not r["ok"]


def test_machine_invariants_rejects_undeclared_identifier():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "bad", "expr": "unknown_var >= 0"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert not r["ok"]
    # Error carries the free variable
    err = r["errors"][0]
    assert "unknown_var" in err.get("free", [])


def test_machine_invariants_rejects_duplicate_id():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "same", "expr": "a >= 0"},
        {"id": "same", "expr": "b >= 0"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert not r["ok"]


def test_machine_invariants_boolean_ops_allowed():
    p = _proposal()
    p["machine_invariants"] = [
        {"id": "conj", "expr": "a >= 0 and b <= 100"},
        {"id": "disj", "expr": "a > 0 or b > 0"},
        {"id": "neg", "expr": "not (a < 0)"},
    ]
    r = mod.gate_machine_invariants_shape(p)
    assert r["ok"]


def test_hash_projection_moves_with_assumption_change():
    a = _proposal()
    b = _proposal()
    b["assumptions"] = ["inputs measured in CENTImeters, not meters"]
    from waggledance.core.learning.solver_hash import solver_hash
    ha = solver_hash(mod._proposal_to_axiom_shape(a))
    hb = solver_hash(mod._proposal_to_axiom_shape(b))
    assert ha != hb
