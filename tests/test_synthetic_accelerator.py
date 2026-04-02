"""Tests for SyntheticTrainingAccelerator — P3 of v3.5.0."""

import pytest

from waggledance.core.learning.synthetic_accelerator import (
    AcceleratorMetrics,
    SyntheticTrainingAccelerator,
)


# ── Fixtures ────────────────────────────────────────────────────


def _make_features(grade_distribution: dict, extra_fields: dict = None):
    """Create a set of features with given grade distribution.

    Args:
        grade_distribution: {"gold": 5, "silver": 2, "bronze": 1}
        extra_fields: Additional fields per row.
    """
    features = []
    for grade, count in grade_distribution.items():
        for i in range(count):
            f = {
                "goal_type": "observe",
                "profile": "HOME",
                "n_capabilities": 2 + i,
                "has_world_snapshot": 1,
                "grade": grade,
            }
            if extra_fields:
                f.update(extra_fields)
            features.append(f)
    return features


# ── Synthetic Row Generation ─────────────────────────────────────


class TestSyntheticGeneration:
    """Verify synthetic row generation on fixture cases."""

    def test_augments_minority_class(self):
        features = _make_features({"gold": 10, "silver": 2})
        acc = SyntheticTrainingAccelerator()
        augmented, metrics = acc.augment_features("route_classifier", features)

        assert metrics.real_rows == 12
        assert metrics.synthetic_rows > 0
        assert metrics.total_rows == len(augmented)
        assert metrics.class_balance_after["silver"] > metrics.class_balance_before["silver"]

    def test_synthetic_rows_tagged(self):
        features = _make_features({"gold": 5, "bronze": 1})
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("quality_grader", features)

        synthetic = [f for f in augmented if f.get("_synthetic")]
        real = [f for f in augmented if not f.get("_synthetic")]
        assert len(real) == 6
        assert len(synthetic) > 0
        for s in synthetic:
            assert "_source_hash" in s

    def test_provenance_hash_present(self):
        features = _make_features({"gold": 5, "silver": 1})
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("test_model", features)

        synthetic = [f for f in augmented if f.get("_synthetic")]
        for s in synthetic:
            assert len(s["_source_hash"]) == 12  # SHA256[:12]

    def test_numeric_perturbation_bounded(self):
        features = [{"value": 100.0, "grade": "gold"}] * 5 + [{"value": 100.0, "grade": "silver"}]
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("test", features)

        synthetic = [f for f in augmented if f.get("_synthetic")]
        for s in synthetic:
            # Perturbation is ±10% of value
            assert 89.0 <= s["value"] <= 111.0

    def test_list_perturbation(self):
        features = [
            {"features": [1.0, 2.0, 3.0], "grade": "gold"},
        ] * 5 + [
            {"features": [1.0, 2.0, 3.0], "grade": "silver"},
        ]
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("test", features)

        synthetic = [f for f in augmented if f.get("_synthetic")]
        for s in synthetic:
            assert len(s["features"]) == 3
            for v in s["features"]:
                assert isinstance(v, float)


# ── Zero-Synthetic Path ──────────────────────────────────────────


class TestZeroSyntheticPath:
    """Verify zero-synthetic path still works."""

    def test_balanced_data_no_augmentation(self):
        features = _make_features({"gold": 5, "silver": 5})
        acc = SyntheticTrainingAccelerator()
        augmented, metrics = acc.augment_features("test", features)

        assert metrics.synthetic_rows == 0
        assert metrics.total_rows == 10
        assert len(augmented) == 10

    def test_single_class_no_augmentation(self):
        features = _make_features({"gold": 10})
        acc = SyntheticTrainingAccelerator()
        augmented, metrics = acc.augment_features("test", features)

        assert metrics.synthetic_rows == 0
        assert metrics.total_rows == 10

    def test_empty_features(self):
        acc = SyntheticTrainingAccelerator()
        augmented, metrics = acc.augment_features("test", [])

        assert metrics.real_rows == 0
        assert metrics.synthetic_rows == 0
        assert metrics.total_rows == 0
        assert augmented == []


# ── GPU Fallback ─────────────────────────────────────────────────


class TestGPUFallback:
    """Verify GPU unavailable -> clean CPU fallback."""

    def test_cpu_default(self):
        acc = SyntheticTrainingAccelerator(gpu_enabled=False)
        assert acc.device == "cpu"
        status = acc.status()
        assert status["device_used"] == "cpu"
        assert status["gpu_enabled"] is False

    def test_gpu_enabled_but_unavailable(self):
        """When GPU is enabled but cuML not installed, falls back to CPU."""
        acc = SyntheticTrainingAccelerator(gpu_enabled=True)
        # cuML is not installed in test env, so should be CPU
        assert acc.device == "cpu"
        status = acc.status()
        assert status["device_used"] == "cpu"
        assert status["gpu_enabled"] is True
        assert status["gpu_available"] is False

    def test_augmentation_works_on_cpu(self):
        features = _make_features({"gold": 8, "bronze": 2})
        acc = SyntheticTrainingAccelerator(gpu_enabled=True)
        augmented, metrics = acc.augment_features("test", features)

        assert metrics.device_used == "cpu"
        assert metrics.total_rows >= 10
        assert len(augmented) == metrics.total_rows


# ── Trainer Output Shape ─────────────────────────────────────────


class TestTrainerOutputShape:
    """Verify trainer output shape unchanged after augmentation."""

    def test_augmented_rows_have_same_keys(self):
        features = _make_features({"gold": 5, "silver": 1})
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("test", features)

        original_keys = set(features[0].keys())
        for row in augmented:
            row_keys = {k for k in row.keys() if not k.startswith("_")}
            assert row_keys == original_keys

    def test_label_key_preserved(self):
        features = _make_features({"gold": 5, "silver": 1})
        acc = SyntheticTrainingAccelerator()
        augmented, _ = acc.augment_features("test", features, label_key="grade")

        for row in augmented:
            assert "grade" in row
            assert row["grade"] in ("gold", "silver")


# ── Class Balance Guard ──────────────────────────────────────────


class TestClassBalanceGuard:
    """Verify class-balance guard coverage."""

    def test_max_augmentation_ratio_respected(self):
        features = _make_features({"gold": 100, "bronze": 1})
        acc = SyntheticTrainingAccelerator(max_augmentation_ratio=2.0)
        _, metrics = acc.augment_features("test", features)

        assert metrics.augmentation_ratio <= 2.0

    def test_augmentation_improves_balance(self):
        features = _make_features({"gold": 10, "silver": 2, "bronze": 1})
        acc = SyntheticTrainingAccelerator()
        _, metrics = acc.augment_features("test", features)

        before = metrics.class_balance_before
        after = metrics.class_balance_after

        # Minority classes should have more samples after
        assert after["silver"] >= before["silver"]
        assert after["bronze"] >= before["bronze"]

    def test_metrics_to_dict(self):
        features = _make_features({"gold": 5, "silver": 2})
        acc = SyntheticTrainingAccelerator()
        _, metrics = acc.augment_features("test", features)

        d = metrics.to_dict()
        assert "real_rows" in d
        assert "synthetic_rows" in d
        assert "total_rows" in d
        assert "device_used" in d
        assert "train_time_ms" in d
        assert "class_balance_before" in d
        assert "class_balance_after" in d
        assert "augmentation_ratio" in d


# ── Determinism ──────────────────────────────────────────────────


class TestDeterminism:
    """Verify deterministic output from fixed inputs."""

    def test_same_input_same_output(self):
        features = _make_features({"gold": 5, "silver": 1})
        acc1 = SyntheticTrainingAccelerator(seed=42)
        acc2 = SyntheticTrainingAccelerator(seed=42)

        aug1, m1 = acc1.augment_features("test", features)
        aug2, m2 = acc2.augment_features("test", features)

        assert len(aug1) == len(aug2)
        assert m1.synthetic_rows == m2.synthetic_rows

        # Compare synthetic rows
        syn1 = [f for f in aug1 if f.get("_synthetic")]
        syn2 = [f for f in aug2 if f.get("_synthetic")]
        for s1, s2 in zip(syn1, syn2):
            assert s1 == s2


# ── No Regression ────────────────────────────────────────────────


class TestNoRegression:
    """Verify existing learning tests are not broken."""

    def test_specialist_trainer_import(self):
        """SpecialistTrainer should still import cleanly."""
        from waggledance.core.specialist_models.specialist_trainer import (
            SpecialistTrainer,
            SPECIALIST_MODELS,
        )
        assert len(SPECIALIST_MODELS) == 14
        assert SpecialistTrainer is not None

    def test_night_pipeline_import(self):
        """NightLearningPipeline should still import cleanly."""
        from waggledance.core.learning.night_learning_pipeline import (
            NightLearningPipeline,
        )
        assert NightLearningPipeline is not None

    def test_accelerator_status(self):
        acc = SyntheticTrainingAccelerator()
        status = acc.status()
        assert status["total_runs"] == 0
        assert status["gpu_enabled"] is False
        assert status["device_used"] == "cpu"
        assert status["last_metrics"] is None
