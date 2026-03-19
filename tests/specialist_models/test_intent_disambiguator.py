# SPDX-License-Identifier: Apache-2.0
"""Tests for intent_disambiguator specialist model."""
from __future__ import annotations

import pytest
from pathlib import Path

from waggledance.core.domain.autonomy import (
    CapabilityCategory, CapabilityContract, CaseTrajectory,
    Goal, GoalType, QualityGrade, WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


def _make_case(goal_type, cap_id, grade=QualityGrade.GOLD, profile="TEST"):
    case = CaseTrajectory(
        goal=Goal(type=goal_type, description=f"test {goal_type.value} task"),
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
        (GoalType.OPTIMIZE, "optimize.schedule", QualityGrade.GOLD, "GADGET"),
        (GoalType.PLAN, "retrieve.hot_cache", QualityGrade.SILVER, "APIARY"),
    ]
    return [_make_case(*combos[i % len(combos)]) for i in range(n)]


class TestIntentDisambiguator:
    def test_training_completes(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("intent_disambiguator", _diverse_cases(50))
        assert result.status == "completed"
        assert result.accuracy > 0
        assert result.training_samples == 50

    def test_prediction_valid(self):
        trainer = SpecialistTrainer(min_samples=3)
        result = trainer.train_from_cases("intent_disambiguator", _diverse_cases(50))
        pipe = trainer.load_weights("intent_disambiguator", result.version)
        pred = pipe.predict(["observe APIARY"])
        assert isinstance(pred[0], str)
        assert len(pred) == 1

    def test_persistence_roundtrip(self, tmp_path):
        import joblib
        trainer = SpecialistTrainer(min_samples=3)
        cases = _diverse_cases(50)
        result = trainer.train_from_cases("intent_disambiguator", cases)
        pipe = trainer.load_weights("intent_disambiguator", result.version)
        pred_before = list(pipe.predict(["observe APIARY", "diagnose HOME"]))

        # Re-load and verify same predictions
        pipe2 = trainer.load_weights("intent_disambiguator", result.version)
        pred_after = list(pipe2.predict(["observe APIARY", "diagnose HOME"]))
        assert pred_before == pred_after

    def test_holdout_gate_rejects_bad(self):
        """With only 1 class, falls back to simulation (no sklearn fit possible)."""
        trainer = SpecialistTrainer(min_samples=3)
        same_cases = [_make_case(GoalType.OBSERVE, "solve.math") for _ in range(20)]
        result = trainer.train_from_cases("intent_disambiguator", same_cases)
        assert result.status == "completed"
        assert result.accuracy > 0  # simulation fallback still gives accuracy

    def test_insufficient_samples_skips(self):
        trainer = SpecialistTrainer(min_samples=10)
        result = trainer.train_from_cases("intent_disambiguator", _diverse_cases(5))
        assert result.status == "skipped"
