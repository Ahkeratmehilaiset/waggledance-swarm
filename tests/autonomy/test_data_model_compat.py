"""
Data model compatibility tests — verify domain models match v3.0 spec.

Checks all enum values, transitions, defaults, and computed fields
against the WAGGLEDANCE_FULL_AUTONOMY_MEGA_SPEC_v3.0.md requirements.
"""

from __future__ import annotations

import pytest

from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalStatus,
    GoalType,
    QualityGrade,
    SourceType,
    WorldSnapshot,
)


# ── SourceType ────────────────────────────────────────────────

class TestSourceTypeAllSpecValues:
    EXPECTED = {
        "observed",
        "inferred_by_solver",
        "inferred_by_stats",
        "inferred_by_rule",
        "proposed_by_llm",
        "confirmed_by_verifier",
        "learned_from_case",
    }

    def test_source_type_all_spec_values(self):
        """All 7 SourceType values from the spec are present."""
        actual = {st.value for st in SourceType}
        assert actual == self.EXPECTED
        assert len(SourceType) == 7


# ── QualityGrade ──────────────────────────────────────────────

class TestQualityGradeAllSpecValues:
    EXPECTED = {"gold", "silver", "bronze", "quarantine"}

    def test_quality_grade_all_spec_values(self):
        """All 4 QualityGrade values from the spec are present."""
        actual = {qg.value for qg in QualityGrade}
        assert actual == self.EXPECTED
        assert len(QualityGrade) == 4


# ── CaseTrajectory grading ───────────────────────────────────

class TestCaseTrajectoryAutoGrading:
    def test_gold_grade(self):
        """Solver + verifier → GOLD."""
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract(capability_id="solver.math", category=CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": True, "conflict": False, "hallucination": False},
        )
        assert ct.grade() == QualityGrade.GOLD

    def test_silver_grade_solver_only(self):
        """Solver without verifier → SILVER."""
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract(capability_id="solver.math", category=CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": False, "conflict": False, "hallucination": False},
        )
        assert ct.grade() == QualityGrade.SILVER

    def test_silver_grade_verifier_only(self):
        """Verifier without solver → SILVER."""
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract(capability_id="llm.generate", category=CapabilityCategory.EXPLAIN),
            ],
            verifier_result={"passed": True, "conflict": False, "hallucination": False},
        )
        assert ct.grade() == QualityGrade.SILVER

    def test_bronze_grade(self):
        """No solver, no verifier → BRONZE."""
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract(capability_id="llm.generate", category=CapabilityCategory.EXPLAIN),
            ],
            verifier_result={"passed": False, "conflict": False, "hallucination": False},
        )
        assert ct.grade() == QualityGrade.BRONZE

    def test_quarantine_conflict(self):
        """Conflict flag → QUARANTINE (overrides solver+verifier)."""
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract(capability_id="solver.math", category=CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": True, "conflict": True, "hallucination": False},
        )
        assert ct.grade() == QualityGrade.QUARANTINE

    def test_quarantine_hallucination(self):
        """Hallucination flag → QUARANTINE."""
        ct = CaseTrajectory(
            verifier_result={"passed": False, "conflict": False, "hallucination": True},
        )
        assert ct.grade() == QualityGrade.QUARANTINE


# ── WorldSnapshot ─────────────────────────────────────────────

class TestWorldSnapshotSourceTypeDefault:
    def test_default_source_type(self):
        """Default source_type is OBSERVED."""
        ws = WorldSnapshot()
        assert ws.source_type == SourceType.OBSERVED


class TestWorldSnapshotComputeResidual:
    def test_compute_residual(self):
        """Residual = current - baseline."""
        ws = WorldSnapshot(baselines={"hive_1.temp": 35.0})
        residual = ws.compute_residual("hive_1.temp", 37.5)
        assert abs(residual - 2.5) < 1e-9
        assert abs(ws.residuals["hive_1.temp"] - 2.5) < 1e-9

    def test_compute_residual_missing_baseline(self):
        """Missing baseline defaults to 0."""
        ws = WorldSnapshot()
        residual = ws.compute_residual("new.metric", 10.0)
        assert abs(residual - 10.0) < 1e-9


# ── Goal transitions ─────────────────────────────────────────

class TestGoalValidTransitions:
    VALID_PATHS = [
        (GoalStatus.PROPOSED, GoalStatus.ACCEPTED),
        (GoalStatus.PROPOSED, GoalStatus.ARCHIVED),
        (GoalStatus.ACCEPTED, GoalStatus.PLANNED),
        (GoalStatus.ACCEPTED, GoalStatus.FAILED),
        (GoalStatus.ACCEPTED, GoalStatus.ARCHIVED),
        (GoalStatus.PLANNED, GoalStatus.EXECUTING),
        (GoalStatus.PLANNED, GoalStatus.FAILED),
        (GoalStatus.PLANNED, GoalStatus.ARCHIVED),
        (GoalStatus.EXECUTING, GoalStatus.VERIFIED),
        (GoalStatus.EXECUTING, GoalStatus.FAILED),
        (GoalStatus.EXECUTING, GoalStatus.ROLLED_BACK),
        (GoalStatus.VERIFIED, GoalStatus.ARCHIVED),
        (GoalStatus.FAILED, GoalStatus.ARCHIVED),
        (GoalStatus.FAILED, GoalStatus.PROPOSED),  # retry
        (GoalStatus.ROLLED_BACK, GoalStatus.ARCHIVED),
        (GoalStatus.ROLLED_BACK, GoalStatus.PROPOSED),  # retry
    ]

    @pytest.mark.parametrize("from_status,to_status", VALID_PATHS)
    def test_goal_valid_transitions(self, from_status, to_status):
        """All spec-defined transitions succeed."""
        goal = Goal(status=from_status)
        goal.transition_to(to_status)
        assert goal.status == to_status


class TestGoalInvalidTransitionRaises:
    INVALID_PATHS = [
        (GoalStatus.PROPOSED, GoalStatus.EXECUTING),
        (GoalStatus.PROPOSED, GoalStatus.VERIFIED),
        (GoalStatus.ACCEPTED, GoalStatus.VERIFIED),
        (GoalStatus.EXECUTING, GoalStatus.ACCEPTED),
        (GoalStatus.VERIFIED, GoalStatus.EXECUTING),
        (GoalStatus.ARCHIVED, GoalStatus.PROPOSED),
        (GoalStatus.ARCHIVED, GoalStatus.ACCEPTED),
    ]

    @pytest.mark.parametrize("from_status,to_status", INVALID_PATHS)
    def test_goal_invalid_transition_raises(self, from_status, to_status):
        """Invalid transitions raise ValueError."""
        goal = Goal(status=from_status)
        with pytest.raises(ValueError, match="Invalid transition"):
            goal.transition_to(to_status)


# ── Memory tiers importable ──────────────────────────────────

class TestMemoryTiersImportable:
    def test_all_four_memory_tiers_importable(self):
        """All 4 memory tiers are importable."""
        from waggledance.core.memory.working_memory import WorkingMemory
        from waggledance.core.learning.procedural_memory import ProceduralMemory
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
        from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory

        assert WorkingMemory is not None
        assert ProceduralMemory is not None
        assert ChromaVectorStore is not None
        assert SQLiteSharedMemory is not None
