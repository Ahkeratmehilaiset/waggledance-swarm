"""Offline tests for tools/run_low_risk_autogrowth_real_loop_proof.py.

Verify the proof shape, deterministic replay, correct loop evidence, and -
critically for the security/structural review lanes - that the proof never flips
runtime authority or the literal claim, and never writes outside an explicit
--out-dir.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_low_risk_autogrowth_real_loop_proof",
    REPO_ROOT / "tools" / "run_low_risk_autogrowth_real_loop_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_proof_ok_and_deterministic():
    report = mod.build_real_loop_proof()
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["deterministic_replay"]["evidence_identical"] is True
    assert report["deterministic_replay"]["runs"] == 2


def test_two_independent_builds_have_identical_evidence():
    a = mod.build_real_loop_proof()
    b = mod.build_real_loop_proof()
    # The evidence (chain + dispatch + authority) must be byte-identical across
    # two independent proof builds (wall-clock generated_at_utc excluded).
    keys = ("chain_evidence", "dispatch_evidence", "evidence_vs_authority", "manifest_contribution")
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}


def test_loop_evidence_complete_and_dispatch_correct():
    report = mod.build_real_loop_proof()
    chain = report["chain_evidence"]
    assert chain["scheduler_outcome"] == "auto_promoted"
    assert chain["auto_promoted_solver_count"] >= 1
    de = report["dispatch_evidence"]
    assert de["matched"] is True
    assert de["output"] == de["expected_output"]
    assert de["output_correct"] is True


def test_no_authority_or_claim_flip():
    report = mod.build_real_loop_proof()
    eva = report["evidence_vs_authority"]
    assert eva["evidence_present"] is True
    assert eva["production_authority_granted"] is False
    assert all(v is False for v in eva["authority_boundary"].values())
    mc = report["manifest_contribution"]
    for flag in ("runtime_authority_granted", "external_writes_applied",
                 "scheduler_enqueue", "production_flip", "claim_safe"):
        assert mc[flag] is False
    assert mc["provider_calls"] == 0


def test_invariants_and_clean_summary():
    report = mod.build_real_loop_proof()
    inv = report["invariants"]
    for flag in ("no_cloud_api_calls_this_session", "deterministic_offline",
                 "no_external_writes", "no_runtime_authority_flip",
                 "no_scheduler_enqueue", "no_production_flip", "no_claim_safe_flip"):
        assert inv[flag] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)
    summary = mod.render_summary(report)
    mod.assert_vocabulary_clean(summary)
    low = summary.lower()
    for phrase in mod.FORBIDDEN_VOCABULARY:
        assert phrase.lower() not in low


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_no_external_write_without_out_dir(tmp_path, monkeypatch):
    # Running without --out-dir must not create any file in the working tree.
    monkeypatch.chdir(tmp_path)
    rc = mod.main([])
    assert rc == 0
    assert list(tmp_path.iterdir()) == []


def test_out_dir_writes_only_when_requested(tmp_path):
    out = tmp_path / "proof_out"
    rc = mod.main(["--out-dir", str(out)])
    assert rc == 0
    artifact = out / "low_risk_autogrowth_real_loop_proof.json"
    assert artifact.is_file()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["manifest_contribution"]["claim_safe"] is False
