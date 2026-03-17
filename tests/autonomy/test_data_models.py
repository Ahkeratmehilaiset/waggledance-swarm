"""
Phase 2 Tests: Autonomy Core Data Models.

Tests cover:
- Goal creation, status transitions, invalid transitions
- WorldSnapshot residual computation
- RiskScore composite calculation, approval threshold
- CapabilityContract creation
- Action lifecycle (execute, deny, rollback)
- Plan progress tracking
- CaseTrajectory auto-grading (gold/silver/bronze/quarantine)
- Serialization (to_dict)
- Pydantic DTO validation
- EventType autonomy extensions
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    Action,
    ActionStatus,
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalStatus,
    GoalType,
    Plan,
    PlanStep,
    QualityGrade,
    RiskScore,
    SourceType,
    WorldSnapshot,
)
from waggledance.core.domain.events import EventType


# ── Goal ─────────────────────────────────────────────────────

class TestGoal:
    def test_create_default(self):
        g = Goal()
        assert g.type == GoalType.OBSERVE
        assert g.status == GoalStatus.PROPOSED
        assert g.priority == 50
        assert len(g.goal_id) == 12

    def test_create_with_params(self):
        g = Goal(type=GoalType.OPTIMIZE, description="Reduce heating cost",
                 priority=80, profile="HOME")
        assert g.type == GoalType.OPTIMIZE
        assert g.description == "Reduce heating cost"
        assert g.profile == "HOME"

    def test_valid_transition(self):
        g = Goal()
        g.transition_to(GoalStatus.ACCEPTED)
        assert g.status == GoalStatus.ACCEPTED
        g.transition_to(GoalStatus.PLANNED)
        assert g.status == GoalStatus.PLANNED

    def test_invalid_transition_raises(self):
        g = Goal()
        with pytest.raises(ValueError, match="Invalid transition"):
            g.transition_to(GoalStatus.EXECUTING)

    def test_full_lifecycle(self):
        g = Goal()
        g.transition_to(GoalStatus.ACCEPTED)
        g.transition_to(GoalStatus.PLANNED)
        g.transition_to(GoalStatus.EXECUTING)
        g.transition_to(GoalStatus.VERIFIED)
        g.transition_to(GoalStatus.ARCHIVED)
        assert g.status == GoalStatus.ARCHIVED

    def test_failed_can_retry(self):
        g = Goal()
        g.transition_to(GoalStatus.ACCEPTED)
        g.transition_to(GoalStatus.FAILED)
        g.transition_to(GoalStatus.PROPOSED)  # retry
        assert g.status == GoalStatus.PROPOSED

    def test_archived_is_terminal(self):
        g = Goal()
        g.transition_to(GoalStatus.ACCEPTED)
        g.transition_to(GoalStatus.PLANNED)
        g.transition_to(GoalStatus.EXECUTING)
        g.transition_to(GoalStatus.VERIFIED)
        g.transition_to(GoalStatus.ARCHIVED)
        with pytest.raises(ValueError):
            g.transition_to(GoalStatus.PROPOSED)

    def test_to_dict(self):
        g = Goal(type=GoalType.DIAGNOSE, description="Check varroa")
        d = g.to_dict()
        assert d["type"] == "diagnose"
        assert d["status"] == "proposed"
        assert "goal_id" in d
        assert "created_at" in d


# ── WorldSnapshot ────────────────────────────────────────────

class TestWorldSnapshot:
    def test_create_default(self):
        ws = WorldSnapshot()
        assert ws.entities == {}
        assert ws.baselines == {}
        assert ws.residuals == {}

    def test_compute_residual(self):
        ws = WorldSnapshot(baselines={"temp.hive_1": 35.0})
        r = ws.compute_residual("temp.hive_1", 37.5)
        assert r == pytest.approx(2.5)
        assert ws.residuals["temp.hive_1"] == pytest.approx(2.5)

    def test_compute_residual_no_baseline(self):
        ws = WorldSnapshot()
        r = ws.compute_residual("unknown_metric", 10.0)
        assert r == pytest.approx(10.0)

    def test_to_dict(self):
        ws = WorldSnapshot(
            entities={"hive_1": {"type": "beehive"}},
            baselines={"temp": 35.0},
            profile="COTTAGE",
        )
        d = ws.to_dict()
        assert d["profile"] == "COTTAGE"
        assert d["entities"]["hive_1"]["type"] == "beehive"
        assert d["source_type"] == "observed"


# ── RiskScore ────────────────────────────────────────────────

class TestRiskScore:
    def test_safe_defaults(self):
        rs = RiskScore()
        assert rs.composite == pytest.approx(0.0)
        assert rs.requires_approval is False

    def test_high_risk(self):
        rs = RiskScore(severity=1.0, reversibility=0.0, observability=0.0,
                       uncertainty=1.0, blast_radius=1.0)
        assert rs.composite == pytest.approx(1.0)
        assert rs.requires_approval is True

    def test_moderate_risk(self):
        rs = RiskScore(severity=0.5, reversibility=0.5, uncertainty=0.3)
        assert 0.2 < rs.composite < 0.5

    def test_explicit_approval(self):
        rs = RiskScore(approval_required=True)
        assert rs.requires_approval is True

    def test_to_dict(self):
        rs = RiskScore(severity=0.3, blast_radius=0.2)
        d = rs.to_dict()
        assert "composite" in d
        assert "approval_required" in d


# ── CapabilityContract ───────────────────────────────────────

class TestCapabilityContract:
    def test_create(self):
        cc = CapabilityContract(
            capability_id="solve.math",
            category=CapabilityCategory.SOLVE,
            description="Mathematical solver",
            preconditions=["numbers_present"],
            success_criteria=["result_verified"],
        )
        assert cc.capability_id == "solve.math"
        assert cc.category == CapabilityCategory.SOLVE
        assert cc.rollback_possible is True

    def test_to_dict(self):
        cc = CapabilityContract(
            capability_id="sense.mqtt_ingest",
            category=CapabilityCategory.SENSE,
        )
        d = cc.to_dict()
        assert d["category"] == "sense"


# ── Action ───────────────────────────────────────────────────

class TestAction:
    def test_create_default(self):
        a = Action()
        assert a.status == ActionStatus.DRY_RUN
        assert a.executed_at is None

    def test_mark_executed(self):
        a = Action(capability_id="solve.math")
        a.mark_executed({"answer": 42})
        assert a.status == ActionStatus.EXECUTED
        assert a.result["answer"] == 42
        assert a.executed_at is not None

    def test_mark_denied(self):
        a = Action()
        a.mark_denied("Policy violation")
        assert a.status == ActionStatus.DENIED
        assert a.error == "Policy violation"

    def test_mark_rolled_back(self):
        a = Action()
        a.status = ActionStatus.EXECUTED
        a.mark_rolled_back("Verifier failed")
        assert a.status == ActionStatus.ROLLED_BACK

    def test_to_dict(self):
        a = Action(capability_id="solve.constraints", goal_id="abc123")
        d = a.to_dict()
        assert d["capability_id"] == "solve.constraints"
        assert d["risk_score"]["composite"] == 0.0


# ── Plan ─────────────────────────────────────────────────────

class TestPlan:
    def test_empty_plan(self):
        p = Plan()
        assert p.progress == 0.0
        assert p.is_complete is False

    def test_progress(self):
        steps = [PlanStep(order=i, completed=(i < 2)) for i in range(4)]
        p = Plan(steps=steps)
        assert p.progress == pytest.approx(0.5)
        assert p.is_complete is False

    def test_complete_plan(self):
        steps = [PlanStep(order=i, completed=True) for i in range(3)]
        p = Plan(steps=steps)
        assert p.is_complete is True
        assert p.progress == pytest.approx(1.0)

    def test_to_dict(self):
        p = Plan(goal_id="goal_abc", steps=[PlanStep(order=0)])
        d = p.to_dict()
        assert d["goal_id"] == "goal_abc"
        assert len(d["steps"]) == 1


# ── CaseTrajectory ───────────────────────────────────────────

class TestCaseTrajectory:
    def test_default_grade_bronze(self):
        ct = CaseTrajectory()
        assert ct.quality_grade == QualityGrade.BRONZE

    def test_grade_gold(self):
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract("solve.math", CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": True},
        )
        grade = ct.grade()
        assert grade == QualityGrade.GOLD

    def test_grade_silver_solver_no_verifier(self):
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract("solve.constraints", CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": False},
        )
        grade = ct.grade()
        assert grade == QualityGrade.SILVER

    def test_grade_silver_verifier_no_solver(self):
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract("explain.llm", CapabilityCategory.EXPLAIN),
            ],
            verifier_result={"passed": True},
        )
        grade = ct.grade()
        assert grade == QualityGrade.SILVER

    def test_grade_bronze_llm_only(self):
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract("explain.llm", CapabilityCategory.EXPLAIN),
            ],
            verifier_result={"passed": False},
        )
        grade = ct.grade()
        assert grade == QualityGrade.BRONZE

    def test_grade_quarantine_conflict(self):
        ct = CaseTrajectory(
            selected_capabilities=[
                CapabilityContract("solve.math", CapabilityCategory.SOLVE),
            ],
            verifier_result={"passed": True, "conflict": True},
        )
        grade = ct.grade()
        assert grade == QualityGrade.QUARANTINE

    def test_grade_quarantine_hallucination(self):
        ct = CaseTrajectory(
            verifier_result={"hallucination": True},
        )
        grade = ct.grade()
        assert grade == QualityGrade.QUARANTINE

    def test_to_dict(self):
        ct = CaseTrajectory(
            goal=Goal(type=GoalType.DIAGNOSE, description="Varroa check"),
            canonical_id="domain.apiary.disease",
            profile="COTTAGE",
        )
        d = ct.to_dict()
        assert d["goal"]["type"] == "diagnose"
        assert d["canonical_id"] == "domain.apiary.disease"
        assert d["quality_grade"] == "bronze"


# ── Enums ────────────────────────────────────────────────────

class TestEnums:
    def test_goal_type_values(self):
        assert GoalType.OBSERVE.value == "observe"
        assert GoalType.PROTECT.value == "protect"

    def test_quality_grade_values(self):
        assert QualityGrade.GOLD.value == "gold"
        assert QualityGrade.QUARANTINE.value == "quarantine"

    def test_source_type_values(self):
        assert SourceType.OBSERVED.value == "observed"
        assert SourceType.PROPOSED_BY_LLM.value == "proposed_by_llm"

    def test_capability_category_values(self):
        assert CapabilityCategory.SOLVE.value == "solve"
        assert CapabilityCategory.RETRIEVE.value == "retrieve"


# ── EventType Extensions ────────────────────────────────────

class TestEventTypeExtensions:
    def test_autonomy_events_exist(self):
        assert EventType.GOAL_PROPOSED.value == "goal_proposed"
        assert EventType.ACTION_EXECUTED.value == "action_executed"
        assert EventType.POLICY_CHECK.value == "policy_check"
        assert EventType.VERIFICATION_PASSED.value == "verification_passed"
        assert EventType.CASE_RECORDED.value == "case_recorded"
        assert EventType.SPECIALIST_PROMOTED.value == "specialist_promoted"

    def test_legacy_events_unchanged(self):
        assert EventType.TASK_RECEIVED.value == "task_received"
        assert EventType.MEMORY_STORED.value == "memory_stored"
        assert EventType.APP_SHUTDOWN.value == "app_shutdown"


# ── Pydantic DTO Validation ─────────────────────────────────

class TestPydanticDTOs:
    def test_goal_create_valid(self):
        from waggledance.application.dto.autonomy_dto import GoalCreate
        gc = GoalCreate(type="optimize", description="Reduce cost", priority=70)
        assert gc.type == "optimize"

    def test_goal_create_invalid_type(self):
        from waggledance.application.dto.autonomy_dto import GoalCreate
        with pytest.raises(Exception):
            GoalCreate(type="invalid_type", description="test")

    def test_goal_create_empty_description(self):
        from waggledance.application.dto.autonomy_dto import GoalCreate
        with pytest.raises(Exception):
            GoalCreate(type="observe", description="")

    def test_goal_create_priority_bounds(self):
        from waggledance.application.dto.autonomy_dto import GoalCreate
        with pytest.raises(Exception):
            GoalCreate(type="observe", description="test", priority=0)
        with pytest.raises(Exception):
            GoalCreate(type="observe", description="test", priority=101)

    def test_goal_status_update_valid(self):
        from waggledance.application.dto.autonomy_dto import GoalStatusUpdate
        gs = GoalStatusUpdate(new_status="accepted")
        assert gs.new_status == "accepted"

    def test_goal_status_update_invalid(self):
        from waggledance.application.dto.autonomy_dto import GoalStatusUpdate
        with pytest.raises(Exception):
            GoalStatusUpdate(new_status="invalid_status")

    def test_action_create(self):
        from waggledance.application.dto.autonomy_dto import ActionCreate
        ac = ActionCreate(capability_id="solve.math", payload={"x": 5})
        assert ac.capability_id == "solve.math"
