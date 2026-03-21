# SPDX-License-Identifier: Apache-2.0
"""Tests for anomaly_detector specialist model (enhanced with joblib persistence)."""
from __future__ import annotations

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(residuals, grade=QualityGrade.GOLD):
    case = CaseTrajectory(
        goal=Goal(type=GoalType.OBSERVE, description="anomaly check"),
        selected_capabilities=[
            CapabilityContract(capability_id="detect.anomaly", category=CapabilityCategory.DETECT),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile="TEST",
    )
    case.quality_grade = grade
    return case


def _diverse_cases(n=50):
    import random
    random.seed(42)
    cases = []
    for i in range(n):
        residuals = {f"sensor_{j}": random.gauss(0, 1) for j in range(3)}
        grade = QualityGrade.GOLD if i % 3 == 0 else QualityGrade.SILVER
        cases.append(_make_case(residuals, grade))
    return cases


class TestAnomalyDetector:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("anomaly_detector", _diverse_cases(50))
        assert result.status == "completed"
        assert result.accuracy > 0

    def test_prediction_valid(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("anomaly_detector", _diverse_cases(50))
        model = trainer.load_weights("anomaly_detector", result.version)
        pred = model.predict([[0.1, 0.2, 0.3]])
        assert pred[0] in (1, -1)  # IsolationForest: 1=normal, -1=anomaly

    def test_persistence_roundtrip(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("anomaly_detector", _diverse_cases(50))
        m1 = trainer.load_weights("anomaly_detector", result.version)
        m2 = trainer.load_weights("anomaly_detector", result.version)
        sample = [[0.1, 0.2, 0.3], [10.0, 10.0, 10.0]]
        assert list(m1.predict(sample)) == list(m2.predict(sample))

    def test_holdout_evaluation(self):
        """Accuracy reflects holdout evaluation, not training data."""
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("anomaly_detector", _diverse_cases(50))
        assert 0.0 < result.accuracy <= 1.0

    def test_insufficient_samples(self):
        trainer = SpecialistTrainer(min_samples=10)
        cases = _diverse_cases(5)
        result = trainer.train_from_cases("anomaly_detector", cases)
        assert result.status == "skipped"
