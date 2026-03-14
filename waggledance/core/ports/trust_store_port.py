"""Trust store port — agent reputation tracking."""

from typing import Protocol

from waggledance.core.domain.trust_score import AgentTrust, TrustSignals


class TrustStorePort(Protocol):
    """Port for agent trust score storage and ranking."""

    async def get_trust(self, agent_id: str) -> AgentTrust | None: ...

    async def update_trust(
        self,
        agent_id: str,
        signals: TrustSignals,
    ) -> AgentTrust: ...

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]: ...
