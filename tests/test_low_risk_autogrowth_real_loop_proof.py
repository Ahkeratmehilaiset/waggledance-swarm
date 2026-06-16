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

import pytest

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


def _fake_dry_run_report(authority_overrides: dict | None = None) -> dict:
    authority = {
        "external_writes_applied": False,
        "production_control_plane_touched": False,
        "production_scheduler_enqueue": False,
        "provider_jobs_created": False,
        "builder_jobs_created": False,
        "gate_skip_authority": False,
        "operator_gate_bypassed": False,
        "runtime_authority_granted": False,
        "fast_track_priority": False,
    }
    if authority_overrides:
        authority.update(authority_overrides)
    return {
        "ok": True,
        "chain": {
            "detector_signals_recorded": 1,
            "intents_created": 1,
            "scheduler_outcome": "auto_promoted",
            "auto_promoted_solver_count": 1,
        },
        "dispatch": {"matched": True, "output": 298.15, "expected_output": 298.15},
        "authority_boundary": authority,
        "no_overclaim_guardrails": {"uses_existing_low_risk_allowlist": True},
    }


@pytest.mark.parametrize("leak_key,contrib_key", [
    ("runtime_authority_granted", "runtime_authority_granted"),
    ("external_writes_applied", "external_writes_applied"),
    ("production_scheduler_enqueue", "scheduler_enqueue"),
    ("provider_jobs_created", "provider_calls"),
])
def test_authority_leak_surfaces_and_fails_closed(monkeypatch, leak_key, contrib_key):
    # Regression for the rco-2 hardening: manifest_contribution flags are DERIVED
    # from the observed authority_boundary, so an injected leak (a) surfaces in
    # the counter feed (never trusts a hardcoded "safe"), (b) fails the proof
    # closed (ok=False), and (c) never upgrades the literal claim.
    monkeypatch.setattr(
        mod, "build_low_risk_autogrowth_chain_dry_run",
        lambda **kw: _fake_dry_run_report({leak_key: True}),
    )
    report = mod.build_real_loop_proof()
    contrib_val = report["manifest_contribution"][contrib_key]
    assert contrib_val not in (False, 0)  # leak reflected (True or provider_calls=1)
    assert report["ok"] is False
    assert "authority_flag_open" in report["blockers"]
    assert report["evidence_vs_authority"]["evidence_present"] is False
    assert report["manifest_contribution"]["claim_safe"] is False


def test_clean_run_derives_all_flags_safe(monkeypatch):
    monkeypatch.setattr(
        mod, "build_low_risk_autogrowth_chain_dry_run",
        lambda **kw: _fake_dry_run_report(),
    )
    report = mod.build_real_loop_proof()
    assert report["ok"] is True
    mc = report["manifest_contribution"]
    assert mc["runtime_authority_granted"] is False
    assert mc["provider_calls"] == 0
    assert report["invariants"]["no_runtime_authority_flip"] is True


def test_out_dir_writes_only_when_requested(tmp_path):
    out = tmp_path / "proof_out"
    rc = mod.main(["--out-dir", str(out)])
    assert rc == 0
    artifact = out / "low_risk_autogrowth_real_loop_proof.json"
    assert artifact.is_file()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["manifest_contribution"]["claim_safe"] is False
