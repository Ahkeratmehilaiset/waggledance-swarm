"""
Model Store — persistence and lifecycle for specialist models.

Specialist models are small locally-trained models (Layer 2) that
learn from CaseTrajectories:
  - Route classifier
  - Capability selector
  - Anomaly detector
  - Baseline scorer
  - Approval predictor
  - Missing-var predictor
  - Verifier prior
  - Domain language adapter

Each model goes through: train → canary → promote/rollback lifecycle.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.specialist_models.store")


class ModelStatus(Enum):
    """Lifecycle status of a specialist model."""
    TRAINING = "training"
    CANARY = "canary"  # 10% traffic for 48h
    PRODUCTION = "production"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


@dataclass
class ModelVersion:
    """A single version of a specialist model."""
    model_id: str
    version: int
    status: ModelStatus = ModelStatus.TRAINING
    accuracy: float = 0.0
    training_samples: int = 0
    trained_at: float = field(default_factory=time.time)
    promoted_at: Optional[float] = None
    rolled_back_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    weight_path: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "model_id": self.model_id,
            "version": self.version,
            "status": self.status.value,
            "accuracy": self.accuracy,
            "training_samples": self.training_samples,
            "trained_at": self.trained_at,
            "promoted_at": self.promoted_at,
            "rolled_back_at": self.rolled_back_at,
        }
        if self.weight_path:
            d["weight_path"] = self.weight_path
        return d


class ModelStore:
    """
    Manages specialist model versions and their lifecycle.

    Stores model metadata (not weights) in JSON. Model weights
    would be stored as .pt or .pkl files in the models directory.
    """

    def __init__(self, store_path: str = "data/specialist_models.json"):
        self._store_path = Path(store_path)
        self._models: Dict[str, List[ModelVersion]] = {}
        self._load()

    # ── Registration ──────────────────────────────────────

    def register_version(
        self,
        model_id: str,
        accuracy: float = 0.0,
        training_samples: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        weight_path: Optional[str] = None,
    ) -> ModelVersion:
        """Register a new trained version of a model."""
        if model_id not in self._models:
            self._models[model_id] = []

        version_num = len(self._models[model_id]) + 1
        mv = ModelVersion(
            model_id=model_id,
            version=version_num,
            accuracy=accuracy,
            training_samples=training_samples,
            metadata=metadata or {},
            weight_path=weight_path,
        )
        self._models[model_id].append(mv)
        self._save()
        log.info("Registered %s v%d (accuracy=%.3f, samples=%d)",
                 model_id, version_num, accuracy, training_samples)
        return mv

    # ── Lifecycle ─────────────────────────────────────────

    def start_canary(self, model_id: str, version: Optional[int] = None) -> Optional[ModelVersion]:
        """Move a model version to canary status."""
        mv = self._get_version(model_id, version)
        if mv is None:
            return None
        mv.status = ModelStatus.CANARY
        self._save()
        log.info("Canary started: %s v%d", model_id, mv.version)
        return mv

    def promote(self, model_id: str, version: Optional[int] = None) -> Optional[ModelVersion]:
        """Promote a canary model to production."""
        mv = self._get_version(model_id, version)
        if mv is None:
            return None

        # Rollback current production version
        for v in self._models.get(model_id, []):
            if v.status == ModelStatus.PRODUCTION and v.version != mv.version:
                v.status = ModelStatus.RETIRED

        mv.status = ModelStatus.PRODUCTION
        mv.promoted_at = time.time()
        self._save()
        log.info("Promoted: %s v%d to production", model_id, mv.version)
        return mv

    def rollback(self, model_id: str, version: Optional[int] = None) -> Optional[ModelVersion]:
        """Rollback a canary model."""
        mv = self._get_version(model_id, version)
        if mv is None:
            return None
        mv.status = ModelStatus.ROLLED_BACK
        mv.rolled_back_at = time.time()
        self._save()
        log.info("Rolled back: %s v%d", model_id, mv.version)
        return mv

    # ── Query ─────────────────────────────────────────────

    def get_production(self, model_id: str) -> Optional[ModelVersion]:
        """Get the current production version of a model."""
        for v in reversed(self._models.get(model_id, [])):
            if v.status == ModelStatus.PRODUCTION:
                return v
        return None

    def get_canary(self, model_id: str) -> Optional[ModelVersion]:
        """Get the current canary version of a model."""
        for v in reversed(self._models.get(model_id, [])):
            if v.status == ModelStatus.CANARY:
                return v
        return None

    def get_latest(self, model_id: str) -> Optional[ModelVersion]:
        versions = self._models.get(model_id, [])
        return versions[-1] if versions else None

    def list_models(self) -> List[str]:
        return list(self._models.keys())

    def list_versions(self, model_id: str) -> List[ModelVersion]:
        return list(self._models.get(model_id, []))

    def stats(self) -> dict:
        result: Dict[str, Any] = {"models": {}}
        for model_id, versions in self._models.items():
            prod = self.get_production(model_id)
            canary = self.get_canary(model_id)
            result["models"][model_id] = {
                "total_versions": len(versions),
                "production": prod.version if prod else None,
                "canary": canary.version if canary else None,
            }
        return result

    # ── Persistence ───────────────────────────────────────

    def _save(self):
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for model_id, versions in self._models.items():
            data[model_id] = [v.to_dict() for v in versions]
        self._store_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load(self):
        if not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            for model_id, versions in data.items():
                self._models[model_id] = [
                    ModelVersion(
                        model_id=v["model_id"],
                        version=v["version"],
                        status=ModelStatus(v["status"]),
                        accuracy=v.get("accuracy", 0.0),
                        training_samples=v.get("training_samples", 0),
                        trained_at=v.get("trained_at", 0.0),
                        promoted_at=v.get("promoted_at"),
                        rolled_back_at=v.get("rolled_back_at"),
                        weight_path=v.get("weight_path"),
                    )
                    for v in versions
                ]
        except Exception as e:
            log.warning("Failed to load model store: %s", e)

    # ── Internal ──────────────────────────────────────────

    def _get_version(self, model_id: str, version: Optional[int] = None) -> Optional[ModelVersion]:
        versions = self._models.get(model_id, [])
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for v in versions:
            if v.version == version:
                return v
        return None
