# SPDX-License-Identifier: Apache-2.0
"""
User Model Lite (v3.3) tests.

Tests cover:
- CognitiveGraph user entity CRUD + semantic edge + stats
- WorldModel user entity delegates
- GoalEngine get_promises_to_user()
- Runtime seeding and update wiring
- Verification failure tracking
- Narrative projector user_entity parameter
- Morning report unfulfilled_promises
- Night learning pipeline promise pass-through
- Hologram user_model + MAGMA nodes
- AutonomyService get_user_model() API shape
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cognitive_graph import CognitiveGraph
from waggledance.core.domain.autonomy import Goal, GoalStatus, GoalType
from waggledance.core.goals.goal_engine import GoalEngine
from waggledance.core.learning.morning_report import MorningReport, MorningReportBuilder
from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
from waggledance.core.projections.narrative_projector import clear_cache, project_narrative
from waggledance.core.world.world_model import WorldModel


# ── Helpers ──────────────────────────────────────────────


def _make_goal(promise=False, status=GoalStatus.EXECUTING, **kw):
    g = Goal(type=GoalType.DIAGNOSE, description="test goal", **kw)
    g.promise_to_user = promise
    g.status = status
    return g


# ── TestUserEntityCognitiveGraph ─────────────────────────


class TestUserEntityCognitiveGraph:
    def test_ensure_creates_with_defaults(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        result = cg.ensure_user_entity()
        assert result["id"] == "user:primary"
        assert result["entity_type"] == "user"
        assert result["interaction_count"] == 0
        assert result["preferred_language"] == ""
        assert result["explicit_correction_count"] == 0
        assert result["verification_fail_count"] == 0
        assert result["last_interaction_at"] == 0.0
        assert result["identity_version"] == "v3.3-user-model-lite"

    def test_get_returns_none_when_absent(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        assert cg.get_user_entity() is None

    def test_update_merges_attrs(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        cg.ensure_user_entity()
        cg.update_user_entity(interaction_count=5, preferred_language="fi")
        ue = cg.get_user_entity()
        assert ue["interaction_count"] == 5
        assert ue["preferred_language"] == "fi"
        # Untouched fields remain
        assert ue["explicit_correction_count"] == 0

    def test_semantic_edge_self_to_user(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        cg.ensure_self_entity()
        cg.ensure_user_entity()
        assert cg.graph.has_edge("self", "user:primary")
        edge_data = cg.graph.edges["self", "user:primary"]
        assert edge_data["link_type"] == "semantic"
        assert edge_data["relation"] == "serves"

    def test_stats_includes_has_user_entity(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        assert cg.stats()["has_user_entity"] is False
        cg.ensure_user_entity()
        assert cg.stats()["has_user_entity"] is True


# ── TestUserEntityWorldModel ─────────────────────────────


class TestUserEntityWorldModel:
    def test_delegates_work(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        result = wm.ensure_user_entity()
        assert result is not None
        assert result["entity_type"] == "user"
        assert wm.get_user_entity() is not None

    def test_returns_none_when_no_graph(self):
        wm = WorldModel(cognitive_graph=None)
        assert wm.ensure_user_entity() is None
        assert wm.get_user_entity() is None
        assert wm.update_user_entity(interaction_count=1) is None

    def test_update_delegates(self, tmp_path):
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        wm.ensure_user_entity()
        result = wm.update_user_entity(interaction_count=3)
        assert result["interaction_count"] == 3


# ── TestGoalEnginePromises ───────────────────────────────


class TestGoalEnginePromises:
    def test_filters_promise_to_user(self):
        ge = GoalEngine()
        g1 = ge.propose("diagnose", "fix sensor")
        g1.promise_to_user = True
        ge.accept(g1.goal_id)
        g2 = ge.propose("observe", "watch temp")
        g2.promise_to_user = False

        promises = ge.get_promises_to_user()
        assert len(promises) == 1
        assert promises[0].goal_id == g1.goal_id

    def test_excludes_all_terminal_states(self):
        ge = GoalEngine()
        for status_name in ("VERIFIED", "FAILED", "ROLLED_BACK", "ARCHIVED"):
            g = ge.propose("diagnose", f"goal-{status_name}")
            g.promise_to_user = True
            g.status = GoalStatus[status_name]
        assert ge.get_promises_to_user() == []

    def test_returns_empty_when_none(self):
        ge = GoalEngine()
        assert ge.get_promises_to_user() == []


# ── TestRuntimeSeedsUser ─────────────────────────────────


class TestRuntimeSeedsUser:
    def test_user_entity_present_after_init(self, tmp_path):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        rt = AutonomyRuntime(world_model=wm, profile="TEST")
        ue = rt.world_model.get_user_entity()
        assert ue is not None
        assert ue["entity_type"] == "user"

    def test_has_expected_default_fields(self, tmp_path):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        rt = AutonomyRuntime(world_model=wm, profile="TEST")
        ue = rt.world_model.get_user_entity()
        assert ue["preferred_language"] == ""
        assert ue["interaction_count"] == 0
        assert ue["verification_fail_count"] == 0
        assert ue["pending_promise_goal_ids"] == []


# ── TestRuntimeUpdatesUser ───────────────────────────────


class TestRuntimeUpdatesUser:
    """Test that handle_query updates the user entity."""

    def _make_runtime(self, tmp_path):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        rt = AutonomyRuntime(world_model=wm, profile="TEST")
        rt.start()
        return rt

    def test_interaction_count_increments(self, tmp_path):
        rt = self._make_runtime(tmp_path)
        rt.handle_query("hello")
        rt.handle_query("world")
        ue = rt.world_model.get_user_entity()
        assert ue["interaction_count"] == 2
        rt.stop()

    def test_promise_ids_synced(self, tmp_path):
        rt = self._make_runtime(tmp_path)
        g = rt.goal_engine.propose("diagnose", "fix sensor")
        g.promise_to_user = True
        rt.goal_engine.accept(g.goal_id)
        rt.handle_query("check sensor")
        ue = rt.world_model.get_user_entity()
        assert g.goal_id in ue["pending_promise_goal_ids"]
        rt.stop()

    def test_last_interaction_at_set(self, tmp_path):
        rt = self._make_runtime(tmp_path)
        before = time.time()
        rt.handle_query("test")
        ue = rt.world_model.get_user_entity()
        assert ue["last_interaction_at"] >= before
        rt.stop()


# ── TestVerificationFailTracking ─────────────────────────


class TestVerificationFailTracking:
    def test_verification_fail_count_increments(self, tmp_path):
        """Direct unit test: update_user_entity correctly increments vfail."""
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        cg.ensure_user_entity()
        ue = cg.get_user_entity()
        assert ue["verification_fail_count"] == 0
        # Simulate what runtime does on verification failure
        cg.update_user_entity(
            verification_fail_count=ue["verification_fail_count"] + 1,
        )
        ue = cg.get_user_entity()
        assert ue["verification_fail_count"] == 1

    def test_does_not_increment_correction_count(self, tmp_path):
        """Verification failure only touches vfail, not correction count."""
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        cg.ensure_user_entity()
        # Simulate vfail increment (same as runtime logic)
        ue = cg.get_user_entity()
        cg.update_user_entity(
            verification_fail_count=ue["verification_fail_count"] + 1,
        )
        ue = cg.get_user_entity()
        assert ue["explicit_correction_count"] == 0
        assert ue["verification_fail_count"] == 1


# ── TestNarrativeProjectorUser ───────────────────────────


class TestNarrativeProjectorUser:
    def setup_method(self):
        clear_cache()

    def test_user_section_renders_in_narrative(self):
        result = project_narrative(
            user_entity={
                "interaction_count": 10,
                "explicit_correction_count": 2,
                "pending_promise_goal_ids": ["g1"],
            },
            promises=[],
        )
        assert "User model:" in result["narrative"]
        assert "Interactions: 10" in result["narrative"]
        assert "Corrections: 2" in result["narrative"]

    def test_sections_dict_contains_user_entity(self):
        result = project_narrative(
            user_entity={"interaction_count": 5, "explicit_correction_count": 1},
        )
        ue_section = result["sections"]["user_entity"]
        assert ue_section["interaction_count"] == 5
        assert ue_section["corrections"] == 1

    def test_promises_empty_overrides_cached_ids(self):
        """When promises=[] and user has cached IDs, pending count should be 0."""
        result = project_narrative(
            promises=[],
            user_entity={
                "interaction_count": 1,
                "explicit_correction_count": 0,
                "pending_promise_goal_ids": ["g1", "g2"],
            },
        )
        assert result["sections"]["user_entity"]["pending_promises"] == 0
        assert "Pending promises: 0" in result["narrative"]

    def test_promises_none_uses_cached_ids(self):
        """When promises=None and user has cached IDs, use cached count."""
        clear_cache()
        result = project_narrative(
            promises=None,
            user_entity={
                "interaction_count": 1,
                "explicit_correction_count": 0,
                "pending_promise_goal_ids": ["g1", "g2", "g3"],
            },
        )
        assert result["sections"]["user_entity"]["pending_promises"] == 3


# ── TestMorningReportPromises ────────────────────────────


class TestMorningReportPromises:
    def test_unfulfilled_promises_in_dict(self):
        report = MorningReport()
        report.unfulfilled_promises = ["fix sensor", "update firmware"]
        d = report.to_dict()
        assert d["unfulfilled_promises"] == ["fix sensor", "update firmware"]

    def test_summary_text_includes_promises(self):
        report = MorningReport()
        report.unfulfilled_promises = ["fix sensor", "update firmware"]
        text = report.summary_text()
        assert "Unfulfilled promises: 2" in text
        assert "fix sensor" in text


# ── TestMorningReportCallerChain ─────────────────────────


class TestMorningReportCallerChain:
    def test_pipeline_propagates_unfulfilled_promises(self):
        pipeline = NightLearningPipeline(profile="TEST")
        result = pipeline.run_cycle(unfulfilled_promises=["promise A", "promise B"])
        assert result.report is not None
        assert result.report.unfulfilled_promises == ["promise A", "promise B"]


# ── TestHologramUserNode ─────────────────────────────────


class TestHologramUserNode:
    def test_user_model_and_magma_nodes_in_state(self, tmp_path):
        from waggledance.adapters.http.routes.hologram import build_hologram_state

        # Create mock service with running runtime
        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        wm.ensure_user_entity(interaction_count=10)

        mock_cc = MagicMock()
        mock_cc.get_all.return_value = {"solve.general": 0.8}

        # Build mock runtime with proper stats for all components
        mock_runtime = MagicMock()
        mock_runtime.is_running = True
        mock_runtime.world_model = wm
        mock_runtime.capability_confidence = mock_cc
        mock_runtime.resource_kernel.stats.return_value = {"active_tasks": 1}
        mock_runtime.case_builder.stats.return_value = {"total": 5}
        mock_runtime.working_memory.stats.return_value = {"size": 2, "capacity": 10}
        mock_runtime.solver_router.stats.return_value = {"total": 10, "quality_distribution": {"gold": 5}}
        mock_runtime.verifier.stats.return_value = {"pass_rate": 0.8}
        mock_runtime.stats.return_value = {
            "magma_audit": {"total_entries": 50},
            "magma_trust": {"total_observations": 25},
            "magma_event_log": {"total_entries": 30},
            "magma_replay": {"total_missions": 5},
            "magma_provenance": {"total_entries": 10},
        }

        mock_service = MagicMock()
        mock_service._runtime = mock_runtime
        mock_service._resource_kernel.stats.return_value = {"active_tasks": 0}

        state = build_hologram_state(mock_service)
        nodes = state["nodes"]
        assert "user_model" in nodes
        assert nodes["user_model"] == round(min(1.0, 10 / 50.0), 2)
        assert "magma_audit" in nodes
        assert "magma_trust" in nodes
        assert "magma_event_log" in nodes
        assert "magma_replay" in nodes
        assert "magma_provenance" in nodes

    def test_zero_state_includes_all_new_nodes(self):
        from waggledance.adapters.http.routes.hologram import build_hologram_state

        mock_service = MagicMock()
        mock_service._runtime = None
        state = build_hologram_state(mock_service)
        nodes = state["nodes"]
        for key in ("user_model", "magma_audit", "magma_trust",
                     "magma_event_log", "magma_replay", "magma_provenance"):
            assert key in nodes
            assert nodes[key] == 0.0


# ── TestUserModelAPI ─────────────────────────────────────


class TestUserModelAPI:
    def test_get_user_model_shape(self, tmp_path):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        cg = CognitiveGraph(persist_path=str(tmp_path / "cg.json"))
        wm = WorldModel(cognitive_graph=cg)
        rt = AutonomyRuntime(world_model=wm, profile="TEST")
        svc = AutonomyService(runtime=rt, profile="TEST")

        result = svc.get_user_model()
        assert result["available"] is True
        assert result["interaction_count"] == 0
        assert result["preferred_language"] == ""
        assert result["promises_pending"] == []
        assert result["verification_fail_count"] == 0

    def test_returns_unavailable_when_no_graph(self):
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        wm = WorldModel(cognitive_graph=None)
        rt = AutonomyRuntime(world_model=wm, profile="TEST")
        svc = AutonomyService(runtime=rt, profile="TEST")

        result = svc.get_user_model()
        assert result["available"] is False
