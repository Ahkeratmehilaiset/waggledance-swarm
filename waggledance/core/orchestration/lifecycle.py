"""Agent lifecycle management — spawn, promote, demote.

Ported from hivemind.py agent initialization logic.
AgentLifecycleManager is the sole owner of agent state changes.
"""

from dataclasses import replace

from waggledance.core.domain.agent import AgentDefinition
from waggledance.core.domain.trust_score import AgentTrust

TRUST_THRESHOLDS = [0.0, 0.2, 0.4, 0.6, 0.8]


class AgentLifecycleManager:
    """Manages agent spawning, promotion, and demotion."""

    def spawn_for_profile(
        self,
        all_agents: list[AgentDefinition],
        profile: str,
    ) -> list[AgentDefinition]:
        """Filter and activate agents matching the given profile.

        Agents with profile="ALL" always match.
        Returns new AgentDefinition instances with active=True.
        """
        profile_upper = profile.upper()
        result: list[AgentDefinition] = []
        for agent in all_agents:
            if agent.profile.upper() in (profile_upper, "ALL"):
                result.append(replace(agent, active=True))
        return result

    def promote_trust_level(
        self,
        agent: AgentDefinition,
        trust: AgentTrust,
    ) -> AgentDefinition:
        """Update agent trust level based on composite trust score.

        Trust levels: 0=NOVICE, 1=APPRENTICE, 2=JOURNEYMAN, 3=EXPERT, 4=MASTER.
        Promotion is based on crossing trust score thresholds.
        """
        new_level = 0
        for i, threshold in enumerate(TRUST_THRESHOLDS):
            if trust.composite_score >= threshold:
                new_level = i

        if new_level != agent.trust_level:
            return replace(agent, trust_level=new_level)
        return agent
