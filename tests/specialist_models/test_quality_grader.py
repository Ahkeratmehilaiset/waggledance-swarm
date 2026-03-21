# SPDX-License-Identifier: Apache-2.0
"""Tests for quality_grader specialist model."""
from __future__ import annotations

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(goal_type, cap_id, grade, profile="TEST"):
    case = CaseTrajectory(
        goal=Goal(type=goal_type, description=f"test {goal_type.value}"),
        selected_capabilities=[
            CapabilityContract(capability_id=cap_id, category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals={"m": 0.5}),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def _diverse_cases(n=50):
    combos = [
        (GoalType.OBSERVE, "solve.math", QualityGrade.GOLD, "APIARY"),
        (GoalType.DIAGNOSE, "solve.symbolic", QualityGrade.SILVER, "HOME"),
        (GoalType.PROTECT, "solve.constraints", QualityGrade.BRONZE, "FACTORY"),
        (GoalType.OPTIMIZE, "optimize.schedule", QualityGrade.QUARANTINE, "GADGET"),
        (GoalType.PLAN, "retrieve.hot_cache", QualityGrade.GOLD, "APIARY"),
    ]
    return [_make_case(*combos[i % len(combos)]) for i in range(n)]


class TestQualityGrader:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("quality_grader", _diverse_cases(50))
        assert result.status == "completed"
        assert result.accuracy > 0

    def test_prediction_valid_grade(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("quality_grader", _diverse_cases(50))
        model = trainer.load_weights("quality_grader", result.version)
        pred = model.predict([[0, 0, 2, 1]])
        assert pred[0] in ("gold", "silver", "bronze", "quarantine")

    def test_persistence_roundtrip(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("quality_grader", _diverse_cases(50))
        m1 = trainer.load_weights("quality_grader", result.version)
        m2 = trainer.load_weights("quality_grader", result.version)
        sample = [[0, 1, 3, 1], [1, 0, 1, 0]]
        assert list(m1.predict(sample)) == list(m2.predict(sample))

    def test_holdout_gate(self):
        trainer = SpecialistTrainer(min_samples=3)
        same = [_make_case(GoalType.OBSERVE, "solve.math", QualityGrade.GOLD) for _ in range(20)]
        result = trainer.train_from_cases("quality_grader", same)
        assert result.status == "completed"
        assert result.accuracy > 0

    def test_insufficient_samples(self):
        trainer = SpecialistTrainer(min_samples=10)
        result = trainer.train_from_cases("quality_grader", _diverse_cases(5))
        assert result.status == "skipped"
