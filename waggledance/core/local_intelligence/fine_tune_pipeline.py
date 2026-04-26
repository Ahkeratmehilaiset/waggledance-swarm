"""Fine-tune pipeline — Phase 9 §N.

Pure scaffolding for offline fine-tune orchestration. This module:
- defines the deterministic JobSpec / JobReport contracts
- enumerates the legal pipeline stages
- NEVER performs an actual fine-tune in normal execution
- NEVER invokes a subprocess; the pipeline only PLANS and validates

A FineTuneJobReport produced by .plan(spec) carries
fine_tune_executed=False and human_required=True. The only legal way
to flip those flags is an explicit human-gated review step that lives
OUTSIDE this scaffold.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Mapping


_ALLOWED_PIPELINE_STAGES = (
    "spec_validated",
    "corpus_pinned",
    "shadow_eval_planned",
    "drift_check_planned",
    "human_review_required",
)

_FORBIDDEN_DATA_SOURCES = (
    "production_runtime_state",
    "main_branch_axioms",
    "foundational_invariants",
    "user_secrets",
)


class FineTunePipelineError(ValueError):
    """Raised when a job spec/plan violates an invariant."""


@dataclass(frozen=True)
class FineTuneJobSpec:
    base_model_family: str
    training_corpus_id: str
    training_corpus_hash: str
    target_capability: str
    training_data_source_kind: str
    max_train_examples: int
    no_foundational_data: bool
    no_runtime_mutation: bool

    def __post_init__(self) -> None:
        if not self.base_model_family:
            raise FineTunePipelineError("base_model_family required")
        if not self.training_corpus_hash:
            raise FineTunePipelineError("training_corpus_hash required")
        if self.max_train_examples <= 0:
            raise FineTunePipelineError("max_train_examples must be > 0")
        if self.training_data_source_kind in _FORBIDDEN_DATA_SOURCES:
            raise FineTunePipelineError(
                f"training_data_source_kind {self.training_data_source_kind!r} "
                f"is forbidden for local fine-tune"
            )
        if self.no_foundational_data is not True:
            raise FineTunePipelineError(
                "no_foundational_data must be True"
            )
        if self.no_runtime_mutation is not True:
            raise FineTunePipelineError(
                "no_runtime_mutation must be True"
            )

    def to_dict(self) -> dict:
        return {
            "base_model_family": self.base_model_family,
            "training_corpus_id": self.training_corpus_id,
            "training_corpus_hash": self.training_corpus_hash,
            "target_capability": self.target_capability,
            "training_data_source_kind": self.training_data_source_kind,
            "max_train_examples": self.max_train_examples,
            "no_foundational_data": self.no_foundational_data,
            "no_runtime_mutation": self.no_runtime_mutation,
        }


def compute_job_id(spec: FineTuneJobSpec) -> str:
    payload = json.dumps(spec.to_dict(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


@dataclass(frozen=True)
class FineTuneJobReport:
    job_id: str
    spec: FineTuneJobSpec
    pipeline_stages: tuple[str, ...]
    fine_tune_executed: bool
    human_required: bool
    advisory_only: bool
    no_runtime_auto_promotion: bool

    def __post_init__(self) -> None:
        if self.fine_tune_executed is not False:
            raise FineTunePipelineError(
                "fine_tune_executed must be False inside scaffold"
            )
        if self.human_required is not True:
            raise FineTunePipelineError(
                "human_required must be True inside scaffold"
            )
        if self.advisory_only is not True:
            raise FineTunePipelineError(
                "advisory_only must be True inside scaffold"
            )
        if self.no_runtime_auto_promotion is not True:
            raise FineTunePipelineError(
                "no_runtime_auto_promotion must be True"
            )
        for s in self.pipeline_stages:
            if s not in _ALLOWED_PIPELINE_STAGES:
                raise FineTunePipelineError(
                    f"unknown pipeline stage {s!r}"
                )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "spec": self.spec.to_dict(),
            "pipeline_stages": list(self.pipeline_stages),
            "fine_tune_executed": self.fine_tune_executed,
            "human_required": self.human_required,
            "advisory_only": self.advisory_only,
            "no_runtime_auto_promotion": self.no_runtime_auto_promotion,
        }


@dataclass
class FineTunePipeline:
    def plan(self, spec: FineTuneJobSpec) -> FineTuneJobReport:
        # Deterministic plan: each spec produces the same set of
        # planned stages; the scaffold never executes anything.
        stages = (
            "spec_validated",
            "corpus_pinned",
            "shadow_eval_planned",
            "drift_check_planned",
            "human_review_required",
        )
        return FineTuneJobReport(
            job_id=compute_job_id(spec),
            spec=spec,
            pipeline_stages=stages,
            fine_tune_executed=False,
            human_required=True,
            advisory_only=True,
            no_runtime_auto_promotion=True,
        )

    def execute(self, *args, **kwargs) -> None:
        # Intentional: there is no execute path inside the scaffold.
        raise FineTunePipelineError(
            "fine-tune execution is intentionally not implemented in "
            "this scaffold; route through human-gated review pipeline"
        )
