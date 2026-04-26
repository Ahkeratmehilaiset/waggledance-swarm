# SPDX-License-Identifier: BUSL-1.1
"""Phase 9 §N — Local Model Distillation (safe scaffold).

This package is intentionally a scaffold. Outputs of any local model
component MUST be treated as advisory/shadow by default and MUST NOT
mutate foundational knowledge automatically.

The CRITICAL invariants enforced here are:
- no live LLM/HTTP calls from this package
- no FAISS or other heavy retrieval imports
- no automatic fine-tune side effects in normal execution
- routing always advisory by default; only opt-in narrow profiles may
  enable shadow inference, never main-branch mutation
"""
from __future__ import annotations

from .local_model_manager import (
    LocalModelManager,
    LocalModelRecord,
    LocalModelManagerError,
)
from .fine_tune_pipeline import (
    FineTunePipeline,
    FineTuneJobSpec,
    FineTuneJobReport,
    FineTunePipelineError,
)
from .inference_router import (
    InferenceRouter,
    InferenceDecision,
    InferenceRouterError,
)
from .model_evaluator import (
    ModelEvaluator,
    ModelEvaluationReport,
)
from .drift_detector import (
    DriftDetector,
    DriftReport,
    DriftSeverity,
)


__all__ = [
    "LocalModelManager",
    "LocalModelRecord",
    "LocalModelManagerError",
    "FineTunePipeline",
    "FineTuneJobSpec",
    "FineTuneJobReport",
    "FineTunePipelineError",
    "InferenceRouter",
    "InferenceDecision",
    "InferenceRouterError",
    "ModelEvaluator",
    "ModelEvaluationReport",
    "DriftDetector",
    "DriftReport",
    "DriftSeverity",
]
