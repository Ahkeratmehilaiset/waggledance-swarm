"""Phase 16B P2 — full-corpus restart continuity proof smoke.

Locks invariants of `tools/run_full_restart_continuity_proof.py`
without re-running the full proof inside the test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


PROOF_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "runs"
    / "phase16b_stabilization_release_gate_2026_05_01"
)
PROOF_JSON = PROOF_DIR / "full_restart_continuity_proof.json"


def _proof() -> dict:
    if not PROOF_JSON.exists():
        pytest.skip(
            f"Proof artifact not present yet at {PROOF_JSON} — run "
            "`python tools/run_full_restart_continuity_proof.py` "
            "to generate."
        )
    return json.loads(PROOF_JSON.read_text(encoding="utf-8"))


def test_selected_caller_is_autonomy_service_handle_query():
    p = _proof()
    assert p["selected_upstream_caller"] == (
        "waggledance.application.services.autonomy_service."
        "AutonomyService.handle_query"
    )


def test_no_manual_structured_request_in_input():
    p = _proof()
    assert p["manual_structured_request_in_input_detected"] is False


def test_no_manual_low_risk_hint_in_input():
    p = _proof()
    assert p["manual_low_risk_hint_in_input_detected"] is False


def test_full_corpus_size_at_least_phase16a_baseline():
    p = _proof()
    assert p["corpus_total"] >= 98


def test_pass1_no_serves_before_harvest():
    p = _proof()
    assert p["pass1_before_harvest"]["served_total"] == 0
    assert (
        p["pass1_before_harvest"]["miss_total"] == p["corpus_total"]
    )


def test_harvest_promoted_full_corpus():
    p = _proof()
    assert p["harvest"]["scheduler_promoted"] == p["corpus_total"]
    assert p["harvest"]["scheduler_rejected"] == 0
    assert p["harvest"]["scheduler_errored"] == 0


def test_pre_restart_pass2_serves_full_corpus():
    p = _proof()
    assert p["pre_restart_pass2"]["served_total"] == p["corpus_total"]
    assert (
        p["pre_restart_pass2"]["served_via_capability_lookup_total"]
        == p["corpus_total"]
    )


def test_persisted_state_unchanged_across_reopen():
    p = _proof()
    before = p["persisted_state_before_close"]
    after = p["persisted_state_after_reopen"]
    assert (
        before["solver_count_auto_promoted"]
        == after["solver_count_auto_promoted"]
    )
    assert (
        before["capability_features_total"]
        == after["capability_features_total"]
    )


def test_post_restart_serves_full_corpus_via_capability_lookup():
    p = _proof()
    assert p["post_restart_pass2"]["served_total"] == p["corpus_total"]
    assert (
        p["post_restart_pass2"]["served_via_capability_lookup_total"]
        == p["corpus_total"]
    )


def test_restart_invariants_all_true():
    p = _proof()
    inv = p["restart_invariants"]
    assert inv["served_unchanged_across_restart"] is True
    assert inv["served_via_capability_lookup_unchanged_across_restart"] is True
    assert inv["solver_count_unchanged_across_reopen"] is True
    assert inv["capability_features_unchanged_across_reopen"] is True
    assert inv["provider_jobs_delta_across_restart"] == 0
    assert inv["builder_jobs_delta_across_restart"] == 0
    assert inv["cache_rebuild_success"] is True


def test_provider_and_builder_delta_zero_during_proof():
    p = _proof()
    assert p["kpis"]["provider_jobs_delta_during_proof"] == 0
    assert p["kpis"]["builder_jobs_delta_during_proof"] == 0


def test_warm_cache_used_post_restart():
    p = _proof()
    cache = p["hot_path_cache_post_restart"]
    assert cache["warm_hits"] > 0 or cache["cold_hits_warmed"] > 0
