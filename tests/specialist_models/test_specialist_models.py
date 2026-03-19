"""
Regression gate: Specialist Model tests.

Validates specialist model training, canary lifecycle, model store,
and integration with case trajectories.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    CaseTrajectory, Goal, GoalType, QualityGrade,
    CapabilityCategory, CapabilityContract,
)
from waggledance.core.specialist_models.specialist_trainer import (
    SpecialistTrainer, SPECIALIST_MODELS,
)
from waggledance.core.specialist_models.model_store import ModelStore


def _gold_case(idx: int = 0) -> CaseTrajectory:
    return CaseTrajectory(
        goal=Goal(type=GoalType.OBSERVE, description=f"test {idx}"),
        selected_capabilities=[
            CapabilityContract(
                capability_id="solver.test",
                category=CapabilityCategory.SOLVE,
            ),
        ],
        verifier_result={"passed": True},
        quality_grade=QualityGrade.GOLD,
    )


class TestSpecialistTrainer:
    @pytest.fixture
    def trainer(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        return SpecialistTrainer(model_store=store)

    def test_model_list(self):
        assert len(SPECIALIST_MODELS) == 14
        assert "route_classifier" in SPECIALIST_MODELS

    def test_train_no_cases(self, trainer):
        result = trainer.train_from_cases("route_classifier", [])
        assert result.training_samples == 0

    def test_train_with_cases(self, trainer):
        cases = [_gold_case(i) for i in range(5)]
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.training_samples == 5
        assert result.model_id == "route_classifier"

    def test_canary_evaluation(self, trainer):
        cases = [_gold_case(i) for i in range(10)]
        trainer.train_from_cases("route_classifier", cases)
        result = trainer.evaluate_canary("route_classifier")
        # returns "promoted", "rolled_back", or None
        assert result in ("promoted", "rolled_back", None)

    def test_stats(self, trainer):
        s = trainer.stats()
        assert "min_samples" in s


class TestModelStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ModelStore(store_path=str(tmp_path / "models.json"))

    def test_register_version(self, store):
        mv = store.register_version("test_model", accuracy=0.9, training_samples=100)
        assert mv.model_id == "test_model"
        assert mv.version == 1

    def test_list_versions(self, store):
        store.register_version("test_model", accuracy=0.8, training_samples=50)
        store.register_version("test_model", accuracy=0.9, training_samples=100)
        versions = store.list_versions("test_model")
        assert len(versions) == 2

    def test_rollback(self, store):
        store.register_version("test_model", accuracy=0.8, training_samples=50)
        mv2 = store.register_version("test_model", accuracy=0.9, training_samples=100)
        rolled_back = store.rollback("test_model", mv2.version)
        assert rolled_back is not None

    def test_stats(self, store):
        store.register_version("test_model", accuracy=0.9)
        s = store.stats()
        assert "models" in s
        assert "test_model" in s["models"]

    def test_list_models(self, store):
        store.register_version("model_a")
        store.register_version("model_b")
        models = store.list_models()
        assert "model_a" in models
        assert "model_b" in models
