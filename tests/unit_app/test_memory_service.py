"""Unit tests for MemoryService — ingest, retrieve, correction."""

import pytest

from waggledance.application.services.memory_service import MemoryService
from waggledance.core.domain.events import EventType
from waggledance.core.domain.memory_record import MemoryRecord


class TestMemoryServiceIngest:
    """Tests for MemoryService.ingest()."""

    @pytest.mark.asyncio
    async def test_ingest_returns_memory_record(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("Temperature is 22C", source="api")
        assert isinstance(record, MemoryRecord)
        assert record.content == "Temperature is 22C"
        assert record.source == "api"

    @pytest.mark.asyncio
    async def test_ingest_generates_uuid(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        r1 = await svc.ingest("fact 1", source="api")
        r2 = await svc.ingest("fact 2", source="api")
        assert r1.id != r2.id
        assert len(r1.id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_ingest_default_confidence(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("fact", source="test")
        assert record.confidence == 0.8

    @pytest.mark.asyncio
    async def test_ingest_stores_in_memory_repo(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.ingest("stored fact", source="mqtt")
        mock_memory_repo.store.assert_awaited_once()
        stored = mock_memory_repo.store.call_args[0][0]
        assert stored.content == "stored fact"

    @pytest.mark.asyncio
    async def test_ingest_upserts_to_vector_store(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.ingest("vector fact", source="api", tags=["bee"])
        mock_vector_store.upsert.assert_awaited_once()
        call_kwargs = mock_vector_store.upsert.call_args[1]
        assert call_kwargs["text"] == "vector fact"
        assert call_kwargs["collection"] == "waggle_memory"
        assert "bee" in call_kwargs["metadata"]["tags"]

    @pytest.mark.asyncio
    async def test_ingest_publishes_memory_stored_event(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.ingest("event fact", source="api")
        mock_event_bus.publish.assert_awaited_once()
        event = mock_event_bus.publish.call_args[0][0]
        assert event.type == EventType.MEMORY_STORED
        assert event.payload["source"] == "api"

    @pytest.mark.asyncio
    async def test_ingest_with_tags(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("tagged", source="api", tags=["a", "b"])
        assert record.tags == ["a", "b"]

    @pytest.mark.asyncio
    async def test_ingest_with_agent_id(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("agent fact", source="api", agent_id="agent_42")
        assert record.agent_id == "agent_42"

    @pytest.mark.asyncio
    async def test_ingest_none_tags_becomes_empty_list(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("no tags", source="api", tags=None)
        assert record.tags == []

    @pytest.mark.asyncio
    async def test_ingest_created_at_is_set(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        record = await svc.ingest("timed", source="api")
        assert record.created_at > 0


class TestMemoryServiceRetrieve:
    """Tests for MemoryService.retrieve_context()."""

    @pytest.mark.asyncio
    async def test_retrieve_delegates_to_memory_repo(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.retrieve_context("temperature")
        mock_memory_repo.search.assert_awaited_once_with(
            query="temperature", limit=5, language="en"
        )

    @pytest.mark.asyncio
    async def test_retrieve_custom_language(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.retrieve_context("lämpötila", language="fi")
        mock_memory_repo.search.assert_awaited_once_with(
            query="lämpötila", limit=5, language="fi"
        )

    @pytest.mark.asyncio
    async def test_retrieve_custom_limit(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.retrieve_context("test", limit=10)
        mock_memory_repo.search.assert_awaited_once_with(
            query="test", limit=10, language="en"
        )

    @pytest.mark.asyncio
    async def test_retrieve_empty_results_no_event(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        mock_memory_repo.search.return_value = []
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        results = await svc.retrieve_context("nothing")
        assert results == []
        mock_event_bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retrieve_with_results_publishes_event(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        mock_memory_repo.search.return_value = [
            MemoryRecord(
                id="r1", content="found", content_fi=None,
                source="test", confidence=0.9,
            )
        ]
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        results = await svc.retrieve_context("search term")
        assert len(results) == 1
        mock_event_bus.publish.assert_awaited_once()
        event = mock_event_bus.publish.call_args[0][0]
        assert event.type == EventType.MEMORY_RETRIEVED
        assert event.payload["results"] == 1

    @pytest.mark.asyncio
    async def test_retrieve_event_truncates_query(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        mock_memory_repo.search.return_value = [
            MemoryRecord(
                id="r1", content="x", content_fi=None,
                source="test", confidence=0.9,
            )
        ]
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        long_query = "a" * 200
        await svc.retrieve_context(long_query)
        event = mock_event_bus.publish.call_args[0][0]
        assert len(event.payload["query"]) == 50


class TestMemoryServiceCorrection:
    """Tests for MemoryService.record_correction()."""

    @pytest.mark.asyncio
    async def test_correction_delegates_to_repo(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.record_correction("query", "wrong answer", "right answer")
        mock_memory_repo.store_correction.assert_awaited_once_with(
            "query", "wrong answer", "right answer"
        )

    @pytest.mark.asyncio
    async def test_correction_publishes_event(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.record_correction("q", "bad", "good")
        event = mock_event_bus.publish.call_args[0][0]
        assert event.type == EventType.CORRECTION_RECORDED

    @pytest.mark.asyncio
    async def test_correction_truncates_query_in_event(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        await svc.record_correction("x" * 100, "bad", "good")
        event = mock_event_bus.publish.call_args[0][0]
        assert len(event.payload["query"]) == 50


class TestMemoryServiceInit:
    """Tests for MemoryService constructor."""

    def test_init_requires_memory_repo(self, mock_vector_store, mock_event_bus):
        with pytest.raises(AssertionError):
            MemoryService(mock_vector_store, None, mock_event_bus)

    def test_init_accepts_valid_deps(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        assert svc is not None
