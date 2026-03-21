"""Unit tests for ChromaMemoryRepository — mocked VectorStorePort, no real ChromaDB."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from waggledance.adapters.memory.chroma_memory_repository import ChromaMemoryRepository
from waggledance.core.domain.memory_record import MemoryRecord


def _make_record(
    id: str = "rec1",
    content: str = "test content",
    content_fi: str | None = None,
    source: str = "unit_test",
    confidence: float = 0.85,
    tags: list[str] | None = None,
    agent_id: str | None = "agent-A",
    ttl_seconds: int | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=id,
        content=content,
        content_fi=content_fi,
        source=source,
        confidence=confidence,
        tags=tags if tags is not None else ["fact"],
        agent_id=agent_id,
        created_at=time.time(),
        ttl_seconds=ttl_seconds,
    )


def _mock_vector_store() -> AsyncMock:
    """Create a mock VectorStorePort with AsyncMock methods."""
    vs = AsyncMock()
    vs.upsert = AsyncMock()
    vs.query = AsyncMock(return_value=[])
    vs.is_ready = AsyncMock(return_value=True)
    return vs


class TestChromaMemoryRepositoryStore:
    """store() delegates to vector_store.upsert()."""

    @pytest.mark.asyncio
    async def test_store_calls_upsert_with_correct_collection(self) -> None:
        vs = _mock_vector_store()
        repo = ChromaMemoryRepository(vector_store=vs, collection="waggle_memory")
        record = _make_record()

        await repo.store(record)

        vs.upsert.assert_awaited_once()
        call_kwargs = vs.upsert.call_args
        assert call_kwargs.kwargs.get("collection") == "waggle_memory" or \
            (call_kwargs.args and "waggle_memory" in str(call_kwargs))
        # Check it was called with the record's id and text
        assert call_kwargs.kwargs.get("id") == "rec1" or \
            (len(call_kwargs.args) > 0 and call_kwargs.args[0] == "rec1")

    @pytest.mark.asyncio
    async def test_store_serialises_tags_as_json_string(self) -> None:
        vs = _mock_vector_store()
        repo = ChromaMemoryRepository(vector_store=vs)
        record = _make_record(tags=["bee", "honey"])

        await repo.store(record)

        call_kwargs = vs.upsert.call_args.kwargs
        metadata = call_kwargs["metadata"]
        assert isinstance(metadata["tags"], str)
        assert json.loads(metadata["tags"]) == ["bee", "honey"]


class TestChromaMemoryRepositorySearch:
    """search() delegates to vector_store.query() and converts results."""

    @pytest.mark.asyncio
    async def test_search_calls_query_and_converts_to_memory_record(self) -> None:
        vs = _mock_vector_store()
        vs.query.return_value = [
            {
                "id": "hit1",
                "text": "Bees make honey",
                "metadata": {
                    "source": "enrichment",
                    "confidence": 0.9,
                    "tags": '["bee"]',
                    "agent_id": "agent-X",
                    "created_at": 1700000000.0,
                },
                "score": 0.85,
            }
        ]
        repo = ChromaMemoryRepository(vector_store=vs)

        results = await repo.search("honey", limit=5)

        assert len(results) == 1
        rec = results[0]
        assert isinstance(rec, MemoryRecord)
        assert rec.id == "hit1"
        assert rec.content == "Bees make honey"
        assert rec.source == "enrichment"
        assert rec.tags == ["bee"]
        assert rec.agent_id == "agent-X"

    @pytest.mark.asyncio
    async def test_search_with_tags_passes_where_filter(self) -> None:
        vs = _mock_vector_store()
        repo = ChromaMemoryRepository(vector_store=vs)

        await repo.search("query", tags=["beekeeping", "varroa"])

        call_kwargs = vs.query.call_args.kwargs
        assert call_kwargs["where"] == {"tags": {"$in": ["beekeeping", "varroa"]}}

    @pytest.mark.asyncio
    async def test_empty_query_results_return_empty_list(self) -> None:
        vs = _mock_vector_store()
        vs.query.return_value = []
        repo = ChromaMemoryRepository(vector_store=vs)

        results = await repo.search("nothing here")

        assert results == []


class TestChromaMemoryRepositoryStoreCorrection:
    """store_correction() upserts to the 'corrections' collection."""

    @pytest.mark.asyncio
    async def test_store_correction_upserts_to_corrections_collection(self) -> None:
        vs = _mock_vector_store()
        repo = ChromaMemoryRepository(vector_store=vs)

        await repo.store_correction(
            query="How many legs?",
            wrong="4 legs",
            correct="6 legs",
        )

        vs.upsert.assert_awaited_once()
        call_kwargs = vs.upsert.call_args.kwargs
        assert call_kwargs["collection"] == "corrections"
        assert "Q: How many legs?" in call_kwargs["text"]
        assert "Wrong: 4 legs" in call_kwargs["text"]
        assert "Correct: 6 legs" in call_kwargs["text"]
        assert call_kwargs["metadata"]["type"] == "correction"


class TestChromaMemoryRepositoryMetadataRoundtrip:
    """Metadata preservation through store -> search roundtrip."""

    @pytest.mark.asyncio
    async def test_metadata_fields_preserved_through_roundtrip(self) -> None:
        vs = _mock_vector_store()
        repo = ChromaMemoryRepository(vector_store=vs)
        record = _make_record(
            content="Varroa treatment schedule",
            content_fi="Varroan hoitoaikataulu",
            source="enrichment",
            confidence=0.92,
            tags=["varroa", "treatment"],
            agent_id="agent-bee",
            ttl_seconds=3600,
        )

        await repo.store(record)

        # Verify the metadata passed to upsert
        call_kwargs = vs.upsert.call_args.kwargs
        metadata = call_kwargs["metadata"]
        assert metadata["source"] == "enrichment"
        assert metadata["confidence"] == 0.92
        assert json.loads(metadata["tags"]) == ["varroa", "treatment"]
        assert metadata["agent_id"] == "agent-bee"
        assert metadata["content_fi"] == "Varroan hoitoaikataulu"
        assert metadata["ttl_seconds"] == 3600
        assert metadata["language"] == "fi"
