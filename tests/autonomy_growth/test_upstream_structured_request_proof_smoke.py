"""Phase 16A P10 — proof smoke test.

Locks in the invariants of `tools/run_upstream_structured_request_proof.py`
without re-running the full proof inside the test. The full proof JSON
is committed under `docs/runs/phase16_upstream_structured_request_2026_05_01/`
and this test asserts the contract the operator and reviewers rely on.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


PROOF_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "runs"
    / "phase16_upstream_structured_request_2026_05_01"
)
PROOF_JSON = PROOF_DIR / "upstream_structured_request_proof.json"


def _proof() -> dict:
    if not PROOF_JSON.exists():
        pytest.skip(
            f"Proof artifact not present yet at {PROOF_JSON} — run "
            "`python tools/run_upstream_structured_request_proof.py` "
            "to generate."
        )
    return json.loads(PROOF_JSON.read_text(encoding="utf-8"))


def test_selected_upstream_caller_is_autonomy_service_handle_query():
    p = _proof()
    assert p["selected_upstream_caller"] == (
        "waggledance.application.services.autonomy_service."
        "AutonomyService.handle_query"
    )


def test_no_manual_structured_request_in_input():
    p = _proof()
    assert p["manual_structured_request_in_input_detected"] is False
    assert "structured_request" not in p["manual_hint_audit_detected_keys"]


def test_no_manual_low_risk_hint_in_input():
    p = _proof()
    assert p["manual_low_risk_hint_in_input_detected"] is False
    assert (
        "low_risk_autonomy_query" not in p["manual_hint_audit_detected_keys"]
    )


def test_proof_did_not_bypass_caller_or_handle_query():
    p = _proof()
    assert p["proof_constructed_runtime_query_objects"] is False
    assert p["proof_bypassed_selected_caller"] is False
    assert p["proof_bypassed_handle_query"] is False


def test_input_keys_do_not_include_forbidden_internal_fields():
    p = _proof()
    forbidden = {
        "structured_request",
        "low_risk_autonomy_query",
        "family_kind",
        "features",
        "spec_seed",
    }
    keys = set(p["selected_upstream_caller_input_keys"])
    assert keys.isdisjoint(forbidden), (
        f"forbidden internal fields appeared in caller input: "
        f"{keys & forbidden}"
    )


def test_corpus_tier_1_full_six_families():
    p = _proof()
    assert p["corpus_total"] == 98
    assert p["corpus_tier"].startswith("Tier 1")


def test_before_after_invariants():
    p = _proof()
    assert p["before"]["served_total"] == 0
    assert p["before"]["fallback_or_miss_total"] == p["corpus_total"]
    assert p["after"]["served_total"] == p["corpus_total"]
    assert (
        p["after"]["served_via_capability_lookup_total"] == p["corpus_total"]
    )
    assert p["after"]["miss_total"] == 0


def test_harvest_promoted_full_corpus():
    p = _proof()
    assert p["harvest"]["growth_intents_total"] == p["corpus_total"]
    assert p["harvest"]["scheduler_promoted"] == p["corpus_total"]
    assert p["harvest"]["scheduler_rejected"] == 0
    assert p["harvest"]["scheduler_errored"] == 0


def test_provider_and_builder_delta_zero():
    p = _proof()
    assert p["kpis"]["provider_jobs_delta_during_proof"] == 0
    assert p["kpis"]["builder_jobs_delta_during_proof"] == 0


def test_negative_cases_seven_of_seven_pass():
    p = _proof()
    assert p["negative_cases_total"] == 7
    assert p["negative_cases_passed_total"] == 7


def test_warm_cache_actually_used_in_pass3():
    p = _proof()
    assert p["hot_path_cache_stats"]["warm_hits"] > 0


def test_upstream_derivation_latency_is_microseconds():
    p = _proof()
    assert p["upstream_derivation_p50_ms"] < 1.0
    assert p["upstream_derivation_p99_ms"] < 5.0


def test_six_low_risk_families_covered_by_input_corpus():
    p = _proof()
    assert set(p["per_family_in_input_corpus"].keys()) == {
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    }


def test_structured_request_derived_for_full_corpus_in_pass1():
    p = _proof()
    assert p["structured_request_derived_total"] == p["corpus_total"]
    assert p["low_risk_hint_derived_total"] == p["corpus_total"]


def test_no_extractor_errors_during_proof():
    p = _proof()
    assert p["upstream_extractor_errors_total"] == 0
