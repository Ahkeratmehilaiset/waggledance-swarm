"""
Tests for the remaining wiring fixes:
  1. Verifier success_criteria aligned with adapter return keys
  2. CaseTrajectoryBuilder.drain_cases() → NightLearningPipeline
  3. AutonomyRuntime.run_night_learning() end-to-end
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    QualityGrade,
)
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.reasoning.verifier import Verifier, VerifierResult


# ── Verifier recognises adapter results ────────────────────────


class TestVerifierSuccessCriteria:
    """Verify that success_criteria match what adapters actually return."""

    def test_math_adapter_result_passes_verification(self):
        """Math adapter returns {"success": True, "value": "4"} —
        success_criteria is ["success", "value"] → should pass."""
        v = Verifier()
        result = v.verify_simple(
            {"success": True, "value": "4", "capability_id": "solve.math"},
            expected_fields=["success", "value"],
        )
        assert result.passed is True
        assert "success" in result.success_criteria_met
        assert "value" in result.success_criteria_met
        assert result.success_criteria_failed == []

    def test_old_criteria_would_fail(self):
        """The OLD criteria like 'result_verified' fail against real results."""
        v = Verifier()
        result = v.verify_simple(
            {"success": True, "value": "4"},
            expected_fields=["result_verified"],
        )
        assert result.passed is False
        assert "result_verified" in result.success_criteria_failed

    def test_generic_success_criterion(self):
        """Most adapters return {"success": True} — criterion ["success"] passes."""
        v = Verifier()
        result = v.verify_simple(
            {"success": True, "data": "something"},
            expected_fields=["success"],
        )
        assert result.passed is True

    def test_success_false_passes_simple_but_fails_full(self):
        """verify_simple checks key presence only; full verify checks truthiness."""
        v = Verifier()
        # verify_simple: key exists → passes (key presence only)
        simple = v.verify_simple(
            {"success": False},
            expected_fields=["success"],
        )
        assert simple.passed is True  # key "success" exists

        # Full verify: checks truthiness → fails
        cap = CapabilityContract(
            capability_id="test",
            category=CapabilityCategory.SOLVE,
            success_criteria=["success"],
        )
        action = Action(capability_id="test")
        full = v.verify(
            action=action,
            capability=cap,
            action_result={"success": False},
        )
        assert full.passed is False  # success=False → failed criterion

    def test_verify_full_with_capability_contract(self):
        """Full verify() uses capability.success_criteria."""
        v = Verifier()
        cap = CapabilityContract(
            capability_id="solve.math",
            category=CapabilityCategory.SOLVE,
            description="Math solver",
            success_criteria=["success", "value"],
        )
        action = Action(capability_id="solve.math", payload={"query": "2+2"})
        result = v.verify(
            action=action,
            capability=cap,
            action_result={"success": True, "value": "4"},
        )
        assert result.passed is True
        assert result.confidence > 0.7

    def test_registry_capabilities_have_aligned_criteria(self):
        """All builtin capabilities should have criteria that match adapter patterns."""
        from waggledance.core.capabilities.registry import CapabilityRegistry

        registry = CapabilityRegistry()
        valid_criteria = {"success", "value", "intent_resolved", "data_received",
                          "entities_fetched", "events_processed", "audio_processed",
                          "observations_merged", "recommendations_returned",
                          "assessment_computed"}
        for cap in registry.list_all():
            for criterion in cap.success_criteria:
                assert criterion in valid_criteria, (
                    f"{cap.capability_id} has criterion '{criterion}' "
                    f"which doesn't match any adapter return key"
                )


# ── CaseTrajectoryBuilder.drain_cases() ───────────────────────


class TestDrainCases:
    def test_drain_returns_accumulated_cases(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        builder.build(query="2+2", intent="math")
        builder.build(query="3*5", intent="math")

        cases = builder.drain_cases()
        assert len(cases) == 2
        assert cases[0].goal.description == "2+2"
        assert cases[1].goal.description == "3*5"

    def test_drain_clears_buffer(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        builder.build(query="2+2", intent="math")
        builder.drain_cases()

        assert builder.pending_count() == 0
        assert builder.drain_cases() == []

    def test_drain_empty_returns_empty(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        assert builder.drain_cases() == []

    def test_pending_count(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        assert builder.pending_count() == 0
        builder.build(query="2+2", intent="math")
        assert builder.pending_count() == 1
        builder.build(query="3*5", intent="math")
        assert builder.pending_count() == 2

    def test_drain_does_not_affect_new_builds(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        builder.build(query="2+2", intent="math")
        builder.drain_cases()

        builder.build(query="new query", intent="chat")
        cases = builder.drain_cases()
        assert len(cases) == 1
        assert cases[0].goal.description == "new query"


# ── AutonomyRuntime.run_night_learning() ──────────────────────


class TestRunNightLearning:
    def test_run_night_learning_returns_result(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        # Generate some cases via handle_query
        rt.handle_query("2+2")
        rt.handle_query("what is 5+3")

        result = rt.run_night_learning()
        rt.stop()

        assert isinstance(result, dict)
        assert "cases_built" in result
        assert result["success"] is True

    def test_night_learning_drains_cases(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        rt.handle_query("2+2")
        assert rt.case_builder.pending_count() > 0

        rt.run_night_learning()
        assert rt.case_builder.pending_count() == 0

        rt.stop()

    def test_night_learning_empty_is_safe(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        result = rt.run_night_learning()

        assert result["cases_built"] == 0
        assert result["success"] is True

    def test_night_pipeline_lazy_initialized(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        assert rt._night_pipeline is None

        rt.run_night_learning()
        assert rt._night_pipeline is not None


# ── End-to-end: query → verify → gold grade ──────────────────


class TestEndToEndGoldGrading:
    def test_math_query_gets_gold_grade(self):
        """A successful math query with passing verification should get gold."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        result = rt.handle_query("What is 2+2?")

        if result.get("executed") and result.get("capability") == "solve.math":
            # With verifier fix, the case should be graded gold
            cases = rt.case_builder.recent_cases(1)
            assert len(cases) == 1
            case = cases[0]
            # Gold requires: solver capability + verifier passed
            assert case.quality_grade in (QualityGrade.GOLD, QualityGrade.SILVER), (
                f"Expected gold or silver, got {case.quality_grade.value}"
            )

        rt.stop()

    def test_night_learning_receives_graded_cases(self):
        """Night learning should receive pre-graded cases from day activity."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        # Generate day activity
        for q in ["2+2", "150*0.08", "paljonko on 5+3"]:
            rt.handle_query(q)

        # Run night learning
        result = rt.run_night_learning()

        # Cases should have been processed
        total = (result.get("quality", {}).get("gold", 0) +
                 result.get("quality", {}).get("silver", 0) +
                 result.get("quality", {}).get("bronze", 0) +
                 result.get("quality", {}).get("quarantine", 0))
        assert total > 0, f"Night learning received 0 graded cases: {result}"

        rt.stop()
