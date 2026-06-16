"""Offline tests for tools/run_deterministic_solver_first_proof.py.

Uses the real in-repo SolverRouter (deterministic, offline). Asserts the
solver-first evidence, deterministic replay, no raw-query leak, and that every
flag is derived (clean run -> all safe; the proof fails closed otherwise).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_deterministic_solver_first_proof",
    REPO_ROOT / "tools" / "run_deterministic_solver_first_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_proof_ok_and_deterministic():
    report = mod.build_deterministic_solver_first_proof()
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["deterministic_replay"]["route_identical"] is True


def test_solver_first_evidence():
    se = mod.build_deterministic_solver_first_proof()["solver_first_evidence"]
    assert se["quality_path"] == "gold"
    assert se["fallback_used"] is False
    assert se["selected_solver_ids"] == mod.EXPECTED_SOLVER_IDS
    assert se["execution_boundaries"] == ["safe_action_bus"]


def test_no_raw_query_leak_and_evidence_vs_authority():
    report = mod.build_deterministic_solver_first_proof()
    eva = report["evidence_vs_authority"]
    assert eva["evidence_present"] is True
    assert eva["deterministic_first_not_llm"] is True
    assert eva["provider_calls"] == 0
    assert eva["raw_query_recorded"] is False
    # the sample never exposes the raw query in the report
    assert "calculate 2 + 2" not in json.dumps(report)


def test_invariants_and_clean_summary():
    report = mod.build_deterministic_solver_first_proof()
    inv = report["invariants"]
    for flag in ("deterministic_offline", "no_llm_fallback",
                 "safe_action_bus_boundary_only", "raw_query_not_recorded"):
        assert inv[flag] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)
    summary = mod.render_summary(report)
    mod.assert_vocabulary_clean(summary)
    low = summary.lower()
    for phrase in mod.FORBIDDEN_VOCABULARY:
        assert phrase.lower() not in low


def test_flags_derived_from_observed_route(monkeypatch):
    # If routing observed an LLM fallback, the derived flags + ok must reflect it
    # (regression against hardcoding "safe").
    def _fake_route(domain, query):
        return {
            "quality_path": "silver",
            "fallback_used": True,
            "selected_solver_ids": [],
            "execution_boundaries": ["safe_action_bus"],
            "trace": [],
            "trace_len": 0,
        }
    monkeypatch.setattr(mod, "_route_once", _fake_route)
    report = mod.build_deterministic_solver_first_proof()
    assert report["ok"] is False
    assert "llm_fallback_used" in report["blockers"]
    assert report["evidence_vs_authority"]["deterministic_first_not_llm"] is False
    assert report["invariants"]["no_llm_fallback"] is False


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_out_dir_writes_artifact(tmp_path):
    out = tmp_path / "proof_out"
    assert mod.main(["--out-dir", str(out)]) == 0
    artifact = out / "deterministic_solver_first_proof.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["ok"] is True
