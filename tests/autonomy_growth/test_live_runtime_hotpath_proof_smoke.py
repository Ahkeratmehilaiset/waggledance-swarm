# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the Phase 14 live runtime hot-path proof.

Locks:
* the corpus is exercised through the production reasoning entrypoint
  ``SolverRouter.route(...)`` (not direct router calls),
* before/after invariants are met at the corpus level,
* P3 acceptance floor is met,
* zero provider/builder-job delta during the proof window
  (inner-loop common path is local-first).
"""
from __future__ import annotations

from pathlib import Path

from tools.run_live_runtime_hotpath_proof import run as run_live_proof


def test_live_runtime_hotpath_proof_before_after(tmp_path: Path) -> None:
    proof = run_live_proof(tmp_path / "out", tmp_path / "p.db")
    n = proof["corpus_total"]
    assert n >= 96, (
        f"corpus has only {n} runtime queries; minimum is 96 per Phase 14 P4"
    )

    # Pass 1: nothing promoted yet
    assert proof["before"]["served_total"] == 0
    assert proof["before"]["fallback_or_miss_total"] == n
    assert proof["before"]["signals_emitted_total"] == n

    # Harvest cycle: every signal becomes a promotion
    assert proof["harvest"]["intents_created"] == n
    assert proof["harvest"]["scheduler_promoted"] == n
    assert proof["harvest"]["scheduler_rejected"] == 0
    assert proof["harvest"]["scheduler_errored"] == 0

    # Pass 2: every query served via capability lookup
    assert proof["after"]["served_total"] == n
    assert proof["after"]["served_via_capability_lookup_total"] == n
    assert proof["after"]["miss_total"] == 0


def test_live_runtime_hotpath_proof_zero_provider_delta(tmp_path: Path) -> None:
    proof = run_live_proof(tmp_path / "out2", tmp_path / "p2.db")
    assert proof["kpis"]["provider_jobs_delta_during_proof"] == 0
    assert proof["kpis"]["builder_jobs_delta_during_proof"] == 0


def test_live_runtime_hotpath_proof_meets_p3_floor(tmp_path: Path) -> None:
    """The Phase 14 minimum acceptable floor must hold:
    warm_p50_ms ≤ 1.0, warm_p99_ms ≤ 10, cold_p50_ms ≤ 75,
    cold_p99_ms ≤ 250, warm_vs_pre_cache_ratio ≥ 5×."""

    proof = run_live_proof(tmp_path / "out3", tmp_path / "p3.db")
    floor = proof["p3_threshold_attainment"]["minimum_floor"]
    assert floor["all_met"] is True, (
        f"P3 minimum floor missed; details={floor['details']}, "
        f"thresholds={floor['thresholds']}, "
        f"warm={proof['latency_warm_ms']}, "
        f"cold={proof['latency_cold_after_promote_ms']}, "
        f"pre_cache={proof['latency_pre_cache_baseline_ms']}, "
        f"ratio={proof['latency_warm_vs_pre_cache_ratio']}"
    )


def test_live_runtime_hotpath_proof_uses_real_entrypoint(tmp_path: Path) -> None:
    """The proof must report SolverRouter.route as the entrypoint —
    not a direct RuntimeQueryRouter call."""

    proof = run_live_proof(tmp_path / "out4", tmp_path / "p4.db")
    assert proof["entrypoint"] == (
        "waggledance.core.reasoning.solver_router.SolverRouter.route"
    )


def test_live_runtime_hotpath_proof_records_buffered_sink_bound(
    tmp_path: Path,
) -> None:
    """RULE P3.3: documented hard-kill loss bound must be present and
    not exceed the prompt-mandated invariant of 1000 signals / 500 ms."""

    proof = run_live_proof(tmp_path / "out5", tmp_path / "p5.db")
    sink = proof["buffered_sink"]
    assert sink["max_unflushed_signals_configured"] <= 1000
    assert sink["max_unflushed_age_ms_configured"] <= 500
    assert sink["documented_hardkill_loss_bound_signals"] == sink[
        "max_unflushed_signals_configured"
    ]
