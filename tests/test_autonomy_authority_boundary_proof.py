"""Offline tests for tools/run_autonomy_authority_boundary_proof.py.

Uses the real in-repo SolverRouter / governor / action_gate (deterministic,
offline). Asserts the two authority-boundary invariants, that every flag is
DERIVED from observed runs (weakening any observation fails the proof closed),
the privacy invariant (no raw query text in the report), and the CLI surface.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_autonomy_authority_boundary_proof",
    REPO_ROOT / "tools" / "run_autonomy_authority_boundary_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_proof_ok_and_deterministic():
    report = mod.build_authority_boundary_proof()
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["deterministic_replay"]["observations_identical"] is True


def test_all_checks_pass():
    checks = mod.build_authority_boundary_proof()["checks"]
    assert all(checks.values()), checks


# ── (a) deterministic solver authority not overridable by LLM advisory ──────-

def test_solver_authority_boundary():
    sab = mod.build_authority_boundary_proof()["solver_authority_boundary"]
    auth = sab["authoritative_route"]
    adv = sab["advisory_route"]
    # solver-eligible intent → authoritative deterministic gold route, no LLM
    assert auth["quality_path"] == "gold"
    assert auth["fallback_used"] is False
    assert auth["deterministic_solver_selected"] is True
    assert auth["llm_selected"] is False
    # non-solver intent → LLM advisory, bronze, fallback, no deterministic solver
    assert adv["quality_path"] == "bronze"
    assert adv["fallback_used"] is True
    assert adv["deterministic_solver_selected"] is False
    # the advisory route can never reach the authoritative tiers
    assert sab["advisory_cannot_be_authoritative"] is True


def test_llm_promotion_to_authoritative_fails_closed(monkeypatch):
    """If an LLM advisory route were observed at gold (overriding a solver),
    the proof must fail closed — not silently report 'safe'."""
    real = mod._route_view

    def fake(intent, query):
        if intent == mod.ADVISORY_INTENT:
            return {
                "quality_path": "gold",          # forged promotion
                "fallback_used": False,
                "selected_capability_ids": [mod.LLM_CAPABILITY_ID],
                "llm_selected": True,
                "deterministic_solver_selected": False,
            }
        return real(intent, query)

    monkeypatch.setattr(mod, "_route_view", fake)
    report = mod.build_authority_boundary_proof()
    assert report["ok"] is False
    assert "llm_advisory_only" in report["blockers"]
    assert "advisory_cannot_be_authoritative" in report["blockers"]


# ── (b) no LLM output can grant runtime-mutation authority ──────────────────-

def test_mutation_authority_boundary():
    mab = mod.build_authority_boundary_proof()["mutation_authority_boundary"]
    assert mab["factory_has_no_mutation_kwarg"] is True
    assert mab["factory_forces_no_runtime_mutation"] is True
    assert mab["gate_clean_verdict"] == "ADMIT_TO_LANE"      # enqueue-only, not exec
    assert mab["gate_forged_verdict"] == "REJECT_HARD"
    assert "action_gate_is_only_exit" in mab["gate_forged_blocking_rule_ids"]


def test_governor_minting_mutation_fails_closed(monkeypatch):
    monkeypatch.setattr(mod, "_governor_view", lambda: {
        "factory_params": ["no_runtime_mutation"],
        "factory_has_no_mutation_kwarg": False,        # factory gained an override
        "factory_mutation_flags": [False],
        "factory_forces_no_runtime_mutation": False,
    })
    report = mod.build_authority_boundary_proof()
    assert report["ok"] is False
    assert "governor_cannot_mint_mutation" in report["blockers"]


def test_gate_admitting_forged_mutation_fails_closed(monkeypatch):
    monkeypatch.setattr(mod, "_action_gate_view", lambda: {
        "clean_verdict": "ADMIT_TO_LANE",
        "forged_verdict": "ADMIT_TO_LANE",              # forgery admitted (bad)
        "forged_blocking_rule_ids": [],
        "forged_blocked_by_only_exit": False,
    })
    report = mod.build_authority_boundary_proof()
    assert report["ok"] is False
    assert "gate_rejects_forged_mutation" in report["blockers"]


# ── privacy + vocabulary + CLI ──────────────────────────────────────────────

def test_no_raw_query_leak():
    report = mod.build_authority_boundary_proof()
    blob = json.dumps(report)
    assert mod.AUTHORITATIVE_QUERY not in blob
    assert mod.ADVISORY_QUERY not in blob


def test_summary_vocabulary_clean():
    report = mod.build_authority_boundary_proof()
    summary = mod.render_summary(report)
    mod.assert_vocabulary_clean(summary)
    low = summary.lower()
    for phrase in mod.FORBIDDEN_VOCABULARY:
        assert phrase.lower() not in low


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_out_dir_writes_artifact(tmp_path):
    out = tmp_path / "proof_out"
    assert mod.main(["--out-dir", str(out)]) == 0
    artifact = out / "autonomy_authority_boundary_proof.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["ok"] is True


def test_out_dir_must_not_exist(tmp_path):
    out = tmp_path / "already_here"
    out.mkdir()
    assert mod.main(["--out-dir", str(out)]) == 1
