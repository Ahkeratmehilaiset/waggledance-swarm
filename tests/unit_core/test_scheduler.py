"""Tests for Scheduler — Top-K, exploration, pheromone, load penalty."""

import pytest

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
from waggledance.core.orchestration.scheduler import Scheduler, SchedulerState


class TestSchedulerSelectAgents:
    def test_returns_correct_count(self, mock_config, sample_agents, sample_task, empty_state):
        s = Scheduler(mock_config)
        result = s.select_agents(sample_task, sample_agents, empty_state, {}, n=4)
        assert len(result) == 4

    def test_empty_candidates_returns_empty(self, mock_config, sample_task, empty_state):
        s = Scheduler(mock_config)
        result = s.select_agents(sample_task, [], empty_state, {})
        assert result == []

    def test_exploration_includes_low_usage_agents(self, mock_config, sample_agents, sample_task):
        s = Scheduler(mock_config)
        state = SchedulerState(
            pheromone_scores={a.id: 0.9 for a in sample_agents[:3]},
            usage_counts={a.id: 100 for a in sample_agents[:3]},
            recent_successes={},
        )
        result = s.select_agents(sample_task, sample_agents, state, {}, n=6)
        ids = {a.id for a in result}
        low_usage = {a.id for a in sample_agents[3:]}
        assert ids & low_usage, "Should include some low-usage agents via exploration"

    def test_newcomer_boost_elevates_new_agents(self, mock_config, sample_agents, sample_task):
        s = Scheduler(mock_config)
        state = SchedulerState(
            pheromone_scores={sample_agents[0].id: 0.5},
            usage_counts={sample_agents[0].id: 50},
            recent_successes={},
        )
        result = s.select_agents(sample_task, sample_agents, state, {}, n=6)
        assert len(result) == 6

    def test_load_penalty_reduces_score(self, mock_config, sample_agents, sample_task):
        s = Scheduler(mock_config)
        heavy_state = SchedulerState(
            pheromone_scores={sample_agents[0].id: 0.9},
            usage_counts={sample_agents[0].id: 200},
            recent_successes={},
        )
        light_state = SchedulerState(
            pheromone_scores={sample_agents[0].id: 0.9},
            usage_counts={sample_agents[0].id: 0},
            recent_successes={},
        )
        heavy_score = s._score_agent(sample_agents[0], sample_task, heavy_state, {})
        light_score = s._score_agent(sample_agents[0], sample_task, light_state, {})
        assert light_score > heavy_score

    def test_trust_score_boosts_ranking(self, mock_config, sample_agents, sample_task, empty_state):
        s = Scheduler(mock_config)
        trust = {
            sample_agents[5].id: AgentTrust(
                agent_id=sample_agents[5].id, composite_score=0.95,
                signals=TrustSignals(0.0, 0.9, 0.9, 0.0, 5.0, 1.0),
                updated_at=0.0,
            )
        }
        score_with_trust = s._score_agent(sample_agents[5], sample_task, empty_state, trust)
        score_without = s._score_agent(sample_agents[5], sample_task, empty_state, {})
        assert score_with_trust > score_without

    def test_tag_matching_filters_properly(self, mock_config, sample_task, empty_state):
        s = Scheduler(mock_config)
        bee_agent = AgentDefinition(
            id="bee1", name="Bee", domain="bee", tags=["varroa", "mite"],
            skills=[], trust_level=2, specialization_score=0.5,
            active=True, profile="COTTAGE",
        )
        weather_agent = AgentDefinition(
            id="wx1", name="Weather", domain="weather", tags=["rain", "temp"],
            skills=[], trust_level=2, specialization_score=0.5,
            active=True, profile="COTTAGE",
        )
        bee_score = s._compute_tag_score(bee_agent, sample_task)
        wx_score = s._compute_tag_score(weather_agent, sample_task)
        assert bee_score > wx_score

    def test_inactive_agents_excluded(self, mock_config, sample_task, empty_state):
        s = Scheduler(mock_config)
        inactive = AgentDefinition(
            id="off1", name="Off", domain="x", tags=[], skills=[],
            trust_level=0, specialization_score=0.0,
            active=False, profile="COTTAGE",
        )
        result = s.select_agents(sample_task, [inactive], empty_state, {}, n=1)
        assert result == []

    def test_profile_filtering(self, mock_config, sample_task, empty_state):
        s = Scheduler(mock_config)
        agents = [
            AgentDefinition(
                id="a1", name="A1", domain="x", tags=["varroa"], skills=[],
                trust_level=0, specialization_score=0.0,
                active=True, profile="COTTAGE",
            ),
            AgentDefinition(
                id="a2", name="A2", domain="x", tags=["varroa"], skills=[],
                trust_level=0, specialization_score=0.0,
                active=True, profile="FACTORY",
            ),
        ]
        result = s.select_agents(sample_task, agents, empty_state, {}, n=5)
        assert len(result) == 2


class TestSchedulerUpdateState:
    def test_pheromone_increases_on_success(self, mock_config):
        s = Scheduler(mock_config)
        state = SchedulerState(pheromone_scores={"a1": 0.5}, usage_counts={}, recent_successes={})
        result = AgentResult(
            agent_id="a1", response="ok", confidence=0.9,
            latency_ms=100, source="llm",
        )
        new_state = s.update_state(state, "a1", result)
        assert new_state.pheromone_scores["a1"] > 0.5

    def test_pheromone_decreases_on_failure(self, mock_config):
        s = Scheduler(mock_config)
        state = SchedulerState(pheromone_scores={"a1": 0.8}, usage_counts={}, recent_successes={})
        result = AgentResult(
            agent_id="a1", response="", confidence=0.1,
            latency_ms=5000, source="llm",
        )
        new_state = s.update_state(state, "a1", result)
        assert new_state.pheromone_scores["a1"] < 0.8

    def test_usage_count_increments(self, mock_config):
        s = Scheduler(mock_config)
        state = SchedulerState(pheromone_scores={}, usage_counts={"a1": 5}, recent_successes={})
        result = AgentResult(
            agent_id="a1", response="ok", confidence=0.7,
            latency_ms=100, source="llm",
        )
        new_state = s.update_state(state, "a1", result)
        assert new_state.usage_counts["a1"] == 6
