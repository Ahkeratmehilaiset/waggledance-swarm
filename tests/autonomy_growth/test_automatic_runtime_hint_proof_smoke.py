# SPDX-License-Identifier: Apache-2.0
"""Phase 15 — automatic runtime hint proof smoke test.

Locks the Phase 15 P5 invariants:
* the proof routes through the production caller AutonomyRuntime.handle_query
* the proof input never carries forbidden hint keys
* RuntimeQuery objects are not constructed in the proof
* pass 1: 0 served / corpus_total misses
* harvest: corpus_total promoted, 0 rejected, 0 errored
* pass 2: corpus_total served via capability lookup
* provider/builder delta during proof window == 0
* HotPathCache warm hits used in pass 2/3
* negative corpus passes all expected outcomes
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.run_automatic_runtime_hint_proof import run as run_phase15_proof


def test_proof_uses_real_production_caller(tmp_path: Path) -> None:
    proof = run_phase15_proof(tmp_path / "out", tmp_path / "p.db")
    assert proof["selected_caller"] == (
        "waggledance.core.autonomy.runtime.AutonomyRuntime.handle_query"
    )


def test_proof_input_carries_no_manual_low_risk_hint(tmp_path: Path) -> None:
    """Regression guard for the operator's hard rule: the proof must
    never pass low_risk_autonomy_query / family_kind / features /
    spec_seed at the top level of context. Those keys must be derived
    by the extractor from structured_request only."""

    proof = run_phase15_proof(tmp_path / "out2", tmp_path / "p2.db")
    assert proof["manual_low_risk_hint_in_input_detected"] is False
    assert proof["proof_constructed_runtime_query_objects"] is False
    assert proof["manual_hint_audit_detected_keys"] == []
    # Sanity: only the natural caller keys appear in the corpus input.
    keys = set(proof["selected_caller_input_keys"])
    forbidden = {"low_risk_autonomy_query", "family_kind",
                 "features", "spec_seed"}
    assert not (keys & forbidden), (
        f"forbidden hint keys leaked into corpus input: {keys & forbidden}"
    )


def test_proof_before_after_invariants(tmp_path: Path) -> None:
    proof = run_phase15_proof(tmp_path / "out3", tmp_path / "p3.db")
    n = proof["corpus_total"]
    assert n >= 96, f"corpus has only {n}; minimum is 96 per Phase 15 P4"
    assert proof["before"]["served_total"] == 0
    assert proof["before"]["fallback_or_miss_total"] == n
    assert proof["harvest"]["scheduler_promoted"] == n
    assert proof["harvest"]["scheduler_rejected"] == 0
    assert proof["harvest"]["scheduler_errored"] == 0
    assert proof["after"]["served_total"] == n
    assert proof["after"]["served_via_capability_lookup_total"] == n
    assert proof["after"]["miss_total"] == 0


def test_proof_zero_provider_delta(tmp_path: Path) -> None:
    proof = run_phase15_proof(tmp_path / "out4", tmp_path / "p4.db")
    assert proof["kpis"]["provider_jobs_delta_during_proof"] == 0
    assert proof["kpis"]["builder_jobs_delta_during_proof"] == 0


def test_proof_warm_cache_used_in_pass2(tmp_path: Path) -> None:
    proof = run_phase15_proof(tmp_path / "out5", tmp_path / "p5.db")
    cache = proof["hot_path_cache_stats"]
    # cold-then-warmed > 0 means pass 2 hit the warm dispatcher; warm_hits > 0
    # means pass 3 stayed in cache.
    assert cache["cold_hits_warmed"] > 0, (
        "pass 2 should populate the warm cache via cold-then-warmed path"
    )
    assert cache["warm_hits"] > 0, (
        "pass 3 should stay warm-only; non-zero warm_hits expected"
    )


def test_proof_extractor_grammar_covers_six_families(tmp_path: Path) -> None:
    """Phase 15 P4 corpus tier 1 — selected caller must support all 6
    families. Per-family served counts must include every allowlisted
    family name."""

    proof = run_phase15_proof(tmp_path / "out6", tmp_path / "p6.db")
    expected = {
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    }
    actual = set(proof["after"]["per_family_served"].keys())
    assert actual == expected, (
        f"per-family served mismatch: missing={expected - actual}, "
        f"extra={actual - expected}"
    )


def test_proof_negative_cases_all_pass(tmp_path: Path) -> None:
    proof = run_phase15_proof(tmp_path / "out7", tmp_path / "p7.db")
    assert proof["negative_cases_passed_total"] == proof["negative_cases_total"]
    # Required negative coverage
    expected_kinds = {
        "rejected_ambiguous", "rejected_family_not_low_risk",
        "rejected_missing_fields", "skipped", "rejected_malformed",
    }
    observed_expected = {r["expected_hint_kind"]
                          for r in proof["negative_cases_detail"]}
    assert observed_expected == expected_kinds


def test_proof_hint_derivation_is_fast(tmp_path: Path) -> None:
    """Hint extractor itself must be sub-millisecond at the median —
    this is a deterministic Python function with no I/O."""

    proof = run_phase15_proof(tmp_path / "out8", tmp_path / "p8.db")
    assert proof["latency_hint_derivation_only_ms"]["p50_ms"] < 1.0
    assert proof["latency_hint_derivation_only_ms"]["p99_ms"] < 5.0
