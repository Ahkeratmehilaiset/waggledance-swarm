"""
Phase 6 Tests: Goal Engine, Mission Store, Planner, Autonomy Runtime.

Tests cover:
- GoalEngine lifecycle (propose → accept → plan → execute → verify → archive)
- GoalEngine decomposition
- GoalEngine prioritization
- MissionStore persistence
- Planner plan creation from goal types
- Planner quality path estimation
- AutonomyRuntime handle_query
- AutonomyRuntime execute_mission
- Full lifecycle integration
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.domain.autonomy import (
    Goal,
    GoalStatus,
    GoalType,
    Plan,
    PlanStep,
)
from waggledance.core.goals.goal_engine import GoalEngine
from waggledance.core.goals.mission_store import MissionStore
from waggledance.core.planning.planner import Planner


# ── MissionStore ──────────────────────────────────────────────

class TestMissionStore:
    def test_save_and_load(self):
        store = MissionStore()
        goal = Goal(type=GoalType.OBSERVE, description="Check hive temp")
        store.save_goal(goal)
        loaded = store.load_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.goal_id == goal.goal_id
        assert loaded.type == GoalType.OBSERVE

    def test_load_nonexistent(self):
        store = MissionStore()
        assert store.load_goal("nonexistent") is None

    def test_list_goals(self):
        store = MissionStore()
        g1 = Goal(type=GoalType.OBSERVE, description="g1")
        g2 = Goal(type=GoalType.PROTECT, description="g2")
        store.save_goal(g1)
        store.save_goal(g2)
        assert store.count() == 2
        all_goals = store.list_goals()
        assert len(all_goals) == 2

    def test_list_by_status(self):
        store = MissionStore()
        g1 = Goal(type=GoalType.OBSERVE, description="g1")
        g2 = Goal(type=GoalType.PROTECT, description="g2")
        g2.transition_to(GoalStatus.ACCEPTED)
        store.save_goal(g1)
        store.save_goal(g2)
        proposed = store.list_goals(status="proposed")
        assert len(proposed) == 1

    def test_delete(self):
        store = MissionStore()
        goal = Goal(type=GoalType.OBSERVE, description="temp")
        store.save_goal(goal)
        assert store.delete(goal.goal_id) is True
        assert store.count() == 0

    def test_clear(self):
        store = MissionStore()
        store.save_goal(Goal(type=GoalType.OBSERVE, description="g1"))
        store.save_goal(Goal(type=GoalType.OBSERVE, description="g2"))
        store.clear()
        assert store.count() == 0

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "missions.json")
        store1 = MissionStore(persist_path=path)
        goal = Goal(type=GoalType.DIAGNOSE, description="diagnose varroa")
        store1.save_goal(goal)

        store2 = MissionStore(persist_path=path)
        loaded = store2.load_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.description == "diagnose varroa"


# ── GoalEngine ────────────────────────────────────────────────

class TestGoalEngine:
    @pytest.fixture
    def engine(self):
        return GoalEngine(profile="TEST")

    def test_propose(self, engine):
        goal = engine.propose("observe", "Check hive temperature")
        assert goal.status == GoalStatus.PROPOSED
        assert goal.type == GoalType.OBSERVE

    def test_full_lifecycle(self, engine):
        goal = engine.propose("observe", "Check temperature")
        engine.accept(goal.goal_id)
        assert goal.status == GoalStatus.ACCEPTED
        engine.mark_planned(goal.goal_id)
        assert goal.status == GoalStatus.PLANNED
        engine.start_execution(goal.goal_id)
        assert goal.status == GoalStatus.EXECUTING
        engine.mark_verified(goal.goal_id)
        assert goal.status == GoalStatus.VERIFIED
        engine.archive(goal.goal_id)
        assert goal.status == GoalStatus.ARCHIVED

    def test_failure_and_retry(self, engine):
        goal = engine.propose("act", "Do something")
        engine.accept(goal.goal_id)
        engine.mark_failed(goal.goal_id, "Step failed")
        assert goal.status == GoalStatus.FAILED
        engine.retry(goal.goal_id)
        assert goal.status == GoalStatus.PROPOSED

    def test_failure_and_rollback(self, engine):
        goal = engine.propose("act", "Critical action")
        engine.accept(goal.goal_id)
        engine.mark_planned(goal.goal_id)
        engine.start_execution(goal.goal_id)
        engine.rollback(goal.goal_id)
        assert goal.status == GoalStatus.ROLLED_BACK

    def test_invalid_transition_raises(self, engine):
        goal = engine.propose("observe", "Test")
        with pytest.raises(ValueError, match="Invalid transition"):
            engine.start_execution(goal.goal_id)  # Can't skip to EXECUTING

    def test_decompose_diagnose(self, engine):
        goal = engine.propose("diagnose", "Check varroa levels")
        sub_goals = engine.decompose(goal.goal_id)
        assert len(sub_goals) == 3  # observe, diagnose, verify
        assert sub_goals[0].type == GoalType.OBSERVE
        assert all(sg.parent_goal_id == goal.goal_id for sg in sub_goals)

    def test_decompose_optimize(self, engine):
        goal = engine.propose("optimize", "Reduce heating cost")
        sub_goals = engine.decompose(goal.goal_id)
        assert len(sub_goals) == 5  # observe, diagnose, plan, act, verify

    def test_decompose_atomic(self, engine):
        goal = engine.propose("observe", "Simple observation")
        sub_goals = engine.decompose(goal.goal_id)
        assert len(sub_goals) == 0  # No pattern for observe

    def test_prioritize(self, engine):
        g1 = engine.propose("observe", "Low priority", priority=20)
        g2 = engine.propose("protect", "High priority protection", priority=80)
        g3 = engine.propose("diagnose", "Medium priority", priority=50)
        ordered = engine.prioritize()
        # protect gets +20 boost, so g2 (80+20=100) > g3 (50+5=55) > g1 (20)
        assert ordered[0].goal_id == g2.goal_id
        assert ordered[-1].goal_id == g1.goal_id

    def test_active_goals(self, engine):
        g1 = engine.propose("observe", "Active")
        g2 = engine.propose("act", "To archive")
        engine.accept(g2.goal_id)
        engine.mark_planned(g2.goal_id)
        engine.start_execution(g2.goal_id)
        engine.mark_verified(g2.goal_id)
        engine.archive(g2.goal_id)
        active = engine.active_goals()
        assert len(active) == 1
        assert active[0].goal_id == g1.goal_id

    def test_goals_by_status(self, engine):
        engine.propose("observe", "g1")
        engine.propose("observe", "g2")
        g3 = engine.propose("act", "g3")
        engine.accept(g3.goal_id)
        proposed = engine.goals_by_status(GoalStatus.PROPOSED)
        assert len(proposed) == 2

    def test_get_nonexistent_raises(self, engine):
        with pytest.raises(KeyError):
            engine.accept("nonexistent")

    def test_count(self, engine):
        engine.propose("observe", "g1")
        engine.propose("observe", "g2")
        assert engine.count() == 2
        assert engine.count(GoalStatus.PROPOSED) == 2

    def test_stats(self, engine):
        engine.propose("observe", "g1")
        engine.propose("protect", "g2")
        s = engine.stats()
        assert s["total"] == 2
        assert s["profile"] == "TEST"


# ── Planner ───────────────────────────────────────────────────

class TestPlanner:
    def test_create_plan_observe(self):
        planner = Planner()
        goal = Goal(type=GoalType.OBSERVE, description="Check temperature")
        plan = planner.create_plan(goal)
        assert isinstance(plan, Plan)
        assert len(plan.steps) > 0
        assert plan.goal_id == goal.goal_id

    def test_create_plan_protect(self):
        planner = Planner()
        goal = Goal(type=GoalType.PROTECT, description="Frost protection")
        plan = planner.create_plan(goal)
        # detect, act, verify
        assert len(plan.steps) >= 2

    def test_create_plan_from_capabilities(self):
        planner = Planner()
        goal = Goal(type=GoalType.DIAGNOSE, description="Diagnose issue")
        plan = planner.create_plan_from_capabilities(
            goal, ["solve.math", "verify.hallucination"]
        )
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_id == "solve.math"

    def test_estimate_quality_gold(self):
        planner = Planner()
        goal = Goal(type=GoalType.DIAGNOSE, description="Test")
        plan = planner.create_plan_from_capabilities(
            goal, ["solve.math", "verify.hallucination"]
        )
        assert planner.estimate_quality_path(plan) == "gold"

    def test_estimate_quality_bronze(self):
        planner = Planner()
        goal = Goal(type=GoalType.OBSERVE, description="Test")
        plan = planner.create_plan_from_capabilities(
            goal, ["explain.llm_reasoning"]
        )
        assert planner.estimate_quality_path(plan) == "bronze"

    def test_plan_progress(self):
        steps = [PlanStep(order=i, completed=(i < 2)) for i in range(4)]
        plan = Plan(goal_id="test", steps=steps)
        assert plan.progress == pytest.approx(0.5)
        assert plan.is_complete is False

    def test_max_steps_limit(self):
        planner = Planner(max_steps=2)
        goal = Goal(type=GoalType.OPTIMIZE, description="Big goal")
        plan = planner.create_plan(goal)
        assert len(plan.steps) <= 2


# ── AutonomyRuntime ───────────────────────────────────────────

class TestAutonomyRuntime:
    @pytest.fixture
    def runtime(self):
        rt = AutonomyRuntime(profile="TEST")
        rt.start()
        return rt

    def test_create(self, runtime):
        assert runtime.is_running is True
        assert runtime.profile == "TEST"

    def test_handle_query_chat(self, runtime):
        result = runtime.handle_query("hello world")
        assert result["intent"] == "chat"
        assert "quality_path" in result
        assert "elapsed_ms" in result

    def test_handle_query_math(self, runtime):
        result = runtime.handle_query("laske 2 + 3")
        assert result["intent"] == "math"

    def test_execute_mission(self, runtime):
        result = runtime.execute_mission(
            goal_type="observe",
            description="Check hive temperature",
            priority=70,
        )
        assert "goal_id" in result
        assert "status" in result
        assert "quality_path" in result
        assert "plan_steps" in result

    def test_execute_mission_protect(self, runtime):
        result = runtime.execute_mission(
            goal_type="protect",
            description="Frost protection",
            priority=90,
        )
        assert result["plan_steps"] >= 2

    def test_stats(self, runtime):
        runtime.handle_query("test query")
        s = runtime.stats()
        assert s["profile"] == "TEST"
        assert "goals" in s
        assert "capabilities" in s
        assert "solver_router" in s

    def test_stop(self, runtime):
        runtime.stop()
        assert runtime.is_running is False
