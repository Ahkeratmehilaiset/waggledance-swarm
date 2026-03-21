"""
Tests for real sklearn training across all 8 specialist models.

Verifies that each model uses a real sklearn algorithm instead of
_simulate_training() when sklearn is available.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import (
    SPECIALIST_MODELS,
    SpecialistTrainer,
)


def _make_case(
    goal_type: GoalType = GoalType.OBSERVE,
    capability_id: str = "solve.math",
    grade: QualityGrade = QualityGrade.GOLD,
    profile: str = "TEST",
    verifier_passed: bool = True,
    residuals: dict | None = None,
) -> CaseTrajectory:
    """Factory for CaseTrajectory fixtures with controllable fields."""
    case = CaseTrajectory(
        goal=Goal(type=goal_type, description=f"test {goal_type.value}"),
        selected_capabilities=[
            CapabilityContract(
                capability_id=capability_id,
                category=CapabilityCategory.SOLVE,
            ),
        ],
        verifier_result={"passed": verifier_passed, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(
            residuals=residuals or {"entity.metric": 0.5},
        ),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def _make_diverse_cases(n: int = 20) -> list[CaseTrajectory]:
    """Create diverse cases covering multiple goal types, capabilities, grades, profiles."""
    combos = [
        (GoalType.OBSERVE, "solve.math", QualityGrade.GOLD, "APIARY", True, {"temp": 0.3}),
        (GoalType.DIAGNOSE, "solve.symbolic", QualityGrade.SILVER, "HOME", False, {"humidity": 1.7}),
        (GoalType.PROTECT, "solve.constraints", QualityGrade.BRONZE, "FACTORY", True, {"weight": 4.1}),
        (GoalType.OPTIMIZE, "optimize.schedule", QualityGrade.GOLD, "GADGET", False, {"latency": 0.9}),
        (GoalType.PLAN, "retrieve.hot_cache", QualityGrade.SILVER, "APIARY", True, {"load": 0.5}),
    ]
    cases = []
    for i in range(n):
        gt, cap, grade, profile, vp, res = combos[i % len(combos)]
        cases.append(_make_case(gt, cap, grade, profile, vp, res))
    return cases


class TestRouteClassifierReal:
    def test_route_classifier_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"
        assert result.accuracy > 0
        assert result.training_samples == 20


class TestCapabilitySelectorReal:
    def test_capability_selector_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("capability_selector", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestAnomalyDetectorReal:
    def test_anomaly_detector_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("anomaly_detector", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestBaselineScorerReal:
    def test_baseline_scorer_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("baseline_scorer", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestApprovalPredictorReal:
    def test_approval_predictor_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("approval_predictor", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestMissingVarPredictorReal:
    def test_missing_var_predictor_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("missing_var_predictor", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestVerifierPriorReal:
    def test_verifier_prior_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("verifier_prior", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestDomainLanguageAdapterReal:
    def test_domain_language_adapter_real_training(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)
        result = trainer.train_from_cases("domain_language_adapter", cases)
        assert result.status == "completed"
        assert result.accuracy > 0


class TestAllModelsNoSimulation:
    def test_all_models_use_real_training(self):
        """Verify none of the 8 models fall back to _simulate_training."""
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)

        with patch.object(trainer, "_simulate_training", wraps=trainer._simulate_training) as mock_sim:
            results = trainer.train_all(cases)
            completed = [r for r in results if r.status == "completed"]
            assert len(completed) == 14

            # _simulate_training should not be called for any model
            assert mock_sim.call_count == 0, (
                f"_simulate_training was called {mock_sim.call_count} time(s) "
                f"for: {[c.args[0] for c in mock_sim.call_args_list]}"
            )


class TestEdgeCases:
    def test_insufficient_samples_skips(self):
        """Less than min_samples → status=skipped."""
        trainer = SpecialistTrainer(min_samples=10)
        cases = _make_diverse_cases(5)

        for model_id in SPECIALIST_MODELS:
            result = trainer.train_from_cases(model_id, cases)
            assert result.status == "skipped", f"{model_id} should be skipped"

    def test_single_class_fallback(self):
        """Single unique label → falls back to simulation but still returns accuracy > 0."""
        trainer = SpecialistTrainer(min_samples=3)
        # All same goal_type, capability, grade, profile
        cases = [
            _make_case(GoalType.OBSERVE, "solve.math", QualityGrade.GOLD, "TEST")
            for _ in range(10)
        ]

        for model_id in SPECIALIST_MODELS:
            result = trainer.train_from_cases(model_id, cases)
            assert result.status == "completed", f"{model_id} should complete"
            assert result.accuracy > 0, f"{model_id} should have accuracy > 0"
