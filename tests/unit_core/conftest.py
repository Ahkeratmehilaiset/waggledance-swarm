"""Shared fixtures for core unit tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.memory_record import MemoryRecord
from waggledance.core.domain.task import TaskRequest
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
from waggledance.core.orchestration.scheduler import SchedulerState


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "swarm.top_k": 8,
        "swarm.exploration_rate": 0.20,
        "swarm.load_penalty": 0.15,
        "swarm.newcomer_boost": 0.2,
        "swarm.low_usage_bonus": 0.15,
        "swarm.cooldown_max": 5,
        "swarm.min_tasks_per_day": 2,
        "swarm.enabled": True,
    }.get(key, default)
    config.get_profile.return_value = "COTTAGE"
    config.get_hardware_tier.return_value = "standard"
    return config


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = "Test response"
    llm.is_available.return_value = True
    llm.get_active_model.return_value = "phi4-mini"
    return llm


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock()
    return bus


@pytest.fixture
def mock_memory_repo():
    repo = AsyncMock()
    repo.search.return_value = []
    repo.store = AsyncMock()
    repo.store_correction = AsyncMock()
    return repo


@pytest.fixture
def mock_vector_store():
    store = AsyncMock()
    store.upsert = AsyncMock()
    store.query.return_value = []
    store.is_ready.return_value = True
    return store


@pytest.fixture
def mock_trust_store():
    store = AsyncMock()
    store.get_trust.return_value = None
    store.update_trust.return_value = AgentTrust(
        agent_id="test", composite_score=0.5,
        signals=TrustSignals(0.0, 0.5, 0.5, 0.0, 1.0, 1.0),
        updated_at=0.0,
    )
    store.get_ranking.return_value = []
    return store


@pytest.fixture
def sample_agents():
    return [
        AgentDefinition(
            id=f"agent_{i}", name=f"Agent {i}", domain="beekeeping",
            tags=["bee", "honey", "varroa"] if i < 3 else ["weather", "temp"],
            skills=["diagnosis"] if i < 3 else ["forecast"],
            trust_level=i % 5, specialization_score=0.5,
            active=True, profile="COTTAGE",
        )
        for i in range(10)
    ]


@pytest.fixture
def sample_task():
    return TaskRequest(
        id="task-1", query="How to treat varroa mites?",
        language="en", profile="COTTAGE", user_id="user-1",
        context=[], timestamp=1000.0,
    )


@pytest.fixture
def empty_state():
    return SchedulerState()
