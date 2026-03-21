# SPDX-License-Identifier: Apache-2.0
"""Tests for Dream Mode — counterfactual simulation (v3.2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    CaseTrajectory, Goal, QualityGrade, CapabilityContract, CapabilityCategory,
)
from waggledance.core.learning.dream_mode import (
    select_candidates, simulate_counterfactual, create_simulated_trajectory,
    run_dream_session, InsightHistory, DreamCandidate,
)


def _trajectory(grade=QualityGrade.BRONZE, verifier_passed=True,
                origin="observed", caps=None):
    return CaseTrajectory(
        goal=Goal(description="test goal"),
        quality_grade=grade,
        trajectory_origin=origin,
        selected_capabilities=caps or [
            CapabilityContract(capability_id="cap_1", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": verifier_passed},
    )


class TestSelectCandidates:
    def test_failures_selected(self):
        trajs = [
            _trajectory(QualityGrade.GOLD, True),
            _trajectory(QualityGrade.QUARANTINE, False),
            _trajectory(QualityGrade.BRONZE, True),
        ]
        candidates = select_candidates(trajs)
        assert len(candidates) >= 1
        assert candidates[0].failure_reason == "quarantine"

    def test_simulated_excluded(self):
        trajs = [_trajectory(QualityGrade.BRONZE, origin="simulated")]
        candidates = select_candidates(trajs)
        assert len(candidates) == 0

    def test_max_candidates_respected(self):
        trajs = [_trajectory(QualityGrade.QUARANTINE) for _ in range(10)]
        candidates = select_candidates(trajs, max_candidates=3)
        assert len(candidates) == 3


class TestSimulateCounterfactual:
    def test_generates_alternative_chain(self):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test",
            original_capabilities=["cap_1"],
            failure_reason="bronze",
        )
        result = simulate_counterfactual(candidate, ["cap_1", "cap_2", "cap_3"])
        assert result.alternative_chain != ["cap_1"]  # different from original

    def test_outcome_is_inconclusive(self):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test",
            original_capabilities=["cap_1"],
        )
        result = simulate_counterfactual(candidate, ["cap_1"])
        assert result.simulated_outcome == "inconclusive"


class TestCreateSimulatedTrajectory:
    def test_always_simulated_origin(self):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test",
            original_capabilities=["cap_1"],
        )
        from waggledance.core.learning.dream_mode import CounterfactualResult
        result = CounterfactualResult(
            original_trajectory_id="t1",
            alternative_chain=["cap_2"],
            simulated_outcome="inconclusive",
        )
        traj = create_simulated_trajectory(candidate, result)
        assert traj.trajectory_origin == "simulated"
        assert traj.synthetic is True
        assert traj.quality_grade == QualityGrade.BRONZE

    def test_never_gold_or_silver(self):
        """Simulated trajectories must never be gold or silver."""
        candidate = DreamCandidate("t1", "test", ["c1"])
        from waggledance.core.learning.dream_mode import CounterfactualResult
        result = CounterfactualResult("t1", ["c2"], "success")
        traj = create_simulated_trajectory(candidate, result)
        assert traj.quality_grade not in (QualityGrade.GOLD, QualityGrade.SILVER)


class TestDreamSession:
    def test_end_to_end(self):
        trajs = [_trajectory(QualityGrade.QUARANTINE, False) for _ in range(3)]
        session = run_dream_session(trajs, ["cap_1", "cap_2"], max_simulations=5)
        assert session.simulations_run > 0
        assert all(t.synthetic for t in session.simulated_trajectories)

    def test_max_simulations_respected(self):
        trajs = [_trajectory(QualityGrade.QUARANTINE) for _ in range(20)]
        session = run_dream_session(trajs, ["cap_1"], max_simulations=2)
        assert session.simulations_run <= 2

    def test_no_production_contamination(self):
        trajs = [_trajectory(QualityGrade.BRONZE)]
        session = run_dream_session(trajs, ["cap_1", "cap_2"])
        for t in session.simulated_trajectories:
            assert t.trajectory_origin == "simulated"
            assert t.synthetic is True
            assert t.quality_grade == QualityGrade.BRONZE


class TestInsightHistory:
    def test_should_reduce_after_negative_streak(self):
        h = InsightHistory(scores=[-0.1, -0.05, -0.02])
        assert h.should_reduce(window=3) is True

    def test_should_not_reduce_mixed(self):
        h = InsightHistory(scores=[-0.1, 0.1, -0.05])
        assert h.should_reduce(window=3) is False

    def test_should_increase_after_positive_streak(self):
        h = InsightHistory(scores=[0.06, 0.07, 0.08])
        assert h.should_increase(window=3) is True

    def test_adapt_count_reduces(self):
        h = InsightHistory(scores=[-0.1, -0.1, -0.1])
        new = h.adapt_count(20, min_count=4)
        assert new == 10  # 50% of 20

    def test_adapt_count_increases(self):
        h = InsightHistory(scores=[0.1, 0.1, 0.1])
        new = h.adapt_count(16, max_count=20)
        assert new == 20  # 125% of 16 = 20

    def test_adapt_count_respects_bounds(self):
        h = InsightHistory(scores=[-0.1, -0.1, -0.1])
        new = h.adapt_count(6, min_count=4)
        assert new >= 4
