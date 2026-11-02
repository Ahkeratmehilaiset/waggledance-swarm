"""Unit tests for MemoryService — ingest, retrieve, correction."""

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.application.services.memory_service import MemoryService
from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
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

    @pytest.mark.asyncio
    async def test_hybrid_mirror_status_success(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        hybrid = MagicMock()
        hybrid.enabled = True
        hybrid._embed_fn = lambda text: [1.0, 0.0]
        hybrid.ingest = AsyncMock(return_value="general")

        svc = MemoryService(
            mock_vector_store, mock_memory_repo, mock_event_bus,
            hybrid_retrieval=hybrid,
        )
        await svc.ingest("mirrored fact", source="api")

        status = svc.hybrid_mirror_status()
        assert status["successes"] == 1
        assert status["failures"] == 0
        assert status["last_cell"] == "general"

    @pytest.mark.asyncio
    async def test_hybrid_mirror_status_failure(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        hybrid = MagicMock()
        hybrid.enabled = True
        hybrid._embed_fn = lambda text: None
        hybrid.ingest = AsyncMock()

        svc = MemoryService(
            mock_vector_store, mock_memory_repo, mock_event_bus,
            hybrid_retrieval=hybrid,
        )
        await svc.ingest("mirror fail", source="api")

        status = svc.hybrid_mirror_status()
        assert status["successes"] == 0
        assert status["failures"] == 1
        assert "returned None" in status["last_error"]


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
    async def test_retrieve_event_omits_query_without_changing_search(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        mock_memory_repo.search.return_value = [
            MemoryRecord(
                id="r1", content="x", content_fi=None,
                source="test", confidence=0.9,
            )
        ]
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        query = "synthetic-private-query-" + "a" * 200
        results = await svc.retrieve_context(query)
        mock_memory_repo.search.assert_awaited_once_with(query=query, limit=5, language="en")
        event = mock_event_bus.publish.call_args[0][0]
        assert event.payload == {"results": 1}
        assert "synthetic-private-query" not in repr(event)
        assert len(results) == 1


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
    async def test_correction_event_omits_query_without_changing_storage(
        self, mock_vector_store, mock_memory_repo, mock_event_bus
    ):
        svc = MemoryService(mock_vector_store, mock_memory_repo, mock_event_bus)
        query = "synthetic-private-correction-" + "x" * 100
        await svc.record_correction(query, "bad", "good")
        mock_memory_repo.store_correction.assert_awaited_once_with(query, "bad", "good")
        event = mock_event_bus.publish.call_args[0][0]
        assert event.payload == {}
        assert "synthetic-private-correction" not in repr(event)


class TestChromaQueryPrivacy:
    """Synthetic query contents must not enter failure logs."""

    @pytest.mark.asyncio
    async def test_successful_query_preserves_search_results(self):
        store = object.__new__(ChromaVectorStore)
        store._breaker = MagicMock()
        store._breaker.allow_request.return_value = True
        store._embed_text = MagicMock(return_value=[0.1, 0.2])
        collection = MagicMock()
        collection.count.return_value = 1
        collection.query.return_value = {
            "ids": [["doc-1"]], "documents": [["synthetic result"]],
            "metadatas": [[{"kind": "test"}]], "distances": [[0.2]],
        }
        store._get_collection = MagicMock(return_value=collection)
        query = "synthetic-private-success-query"

        results = await store.query(query, n_results=3)
        store._embed_text.assert_called_once_with(query, "search_query: ")
        collection.query.assert_called_once_with(
            query_embeddings=[[0.1, 0.2]], n_results=1,
            include=["documents", "metadatas", "distances"],
        )
        assert results == [{"id": "doc-1", "text": "synthetic result",
                            "metadata": {"kind": "test"}, "score": 0.9}]
        store._breaker.record_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_log_query(self, caplog):
        store = object.__new__(ChromaVectorStore)
        store._breaker = MagicMock()
        store._breaker.allow_request.return_value = True
        store._embed_text = MagicMock(return_value=None)
        query = "synthetic-private-embed-query"

        with caplog.at_level("WARNING"):
            assert await store.query(query) == []
        store._embed_text.assert_called_once_with(query, "search_query: ")
        assert "Query embedding failed" in caplog.text
        assert query not in caplog.text

    @pytest.mark.asyncio
    async def test_backend_exception_does_not_log_exception_text(self, caplog):
        store = object.__new__(ChromaVectorStore)
        store._breaker = MagicMock()
        store._breaker.allow_request.return_value = True
        store._embed_text = MagicMock(return_value=[0.1, 0.2])
        collection = MagicMock()
        collection.count.return_value = 1
        query = "synthetic-private-backend-query"
        collection.query.side_effect = RuntimeError(query)
        store._get_collection = MagicMock(return_value=collection)

        with caplog.at_level("ERROR"):
            assert await store.query(query) == []
        assert query not in caplog.text
        store._breaker.record_failure.assert_called_once()

    def test_embed_exception_does_not_log_exception_text(self, caplog):
        store = object.__new__(ChromaVectorStore)
        store._embed_cache = OrderedDict()
        store._embed_cache_max = 1
        store._ollama_base_url = "http://127.0.0.1:11434"
        store._embedding_model = "synthetic-model"
        store._embed_timeout = 1
        query = "synthetic-private-exception-query"

        with patch("requests.post", side_effect=RuntimeError(query)), caplog.at_level("ERROR"):
            assert store._embed_text(query, "search_query: ") is None
        assert query not in caplog.text

    def test_embed_timeout_does_not_log_exception_text(self, caplog):
        store = object.__new__(ChromaVectorStore)
        store._embed_cache = OrderedDict()
        store._embed_cache_max = 1
        store._ollama_base_url = "http://127.0.0.1:11434"
        store._embedding_model = "synthetic-model"
        store._embed_timeout = 1
        query = "synthetic-private-timeout-query"

        with patch("requests.post", side_effect=TimeoutError(f"timed out: {query}")), caplog.at_level("WARNING"):
            assert store._embed_text(query, "search_query: ") is None
        assert "Ollama embed timeout" in caplog.text
        assert query not in caplog.text


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
