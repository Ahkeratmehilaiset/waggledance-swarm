# SPDX-License-Identifier: BUSL-1.1
"""Agent pool registry — Phase 9 §J.

Specialized mentor / builder agents (e.g. opus_factory_specialist_v1,
opus_personal_specialist_v1) with session affinity metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import PROVIDERS


@dataclass(frozen=True)
class AgentRecord:
    schema_version: int
    agent_id: str
    provider_type: str
    specialization_tags: tuple[str, ...]
    preferred_capsules: tuple[str, ...]
    context_pack_refs: tuple[str, ...]
    max_concurrent_jobs: int
    daily_budget_calls: float
    warm_availability: bool
    session_affinity_hint: str | None = None

    def __post_init__(self) -> None:
        if self.provider_type not in PROVIDERS:
            raise ValueError(
                f"unknown provider_type: {self.provider_type!r}"
            )
        if self.max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be >= 1")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "provider_type": self.provider_type,
            "specialization_tags": list(self.specialization_tags),
            "preferred_capsules": list(self.preferred_capsules),
            "context_pack_refs": list(self.context_pack_refs),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "daily_budget_calls": self.daily_budget_calls,
            "warm_availability": self.warm_availability,
            "session_affinity_hint": self.session_affinity_hint,
        }


@dataclass
class AgentPoolRegistry:
    agents: dict[str, AgentRecord] = field(default_factory=dict)

    def register(self, a: AgentRecord) -> "AgentPoolRegistry":
        self.agents[a.agent_id] = a
        return self

    def get(self, agent_id: str) -> AgentRecord | None:
        return self.agents.get(agent_id)

    def for_capsule(self, capsule_context: str) -> list[AgentRecord]:
        out = [a for a in self.agents.values()
               if capsule_context in a.preferred_capsules]
        return sorted(out, key=lambda a: (-a.daily_budget_calls,
                                              a.agent_id))

    def for_specialization(self, tag: str) -> list[AgentRecord]:
        out = [a for a in self.agents.values()
               if tag in a.specialization_tags]
        return sorted(out, key=lambda a: a.agent_id)

    def warm_for(self, capsule_context: str) -> list[AgentRecord]:
        return [a for a in self.for_capsule(capsule_context)
                 if a.warm_availability]
