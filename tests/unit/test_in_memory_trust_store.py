"""Unit tests for InMemoryTrustStore — trust scoring and ranking."""

import pytest

from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals


def _make_signals(
    hallucination_rate: float = 0.1,
    validation_rate: float = 0.8,
    consensus_agreement: float = 0.7,
    correction_rate: float = 0.05,
    fact_production_rate: float = 0.6,
    freshness_score: float = 0.9,
) -> TrustSignals:
    return TrustSignals(
        hallucination_rate=hallucination_rate,
        validation_rate=validation_rate,
        consensus_agreement=consensus_agreement,
        correction_rate=correction_rate,
        fact_production_rate=fact_production_rate,
        freshness_score=freshness_score,
    )


class TestInMemoryTrustStoreGetTrust:
    """get_trust() behavior."""

    @pytest.mark.asyncio
    async def test_get_trust_unknown_agent_returns_none(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        result = await stub_trust_store.get_trust("nonexistent-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_trust_returns_stored_agent(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        signals = _make_signals()
        await stub_trust_store.update_trust("agent-A", signals)
        trust = await stub_trust_store.get_trust("agent-A")
        assert trust is not None
        assert trust.agent_id == "agent-A"


class TestInMemoryTrustStoreUpdateTrust:
    """update_trust() behavior."""

    @pytest.mark.asyncio
    async def test_update_trust_stores_and_returns_agent_trust(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        signals = _make_signals()
        result = await stub_trust_store.update_trust("agent-B", signals)
        assert isinstance(result, AgentTrust)
        assert result.agent_id == "agent-B"
        assert result.updated_at > 0.0

    @pytest.mark.asyncio
    async def test_composite_score_is_between_0_and_1(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        # Test with extreme high values
        high_signals = _make_signals(
            hallucination_rate=0.0,
            validation_rate=1.0,
            consensus_agreement=1.0,
            correction_rate=0.0,
            fact_production_rate=1.0,
            freshness_score=1.0,
        )
        high_result = await stub_trust_store.update_trust("high", high_signals)
        assert 0.0 <= high_result.composite_score <= 1.0

        # Test with extreme low values
        low_signals = _make_signals(
            hallucination_rate=1.0,
            validation_rate=0.0,
            consensus_agreement=0.0,
            correction_rate=1.0,
            fact_production_rate=0.0,
            freshness_score=0.0,
        )
        low_result = await stub_trust_store.update_trust("low", low_signals)
        assert 0.0 <= low_result.composite_score <= 1.0

    @pytest.mark.asyncio
    async def test_updating_same_agent_overwrites_previous(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        signals_v1 = _make_signals(hallucination_rate=0.5)
        signals_v2 = _make_signals(hallucination_rate=0.1)
        await stub_trust_store.update_trust("agent-C", signals_v1)
        result = await stub_trust_store.update_trust("agent-C", signals_v2)
        assert result.signals.hallucination_rate == 0.1

        # Only one entry for agent-C
        ranking = await stub_trust_store.get_ranking(limit=100)
        agent_c_entries = [t for t in ranking if t.agent_id == "agent-C"]
        assert len(agent_c_entries) == 1


class TestInMemoryTrustStoreGetRanking:
    """get_ranking() behavior."""

    @pytest.mark.asyncio
    async def test_get_ranking_returns_sorted_by_composite_score(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        # Create agents with clearly different scores
        low = _make_signals(hallucination_rate=0.9, validation_rate=0.1)
        high = _make_signals(hallucination_rate=0.0, validation_rate=1.0)
        mid = _make_signals(hallucination_rate=0.5, validation_rate=0.5)

        await stub_trust_store.update_trust("low-agent", low)
        await stub_trust_store.update_trust("high-agent", high)
        await stub_trust_store.update_trust("mid-agent", mid)

        ranking = await stub_trust_store.get_ranking()
        scores = [t.composite_score for t in ranking]
        assert scores == sorted(scores, reverse=True)
        assert ranking[0].agent_id == "high-agent"

    @pytest.mark.asyncio
    async def test_get_ranking_limit_is_respected(
        self, stub_trust_store: InMemoryTrustStore
    ) -> None:
        for i in range(10):
            signals = _make_signals(validation_rate=i / 10.0)
            await stub_trust_store.update_trust(f"agent-{i}", signals)

        ranking = await stub_trust_store.get_ranking(limit=3)
        assert len(ranking) == 3
