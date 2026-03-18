"""
Specialist Trainer — trains specialist models from case trajectory data.

Specialist models are small, locally-trained models (Layer 2) that learn
patterns from gold/silver case trajectories:
  - Route classifier: learns optimal routing from case routing data
  - Capability selector: learns successful capability chains
  - Anomaly detector: learns baseline deviation patterns
  - Verifier prior: learns to predict action outcomes

Training flow:
  1. Collect gold/silver cases from quality gate
  2. Extract training features per model type
  3. Train new model version
  4. Start canary (10% traffic, 48h)
  5. If accuracy >= threshold: promote to production
  6. If accuracy < threshold: rollback

All training runs on CPU (no GPU needed for small specialists).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    CaseTrajectory,
    CapabilityCategory,
    QualityGrade,
)
from waggledance.core.specialist_models.model_store import (
    ModelStatus,
    ModelStore,
    ModelVersion,
)

log = logging.getLogger("waggledance.specialist_models.trainer")

# Known specialist model types
SPECIALIST_MODELS = [
    "route_classifier",
    "capability_selector",
    "anomaly_detector",
    "baseline_scorer",
    "approval_predictor",
    "missing_var_predictor",
    "verifier_prior",
    "domain_language_adapter",
]


@dataclass
class TrainingResult:
    """Result of a training run."""
    model_id: str
    version: int = 0
    accuracy: float = 0.0
    training_samples: int = 0
    duration_s: float = 0.0
    status: str = "completed"
    error: str = ""


class SpecialistTrainer:
    """
    Trains specialist models from case trajectory data.

    Integrates with ModelStore for lifecycle management
    (train → canary → promote/rollback).
    """

    def __init__(
        self,
        model_store: Optional[ModelStore] = None,
        min_samples: int = 3,
        min_accuracy: float = 0.85,
        canary_hours: int = 48,
    ):
        self._store = model_store or ModelStore(
            store_path="data/specialist_models.json"
        )
        self._min_samples = min_samples
        self._min_accuracy = min_accuracy
        self._canary_hours = canary_hours
        self._training_history: List[TrainingResult] = []

    def train_from_cases(
        self,
        model_id: str,
        cases: List[CaseTrajectory],
    ) -> TrainingResult:
        """
        Train a specialist model from case trajectories.

        Extracts features relevant to the model type, trains a new version,
        and registers it in the model store.
        """
        t0 = time.time()

        # 1. Extract training features
        features = self._extract_features(model_id, cases)
        if len(features) < self._min_samples:
            log.info("Skipped %s: %d features < %d min_samples",
                     model_id, len(features), self._min_samples)
            return TrainingResult(
                model_id=model_id,
                training_samples=len(features),
                status="skipped",
                error=f"Insufficient samples: {len(features)} < {self._min_samples}",
            )

        # 2. Simulate training (actual ML training would go here)
        accuracy = self._simulate_training(model_id, features)

        # 3. Register new version
        mv = self._store.register_version(
            model_id=model_id,
            accuracy=accuracy,
            training_samples=len(features),
            metadata={"feature_count": len(features)},
        )

        result = TrainingResult(
            model_id=model_id,
            version=mv.version,
            accuracy=accuracy,
            training_samples=len(features),
            duration_s=round(time.time() - t0, 3),
        )
        self._training_history.append(result)

        log.info("Trained %s v%d: accuracy=%.3f, samples=%d",
                 model_id, mv.version, accuracy, len(features))
        return result

    def train_all(self, cases: List[CaseTrajectory]) -> List[TrainingResult]:
        """Train all specialist models from available cases."""
        results = []
        for model_id in SPECIALIST_MODELS:
            result = self.train_from_cases(model_id, cases)
            results.append(result)
        return results

    def evaluate_canary(self, model_id: str) -> Optional[str]:
        """
        Evaluate canary model performance and decide: promote or rollback.

        Returns: "promoted", "rolled_back", or None (no canary).
        """
        canary = self._store.get_canary(model_id)
        if canary is None:
            return None

        # Check if canary period has elapsed
        elapsed_h = (time.time() - canary.trained_at) / 3600
        if elapsed_h < self._canary_hours:
            return None  # Still in canary period

        # Evaluate: compare canary vs production accuracy
        prod = self._store.get_production(model_id)
        prod_acc = prod.accuracy if prod else 0.0

        if canary.accuracy >= prod_acc and canary.accuracy >= self._min_accuracy:
            self._store.promote(model_id, canary.version)
            log.info("Promoted canary %s v%d (%.3f >= %.3f)",
                     model_id, canary.version, canary.accuracy, prod_acc)
            return "promoted"
        else:
            self._store.rollback(model_id, canary.version)
            log.info("Rolled back canary %s v%d (%.3f < threshold)",
                     model_id, canary.version, canary.accuracy)
            return "rolled_back"

    def start_canary(self, model_id: str) -> Optional[ModelVersion]:
        """Start canary testing for latest trained model."""
        latest = self._store.get_latest(model_id)
        if latest is None or latest.status != ModelStatus.TRAINING:
            return None
        return self._store.start_canary(model_id, latest.version)

    def evaluate_all_canaries(self) -> Dict[str, str]:
        """Evaluate all active canaries."""
        results = {}
        for model_id in self._store.list_models():
            result = self.evaluate_canary(model_id)
            if result:
                results[model_id] = result
        return results

    def stats(self) -> dict:
        return {
            "training_runs": len(self._training_history),
            "min_samples": self._min_samples,
            "min_accuracy": self._min_accuracy,
            "canary_hours": self._canary_hours,
            "model_store": self._store.stats(),
        }

    # ── Internal ───────────────────────────────────────────

    def _extract_features(
        self, model_id: str, cases: List[CaseTrajectory],
    ) -> List[Dict[str, Any]]:
        """Extract training features relevant to a specific model type."""
        features = []
        for case in cases:
            f = self._case_to_features(model_id, case)
            if f:
                features.append(f)
        return features

    def _case_to_features(
        self, model_id: str, case: CaseTrajectory,
    ) -> Optional[Dict[str, Any]]:
        """Extract features from a single case for a specific model."""
        if model_id == "route_classifier":
            return {
                "goal_type": case.goal.type.value if case.goal else "",
                "capabilities": [c.capability_id for c in case.selected_capabilities],
                "grade": case.quality_grade.value,
                "profile": case.profile,
            }
        elif model_id == "capability_selector":
            if not case.selected_capabilities:
                return None
            return {
                "goal_type": case.goal.type.value if case.goal else "",
                "chain": [c.capability_id for c in case.selected_capabilities],
                "success": case.quality_grade in (QualityGrade.GOLD, QualityGrade.SILVER),
            }
        elif model_id == "anomaly_detector":
            if not case.world_snapshot_before:
                return None
            return {
                "residuals": case.world_snapshot_before.residuals,
                "grade": case.quality_grade.value,
            }
        elif model_id == "verifier_prior":
            return {
                "capabilities": [c.capability_id for c in case.selected_capabilities],
                "verifier_passed": case.verifier_result.get("passed", False),
                "grade": case.quality_grade.value,
            }
        else:
            # Generic features for other model types
            return {
                "goal_type": case.goal.type.value if case.goal else "",
                "grade": case.quality_grade.value,
                "profile": case.profile,
            }

    def _simulate_training(
        self, model_id: str, features: List[Dict[str, Any]],
    ) -> float:
        """
        Simulate model training and return accuracy.

        In production, this would run actual sklearn/PyTorch training.
        For now, estimate accuracy from data quality distribution.
        """
        if not features:
            return 0.0

        # Estimate accuracy from grade distribution
        gold = sum(1 for f in features if f.get("grade") == "gold")
        silver = sum(1 for f in features if f.get("grade") == "silver")
        total = len(features)

        # Better data → higher simulated accuracy
        gold_ratio = gold / total if total > 0 else 0
        silver_ratio = silver / total if total > 0 else 0

        base_accuracy = 0.7
        accuracy = base_accuracy + (gold_ratio * 0.2) + (silver_ratio * 0.1)
        return min(accuracy, 0.99)
