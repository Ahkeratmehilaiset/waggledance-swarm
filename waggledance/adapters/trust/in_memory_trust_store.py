"""In-memory trust store — implements TrustStorePort."""
# implements TrustStorePort

import time

try:
    from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class TrustSignals:
        """Six-signal trust input for an agent."""

        hallucination_rate: float
        validation_rate: float
        consensus_agreement: float
        correction_rate: float
        fact_production_rate: float
        freshness_score: float

    @dataclass
    class AgentTrust:
        """Computed trust score for an agent."""

        agent_id: str
        composite_score: float
        signals: TrustSignals
        updated_at: float


class InMemoryTrustStore:
    """In-memory trust store. Production may use SQLite later."""

    def __init__(self) -> None:
        self._scores: dict[str, AgentTrust] = {}

    async def get_trust(self, agent_id: str) -> AgentTrust | None:
        """Return trust score for the given agent, or None if not tracked."""
        return self._scores.get(agent_id)

    async def update_trust(
        self,
        agent_id: str,
        signals: TrustSignals,
    ) -> AgentTrust:
        """Compute composite score from signals and store the result."""
        composite = self._compute_composite(signals)
        trust = AgentTrust(
            agent_id=agent_id,
            composite_score=composite,
            signals=signals,
            updated_at=time.time(),
        )
        self._scores[agent_id] = trust
        return trust

    async def get_ranking(self, limit: int = 20) -> list[AgentTrust]:
        """Return top agents ranked by composite score, descending."""
        ranked = sorted(
            self._scores.values(),
            key=lambda t: t.composite_score,
            reverse=True,
        )
        return ranked[:limit]

    def _compute_composite(self, s: TrustSignals) -> float:
        """Weighted composite of six trust signals.

        Weights:
          (1 - hallucination_rate) * 0.25   -- lower hallucination is better
          validation_rate          * 0.20
          consensus_agreement      * 0.20
          (1 - correction_rate)    * 0.15   -- fewer corrections is better
          fact_production_rate     * 0.10
          freshness_score          * 0.10
        """
        composite = (
            (1.0 - s.hallucination_rate) * 0.25
            + s.validation_rate * 0.20
            + s.consensus_agreement * 0.20
            + (1.0 - s.correction_rate) * 0.15
            + s.fact_production_rate * 0.10
            + s.freshness_score * 0.10
        )
        return max(0.0, min(1.0, composite))
