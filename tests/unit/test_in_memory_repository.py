"""Unit tests for InMemoryRepository — MemoryRepositoryPort stub."""

import time

import pytest

from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.core.domain.memory_record import MemoryRecord


def _make_record(
    id: str = "r1",
    content: str = "test content",
    content_fi: str | None = None,
    source: str = "test",
    confidence: float = 0.8,
    tags: list[str] | None = None,
    agent_id: str | None = None,
    ttl_seconds: int | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=id,
        content=content,
        content_fi=content_fi,
        source=source,
        confidence=confidence,
        tags=tags if tags is not None else [],
        agent_id=agent_id,
        created_at=time.time(),
        ttl_seconds=ttl_seconds,
    )


class TestInMemoryRepositoryStoreAndSearch:
    """Store and search operations."""

    @pytest.mark.asyncio
    async def test_store_and_search_returns_record(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        record = _make_record(content="Bees produce honey from nectar")
        await stub_memory_repo.store(record)
        results = await stub_memory_repo.search("honey")
        assert len(results) == 1
        assert results[0].id == "r1"
        assert "honey" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_search_with_tags_filters_correctly(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        tagged = _make_record(id="tagged", content="varroa mite treatment", tags=["beekeeping"])
        untagged = _make_record(id="untagged", content="varroa mite info", tags=["general"])
        await stub_memory_repo.store(tagged)
        await stub_memory_repo.store(untagged)
        results = await stub_memory_repo.search("varroa", tags=["beekeeping"])
        assert len(results) == 1
        assert results[0].id == "tagged"

    @pytest.mark.asyncio
    async def test_store_correction_stores_entry(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        await stub_memory_repo.store_correction(
            query="How many legs?",
            wrong="4 legs",
            correct="6 legs",
        )
        assert stub_memory_repo.correction_count == 1

    @pytest.mark.asyncio
    async def test_search_on_empty_returns_empty(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        results = await stub_memory_repo.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_limit_parameter_respected(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        for i in range(10):
            record = _make_record(
                id=f"r{i}",
                content=f"beekeeping fact {i}",
                confidence=0.9,
            )
            await stub_memory_repo.store(record)
        results = await stub_memory_repo.search("beekeeping", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_language_parameter_does_not_crash(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        record = _make_record(content="test content", content_fi="testisisältö")
        await stub_memory_repo.store(record)
        results_en = await stub_memory_repo.search("test", language="en")
        results_fi = await stub_memory_repo.search("test", language="fi")
        assert len(results_en) >= 1
        assert len(results_fi) >= 1

    @pytest.mark.asyncio
    async def test_store_replaces_existing_by_id(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        v1 = _make_record(id="same", content="version one")
        v2 = _make_record(id="same", content="version two")
        await stub_memory_repo.store(v1)
        await stub_memory_repo.store(v2)
        assert stub_memory_repo.record_count == 1
        results = await stub_memory_repo.search("version")
        assert results[0].content == "version two"

    @pytest.mark.asyncio
    async def test_finnish_content_is_searchable(
        self, stub_memory_repo: InMemoryRepository
    ) -> None:
        record = _make_record(
            content="Honey production",
            content_fi="Hunajan tuotanto",
        )
        await stub_memory_repo.store(record)
        results = await stub_memory_repo.search("hunajan")
        assert len(results) == 1
