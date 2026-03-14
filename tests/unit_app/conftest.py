"""Shared fixtures for application unit tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.memory_record import MemoryRecord
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.round_table import ConsensusResult


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "swarm.top_k": 6,
        "swarm.enabled": False,
        "swarm.exploration_rate": 0.20,
        "swarm.load_penalty": 0.15,
        "swarm.newcomer_boost": 0.2,
        "swarm.low_usage_bonus": 0.15,
        "swarm.cooldown_max": 5,
        "swarm.min_tasks_per_day": 2,
    }.get(key, default)
    config.get_profile.return_value = "COTTAGE"
    config.get_hardware_tier.return_value = "standard"
    return config


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock(spec=Orchestrator)
    orch.handle_task.return_value = AgentResult(
        agent_id="a1", response="Test answer", confidence=0.8,
        latency_ms=50, source="llm",
    )
    orch.run_round_table.return_value = ConsensusResult(
        consensus="Consensus answer", confidence=0.9,
        participating_agents=["a1", "a2"], dissenting_views=[],
        latency_ms=200,
    )
    orch.is_ready.return_value = True
    return orch


@pytest.fixture
def mock_memory_service():
    ms = AsyncMock()
    ms.retrieve_context.return_value = []
    ms.ingest.return_value = MemoryRecord(
        id="m1", content="test", content_fi=None, source="test",
        confidence=0.8, tags=[], created_at=0.0,
    )
    ms.record_correction = AsyncMock()
    return ms


@pytest.fixture
def mock_hot_cache():
    cache = MagicMock()
    cache.get.return_value = None
    cache.set = MagicMock()
    cache.evict_expired.return_value = 0
    cache.stats.return_value = {"size": 0, "hits": 0, "misses": 0}
    return cache


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = "Generated fact"
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
def mock_vector_store():
    store = AsyncMock()
    store.upsert = AsyncMock()
    store.query.return_value = []
    store.is_ready.return_value = True
    return store


@pytest.fixture
def mock_memory_repo():
    repo = AsyncMock()
    repo.search.return_value = []
    repo.store = AsyncMock()
    repo.store_correction = AsyncMock()
    return repo
