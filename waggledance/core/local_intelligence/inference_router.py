"""Inference router — Phase 9 §N.

Routes inference requests between local-model candidates and the
external provider plane. The router enforces a SAFE ROUTING CONTRACT:

- For any request marked critical / foundational / runtime-mutating,
  the router MUST refuse to route to a local model.
- For non-critical requests, local models may be selected only as
  shadow / advisory paths, never as the sole authoritative answer.
- The router never fabricates an inference result. It returns a
  decision record describing where the request should go and what
  fallbacks apply.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


_ALLOWED_TASK_KINDS = (
    "scratch_summarization",
    "candidate_completion",
    "low_stakes_classification",
    "shadow_replay",
    "advisory_synthesis",
)

_CRITICAL_TASK_KINDS = (
    "foundational_axiom_authoring",
    "runtime_mutation_proposal",
    "main_branch_merge_decision",
    "human_facing_calibration_claim",
    "self_model_promotion",
    "world_model_promotion",
)

_ALLOWED_DESTINATIONS = (
    "local_model_shadow",
    "local_model_advisory",
    "external_provider",
    "refuse",
)


class InferenceRouterError(ValueError):
    """Raised when a request violates the safe routing contract."""


@dataclass(frozen=True)
class InferenceDecision:
    request_id: str
    task_kind: str
    chosen_destination: str
    fallback_destination: str | None
    rationale: str
    advisory_only: bool
    no_runtime_mutation: bool
    no_foundational_authority: bool

    def __post_init__(self) -> None:
        if self.chosen_destination not in _ALLOWED_DESTINATIONS:
            raise InferenceRouterError(
                f"unknown destination {self.chosen_destination!r}"
            )
        if (self.fallback_destination is not None
                and self.fallback_destination not in _ALLOWED_DESTINATIONS):
            raise InferenceRouterError(
                f"unknown fallback {self.fallback_destination!r}"
            )
        if self.advisory_only is not True:
            raise InferenceRouterError(
                "advisory_only must be True"
            )
        if self.no_runtime_mutation is not True:
            raise InferenceRouterError(
                "no_runtime_mutation must be True"
            )
        if self.no_foundational_authority is not True:
            raise InferenceRouterError(
                "no_foundational_authority must be True"
            )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_kind": self.task_kind,
            "chosen_destination": self.chosen_destination,
            "fallback_destination": self.fallback_destination,
            "rationale": self.rationale,
            "advisory_only": self.advisory_only,
            "no_runtime_mutation": self.no_runtime_mutation,
            "no_foundational_authority": self.no_foundational_authority,
        }


def compute_request_id(payload: Mapping[str, object]) -> str:
    canon = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()[:12]


@dataclass
class InferenceRouter:
    local_model_enabled: bool = False  # advisory-only opt-in flag

    def decide(self, request: Mapping[str, object]) -> InferenceDecision:
        task_kind = str(request.get("task_kind", ""))
        if not task_kind:
            raise InferenceRouterError("task_kind is required")
        request_id = compute_request_id(request)

        if task_kind in _CRITICAL_TASK_KINDS:
            return InferenceDecision(
                request_id=request_id,
                task_kind=task_kind,
                chosen_destination="refuse",
                fallback_destination=None,
                rationale=(
                    f"task_kind {task_kind!r} is critical; local model "
                    f"routing is forbidden by safe routing contract"
                ),
                advisory_only=True,
                no_runtime_mutation=True,
                no_foundational_authority=True,
            )

        if task_kind not in _ALLOWED_TASK_KINDS:
            return InferenceDecision(
                request_id=request_id,
                task_kind=task_kind,
                chosen_destination="external_provider",
                fallback_destination=None,
                rationale=(
                    f"task_kind {task_kind!r} unrecognized; default to "
                    f"external provider for visibility/audit"
                ),
                advisory_only=True,
                no_runtime_mutation=True,
                no_foundational_authority=True,
            )

        if not self.local_model_enabled:
            return InferenceDecision(
                request_id=request_id,
                task_kind=task_kind,
                chosen_destination="external_provider",
                fallback_destination=None,
                rationale=(
                    "local_model_enabled=False; routing to external "
                    "provider per scaffold default"
                ),
                advisory_only=True,
                no_runtime_mutation=True,
                no_foundational_authority=True,
            )

        # Even when locally enabled, the router only picks shadow or
        # advisory destinations and ALWAYS retains the external
        # provider as the fallback for the authoritative answer.
        return InferenceDecision(
            request_id=request_id,
            task_kind=task_kind,
            chosen_destination="local_model_shadow",
            fallback_destination="external_provider",
            rationale=(
                f"task_kind {task_kind!r} eligible for shadow; external "
                f"provider remains authoritative fallback"
            ),
            advisory_only=True,
            no_runtime_mutation=True,
            no_foundational_authority=True,
        )

    def call_local_model(self, *args, **kwargs) -> None:
        # Intentional: actual local model invocation is not implemented
        # in the scaffold. No HTTP, no subprocess, no model load.
        raise InferenceRouterError(
            "local model invocation is intentionally not implemented "
            "in this scaffold"
        )
