# SPDX-License-Identifier: Apache-2.0
"""Tests for sensor_health specialist model."""
from __future__ import annotations

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(max_residual, grade=QualityGrade.GOLD):
    """Create case with controlled residual magnitude for health labeling."""
    residuals = {"sensor_0": max_residual, "sensor_1": max_residual * 0.5}
    case = CaseTrajectory(
        goal=Goal(type=GoalType.OBSERVE, description="sensor check"),
        selected_capabilities=[
            CapabilityContract(capability_id="detect.health", category=CapabilityCategory.DETECT),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile="TEST",
    )
    case.quality_grade = grade
    return case


def _diverse_cases(n=60):
    """Generate cases with healthy/degraded/failed distribution."""
    cases = []
    for i in range(n):
        if i % 3 == 0:
            cases.append(_make_case(0.3))  # healthy
        elif i % 3 == 1:
            cases.append(_make_case(1.5))  # degraded
        else:
            cases.append(_make_case(4.0))  # failed
    return cases


class TestSensorHealth:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("sensor_health", _diverse_cases(60))
        assert result.status == "completed"
        assert result.accuracy > 0

    def test_prediction_valid_label(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("sensor_health", _diverse_cases(60))
        model = trainer.load_weights("sensor_health", result.version)
        pred = model.predict([[2, 0.3, 0.2]])
        assert pred[0] in ("healthy", "degraded", "failed")

    def test_persistence_roundtrip(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("sensor_health", _diverse_cases(60))
        m1 = trainer.load_weights("sensor_health", result.version)
        m2 = trainer.load_weights("sensor_health", result.version)
        sample = [[2, 0.3, 0.2], [2, 4.0, 3.0]]
        assert list(m1.predict(sample)) == list(m2.predict(sample))

    def test_holdout_gate(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("sensor_health", _diverse_cases(60))
        assert 0.0 < result.accuracy <= 1.0

    def test_insufficient_samples(self):
        trainer = SpecialistTrainer(min_samples=10)
        result = trainer.train_from_cases("sensor_health", _diverse_cases(5))
        assert result.status == "skipped"
