# SPDX-License-Identifier: Apache-2.0
"""Tests for closing the v3.2 learning loop — CognitiveGraph + Dream Mode.

Validates:
  - CognitiveGraph populates after simulated queries
  - Dream mode produces non-inconclusive results when solvers are available
  - WorldModel rejects None graph in autonomy mode
  - insight_score calculation correctness
  - Dream routing hints feed into solver_router
  - Graph health check logs ERROR when 0 user-created nodes after 10 queries
  - Sensor observations update CognitiveGraph nodes
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.domain.autonomy import (
    Action,
    CaseTrajectory,
    CapabilityContract,
    CapabilityCategory,
    Goal,
    QualityGrade,
)
from waggledance.core.learning.dream_mode import (
    CounterfactualResult,
    DreamCandidate,
    DreamSession,
    compute_insight_score,
    create_simulated_trajectory,
    run_dream_session,
    select_candidates,
    simulate_counterfactual,
)
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.world.world_model import WorldModel


@pytest.fixture
def temp_graph():
    from core.cognitive_graph import CognitiveGraph
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "test_graph.json")
        graph = CognitiveGraph(persist_path=path)
        yield graph


# ── Problem 1: CognitiveGraph populates ──────────────────────


class TestCognitiveGraphPopulates:
    def test_graph_populates_after_5_queries(self):
        """CognitiveGraph should have query nodes after 5 handle_query calls."""
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        for i in range(5):
            rt.handle_query(f"laske {i}+{i}")

        graph = rt.world_model.graph
        assert graph is not None

        # Check that query nodes were created
        query_nodes = [
            nid for nid in graph.graph.nodes
            if nid.startswith("query:")
        ]
        assert len(query_nodes) >= 5
        rt.stop()

    def test_intent_and_capability_nodes_populated(self):
        """Intent and capability nodes should exist after queries."""
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        rt.handle_query("laske 2+3")
        rt.handle_query("What is the temperature?")

        graph = rt.world_model.graph
        intent_nodes = [
            nid for nid in graph.graph.nodes
            if nid.startswith("intent:")
        ]
        cap_nodes = [
            nid for nid in graph.graph.nodes
            if nid.startswith("capability:")
        ]
        assert len(intent_nodes) >= 1
        assert len(cap_nodes) >= 1
        rt.stop()

    def test_verifier_confidence_on_edges(self):
        """Edges from query to capability should carry confidence score."""
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        rt.handle_query("laske 10+5")

        graph = rt.world_model.graph
        query_node = "query:1"
        edges = graph.get_edges(query_node)
        input_edges = [e for e in edges if e.get("link_type") == "input_to"]
        assert len(input_edges) >= 1
        # Confidence should be a float
        assert isinstance(input_edges[0].get("confidence", None), float)
        rt.stop()

    def test_sensor_observation_updates_graph(self):
        """Sensor observations should create entity nodes in CognitiveGraph."""
        rt = AutonomyRuntime(profile="TEST")
        rt.start()

        rt.ingest_sensor_observation({
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 35.2,
            "source": "mqtt",
            "quality": 0.9,
        })

        graph = rt.world_model.graph
        assert graph.has_node("hive_1")
        node = graph.get_node("hive_1")
        assert node["latest_value"] == 35.2
        assert node["node_type"] == "sensor_entity"

        # Baseline should also be updated
        bl = rt.world_model.get_baseline("hive_1", "temperature")
        assert bl is not None
        rt.stop()


class TestGraphHealthCheck:
    def test_logs_error_when_zero_user_nodes(self, caplog, temp_graph):
        """Graph health should log ERROR when 0 user-created nodes after 10 queries."""
        # Use a fresh temporary graph so no pre-existing nodes interfere
        wm = WorldModel(cognitive_graph=temp_graph, profile="TEST")
        rt = AutonomyRuntime(profile="TEST", world_model=wm)
        rt.start()

        # Patch _update_graph_from_result to be a no-op
        # so queries don't create user-type nodes
        rt._update_graph_from_result = lambda *a, **kw: None

        with caplog.at_level(logging.ERROR, logger="waggledance.autonomy.runtime"):
            for i in range(12):
                rt.handle_query(f"query {i}")

        health_errors = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and "user-created nodes" in r.message
        ]
        assert len(health_errors) >= 1
        rt.stop()


# ── Problem 2: Dream Mode non-inconclusive ───────────────────


class TestDreamModeNonInconclusive:
    def _make_trajectory(self, grade=QualityGrade.BRONZE, caps=None,
                         goal_desc="laske 2+2"):
        """Helper to create a CaseTrajectory for testing."""
        caps = caps or [
            CapabilityContract(
                capability_id="solve.math",
                category=CapabilityCategory.SOLVE,
            )
        ]
        return CaseTrajectory(
            goal=Goal(description=goal_desc),
            selected_capabilities=caps,
            quality_grade=grade,
            verifier_result={"passed": False},
            trajectory_origin="observed",
        )

    def test_dream_with_graph_produces_non_inconclusive(self, temp_graph):
        """Dream mode with graph data should produce non-inconclusive results."""
        from waggledance.core.world.graph_builder import GraphBuilder
        builder = GraphBuilder(temp_graph)

        # Seed graph with good alternative
        for _ in range(5):
            builder.record("q", "math", "solve.symbolic", True, "gold", "gold")
        builder.record("q", "math", "solve.math", False, "bronze", "bronze")

        traj = self._make_trajectory(QualityGrade.QUARANTINE)
        session = run_dream_session(
            day_trajectories=[traj],
            available_capabilities=["solve.math", "solve.symbolic", "solve.constraints"],
            graph_builder=builder,
        )

        assert session.simulations_run >= 1
        outcomes = [
            t.verifier_result.get("outcome") for t in session.simulated_trajectories
        ]
        # At least one should be non-inconclusive since graph has good data
        assert any(o != "inconclusive" for o in outcomes)

    def test_dream_without_graph_may_inconclusive(self):
        """Dream mode without graph builder may produce inconclusive results."""
        traj = self._make_trajectory(QualityGrade.QUARANTINE)
        session = run_dream_session(
            day_trajectories=[traj],
            available_capabilities=["solve.math", "solve.symbolic"],
            graph_builder=None,
        )
        assert session.simulations_run >= 1

    def test_simulated_trajectories_marked_synthetic(self, temp_graph):
        """All dream trajectories must be synthetic=True, origin=simulated."""
        from waggledance.core.world.graph_builder import GraphBuilder
        builder = GraphBuilder(temp_graph)
        builder.record("q", "math", "solve.symbolic", True, "gold", "gold")

        traj = self._make_trajectory(QualityGrade.BRONZE)
        session = run_dream_session(
            day_trajectories=[traj],
            available_capabilities=["solve.math", "solve.symbolic"],
            graph_builder=builder,
        )
        for st in session.simulated_trajectories:
            assert st.synthetic is True
            assert st.trajectory_origin == "simulated"
            assert st.verifier_result.get("simulated") is True


class TestInsightScore:
    def test_all_successes(self):
        """All successes should give positive score."""
        session = DreamSession(simulations_run=3)
        for _ in range(3):
            session.simulated_trajectories.append(
                CaseTrajectory(
                    verifier_result={"outcome": "success", "simulated": True},
                    trajectory_origin="simulated",
                    synthetic=True,
                    quality_grade=QualityGrade.BRONZE,
                )
            )
        score = compute_insight_score(session)
        assert score == pytest.approx(1.0)

    def test_all_failures(self):
        """All failures should give negative score."""
        session = DreamSession(simulations_run=3)
        for _ in range(3):
            session.simulated_trajectories.append(
                CaseTrajectory(
                    verifier_result={"outcome": "failure", "simulated": True},
                    trajectory_origin="simulated",
                    synthetic=True,
                    quality_grade=QualityGrade.BRONZE,
                )
            )
        score = compute_insight_score(session)
        assert score == pytest.approx(-1.0)

    def test_mixed_results(self):
        """Mixed results: 2 success, 1 failure, 1 inconclusive out of 4."""
        session = DreamSession(simulations_run=4)
        for outcome in ["success", "success", "failure", "inconclusive"]:
            session.simulated_trajectories.append(
                CaseTrajectory(
                    verifier_result={"outcome": outcome, "simulated": True},
                    trajectory_origin="simulated",
                    synthetic=True,
                    quality_grade=QualityGrade.BRONZE,
                )
            )
        score = compute_insight_score(session)
        # (2 - 1) / 4 = 0.25
        assert score == pytest.approx(0.25)

    def test_zero_simulations(self):
        """Zero simulations should return 0.0."""
        session = DreamSession(simulations_run=0)
        assert compute_insight_score(session) == 0.0

    def test_all_inconclusive(self):
        """All inconclusive should return 0.0."""
        session = DreamSession(simulations_run=2)
        for _ in range(2):
            session.simulated_trajectories.append(
                CaseTrajectory(
                    verifier_result={"outcome": "inconclusive", "simulated": True},
                    trajectory_origin="simulated",
                    synthetic=True,
                    quality_grade=QualityGrade.BRONZE,
                )
            )
        assert compute_insight_score(session) == 0.0


# ── Dream routing hints ──────────────────────────────────────


class TestDreamRoutingHints:
    def test_successful_dream_hints_applied_to_router(self):
        """SolverRouter should ingest successful dream hints."""
        router = SolverRouter()
        session = DreamSession(simulations_run=2)

        # One success, one failure
        session.simulated_trajectories.append(
            CaseTrajectory(
                verifier_result={
                    "outcome": "success",
                    "simulated": True,
                    "original_trajectory_id": "orig_1",
                },
                counterfactual_alternatives=["solve.symbolic"],
                trajectory_origin="simulated",
                synthetic=True,
                quality_grade=QualityGrade.BRONZE,
            )
        )
        session.simulated_trajectories.append(
            CaseTrajectory(
                verifier_result={
                    "outcome": "failure",
                    "simulated": True,
                    "original_trajectory_id": "orig_2",
                },
                counterfactual_alternatives=["solve.bad"],
                trajectory_origin="simulated",
                synthetic=True,
                quality_grade=QualityGrade.BRONZE,
            )
        )

        applied = router.apply_dream_hints(session)
        assert applied == 1  # only the success

        # Verify hint is in working memory
        wm_ctx = router._working_memory.as_context_dict()
        assert any("dream_hint" in k for k in wm_ctx)

    def test_no_hints_from_all_inconclusive(self):
        """No hints should be applied if all results are inconclusive."""
        router = SolverRouter()
        session = DreamSession(simulations_run=1)
        session.simulated_trajectories.append(
            CaseTrajectory(
                verifier_result={"outcome": "inconclusive", "simulated": True},
                counterfactual_alternatives=["solve.x"],
                trajectory_origin="simulated",
                synthetic=True,
                quality_grade=QualityGrade.BRONZE,
            )
        )
        applied = router.apply_dream_hints(session)
        assert applied == 0


# ── Problem 3: WorldModel rejects None graph in autonomy mode ─


class TestWorldModelAutonomyMode:
    def test_rejects_none_graph_in_autonomy_mode(self):
        """WorldModel should raise RuntimeError when graph=None and autonomy_mode=True."""
        with pytest.raises(RuntimeError, match="CognitiveGraph required"):
            WorldModel(cognitive_graph=None, profile="TEST", autonomy_mode=True)

    def test_accepts_none_graph_in_compatibility_mode(self):
        """WorldModel should accept graph=None when autonomy_mode=False (default)."""
        wm = WorldModel(cognitive_graph=None, profile="TEST", autonomy_mode=False)
        assert wm.graph is None

    def test_accepts_graph_in_autonomy_mode(self, temp_graph):
        """WorldModel should accept a valid graph in autonomy mode."""
        wm = WorldModel(cognitive_graph=temp_graph, profile="TEST", autonomy_mode=True)
        assert wm.graph is not None

    def test_default_mode_is_not_autonomy(self):
        """Default WorldModel (no autonomy_mode) should not reject None graph."""
        wm = WorldModel(cognitive_graph=None, profile="TEST")
        assert wm.graph is None


class TestValidateCutoverAutonomyGuard:
    def test_autonomy_mode_guard_check(self):
        """validate_cutover should include autonomy mode graph guard."""
        from waggledance.tools.validate_cutover import check_autonomy_mode_graph
        ok, msg = check_autonomy_mode_graph()
        assert ok is True
        assert "correctly rejects" in msg
