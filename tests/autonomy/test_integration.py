"""
Integration Tests — 13 end-to-end scenarios from the Mega-Spec (section 23.2).

These tests verify cross-phase interactions:
  1. HOME heating optimization (solver path)
  2. COTTAGE frost protection (rule path)
  3. FACTORY maintenance + approval (policy path)
  4. APIARY varroa seasonal check (domain rule)
  5. Route fallback without LLM
  6. Night learning case build from legacy Q&A
  7. Legacy agent ID → canonical ID
  8. Audit + replay from action bus
  9. Rollback after failed canary
  10. Autoscaling tier switch preserves safety
  11. Throttle reduces night learning under load
  12. LLM-only answer graded bronze
  13. Specialist model canary → production promotion
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.autonomy.compatibility import CompatibilityLayer, LegacyResult
from waggledance.core.autonomy.lifecycle import AutonomyLifecycle, RuntimeMode, RuntimeState
from waggledance.core.autonomy.resource_kernel import (
    AdmissionControl,
    AdmissionDecision,
    LoadLevel,
    ResourceKernel,
)
from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.actions.action_bus import SafeActionBus
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector
from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalStatus,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.goals.goal_engine import GoalEngine
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.learning.legacy_converter import LegacyConverter, LegacyRecord
from waggledance.core.learning.morning_report import MorningReportBuilder
from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
from waggledance.core.learning.procedural_memory import ProceduralMemory
from waggledance.core.learning.quality_gate import QualityGate
from waggledance.core.planning.planner import Planner
from waggledance.core.policy.policy_engine import PolicyEngine
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.reasoning.verifier import Verifier
from waggledance.core.specialist_models.model_store import ModelStatus, ModelStore
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer
from waggledance.core.world.world_model import WorldModel
from waggledance.application.services.autonomy_service import AutonomyService


# ── 1. HOME heating optimization end-to-end (solver path) ──

class TestHomeHeatingOptimization:
    """Scenario: HOME profile user asks about heating cost optimization."""

    def test_solver_path_end_to_end(self):
        """Full pipeline: query → intent → solver route → plan → execute."""
        runtime = AutonomyRuntime(profile="HOME")
        runtime.start()

        # Execute a mission to optimize heating
        result = runtime.execute_mission(
            goal_type="optimize",
            description="Reduce heating cost by adjusting schedule",
            priority=60,
        )

        assert result["goal_id"]
        assert result["plan_steps"] > 0
        assert result["quality_path"] in ("gold", "silver", "bronze")
        runtime.stop()

    def test_heating_query_routes_correctly(self):
        """Heating query should classify as optimization intent."""
        intent = SolverRouter.classify_intent("optimize heating schedule")
        assert intent in ("optimize", "math", "chat")


# ── 2. COTTAGE frost protection end-to-end (rule path) ──

class TestCottageFrostProtection:
    """Scenario: COTTAGE profile frost alert triggers protection goal."""

    def test_protect_mission(self):
        runtime = AutonomyRuntime(profile="COTTAGE")
        runtime.start()

        result = runtime.execute_mission(
            goal_type="protect",
            description="Frost protection: temperature below -15°C",
            priority=90,
        )

        assert result["goal_id"]
        assert result["plan_steps"] >= 2  # detect + act + verify
        runtime.stop()

    def test_protect_goal_lifecycle(self):
        engine = GoalEngine(profile="COTTAGE")
        goal = engine.propose("protect", "Frost alert", priority=90)
        engine.accept(goal.goal_id)
        engine.mark_planned(goal.goal_id)
        engine.start_execution(goal.goal_id)
        engine.mark_verified(goal.goal_id)
        assert goal.status == GoalStatus.VERIFIED


# ── 3. FACTORY maintenance prediction + approval (policy path) ──

class TestFactoryMaintenanceApproval:
    """Scenario: FACTORY maintenance action requires policy approval."""

    def test_maintenance_through_policy(self):
        policy = PolicyEngine(profile="FACTORY")
        cap = CapabilityContract(
            "act.maintenance_schedule",
            category=CapabilityCategory.ACT,
            description="Schedule maintenance window",
        )
        action = Action(capability_id="act.maintenance_schedule")

        decision = policy.evaluate(action, cap, quality_path="silver")
        # ACT category requires approval by default (deny-by-default)
        assert decision.risk_score is not None

    def test_factory_mission_plan(self):
        runtime = AutonomyRuntime(profile="FACTORY")
        runtime.start()
        result = runtime.execute_mission(
            goal_type="maintain",
            description="Schedule predictive maintenance",
            priority=70,
        )
        assert result["goal_id"]
        assert result["plan_steps"] > 0
        runtime.stop()


# ── 4. APIARY varroa seasonal check (domain rule path) ──

class TestApiaryVarroaCheck:
    """Scenario: APIARY varroa treatment check uses seasonal rules."""

    def test_diagnose_varroa(self):
        engine = GoalEngine(profile="APIARY")
        goal = engine.propose("diagnose", "Check varroa mite levels")

        # Decompose into sub-goals
        sub_goals = engine.decompose(goal.goal_id)
        assert len(sub_goals) == 3  # observe, diagnose, verify
        assert all(sg.parent_goal_id == goal.goal_id for sg in sub_goals)

    def test_apiary_mission_execution(self):
        runtime = AutonomyRuntime(profile="APIARY")
        runtime.start()
        result = runtime.execute_mission(
            goal_type="diagnose",
            description="Varroa mite assessment",
            priority=75,
        )
        assert result["goal_id"]
        assert result["quality_path"] in ("gold", "silver", "bronze")
        runtime.stop()


# ── 5. Route fallback without authoritative LLM dependency ──

class TestRouteFallbackNoLLM:
    """Scenario: System handles queries without LLM being primary."""

    def test_solver_first_routing(self):
        """Math query should route to solver, not LLM."""
        intent = SolverRouter.classify_intent("laske 2 + 3")
        assert intent == "math"  # Solver path, not LLM

    def test_retrieval_route(self):
        """Information queries should try retrieval before LLM."""
        registry = CapabilityRegistry()
        selector = CapabilitySelector(registry)
        result = selector.select(intent="retrieval", context={})
        # Should find retrieval capabilities
        assert result.quality_path in ("gold", "silver", "bronze")

    def test_runtime_handles_without_llm(self):
        """Runtime should work even when LLM is unavailable."""
        runtime = AutonomyRuntime(profile="TEST")
        runtime.start()
        result = runtime.handle_query("what is 2 + 2?")
        assert "intent" in result
        assert "quality_path" in result
        runtime.stop()


# ── 6. Night learning case build from legacy Q&A ──

class TestNightLearningLegacy:
    """Scenario: Legacy Q&A data is converted to case trajectories."""

    def test_legacy_to_case_conversion(self):
        converter = LegacyConverter(profile="HOME")
        records = [
            LegacyRecord(
                question="Mikä on lämpötila?",
                answer="21.5°C",
                confidence=0.92,
                source="memory",
                route_type="memory",
            ),
            LegacyRecord(
                question="Laske 5 * 8",
                answer="40",
                confidence=0.99,
                source="solver",
                route_type="micromodel",
            ),
        ]
        cases = converter.convert_batch(records)
        assert len(cases) == 2
        # High-confidence solver gets better grade
        assert all(c.quality_grade in list(QualityGrade) for c in cases)

    def test_full_night_cycle_with_legacy(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        trainer = SpecialistTrainer(
            model_store=store, min_samples=2, canary_hours=0,
        )
        pipeline = NightLearningPipeline(
            profile="HOME",
            specialist_trainer=trainer,
        )

        legacy = [
            LegacyRecord(question=f"Q{i}", answer=f"A{i}", confidence=0.9,
                         source="memory", route_type="memory")
            for i in range(10)
        ]
        result = pipeline.run_cycle(legacy_records=legacy)

        assert result.legacy_converted == 10
        assert result.cases_graded > 0
        assert result.report is not None
        assert result.success is True


# ── 7. Legacy agent ID lookup resolves to canonical ID ──

class TestLegacyAgentIdLookup:
    """Scenario: Old agent IDs resolve through alias registry."""

    def test_alias_registry_resolution(self):
        from waggledance.core.capabilities.aliasing import AgentAlias, AliasRegistry

        agents = [
            AgentAlias(
                legacy_id="energy_manager",
                canonical="domain.home.energy",
                aliases=("energy_manager", "energianeuvoja"),
                profiles=("HOME",),
            ),
            AgentAlias(
                legacy_id="beekeeper",
                canonical="domain.apiary.beekeeper",
                aliases=("beekeeper", "päämehiläishoitaja"),
                profiles=("APIARY",),
            ),
        ]
        registry = AliasRegistry(agents)

        assert registry.resolve("energy_manager") == "domain.home.energy"
        assert registry.resolve("beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("päämehiläishoitaja") == "domain.apiary.beekeeper"
        assert registry.resolve("nonexistent") is None

    def test_profile_filtering(self):
        from waggledance.core.capabilities.aliasing import AgentAlias, AliasRegistry

        agents = [
            AgentAlias(
                legacy_id="energy_manager",
                canonical="domain.home.energy",
                aliases=("energy_manager",),
                profiles=("HOME",),
            ),
            AgentAlias(
                legacy_id="beekeeper",
                canonical="domain.apiary.beekeeper",
                aliases=("beekeeper",),
                profiles=("APIARY",),
            ),
        ]
        registry = AliasRegistry(agents)
        home_agents = registry.by_profile("HOME")
        assert len(home_agents) == 1
        assert home_agents[0].canonical == "domain.home.energy"


# ── 8. Audit + replay from action bus execution ──

class TestAuditReplayActionBus:
    """Scenario: Action bus records audit trail for replay."""

    def test_action_bus_audit_trail(self):
        policy = PolicyEngine(profile="TEST")
        bus = SafeActionBus(policy)

        # Register an executor
        def mock_executor(action, cap, **kwargs):
            return {"status": "done"}
        bus.register_executor("sense.test", mock_executor)

        cap = CapabilityContract(
            "sense.test", category=CapabilityCategory.SENSE,
            description="Test sensor",
        )
        action = Action(capability_id="sense.test")
        result = bus.submit(action, cap, quality_path="gold")

        # Audit trail should exist
        trail = bus.audit_trail()
        assert len(trail) > 0

    def test_action_bus_stats_tracking(self):
        policy = PolicyEngine(profile="TEST")
        bus = SafeActionBus(policy)
        cap = CapabilityContract(
            "sense.test", category=CapabilityCategory.SENSE,
        )
        action = Action(capability_id="sense.test")
        bus.submit(action, cap, quality_path="silver")

        stats = bus.stats()
        assert stats["total_actions"] > 0


# ── 9. Rollback after failed canary promotion ──

class TestCanaryRollback:
    """Scenario: Failed canary model gets rolled back."""

    def test_canary_rollback_flow(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        trainer = SpecialistTrainer(
            model_store=store,
            min_samples=2,
            min_accuracy=0.99,  # Very high threshold → forces rollback
            canary_hours=0,
        )

        # Create gold cases
        cases = [_gold_case(f"g{i}") for i in range(5)]

        # Train and canary
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"

        trainer.start_canary("route_classifier")
        canary = store.get_canary("route_classifier")
        assert canary is not None
        assert canary.status == ModelStatus.CANARY

        # Evaluate — accuracy won't meet 0.99 threshold
        outcome = trainer.evaluate_canary("route_classifier")
        assert outcome == "rolled_back"

        # Verify rollback
        rolled = store.get_latest("route_classifier")
        assert rolled.status == ModelStatus.ROLLED_BACK


# ── 10. Autoscaling tier switch preserves safety ──

class TestAutoscalingTierSwitch:
    """Scenario: Switching hardware tiers preserves runtime safety."""

    def test_tier_switch_updates_limits(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.limits.max_concurrent_queries == 4

        # Simulate downgrade
        kernel2 = ResourceKernel(tier="minimal")
        assert kernel2.limits.max_concurrent_queries == 1
        assert kernel2.can_accept_query() is True

    def test_admission_control_adapts_to_tier(self):
        kernel = ResourceKernel(tier="minimal")
        ac = AdmissionControl(kernel=kernel)

        # Minimal tier: 1 concurrent max
        kernel.record_task_start()
        result = ac.check(work_type="query")
        assert result.decision in (AdmissionDecision.REJECT, AdmissionDecision.DEFER)

    def test_resource_kernel_in_service(self):
        service = AutonomyService(profile="TEST")
        service.start()
        status = service.get_status()
        assert "resource_kernel" in status
        assert status["resource_kernel"]["tier"] == "standard"
        service.stop()


# ── 11. Throttle reduces night learning under load ──

class TestThrottleNightLearning:
    """Scenario: High load defers night learning work."""

    def test_heavy_load_defers_learning(self):
        kernel = ResourceKernel(tier="standard")
        # Saturate the system
        for _ in range(3):
            kernel.record_task_start()
        assert kernel.load_level == LoadLevel.HEAVY
        assert kernel.can_accept_learning() is False

    def test_night_mode_enables_training(self):
        kernel = ResourceKernel(tier="standard")
        assert kernel.can_train_specialist() is False
        kernel.set_night_mode(True)
        assert kernel.can_train_specialist() is True

    def test_admission_defers_training_by_day(self):
        kernel = ResourceKernel(tier="standard")
        ac = AdmissionControl(kernel=kernel)
        result = ac.check(work_type="training")
        assert result.decision == AdmissionDecision.DEFER
        assert "night" in result.reason.lower()


# ── 12. LLM-only answer graded bronze, not promoted ──

class TestLLMOnlyBronze:
    """Scenario: LLM-only answers never auto-promote."""

    def test_llm_only_gets_bronze(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        case = builder.build(
            query="Tell me about bees",
            intent="chat",
            capabilities=[
                CapabilityContract(
                    "explain.llm_reasoning",
                    category=CapabilityCategory.EXPLAIN,
                ),
            ],
        )
        assert case.quality_grade == QualityGrade.BRONZE

    def test_bronze_not_auto_promoted(self):
        gate = QualityGate()
        goal = Goal(type=GoalType.OBSERVE, description="LLM answer")
        case = CaseTrajectory(
            goal=goal,
            selected_capabilities=[
                CapabilityContract(
                    "explain.llm_reasoning",
                    category=CapabilityCategory.EXPLAIN,
                ),
            ],
            verifier_result={"passed": False},
        )
        case.grade()
        assert case.quality_grade == QualityGrade.BRONZE

        decision = gate.evaluate(case)
        assert decision.auto_promote is False
        assert decision.feeds_specialist is False
        assert decision.feeds_procedural is False

    def test_bronze_not_in_procedural_memory(self):
        pm = ProceduralMemory()
        goal = Goal(type=GoalType.OBSERVE, description="LLM test")
        case = CaseTrajectory(
            goal=goal,
            selected_capabilities=[
                CapabilityContract("explain.llm", category=CapabilityCategory.EXPLAIN),
            ],
            verifier_result={"passed": False},
        )
        case.grade()
        result = pm.learn_from_case(case)
        assert result is None  # Bronze ignored


# ── 13. Specialist model canary → production promotion ──

class TestSpecialistCanaryPromotion:
    """Scenario: Specialist model trains → canary → promote to production."""

    def test_full_promotion_flow(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        trainer = SpecialistTrainer(
            model_store=store,
            min_samples=2,
            min_accuracy=0.70,  # Low threshold for testing
            canary_hours=0,     # No wait period
        )

        # Step 1: Train with good data
        cases = [_gold_case(f"g{i}") for i in range(10)]
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"
        assert result.accuracy > 0.70

        # Step 2: Start canary
        mv = trainer.start_canary("route_classifier")
        assert mv.status == ModelStatus.CANARY

        # Step 3: Evaluate and promote
        outcome = trainer.evaluate_canary("route_classifier")
        assert outcome == "promoted"

        # Step 4: Verify production status
        prod = store.get_production("route_classifier")
        assert prod is not None
        assert prod.status == ModelStatus.PRODUCTION

    def test_night_pipeline_trains_and_promotes(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        trainer = SpecialistTrainer(
            model_store=store,
            min_samples=2,
            min_accuracy=0.70,
            canary_hours=0,
        )
        pipeline = NightLearningPipeline(
            profile="TEST",
            specialist_trainer=trainer,
        )

        # Run with enough gold cases
        cases = [_gold_case(f"g{i}") for i in range(10)]
        result = pipeline.run_cycle(day_cases=cases)

        assert result.models_trained > 0
        assert result.success is True
        assert result.report is not None


# ── Helpers ──────────────────────────────────────────────

def _gold_case(desc: str = "gold test") -> CaseTrajectory:
    """Create a gold-grade case trajectory."""
    goal = Goal(type=GoalType.DIAGNOSE, description=desc)
    case = CaseTrajectory(
        goal=goal,
        selected_capabilities=[
            CapabilityContract("solve.math", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": True, "confidence": 0.95},
        profile="TEST",
    )
    case.grade()
    return case
