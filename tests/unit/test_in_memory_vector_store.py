"""Unit tests for InMemoryVectorStore — substring-based vector store stub."""

import pytest

from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore


class TestInMemoryVectorStoreUpsertQuery:
    """Core upsert and query operations."""

    @pytest.mark.asyncio
    async def test_upsert_and_query_returns_item(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="doc1",
            text="Bees produce honey in summer",
            metadata={"source": "test"},
            collection="default",
        )
        results = await stub_vector_store.query("honey", collection="default")
        assert len(results) == 1
        assert results[0]["id"] == "doc1"
        assert results[0]["text"] == "Bees produce honey in summer"

    @pytest.mark.asyncio
    async def test_n_results_limit_respected(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        for i in range(10):
            await stub_vector_store.upsert(
                id=f"doc{i}",
                text=f"beekeeping fact number {i}",
                metadata={"idx": i},
            )
        results = await stub_vector_store.query("beekeeping", n_results=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_different_collections_are_isolated(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="a1", text="alpha content", metadata={}, collection="alpha"
        )
        await stub_vector_store.upsert(
            id="b1", text="beta content", metadata={}, collection="beta"
        )
        alpha_results = await stub_vector_store.query("content", collection="alpha")
        beta_results = await stub_vector_store.query("content", collection="beta")
        assert len(alpha_results) == 1
        assert alpha_results[0]["id"] == "a1"
        assert len(beta_results) == 1
        assert beta_results[0]["id"] == "b1"


class TestInMemoryVectorStoreReadiness:
    """Readiness checks."""

    @pytest.mark.asyncio
    async def test_is_ready_returns_true(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        assert await stub_vector_store.is_ready() is True


class TestInMemoryVectorStoreEdgeCases:
    """Edge cases and metadata handling."""

    @pytest.mark.asyncio
    async def test_query_on_empty_store_returns_empty_list(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        results = await stub_vector_store.query("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_upsert_overwrites_on_same_id(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="dup", text="original content", metadata={"v": 1}
        )
        await stub_vector_store.upsert(
            id="dup", text="updated content", metadata={"v": 2}
        )
        results = await stub_vector_store.query("content")
        # Should have only one result for the same id
        assert len(results) == 1
        assert results[0]["text"] == "updated content"
        assert results[0]["metadata"]["v"] == 2

    @pytest.mark.asyncio
    async def test_metadata_is_preserved(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        meta = {"source": "test", "language": "fi", "confidence": 0.95}
        await stub_vector_store.upsert(
            id="m1", text="metadata test doc", metadata=meta
        )
        results = await stub_vector_store.query("metadata")
        assert results[0]["metadata"]["source"] == "test"
        assert results[0]["metadata"]["language"] == "fi"
        assert results[0]["metadata"]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_where_filter_direct_equality(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="en1", text="English bee doc", metadata={"lang": "en"}
        )
        await stub_vector_store.upsert(
            id="fi1", text="Finnish bee doc", metadata={"lang": "fi"}
        )
        results = await stub_vector_store.query(
            "bee", where={"lang": "fi"}
        )
        assert len(results) == 1
        assert results[0]["id"] == "fi1"

    @pytest.mark.asyncio
    async def test_where_filter_in_operator(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="a", text="topic alpha", metadata={"category": "alpha"}
        )
        await stub_vector_store.upsert(
            id="b", text="topic beta", metadata={"category": "beta"}
        )
        await stub_vector_store.upsert(
            id="c", text="topic gamma", metadata={"category": "gamma"}
        )
        results = await stub_vector_store.query(
            "topic", where={"category": {"$in": ["alpha", "gamma"]}}
        )
        ids = {r["id"] for r in results}
        assert ids == {"a", "c"}

    @pytest.mark.asyncio
    async def test_where_filter_eq_operator(
        self, stub_vector_store: InMemoryVectorStore
    ) -> None:
        await stub_vector_store.upsert(
            id="x", text="exact match test", metadata={"status": "active"}
        )
        await stub_vector_store.upsert(
            id="y", text="exact match test", metadata={"status": "archived"}
        )
        results = await stub_vector_store.query(
            "exact", where={"status": {"$eq": "active"}}
        )
        assert len(results) == 1
        assert results[0]["id"] == "x"
