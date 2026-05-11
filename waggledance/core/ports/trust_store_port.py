"""Trust store port — agent reputation tracking."""

from typing import Iterable, Protocol

from waggledance.core.domain.trust_score import AgentTrust, TrustSignals


class TrustStorePort(Protocol):
    """Port for agent trust score storage and ranking."""

    async def get_trust(self, agent_id: str) -> AgentTrust | None: ...

    async def get_trust_many(
        self, agent_ids: Iterable[str]
    ) -> dict[str, AgentTrust]:
        """Batch-fetch trust scores for many agents.

        Returns a dict mapping agent_id -> AgentTrust for agents that
        have a score on file; missing agents are omitted from the dict.

        Added in audit fix H47: the orchestrator chat hot path used to
        issue N sequential get_trust() calls (75 per request on a HOME
        profile), each round-trip costing ~0.5 ms on local sqlite. This
        method lets implementations collapse to a single query.
        """
        ...

    async def update_trust(
        self,
        agent_id: str,
        signals: TrustSignals,
    ) -> AgentTrust: ...

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]: ...
