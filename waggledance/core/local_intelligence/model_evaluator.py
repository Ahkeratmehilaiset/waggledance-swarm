# SPDX-License-Identifier: BUSL-1.1
"""Model evaluator — Phase 9 §N.

Pure scoring of model outputs against shadow eval sets. The evaluator
DOES NOT pull a model output itself; it consumes precomputed
(predicted, gold) pairs and returns a deterministic report. The
report is advisory and never gates a runtime mutation directly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Sequence


class _ModelEvaluatorError(ValueError):
    """Internal sentinel — surfaced as ValueError to callers."""


@dataclass(frozen=True)
class ModelEvaluationReport:
    eval_set_id: str
    model_id: str
    model_version: str
    n_examples: int
    n_correct: int
    accuracy: float
    advisory_only: bool
    no_runtime_authority: bool

    def __post_init__(self) -> None:
        if self.n_examples <= 0:
            raise _ModelEvaluatorError("n_examples must be > 0")
        if self.n_correct < 0 or self.n_correct > self.n_examples:
            raise _ModelEvaluatorError("n_correct out of range")
        if self.advisory_only is not True:
            raise _ModelEvaluatorError("advisory_only must be True")
        if self.no_runtime_authority is not True:
            raise _ModelEvaluatorError("no_runtime_authority must be True")

    def to_dict(self) -> dict:
        return {
            "eval_set_id": self.eval_set_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "n_examples": self.n_examples,
            "n_correct": self.n_correct,
            "accuracy": self.accuracy,
            "advisory_only": self.advisory_only,
            "no_runtime_authority": self.no_runtime_authority,
        }


def compute_eval_set_id(pairs: Sequence[tuple[str, str]]) -> str:
    canon = json.dumps([list(p) for p in pairs], sort_keys=False).encode(
        "utf-8")
    return hashlib.sha256(canon).hexdigest()[:12]


@dataclass
class ModelEvaluator:
    def score(self, model_id: str, model_version: str,
              pairs: Sequence[tuple[str, str]]) -> ModelEvaluationReport:
        if not pairs:
            raise _ModelEvaluatorError("pairs must be non-empty")
        n = len(pairs)
        correct = sum(1 for predicted, gold in pairs if predicted == gold)
        return ModelEvaluationReport(
            eval_set_id=compute_eval_set_id(pairs),
            model_id=model_id,
            model_version=model_version,
            n_examples=n,
            n_correct=correct,
            accuracy=correct / n,
            advisory_only=True,
            no_runtime_authority=True,
        )

    def call_model(self, *args, **kwargs) -> None:
        # Intentional: evaluator never invokes a model directly.
        raise _ModelEvaluatorError(
            "evaluator does not invoke models; supply precomputed pairs"
        )
