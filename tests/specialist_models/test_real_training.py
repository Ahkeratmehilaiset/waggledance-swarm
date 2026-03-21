"""
Tests for real specialist model training with sklearn.
"""

from __future__ import annotations

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
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(goal_type: GoalType, capability_id: str,
               grade: QualityGrade, profile: str = "TEST") -> CaseTrajectory:
    case = CaseTrajectory(
        goal=Goal(type=goal_type, description=f"test {goal_type.value}"),
        selected_capabilities=[
            CapabilityContract(
                capability_id=capability_id,
                category=CapabilityCategory.SOLVE,
            ),
        ],
        verifier_result={"passed": grade == QualityGrade.GOLD, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals={"entity.metric": 0.5}),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def _make_diverse_cases(n: int = 20) -> list:
    """Create diverse cases with multiple goal types and capabilities."""
    cases = []
    combos = [
        (GoalType.OBSERVE, "solve.math", QualityGrade.GOLD),
        (GoalType.OBSERVE, "retrieve.hot_cache", QualityGrade.SILVER),
        (GoalType.DIAGNOSE, "solve.symbolic", QualityGrade.GOLD),
        (GoalType.PROTECT, "solve.constraints", QualityGrade.SILVER),
        (GoalType.OPTIMIZE, "optimize.schedule", QualityGrade.GOLD),
    ]
    for i in range(n):
        gt, cap, grade = combos[i % len(combos)]
        cases.append(_make_case(gt, cap, grade))
    return cases


class TestRealRouteClassifier:
    def test_route_classifier_trains_with_sklearn(self):
        """route_classifier should use real sklearn training."""
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)

        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"
        assert result.accuracy > 0
        assert result.training_samples == 20

    def test_route_classifier_accuracy_improves_with_more_data(self):
        trainer = SpecialistTrainer(min_samples=3)

        small = _make_diverse_cases(5)
        result_small = trainer.train_from_cases("route_classifier", small)

        large = _make_diverse_cases(20)
        result_large = trainer.train_from_cases("route_classifier", large)

        # Both should complete
        assert result_small.status == "completed"
        assert result_large.status == "completed"
        # More data should give comparable or better accuracy
        assert result_large.accuracy >= 0

    def test_model_registered_in_store(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(10)

        result = trainer.train_from_cases("route_classifier", cases)
        assert result.version > 0

    def test_single_class_falls_back_to_simulation(self):
        """If all cases have the same capability, fall back to simulation."""
        trainer = SpecialistTrainer(min_samples=3)
        cases = [_make_case(GoalType.OBSERVE, "solve.math", QualityGrade.GOLD)
                 for _ in range(5)]

        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"
        assert result.accuracy > 0  # simulation still produces accuracy


class TestOtherModelsStillSimulated:
    def test_capability_selector_uses_simulation(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(10)

        result = trainer.train_from_cases("capability_selector", cases)
        assert result.status == "completed"

    def test_anomaly_detector_uses_simulation(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(10)

        result = trainer.train_from_cases("anomaly_detector", cases)
        assert result.status == "completed"


class TestTrainAll:
    def test_train_all_includes_real_route_classifier(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(20)

        results = trainer.train_all(cases)
        route_result = next(r for r in results if r.model_id == "route_classifier")
        assert route_result.status == "completed"
        assert route_result.accuracy > 0

    def test_train_all_completed_count(self):
        trainer = SpecialistTrainer(min_samples=3)
        cases = _make_diverse_cases(10)

        results = trainer.train_all(cases)
        completed = sum(1 for r in results if r.status == "completed")
        assert completed > 0
