# SPDX-License-Identifier: Apache-2.0
"""Tests for thermal_predictor specialist model."""
from __future__ import annotations

import random

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(temp, humidity, grade=QualityGrade.GOLD):
    residuals = {"temperature": temp, "humidity": humidity}
    case = CaseTrajectory(
        goal=Goal(type=GoalType.OBSERVE, description="thermal check"),
        selected_capabilities=[
            CapabilityContract(capability_id="predict.thermal", category=CapabilityCategory.PREDICT),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile="TEST",
    )
    case.quality_grade = grade
    return case


def _diverse_cases(n=60):
    random.seed(42)
    cases = []
    for i in range(n):
        temp = random.gauss(25, 10)
        humidity = random.gauss(60, 15)
        grade = QualityGrade.GOLD if i % 2 == 0 else QualityGrade.SILVER
        cases.append(_make_case(temp, humidity, grade))
    return cases


class TestThermalPredictor:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("thermal_predictor", _diverse_cases(60))
        assert result.status == "completed"
        assert result.accuracy >= 0  # R² can be low on random data

    def test_prediction_is_float(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("thermal_predictor", _diverse_cases(60))
        model = trainer.load_weights("thermal_predictor", result.version)
        pred = model.predict([[0.5, 0.75, 2]])
        assert isinstance(pred[0], float)

    def test_persistence_roundtrip(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("thermal_predictor", _diverse_cases(60))
        m1 = trainer.load_weights("thermal_predictor", result.version)
        m2 = trainer.load_weights("thermal_predictor", result.version)
        sample = [[0.5, 0.75, 2], [1.0, 0.5, 1]]
        p1 = list(m1.predict(sample))
        p2 = list(m2.predict(sample))
        assert p1 == p2

    def test_insufficient_samples(self):
        trainer = SpecialistTrainer(min_samples=10)
        result = trainer.train_from_cases("thermal_predictor", _diverse_cases(5))
        assert result.status == "skipped"
