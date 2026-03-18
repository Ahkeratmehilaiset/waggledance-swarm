"""
Phase 5 Tests: Solver Router, Verifier, Case Builder, Model Store.

Tests cover:
- SolverRouter intent classification
- SolverRouter solver-first routing
- SolverRouter working memory integration
- Verifier residual comparison and criteria checking
- Verifier conflict and hallucination detection
- CaseTrajectoryBuilder auto-grading
- CaseTrajectoryBuilder legacy backfill
- ModelStore lifecycle (train → canary → promote/rollback)
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.reasoning.solver_router import SolverRouteResult, SolverRouter
from waggledance.core.reasoning.verifier import Verifier, VerifierResult
from waggledance.core.specialist_models.model_store import ModelStatus, ModelStore, ModelVersion


# ── SolverRouter ──────────────────────────────────────────────

class TestSolverRouterIntentClassification:
    def test_math_intent(self):
        assert SolverRouter.classify_intent("laske 2 + 3") == "math"
        assert SolverRouter.classify_intent("calculate the sum") == "math"

    def test_symbolic_intent(self):
        assert SolverRouter.classify_intent("use the formula for X") == "symbolic"
        assert SolverRouter.classify_intent("kaava lämpöhäviölle") == "symbolic"

    def test_constraint_intent(self):
        assert SolverRouter.classify_intent("check the rule for varroa") == "constraint"
        assert SolverRouter.classify_intent("tarkista sääntö") == "constraint"

    def test_seasonal_intent(self):
        assert SolverRouter.classify_intent("what to do in spring?") == "seasonal"
        assert SolverRouter.classify_intent("kevät tehtävät") == "seasonal"

    def test_anomaly_intent(self):
        assert SolverRouter.classify_intent("detect anomaly in sensor data") == "anomaly"
        assert SolverRouter.classify_intent("onko poikkeama?") == "anomaly"

    def test_retrieval_intent(self):
        assert SolverRouter.classify_intent("what is varroa?") == "retrieval"
        assert SolverRouter.classify_intent("mikä on mehiläisten katoilmiö?") == "retrieval"

    def test_chat_fallback(self):
        assert SolverRouter.classify_intent("hello world") == "chat"


class TestSolverRouterRouting:
    def test_route_math_selects_solver(self):
        router = SolverRouter()
        result = router.route("math", context={"numbers_present": True})
        assert result.quality_path == "gold"
        assert "solve.math" in result.selected_ids

    def test_route_retrieval_selects_retriever(self):
        router = SolverRouter()
        result = router.route("retrieval")
        assert result.quality_path == "silver"
        assert any("retrieve" in cid for cid in result.selected_ids)

    def test_route_chat_uses_llm_fallback(self):
        router = SolverRouter()
        result = router.route("chat")
        assert result.quality_path == "bronze"
        assert result.selection.fallback_used is True

    def test_route_direct_by_ids(self):
        router = SolverRouter()
        result = router.route_direct(["solve.math", "verify.hallucination"])
        assert len(result.selection.selected) == 2
        assert result.quality_path == "gold"

    def test_route_result_to_dict(self):
        router = SolverRouter()
        result = router.route("math", context={"numbers_present": True})
        d = result.to_dict()
        assert "intent" in d
        assert "quality_path" in d
        assert "selected" in d
        assert "execution_time_ms" in d

    def test_set_and_clear_context(self):
        router = SolverRouter()
        router.set_context("current_temp", 35.0, category="observation")
        result = router.route("chat")
        assert "current_temp" in result.context_keys
        router.clear_context()
        result2 = router.route("chat")
        assert "current_temp" not in result2.context_keys

    def test_stats(self):
        router = SolverRouter()
        router.route("math", context={"numbers_present": True})
        router.route("chat")
        s = router.stats()
        assert s["total"] == 2
        assert "gold" in s["quality_distribution"]
        assert "bronze" in s["quality_distribution"]

    def test_recent_routes(self):
        router = SolverRouter()
        for _ in range(5):
            router.route("chat")
        assert len(router.recent_routes()) == 5


# ── Verifier ──────────────────────────────────────────────────

class TestVerifier:
    def test_verify_simple_pass(self):
        v = Verifier()
        result = v.verify_simple({"answer": 42}, expected_fields=["answer"])
        assert result.passed is True
        assert "answer" in result.success_criteria_met

    def test_verify_simple_fail_missing_field(self):
        v = Verifier()
        result = v.verify_simple({"answer": 42}, expected_fields=["answer", "explanation"])
        assert result.passed is False
        assert "explanation" in result.success_criteria_failed

    def test_verify_simple_conflict(self):
        v = Verifier()
        result = v.verify_simple({"answer": 42, "conflict": True})
        assert result.passed is False
        assert result.conflict is True

    def test_verify_simple_hallucination(self):
        v = Verifier()
        result = v.verify_simple({"text": "...", "hallucination": True})
        assert result.passed is False
        assert result.hallucination is True

    def test_verify_with_snapshots(self):
        v = Verifier()
        before = WorldSnapshot(
            residuals={"hive_1.temperature": 5.0},
        )
        after = WorldSnapshot(
            residuals={"hive_1.temperature": 1.0},
        )
        action = Action(capability_id="act.heating")
        result = v.verify(
            action,
            snapshot_before=before,
            snapshot_after=after,
            action_result={"result_verified": True},
        )
        assert result.residual_improvement > 0

    def test_verify_no_snapshots(self):
        v = Verifier()
        action = Action(capability_id="solve.math")
        cap = CapabilityContract("solve.math", CapabilityCategory.SOLVE,
                                 success_criteria=["result_verified"])
        result = v.verify(action, cap, action_result={"result_verified": True})
        assert result.passed is True

    def test_verify_failed_criteria(self):
        v = Verifier()
        action = Action(capability_id="solve.math")
        cap = CapabilityContract("solve.math", CapabilityCategory.SOLVE,
                                 success_criteria=["result_verified"])
        result = v.verify(action, cap, action_result={})
        assert result.passed is False

    def test_verifier_result_to_dict(self):
        result = VerifierResult(passed=True, confidence=0.9)
        d = result.to_dict()
        assert d["passed"] is True
        assert d["confidence"] == 0.9

    def test_side_effect_detection(self):
        v = Verifier()
        before = WorldSnapshot(entities={"hive_1": {"type": "beehive"}})
        after = WorldSnapshot(
            entities={"hive_1": {"type": "beehive"}, "sensor_new": {"type": "sensor"}},
        )
        action = Action(capability_id="act.test")
        result = v.verify(action, snapshot_before=before, snapshot_after=after)
        assert len(result.side_effects) > 0

    def test_stats(self):
        v = Verifier()
        v.verify_simple({"ok": True}, expected_fields=["ok"])
        v.verify_simple({"conflict": True})
        s = v.stats()
        assert s["total"] == 2
        assert s["passed"] == 1
        assert s["conflicts"] == 1

    def test_confidence_high_for_criteria_met(self):
        v = Verifier()
        result = v.verify_simple(
            {"a": True, "b": True, "c": True},
            expected_fields=["a", "b", "c"],
        )
        assert result.confidence >= 0.8

    def test_confidence_low_for_conflict(self):
        v = Verifier()
        result = v.verify_simple({"conflict": True})
        assert result.confidence <= 0.3


# ── CaseTrajectoryBuilder ────────────────────────────────────

class TestCaseTrajectoryBuilder:
    def test_build_basic(self):
        builder = CaseTrajectoryBuilder(profile="TEST")
        case = builder.build(
            query="What is hive temperature?",
            intent="retrieval",
        )
        assert isinstance(case, CaseTrajectory)
        assert case.profile == "TEST"
        assert case.goal.type == GoalType.OBSERVE

    def test_build_with_solver_and_verifier(self):
        builder = CaseTrajectoryBuilder()
        solver = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        verifier_result = VerifierResult(passed=True, confidence=0.9)

        case = builder.build(
            query="Calculate honey yield",
            intent="math",
            capabilities=[solver],
            verifier_result=verifier_result,
        )
        assert case.quality_grade == QualityGrade.GOLD

    def test_build_solver_no_verifier(self):
        builder = CaseTrajectoryBuilder()
        solver = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        verifier_result = VerifierResult(passed=False)

        case = builder.build(
            query="Calculate X",
            intent="math",
            capabilities=[solver],
            verifier_result=verifier_result,
        )
        assert case.quality_grade == QualityGrade.SILVER

    def test_build_llm_only_bronze(self):
        builder = CaseTrajectoryBuilder()
        llm = CapabilityContract("explain.llm_reasoning", CapabilityCategory.EXPLAIN)
        verifier_result = VerifierResult(passed=False)

        case = builder.build(
            query="Tell me about bees",
            intent="chat",
            capabilities=[llm],
            verifier_result=verifier_result,
        )
        assert case.quality_grade == QualityGrade.BRONZE

    def test_build_quarantine_conflict(self):
        builder = CaseTrajectoryBuilder()
        solver = CapabilityContract("solve.math", CapabilityCategory.SOLVE)
        verifier_result = VerifierResult(passed=True, conflict=True)

        case = builder.build(
            query="Calculate X",
            intent="math",
            capabilities=[solver],
            verifier_result=verifier_result,
        )
        assert case.quality_grade == QualityGrade.QUARANTINE

    def test_build_from_legacy(self):
        builder = CaseTrajectoryBuilder()
        case = builder.build_from_legacy(
            question="What is varroa?",
            answer="Varroa is a mite...",
            confidence=0.85,
            source="llm",
            route_type="llm",
        )
        assert isinstance(case, CaseTrajectory)
        assert case.quality_grade in (QualityGrade.BRONZE, QualityGrade.SILVER)

    def test_build_from_legacy_high_confidence(self):
        builder = CaseTrajectoryBuilder()
        case = builder.build_from_legacy(
            question="What is 2+2?",
            answer="4",
            confidence=0.95,
            source="micromodel",
            route_type="micromodel",
        )
        # micromodel = solver, no corrections = verifier pass
        assert case.quality_grade in (QualityGrade.GOLD, QualityGrade.SILVER)

    def test_stats(self):
        builder = CaseTrajectoryBuilder()
        builder.build(query="q1", intent="chat")
        builder.build(query="q2", intent="chat")
        s = builder.stats()
        assert s["total"] == 2
        assert "grades" in s

    def test_cases_by_grade(self):
        builder = CaseTrajectoryBuilder()
        builder.build(query="q1", intent="chat")
        bronze = builder.cases_by_grade(QualityGrade.BRONZE)
        assert len(bronze) >= 1


# ── ModelStore ────────────────────────────────────────────────

class TestModelStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ModelStore(store_path=str(tmp_path / "models.json"))

    def test_register_version(self, store):
        mv = store.register_version("route_classifier", accuracy=0.85, training_samples=500)
        assert mv.model_id == "route_classifier"
        assert mv.version == 1
        assert mv.status == ModelStatus.TRAINING

    def test_multiple_versions(self, store):
        store.register_version("route_classifier", accuracy=0.80)
        store.register_version("route_classifier", accuracy=0.85)
        store.register_version("route_classifier", accuracy=0.90)
        versions = store.list_versions("route_classifier")
        assert len(versions) == 3
        assert versions[-1].accuracy == 0.90

    def test_canary_lifecycle(self, store):
        store.register_version("anomaly_detector", accuracy=0.88)
        mv = store.start_canary("anomaly_detector")
        assert mv.status == ModelStatus.CANARY
        assert store.get_canary("anomaly_detector") is not None

    def test_promote_to_production(self, store):
        store.register_version("anomaly_detector", accuracy=0.88)
        store.start_canary("anomaly_detector")
        mv = store.promote("anomaly_detector")
        assert mv.status == ModelStatus.PRODUCTION
        assert mv.promoted_at is not None
        assert store.get_production("anomaly_detector") is not None

    def test_rollback_canary(self, store):
        store.register_version("anomaly_detector", accuracy=0.88)
        store.start_canary("anomaly_detector")
        mv = store.rollback("anomaly_detector")
        assert mv.status == ModelStatus.ROLLED_BACK
        assert store.get_canary("anomaly_detector") is None

    def test_promote_retires_old_production(self, store):
        store.register_version("route_classifier", accuracy=0.80)
        store.promote("route_classifier")
        store.register_version("route_classifier", accuracy=0.90)
        store.promote("route_classifier", version=2)
        v1 = store.list_versions("route_classifier")[0]
        assert v1.status == ModelStatus.RETIRED

    def test_get_latest(self, store):
        store.register_version("test_model", accuracy=0.70)
        store.register_version("test_model", accuracy=0.80)
        latest = store.get_latest("test_model")
        assert latest.version == 2

    def test_get_nonexistent(self, store):
        assert store.get_production("nonexistent") is None
        assert store.get_canary("nonexistent") is None
        assert store.get_latest("nonexistent") is None

    def test_list_models(self, store):
        store.register_version("model_a")
        store.register_version("model_b")
        models = store.list_models()
        assert "model_a" in models
        assert "model_b" in models

    def test_stats(self, store):
        store.register_version("route_classifier", accuracy=0.85)
        store.promote("route_classifier")
        store.register_version("anomaly_detector", accuracy=0.88)
        store.start_canary("anomaly_detector")
        s = store.stats()
        assert s["models"]["route_classifier"]["production"] == 1
        assert s["models"]["anomaly_detector"]["canary"] == 1

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "persist_test.json")
        store1 = ModelStore(store_path=path)
        store1.register_version("test_model", accuracy=0.85)
        store1.promote("test_model")

        store2 = ModelStore(store_path=path)
        prod = store2.get_production("test_model")
        assert prod is not None
        assert prod.accuracy == 0.85

    def test_model_version_to_dict(self, store):
        mv = store.register_version("test", accuracy=0.9)
        d = mv.to_dict()
        assert d["model_id"] == "test"
        assert d["status"] == "training"
        assert d["accuracy"] == 0.9
