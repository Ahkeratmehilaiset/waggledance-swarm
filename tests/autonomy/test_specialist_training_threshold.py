"""
Tests for specialist training threshold fix.

Root cause: min_samples defaulted to 10, but bootstrap scenarios
only produce 3-5 cases → all models skipped. Default lowered to 3.
"""

from __future__ import annotations

import pytest

from waggledance.core.domain.autonomy import (
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.specialist_models.specialist_trainer import (
    SpecialistTrainer,
    SPECIALIST_MODELS,
)


def _make_case(grade: QualityGrade = QualityGrade.SILVER) -> CaseTrajectory:
    """Create a minimal case trajectory for training."""
    from waggledance.core.domain.autonomy import CapabilityCategory, CapabilityContract

    case = CaseTrajectory(
        goal=Goal(type=GoalType.OBSERVE, description="test query"),
        selected_capabilities=[
            CapabilityContract(
                capability_id="solve.math",
                category=CapabilityCategory.SOLVE,
            ),
        ],
        verifier_result={"passed": True, "confidence": 0.8},
        world_snapshot_before=WorldSnapshot(
            residuals={"hive1.temp": 0.5},
        ),
        profile="TEST",
    )
    case.quality_grade = grade
    return case


class TestMinSamplesThreshold:
    def test_default_min_samples_is_3(self):
        trainer = SpecialistTrainer()
        assert trainer._min_samples == 3

    def test_5_cases_trains_at_least_one_model(self):
        """With 5 silver cases and min_samples=3, at least one model should train."""
        trainer = SpecialistTrainer()
        cases = [_make_case(QualityGrade.SILVER) for _ in range(5)]

        results = trainer.train_all(cases)
        completed = [r for r in results if r.status == "completed"]

        assert len(completed) > 0, (
            f"Expected at least 1 model trained from 5 cases, "
            f"got: {[(r.model_id, r.status, r.error) for r in results]}"
        )

    def test_3_cases_trains_at_least_one_model(self):
        """Even 3 cases should be enough for some models."""
        trainer = SpecialistTrainer()
        cases = [_make_case(QualityGrade.GOLD) for _ in range(3)]

        results = trainer.train_all(cases)
        completed = [r for r in results if r.status == "completed"]

        assert len(completed) > 0, (
            f"3 cases should be enough for at least 1 model, "
            f"got: {[(r.model_id, r.status, r.error) for r in results]}"
        )

    def test_2_cases_still_skipped(self):
        """2 cases should still be below min_samples=3."""
        trainer = SpecialistTrainer()
        cases = [_make_case() for _ in range(2)]

        results = trainer.train_all(cases)
        completed = [r for r in results if r.status == "completed"]

        assert len(completed) == 0, "2 cases should not be enough"

    def test_explicit_min_samples_override(self):
        """Can still override min_samples for strict scenarios."""
        trainer = SpecialistTrainer(min_samples=10)
        cases = [_make_case() for _ in range(5)]

        results = trainer.train_all(cases)
        completed = [r for r in results if r.status == "completed"]

        assert len(completed) == 0, "5 < 10, should all be skipped"

    def test_skipped_result_has_error_message(self):
        """Skipped results should include an informative error."""
        trainer = SpecialistTrainer(min_samples=100)
        cases = [_make_case() for _ in range(5)]

        results = trainer.train_all(cases)
        for r in results:
            if r.status == "skipped":
                assert "Insufficient samples" in r.error
                assert "100" in r.error


class TestEndToEndNightTraining:
    def test_night_learning_trains_with_5_cases(self):
        """Full pipeline: 5 handle_query → night learning → models_trained > 0."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        for q in ["2+2", "3*5", "10/2", "paljonko on 7+1", "150*0.08"]:
            rt.handle_query(q)

        result = rt.run_night_learning()
        rt.stop()

        assert result["models_trained"] > 0, (
            f"Expected models_trained > 0 with 5 queries, got: {result}"
        )

    def test_night_learning_quality_counts(self):
        """Night learning should report quality distribution."""
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()

        for q in ["2+2", "3*5", "10/2"]:
            rt.handle_query(q)

        result = rt.run_night_learning()
        rt.stop()

        quality = result.get("quality", {})
        total = sum(quality.values())
        assert total > 0, f"Expected graded cases, got quality={quality}"
