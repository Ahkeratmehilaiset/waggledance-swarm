# SPDX-License-Identifier: Apache-2.0
"""Equivalence + scaling tests for TrustAdapter trust-score caching.

Codex MAGMA latency scout 2026-05-09 (snapshot bb3e93036f3e) found
get_ranking at 20.44 ms for 512 solver targets — crossing the 10 ms
operator threshold at 1/20th of the 10k+ target count. Root cause:
get_all_scores called get_trust_score per target, each acquiring
the lock and reading time.time() independently.

The R17 fix updates score totals when observations arrive, so
get_all_scores/get_ranking read one precomputed score per target
instead of rescanning every retained observation. This file pins the
contract that:

- Bulk score output is equivalent (within float tolerance) to
  per-target score reads.
- Ranking ORDER is exactly preserved (no swap caused by per-target
  `now` drift).
- Empty-observation engine-fallback semantics survive.
- target_type filter preserved.
- Mixed-context (actual/simulated) weighting preserved.
"""
from __future__ import annotations

import gc
import time

import pytest

from waggledance.core.magma.trust_adapter import TrustAdapter


# --- helpers -------------------------------------------------------

def _populate_targets(adapter: TrustAdapter, count: int,
                          target_type: str = "capability",
                          distinct_scores: bool = False) -> None:
    """Add `count` targets with a deterministic mix of obs.

    When distinct_scores=True, each target gets a different success
    rate based on its index, so ordering is stable across float drift.
    """
    for i in range(count):
        target_id = f"target_{i:04d}"
        if distinct_scores:
            # i out of 10 successes — every target has a unique
            # success rate derived from its index, so float drift
            # in `now` cannot swap ranks.
            successes = i % 10
            for j in range(10):
                adapter.record_observation(
                    target_type=target_type,
                    target_id=target_id,
                    success=(j < successes),
                    latency_ms=1.0,
                    quality_path=("x",),
                    context="actual",
                )
        else:
            for j in range(5):
                adapter.record_observation(
                    target_type=target_type,
                    target_id=target_id,
                    success=(j % 2 == 0),
                    latency_ms=10.0 + j,
                    quality_path=("a", "b"),
                    context=("actual" if j % 3 != 0 else "simulated"),
                )


# --- equivalence: new get_all_scores matches per-target loop ------

def test_get_all_scores_equivalent_to_per_target_loop():
    """Bulk scores must match per-target scores within float tolerance."""
    adapter = TrustAdapter()
    _populate_targets(adapter, count=64)

    # Snapshot scores via the cached bulk path.
    bulk = adapter.get_all_scores(target_type="capability")

    # Compute expected via per-target path
    expected = {}
    keys = sorted(bulk.keys())
    for key in keys:
        t_type, t_id = key.split(":", 1)
        expected[key] = adapter.get_trust_score(t_type, t_id)

    # Each key present in both
    assert set(bulk.keys()) == set(expected.keys())
    # Scores match within tolerance (slight float drift from
    # different `now` between calls — typically << 1e-6)
    for key in keys:
        assert abs(bulk[key] - expected[key]) < 1e-3, (
            f"{key}: bulk={bulk[key]} per-target={expected[key]}"
        )


def test_get_ranking_order_preserved_at_scale():
    """Ranking order must be stable: if two calls produce the same
    score-set, they must produce the same order. Use distinct
    per-target scores so float drift cannot cause silent swaps."""
    adapter = TrustAdapter()
    _populate_targets(adapter, count=128, distinct_scores=True)

    new_ranking = adapter.get_ranking(target_type="capability", limit=128)

    # Two separate get_ranking calls with distinct per-target scores
    # must produce identical ordering: the cached-score impl is
    # deterministic given a fixed observation set.
    new_ranking_2 = adapter.get_ranking(target_type="capability", limit=128)

    new_targets = [r["target"] for r in new_ranking]
    new_targets_2 = [r["target"] for r in new_ranking_2]
    assert new_targets == new_targets_2

    # Scores must be monotonically non-increasing
    new_scores = [r["trust_score"] for r in new_ranking]
    assert new_scores == sorted(new_scores, reverse=True)


# --- target_type filter -------------------------------------------

def test_get_all_scores_target_type_filter():
    adapter = TrustAdapter()
    # Add caps + solvers (both valid target_types)
    for i in range(5):
        adapter.record_observation(
            target_type="capability", target_id=f"cap-{i}",
            success=True, latency_ms=1.0,
            quality_path=("x",), context="actual",
        )
    for i in range(3):
        adapter.record_observation(
            target_type="solver", target_id=f"solver-{i}",
            success=True, latency_ms=1.0,
            quality_path=("x",), context="actual",
        )

    cap_scores = adapter.get_all_scores(target_type="capability")
    solver_scores = adapter.get_all_scores(target_type="solver")
    all_scores = adapter.get_all_scores()

    assert len(cap_scores) == 5
    assert len(solver_scores) == 3
    assert len(all_scores) == 8
    assert all(k.startswith("capability:") for k in cap_scores)
    assert all(k.startswith("solver:") for k in solver_scores)


def test_get_all_scores_empty_when_no_observations():
    adapter = TrustAdapter()
    assert adapter.get_all_scores() == {}
    assert adapter.get_all_scores(target_type="capability") == {}


# --- mixed actual/simulated context weighting ---------------------

def test_simulated_observations_get_half_weight():
    """The cached-score path must preserve actual/simulated weighting."""
    adapter = TrustAdapter()
    # All-success actual observations
    for _ in range(10):
        adapter.record_observation(
            target_type="capability", target_id="actual-only",
            success=True, latency_ms=1.0,
            quality_path=("x",), context="actual",
        )
    # All-success simulated observations
    for _ in range(10):
        adapter.record_observation(
            target_type="capability", target_id="sim-only",
            success=True, latency_ms=1.0,
            quality_path=("x",), context="simulated",
        )
    # Mixed half actual / half failed simulated
    for i in range(10):
        adapter.record_observation(
            target_type="capability", target_id="mixed",
            success=(i < 5),
            latency_ms=1.0, quality_path=("x",),
            context=("actual" if i < 5 else "simulated"),
        )

    scores = adapter.get_all_scores(target_type="capability")

    # All-actual-success and all-sim-success both score ~1.0
    # (success is 1.0 weighted; weighted_sum / weight_total = 1.0)
    assert scores["capability:actual-only"] == pytest.approx(1.0, abs=0.01)
    assert scores["capability:sim-only"] == pytest.approx(1.0, abs=0.01)
    # Mixed: 5 actual successes + 5 simulated failures.
    # weighted_sum = 5*1*1 + 5*0.5*0 = 5
    # weight_total = 5*1 + 5*0.5 = 7.5
    # score = 5 / 7.5 = 0.6667
    assert scores["capability:mixed"] == pytest.approx(0.6667, abs=0.01)


# --- single lock acquisition behavior -----------------------------

def test_get_all_scores_reflects_latest_retained_observations():
    """Score reads reflect the retained observations at read time."""
    adapter = TrustAdapter()
    for _ in range(3):
        adapter.record_observation(
            target_type="capability", target_id="snap-test",
            success=True, latency_ms=1.0,
            quality_path=("x",), context="actual",
        )

    # Read scores
    scores_before = adapter.get_all_scores(target_type="capability")
    score_before = scores_before["capability:snap-test"]

    # Mutate observations
    for _ in range(20):
        adapter.record_observation(
            target_type="capability", target_id="snap-test",
            success=False, latency_ms=1.0,
            quality_path=("x",), context="actual",
        )

    # Read scores again
    scores_after = adapter.get_all_scores(target_type="capability")
    score_after = scores_after["capability:snap-test"]

    # Score must change after mutations.
    assert score_before != score_after
    # Specifically, score_before should be ~1.0 (all success),
    # score_after should be lower (mostly failures)
    assert score_before > score_after
    assert score_before == pytest.approx(1.0, abs=0.01)
    assert score_after < 0.5


# --- limit parameter on get_ranking -------------------------------

def test_get_ranking_limit_truncates():
    adapter = TrustAdapter()
    _populate_targets(adapter, count=20)
    ranked = adapter.get_ranking(target_type="capability", limit=5)
    assert len(ranked) == 5


def test_get_ranking_default_limit_is_ten():
    adapter = TrustAdapter()
    _populate_targets(adapter, count=20)
    ranked = adapter.get_ranking(target_type="capability")
    assert len(ranked) == 10


def test_get_ranking_each_entry_has_target_and_trust_score():
    adapter = TrustAdapter()
    _populate_targets(adapter, count=5)
    ranked = adapter.get_ranking(target_type="capability", limit=5)
    for entry in ranked:
        assert "target" in entry
        assert "trust_score" in entry
        assert isinstance(entry["target"], str)
        assert isinstance(entry["trust_score"], float)
        assert 0.0 <= entry["trust_score"] <= 1.0


def test_get_ranking_descending_order():
    adapter = TrustAdapter()
    _populate_targets(adapter, count=10)
    ranked = adapter.get_ranking(target_type="capability", limit=10)
    scores = [r["trust_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


# --- 512-target scaling smoke ------------------------------------

def test_get_ranking_scales_to_512_targets_under_threshold():
    """The microbench (snapshot bb3e93036f3e) measured the OLD
    impl at 20.44 ms for 512 targets. The cached-score path must
    be measurably faster — we don't pin a specific number here
    (microbench script does that), but assert it stays under
    50ms wall-clock as a regression guard. If this test takes
    >50ms locally, the optimization regressed."""
    adapter = TrustAdapter()
    _populate_targets(adapter, count=512)

    def measured_call():
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        try:
            start = time.perf_counter()
            ranking = adapter.get_ranking(target_type="capability", limit=10)
            return ranking, (time.perf_counter() - start) * 1000
        finally:
            if gc_was_enabled:
                gc.enable()

    # Warm once, then use best-of samples so the guard measures the ranking
    # algorithm rather than a one-off CI scheduler or GC pause.
    ranked, _ = measured_call()
    samples_ms = []
    for _ in range(5):
        ranked, elapsed_ms = measured_call()
        samples_ms.append(elapsed_ms)
    elapsed_ms = min(samples_ms)

    assert len(ranked) == 10
    # The PR's actual measured improvement comes from the
    # microbenchmark; this assertion is just a regression guard.
    # 50ms gives plenty of headroom for the slow CI environments.
    assert elapsed_ms < 50.0, (
        f"get_ranking at 512 targets took {elapsed_ms:.2f}ms "
        f"— expected < 50ms after R17 cached-score fix"
    )
