# SPDX-License-Identifier: Apache-2.0
"""Tests for Dream Mode counterfactual evaluation with graph data.

Validates:
  - simulate_counterfactual produces non-inconclusive with graph_builder
  - insight_score computation
  - run_dream_session with graph_builder
  - Routing hints via SolverRouter.apply_dream_hints
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    QualityGrade,
)
from waggledance.core.learning.dream_mode import (
    CounterfactualResult,
    DreamCandidate,
    DreamSession,
    compute_insight_score,
    run_dream_session,
    simulate_counterfactual,
)
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.world.graph_builder import GraphBuilder


@pytest.fixture
def temp_graph():
    from core.cognitive_graph import CognitiveGraph
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "test_graph.json")
        graph = CognitiveGraph(persist_path=path)
        yield graph


@pytest.fixture
def populated_builder(temp_graph):
    """GraphBuilder with recorded capabilities at various success rates."""
    builder = GraphBuilder(temp_graph)
    # High success: solve.math
    for _ in range(8):
        builder.record("q", "math", "solve.math", True, "gold", "gold")
    for _ in range(2):
        builder.record("q", "math", "solve.math", False, "bronze", "bronze")
    # Medium success: solve.symbolic
    for _ in range(5):
        builder.record("q", "math", "solve.symbolic", True, "silver", "silver")
    for _ in range(5):
        builder.record("q", "math", "solve.symbolic", False, "bronze", "bronze")
    # Low success: solve.bad
    for _ in range(1):
        builder.record("q", "math", "solve.bad", True)
    for _ in range(9):
        builder.record("q", "math", "solve.bad", False)
    return builder


def _trajectory(grade=QualityGrade.BRONZE, verifier_passed=True,
                origin="observed", caps=None):
    return CaseTrajectory(
        goal=Goal(description="test math goal"),
        quality_grade=grade,
        trajectory_origin=origin,
        selected_capabilities=caps or [
            CapabilityContract(capability_id="solve.bad", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": verifier_passed},
    )


class TestCounterfactualWithGraph:
    def test_non_inconclusive_with_good_alternatives(self, populated_builder):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test math calculation",
            original_capabilities=["solve.bad"],
            failure_reason="bronze",
        )
        result = simulate_counterfactual(
            candidate,
            ["solve.math", "solve.symbolic", "solve.bad"],
            graph_builder=populated_builder,
        )
        assert result.simulated_outcome != "inconclusive"
        assert result.confidence > 0.3

    def test_alternative_chain_differs_from_original(self, populated_builder):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test math goal",
            original_capabilities=["solve.bad"],
            failure_reason="bronze",
        )
        result = simulate_counterfactual(
            candidate,
            ["solve.math", "solve.symbolic", "solve.bad"],
            graph_builder=populated_builder,
        )
        assert result.alternative_chain != ["solve.bad"]

    def test_no_graph_falls_back_to_pool(self):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test",
            original_capabilities=["cap_1"],
            failure_reason="bronze",
        )
        result = simulate_counterfactual(candidate, ["cap_1", "cap_2"])
        # Without graph, uses pool-based swap → still produces a result
        assert result.alternative_chain == ["cap_2"]

    def test_no_alternative_stays_inconclusive(self):
        candidate = DreamCandidate(
            original_trajectory_id="t1",
            goal_description="test",
            original_capabilities=["cap_1"],
        )
        result = simulate_counterfactual(candidate, ["cap_1"])
        assert result.simulated_outcome == "inconclusive"


class TestInsightScore:
    def test_all_success(self):
        session = DreamSession(simulations_run=3)
        session.simulated_trajectories = [
            CaseTrajectory(
                goal=Goal(description="t"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                verifier_result={"outcome": "success"},
            )
            for _ in range(3)
        ]
        score = compute_insight_score(session)
        assert score == pytest.approx(1.0)

    def test_all_failure(self):
        session = DreamSession(simulations_run=2)
        session.simulated_trajectories = [
            CaseTrajectory(
                goal=Goal(description="t"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                verifier_result={"outcome": "failure"},
            )
            for _ in range(2)
        ]
        score = compute_insight_score(session)
        assert score == pytest.approx(-1.0)

    def test_mixed(self):
        session = DreamSession(simulations_run=4)
        outcomes = ["success", "success", "failure", "inconclusive"]
        session.simulated_trajectories = [
            CaseTrajectory(
                goal=Goal(description="t"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                verifier_result={"outcome": o},
            )
            for o in outcomes
        ]
        score = compute_insight_score(session)
        # 2 success - 1 failure = 1, /4 = 0.25
        assert score == pytest.approx(0.25)

    def test_zero_simulations(self):
        session = DreamSession(simulations_run=0)
        assert compute_insight_score(session) == 0.0


class TestDreamSessionWithGraph:
    def test_session_produces_insights(self, populated_builder):
        trajs = [_trajectory(QualityGrade.QUARANTINE, False) for _ in range(3)]
        session = run_dream_session(
            trajs,
            ["solve.math", "solve.symbolic", "solve.bad"],
            graph_builder=populated_builder,
        )
        assert session.simulations_run > 0
        assert session.insights_found > 0
        assert session.insight_score != 0.0

    def test_session_without_graph_still_works(self):
        trajs = [_trajectory(QualityGrade.QUARANTINE, False)]
        session = run_dream_session(trajs, ["solve.bad", "solve.other"])
        assert session.simulations_run > 0


class TestRoutingHints:
    def test_apply_dream_hints(self):
        router = SolverRouter()
        # Create a mock dream session with successful counterfactuals
        session = DreamSession(simulations_run=2)
        session.simulated_trajectories = [
            CaseTrajectory(
                goal=Goal(description="t"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                counterfactual_alternatives=["solve.math"],
                verifier_result={
                    "outcome": "success",
                    "original_trajectory_id": "t1",
                },
            ),
            CaseTrajectory(
                goal=Goal(description="t2"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                counterfactual_alternatives=["solve.symbolic"],
                verifier_result={
                    "outcome": "inconclusive",
                    "original_trajectory_id": "t2",
                },
            ),
        ]
        applied = router.apply_dream_hints(session)
        assert applied == 1  # only the "success" one

    def test_no_hints_when_no_success(self):
        router = SolverRouter()
        session = DreamSession(simulations_run=1)
        session.simulated_trajectories = [
            CaseTrajectory(
                goal=Goal(description="t"),
                quality_grade=QualityGrade.BRONZE,
                trajectory_origin="simulated",
                synthetic=True,
                verifier_result={"outcome": "inconclusive"},
            ),
        ]
        applied = router.apply_dream_hints(session)
        assert applied == 0
