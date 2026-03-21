# SPDX-License-Identifier: Apache-2.0
"""Tests for energy_forecaster specialist model."""
from __future__ import annotations

import random

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(vals, grade=QualityGrade.GOLD, profile="TEST"):
    residuals = {f"ch_{i}": v for i, v in enumerate(vals)}
    case = CaseTrajectory(
        goal=Goal(type=GoalType.OPTIMIZE, description="energy forecast"),
        selected_capabilities=[
            CapabilityContract(capability_id="predict.energy", category=CapabilityCategory.PREDICT),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def _diverse_cases(n=60):
    random.seed(42)
    cases = []
    profiles = ["APIARY", "HOME", "FACTORY"]
    for i in range(n):
        vals = [random.gauss(0, 1) for _ in range(3)]
        grade = QualityGrade.GOLD if i % 2 == 0 else QualityGrade.SILVER
        cases.append(_make_case(vals, grade, profiles[i % 3]))
    return cases


class TestEnergyForecaster:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("energy_forecaster", _diverse_cases(60))
        assert result.status == "completed"
        assert result.accuracy >= 0

    def test_prediction_is_float(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("energy_forecaster", _diverse_cases(60))
        model = trainer.load_weights("energy_forecaster", result.version)
        pred = model.predict([[0.1, 0.2, 0.3, 2]])
        assert isinstance(pred[0], float)

    def test_persistence_roundtrip(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("energy_forecaster", _diverse_cases(60))
        m1 = trainer.load_weights("energy_forecaster", result.version)
        m2 = trainer.load_weights("energy_forecaster", result.version)
        sample = [[0.1, 0.2, 0.3, 2]]
        assert list(m1.predict(sample)) == list(m2.predict(sample))

    def test_insufficient_samples(self):
        trainer = SpecialistTrainer(min_samples=10)
        result = trainer.train_from_cases("energy_forecaster", _diverse_cases(5))
        assert result.status == "skipped"
