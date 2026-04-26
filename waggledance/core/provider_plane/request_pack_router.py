"""Request pack construction + routing — Phase 9 §J.

Builds deterministic ProviderRequest packs from kernel
ActionRecommendations or other inputs, then hands them to
provider_router.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import PROVIDER_PLANE_SCHEMA_VERSION, TASK_CLASSES


@dataclass(frozen=True)
class ProviderRequest:
    schema_version: int
    request_id: str
    task_class: str
    provider_priority_list: tuple[str, ...]
    intent: str
    input_payload: dict
    budget: dict
    no_runtime_mutation: bool
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    capsule_context: str = "neutral_v1"
    agent_id_hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_class": self.task_class,
            "provider_priority_list": list(self.provider_priority_list),
            "intent": self.intent,
            "input_payload": dict(self.input_payload),
            "budget": dict(self.budget),
            "agent_id_hint": self.agent_id_hint,
            "capsule_context": self.capsule_context,
            "no_runtime_mutation": self.no_runtime_mutation,
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


def compute_request_id(*, task_class: str, intent: str,
                              input_payload: dict,
                              capsule_context: str) -> str:
    canonical = json.dumps({
        "task_class": task_class, "intent": intent,
        "input_payload": input_payload,
        "capsule_context": capsule_context,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_request(*,
                      task_class: str, intent: str,
                      input_payload: dict | None = None,
                      provider_priority_list: tuple[str, ...] = (),
                      max_calls: float = 1.0,
                      max_tokens: float = 0.0,
                      max_latency_ms: float = 0.0,
                      capsule_context: str = "neutral_v1",
                      agent_id_hint: str | None = None,
                      branch_name: str = "phase9/autonomy-fabric",
                      base_commit_hash: str = "",
                      pinned_input_manifest_sha256: str = "sha256:unknown",
                      ) -> ProviderRequest:
    if task_class not in TASK_CLASSES:
        raise ValueError(
            f"unknown task_class: {task_class!r}; allowed: {TASK_CLASSES}"
        )
    payload = dict(input_payload or {})
    rid = compute_request_id(
        task_class=task_class, intent=intent,
        input_payload=payload, capsule_context=capsule_context,
    )
    budget = {"max_calls": max_calls}
    if max_tokens > 0:
        budget["max_tokens"] = max_tokens
    if max_latency_ms > 0:
        budget["max_latency_ms"] = max_latency_ms
    return ProviderRequest(
        schema_version=PROVIDER_PLANE_SCHEMA_VERSION,
        request_id=rid, task_class=task_class,
        provider_priority_list=tuple(provider_priority_list),
        intent=intent, input_payload=payload,
        budget=budget,
        no_runtime_mutation=True,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        capsule_context=capsule_context,
        agent_id_hint=agent_id_hint,
    )
