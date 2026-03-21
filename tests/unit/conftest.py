"""Shared fixtures for unit tests — zero I/O, all in-memory."""

import pytest

from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
from waggledance.adapters.memory.hot_cache import HotCache
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
from waggledance.bootstrap.event_bus import InMemoryEventBus


@pytest.fixture
def stub_llm() -> StubLLMAdapter:
    """Return a fresh StubLLMAdapter instance."""
    return StubLLMAdapter()


@pytest.fixture
def stub_vector_store() -> InMemoryVectorStore:
    """Return a fresh InMemoryVectorStore instance."""
    return InMemoryVectorStore()


@pytest.fixture
def stub_memory_repo() -> InMemoryRepository:
    """Return a fresh InMemoryRepository instance."""
    return InMemoryRepository()


@pytest.fixture
def stub_trust_store() -> InMemoryTrustStore:
    """Return a fresh InMemoryTrustStore instance."""
    return InMemoryTrustStore()


@pytest.fixture
def hot_cache() -> HotCache:
    """Return a HotCache with small max_size for testing eviction."""
    return HotCache(max_size=5, default_ttl=60)


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    """Return a fresh InMemoryEventBus instance."""
    return InMemoryEventBus()
