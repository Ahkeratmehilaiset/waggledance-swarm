"""Unit tests for RoundTableEngine — deliberation, consensus, dissent."""

import pytest
from unittest.mock import AsyncMock

from waggledance.core.orchestration.round_table import RoundTableEngine, ConsensusResult
from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.domain.events import EventType


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = "I agree with the assessment."
    return llm


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def engine(mock_llm, mock_event_bus):
    return RoundTableEngine(mock_llm, mock_event_bus)


def _agent(name: str, domain: str = "general") -> AgentDefinition:
    return AgentDefinition(
        id=name, name=name, domain=domain, tags=[], skills=[],
        trust_level=0, specialization_score=0.0, active=True, profile="ALL",
    )


def _result(agent_id: str, response: str) -> AgentResult:
    return AgentResult(
        agent_id=agent_id, response=response, confidence=0.8,
        latency_ms=50.0, source="llm",
    )


def _task(query: str = "What is varroa?") -> TaskRequest:
    return TaskRequest(id="t1", query=query, language="en", profile="COTTAGE", user_id=None)


class TestDeliberate:
    """Core deliberation logic."""

    @pytest.mark.asyncio
    async def test_returns_consensus_result(self, engine):
        agents = [_agent("bee_expert")]
        responses = [_result("bee_expert", "Varroa is a mite.")]
        result = await engine.deliberate(_task(), agents, responses)
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_consensus_not_empty(self, engine):
        agents = [_agent("a1"), _agent("a2")]
        responses = [_result("a1", "Answer 1"), _result("a2", "Answer 2")]
        result = await engine.deliberate(_task(), agents, responses)
        assert len(result.consensus) > 0

    @pytest.mark.asyncio
    async def test_participating_agents_listed(self, engine):
        agents = [_agent("expert_1"), _agent("expert_2")]
        responses = [_result("expert_1", "Yes"), _result("expert_2", "No")]
        result = await engine.deliberate(_task(), agents, responses)
        assert "expert_1" in result.participating_agents
        assert "expert_2" in result.participating_agents

    @pytest.mark.asyncio
    async def test_confidence_scales_with_participants(self, engine):
        agents = [_agent(f"a{i}") for i in range(4)]
        responses = [_result(f"a{i}", f"Answer {i}") for i in range(4)]
        result = await engine.deliberate(_task(), agents, responses)
        # confidence = min(0.9, 0.5 + n * 0.1)
        assert result.confidence == min(0.9, 0.5 + 4 * 0.1)

    @pytest.mark.asyncio
    async def test_single_agent_confidence(self, engine):
        result = await engine.deliberate(
            _task(), [_agent("solo")], [_result("solo", "Only me")]
        )
        assert result.confidence == 0.6  # 0.5 + 1*0.1

    @pytest.mark.asyncio
    async def test_latency_is_positive(self, engine):
        result = await engine.deliberate(
            _task(), [_agent("a1")], [_result("a1", "Fast")]
        )
        assert result.latency_ms > 0


class TestEmptyDeliberation:
    """Edge case: no agents or LLM returns empty."""

    @pytest.mark.asyncio
    async def test_no_agents_returns_no_participation(self, engine):
        result = await engine.deliberate(_task(), [], [])
        assert result.consensus == "No agents participated"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_returns_empty(self, engine, mock_llm):
        mock_llm.generate.return_value = ""
        result = await engine.deliberate(
            _task(), [_agent("a1")], [_result("a1", "My view")]
        )
        # Empty refined response → not added to discussion → no participants
        assert result.confidence == 0.0


class TestDissent:
    """Dissent detection in discussion."""

    @pytest.mark.asyncio
    async def test_detects_disagreement(self, engine, mock_llm):
        mock_llm.generate.side_effect = [
            "I agree with the assessment.",
            "However, I disagree with the approach.",
            "Synthesis: mixed views.",
        ]
        agents = [_agent("a1"), _agent("a2")]
        responses = [_result("a1", "View A"), _result("a2", "View B")]
        result = await engine.deliberate(_task(), agents, responses)
        assert len(result.dissenting_views) >= 1

    @pytest.mark.asyncio
    async def test_no_dissent_when_agreement(self, engine, mock_llm):
        mock_llm.generate.side_effect = [
            "I fully support this.",
            "I also support this.",
            "Full consensus reached.",
        ]
        agents = [_agent("a1"), _agent("a2")]
        responses = [_result("a1", "Same"), _result("a2", "Same")]
        result = await engine.deliberate(_task(), agents, responses)
        assert len(result.dissenting_views) == 0

    def test_find_dissent_markers(self, engine):
        discussion = [
            {"agent": "a1", "response": "I agree."},
            {"agent": "a2", "response": "However, I think differently."},
            {"agent": "a3", "response": "Eri mieltä tästä asiasta."},
        ]
        dissenting = engine._find_dissent(discussion)
        assert len(dissenting) == 2


class TestEvents:
    """Event publishing during deliberation."""

    @pytest.mark.asyncio
    async def test_publishes_started_event(self, engine, mock_event_bus):
        await engine.deliberate(
            _task("test query"), [_agent("a1")], [_result("a1", "x")]
        )
        calls = mock_event_bus.publish.call_args_list
        started = [c for c in calls if c[0][0].type == EventType.ROUND_TABLE_STARTED]
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_publishes_completed_event(self, engine, mock_event_bus):
        await engine.deliberate(
            _task("test query"), [_agent("a1")], [_result("a1", "x")]
        )
        calls = mock_event_bus.publish.call_args_list
        completed = [c for c in calls if c[0][0].type == EventType.ROUND_TABLE_COMPLETED]
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_completed_event_payload(self, engine, mock_event_bus):
        await engine.deliberate(
            _task("bee query"), [_agent("a1"), _agent("a2")],
            [_result("a1", "x"), _result("a2", "y")],
        )
        calls = mock_event_bus.publish.call_args_list
        completed = [c for c in calls if c[0][0].type == EventType.ROUND_TABLE_COMPLETED][0]
        payload = completed[0][0].payload
        assert payload["query"] == "bee query"
        assert payload["participants"] == 2
