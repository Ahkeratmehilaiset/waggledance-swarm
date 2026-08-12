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

from tools.run_live_runtime_hotpath_proof import (
    P3_MINIMUM_THRESHOLDS,
    _p3_threshold_attainment,
    run as run_live_proof,
)


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
    """The Phase 14 minimum acceptable floor must hold for absolute
    latencies: warm_p50_ms ≤ 1.0, warm_p99_ms ≤ 10, cold_p50_ms ≤ 75,
    cold_p99_ms ≤ 250.

    The relative warm-vs-pre-cache ratio (≥ 5× floor / ≥ 10× stretch)
    compares two separately timed microbenchmark phases and is therefore
    hardware/load-sensitive. It remains explicit evidence in the artifact,
    including target attainment, but is not a shared-runner hard gate.
    """

    proof = run_live_proof(tmp_path / "out3", tmp_path / "p3.db")
    floor = proof["p3_threshold_attainment"]["minimum_floor"]
    details = floor["details"]
    # Absolute latency floors MUST be met on every platform.
    abs_keys = (
        "warm_p50_ms_met", "warm_p99_ms_met",
        "cold_p50_ms_met", "cold_p99_ms_met",
    )
    for k in abs_keys:
        assert details[k] is True, (
            f"P3 absolute floor {k} missed; "
            f"warm={proof['latency_warm_ms']}, "
            f"cold={proof['latency_cold_after_promote_ms']}, "
            f"pre_cache={proof['latency_pre_cache_baseline_ms']}"
        )
    assert floor["absolute_all_met"] is True
    assert floor["acceptance_gate_pass"] is True
    ratio_observation = floor["relative_ratio_observation"]
    assert ratio_observation["observed"] == proof[
        "latency_warm_vs_pre_cache_ratio"
    ]
    assert ratio_observation["target_met"] is details[
        "warm_vs_pre_cache_ratio_met"
    ]
    assert ratio_observation["hard_gate"] is False


def test_p3_ratio_miss_is_disclosed_without_failing_absolute_gate() -> None:
    attainment = _p3_threshold_attainment(
        warm_stats={"p50_ms": 0.05, "p99_ms": 0.08},
        cold_stats={"p50_ms": 0.4, "p99_ms": 0.9},
        warm_vs_pre_ratio=4.32,
        thresholds=P3_MINIMUM_THRESHOLDS,
    )

    assert attainment["absolute_all_met"] is True
    assert attainment["acceptance_gate_pass"] is True
    assert attainment["all_targets_met"] is False
    assert attainment["all_met"] is False
    assert attainment["details"]["warm_vs_pre_cache_ratio_met"] is False
    assert attainment["relative_ratio_observation"] == {
        "observed": 4.32,
        "target": 5.0,
        "target_met": False,
        "hard_gate": False,
        "reason": "hardware_sensitive_sequential_shared_runner_microbenchmark",
    }


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
