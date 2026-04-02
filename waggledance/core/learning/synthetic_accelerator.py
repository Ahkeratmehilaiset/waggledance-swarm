"""Synthetic Training Accelerator — augments specialist training with deterministic synthetic data.

Builds synthetic tabular training rows from existing trusted cases via
deterministic transformations. Augments sparse classes to improve specialist
model balance. Logs provenance of synthetic vs real rows.

Optional GPU acceleration via cuML/RAPIDS when explicitly enabled and available.
CPU fallback is always safe and fully supported.
"""

import copy
import hashlib
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Deterministic seed for reproducibility
_RNG_SEED = 42


@dataclass
class AcceleratorMetrics:
    """Metrics from a single accelerated training run."""

    model_id: str = ""
    real_rows: int = 0
    synthetic_rows: int = 0
    total_rows: int = 0
    device_used: str = "cpu"
    train_time_ms: float = 0.0
    class_balance_before: Dict[str, int] = field(default_factory=dict)
    class_balance_after: Dict[str, int] = field(default_factory=dict)
    augmentation_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "real_rows": self.real_rows,
            "synthetic_rows": self.synthetic_rows,
            "total_rows": self.total_rows,
            "device_used": self.device_used,
            "train_time_ms": round(self.train_time_ms, 1),
            "class_balance_before": self.class_balance_before,
            "class_balance_after": self.class_balance_after,
            "augmentation_ratio": round(self.augmentation_ratio, 3),
        }


@dataclass
class AcceleratorStatus:
    """Overall accelerator status and cumulative metrics."""

    total_runs: int = 0
    total_real_rows: int = 0
    total_synthetic_rows: int = 0
    gpu_available: bool = False
    gpu_enabled: bool = False
    device_used: str = "cpu"
    last_metrics: Optional[AcceleratorMetrics] = None

    def to_dict(self) -> dict:
        return {
            "total_runs": self.total_runs,
            "total_real_rows": self.total_real_rows,
            "total_synthetic_rows": self.total_synthetic_rows,
            "gpu_available": self.gpu_available,
            "gpu_enabled": self.gpu_enabled,
            "device_used": self.device_used,
            "last_metrics": self.last_metrics.to_dict() if self.last_metrics else None,
        }


def _detect_gpu() -> bool:
    """Check if CUDA GPU is available for cuML/RAPIDS."""
    try:
        import cuml  # noqa: F401
        return True
    except ImportError:
        return False


class SyntheticTrainingAccelerator:
    """Augments specialist training data with deterministic synthetic rows.

    Strategies:
    1. Class balance: oversample minority classes to match majority
    2. Deterministic perturbation: apply bounded noise to numeric features
    3. Provenance: all synthetic rows are tagged for audit

    GPU acceleration (cuML) is optional and off by default.
    """

    def __init__(
        self,
        gpu_enabled: bool = False,
        max_augmentation_ratio: float = 3.0,
        min_class_samples: int = 3,
        seed: int = _RNG_SEED,
    ):
        self._gpu_enabled = gpu_enabled
        self._gpu_available = _detect_gpu()
        self._max_aug_ratio = max_augmentation_ratio
        self._min_class_samples = min_class_samples
        self._seed = seed
        self._rng = random.Random(seed)
        self._status = AcceleratorStatus(
            gpu_available=self._gpu_available,
            gpu_enabled=gpu_enabled,
            device_used=self._resolve_device(),
        )

    @property
    def device(self) -> str:
        return self._resolve_device()

    def _resolve_device(self) -> str:
        if self._gpu_enabled and self._gpu_available:
            return "cuda"
        return "cpu"

    def status(self) -> dict:
        return self._status.to_dict()

    def augment_features(
        self,
        model_id: str,
        features: List[Dict[str, Any]],
        label_key: str = "grade",
    ) -> Tuple[List[Dict[str, Any]], AcceleratorMetrics]:
        """Augment a feature set with synthetic rows for class balance.

        Args:
            model_id: Specialist model identifier.
            features: Original training features (list of dicts).
            label_key: The dict key used as the class label.

        Returns:
            (augmented_features, metrics) where augmented_features includes
            both real and synthetic rows.
        """
        t0 = time.time()
        metrics = AcceleratorMetrics(
            model_id=model_id,
            real_rows=len(features),
            device_used=self._resolve_device(),
        )

        if not features:
            metrics.total_rows = 0
            metrics.train_time_ms = (time.time() - t0) * 1000
            self._record_run(metrics)
            return features, metrics

        # Compute class distribution
        labels = [f.get(label_key, "unknown") for f in features]
        class_counts = dict(Counter(labels))
        metrics.class_balance_before = dict(class_counts)

        if len(class_counts) < 2:
            # Single class — no augmentation needed
            metrics.total_rows = len(features)
            metrics.class_balance_after = dict(class_counts)
            metrics.train_time_ms = (time.time() - t0) * 1000
            self._record_run(metrics)
            return features, metrics

        # Target: bring all classes up to majority count (capped)
        max_count = max(class_counts.values())
        target_count = min(
            max_count,
            int(len(features) * self._max_aug_ratio / len(class_counts)),
        )

        synthetic_rows = []
        for label, count in class_counts.items():
            if count >= target_count:
                continue
            # Get rows of this class
            class_rows = [f for f in features if f.get(label_key) == label]
            needed = target_count - count
            for i in range(needed):
                # Pick a source row deterministically
                source = class_rows[i % len(class_rows)]
                syn = self._perturb_row(source, model_id, i)
                syn["_synthetic"] = True
                syn["_source_hash"] = self._row_hash(source)
                synthetic_rows.append(syn)

        augmented = list(features) + synthetic_rows
        metrics.synthetic_rows = len(synthetic_rows)
        metrics.total_rows = len(augmented)
        metrics.augmentation_ratio = (
            len(synthetic_rows) / len(features) if features else 0.0
        )

        # Recompute class balance
        aug_labels = [f.get(label_key, "unknown") for f in augmented]
        metrics.class_balance_after = dict(Counter(aug_labels))

        metrics.train_time_ms = (time.time() - t0) * 1000
        self._record_run(metrics)

        log.info(
            "Augmented %s: %d real + %d synthetic = %d total (device=%s)",
            model_id,
            metrics.real_rows,
            metrics.synthetic_rows,
            metrics.total_rows,
            metrics.device_used,
        )
        return augmented, metrics

    def _perturb_row(
        self, source: Dict[str, Any], model_id: str, index: int
    ) -> Dict[str, Any]:
        """Create a synthetic row by deterministically perturbing a source row.

        Numeric values get bounded noise. Categorical values stay unchanged.
        """
        row = copy.deepcopy(source)
        # Seed based on source + index for determinism
        seed_str = f"{model_id}:{self._row_hash(source)}:{index}"
        rng = random.Random(seed_str)

        for key, value in row.items():
            if key.startswith("_"):
                continue
            if isinstance(value, (int, float)) and key not in (
                "has_world_snapshot",
            ):
                # Apply bounded perturbation (±10%)
                noise = rng.uniform(-0.1, 0.1) * (abs(value) + 0.01)
                if isinstance(value, int):
                    row[key] = max(0, value + int(round(noise)))
                else:
                    row[key] = round(value + noise, 6)
            elif isinstance(value, list) and all(
                isinstance(v, (int, float)) for v in value
            ):
                # Perturb numeric lists (e.g., residuals, features)
                row[key] = [
                    round(v + rng.uniform(-0.1, 0.1) * (abs(v) + 0.01), 6)
                    for v in value
                ]
        return row

    @staticmethod
    def _row_hash(row: Dict[str, Any]) -> str:
        """Deterministic hash of a feature row for provenance."""
        # Sort keys for stability
        items = sorted(
            (k, str(v)) for k, v in row.items() if not k.startswith("_")
        )
        content = "|".join(f"{k}={v}" for k, v in items)
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _record_run(self, metrics: AcceleratorMetrics) -> None:
        """Update cumulative status."""
        self._status.total_runs += 1
        self._status.total_real_rows += metrics.real_rows
        self._status.total_synthetic_rows += metrics.synthetic_rows
        self._status.last_metrics = metrics
