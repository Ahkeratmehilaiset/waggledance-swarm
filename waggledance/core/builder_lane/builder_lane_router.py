"""Builder lane router — Phase 9 §U2.

Decides which builder/mentor agent should receive a BuilderRequest.
Reuses the agent_pool_registry (Phase J) when available; otherwise
falls back to the default Claude Code builder lane.
"""
from __future__ import annotations

from dataclasses import dataclass

from .builder_request_pack import BuilderRequest


@dataclass(frozen=True)
class BuilderRoutingDecision:
    chosen_agent_id: str | None
    chosen_provider_type: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "chosen_agent_id": self.chosen_agent_id,
            "chosen_provider_type": self.chosen_provider_type,
            "rationale": self.rationale,
        }


def route(request: BuilderRequest,
             agent_pool=None) -> BuilderRoutingDecision:
    """Pick an agent for `request`.

    Priority:
    1. agent_id_hint if set and in pool
    2. capsule-affinity match in agent_pool
    3. specialization match by task_kind
    4. fall back to default Claude Code builder lane
    """
    if agent_pool is not None and request.agent_id_hint:
        a = agent_pool.get(request.agent_id_hint)
        if a is not None:
            return BuilderRoutingDecision(
                chosen_agent_id=a.agent_id,
                chosen_provider_type=a.provider_type,
                rationale="agent_id_hint matched",
            )

    if agent_pool is not None:
        # Capsule affinity
        capsule_agents = [
            a for a in agent_pool.for_capsule(request.capsule_context)
            if a.warm_availability
        ]
        if capsule_agents:
            chosen = capsule_agents[0]
            return BuilderRoutingDecision(
                chosen_agent_id=chosen.agent_id,
                chosen_provider_type=chosen.provider_type,
                rationale=f"capsule affinity {request.capsule_context}",
            )
        # Specialization
        spec_agents = agent_pool.for_specialization(request.task_kind)
        if spec_agents:
            chosen = spec_agents[0]
            return BuilderRoutingDecision(
                chosen_agent_id=chosen.agent_id,
                chosen_provider_type=chosen.provider_type,
                rationale=f"specialization match {request.task_kind}",
            )

    # Default fallback
    return BuilderRoutingDecision(
        chosen_agent_id=None,
        chosen_provider_type="claude_code_builder_lane",
        rationale="default fallback to Claude Code builder lane",
    )
