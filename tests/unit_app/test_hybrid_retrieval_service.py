# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from waggledance.application.services.hybrid_retrieval_service import (
    HybridRetrievalService,
)


class _Topology:
    def assign_cell(self, intent: str, query: str):
        return SimpleNamespace(
            cell_id="hub",
            neighbors_ring1=["bee_ops"],
            neighbors_ring2=[],
        )

    def stats(self) -> dict:
        return {}


class _LegacyRegistry:
    def __init__(self) -> None:
        self.created: list[str] = []

    def get_or_create(self, name: str):
        self.created.append(name)
        return SimpleNamespace(count=0)

    def stats(self) -> dict:
        return {"created": list(self.created)}


def test_retrieve_does_not_create_missing_faiss_collections_on_read_path() -> None:
    registry = _LegacyRegistry()
    service = HybridRetrievalService(
        faiss_registry=registry,
        topology=_Topology(),
        vector_store=None,
        embed_fn=lambda _query: [1.0],
        enabled=True,
    )

    trace = asyncio.run(service.retrieve("hello", k=1))

    assert registry.created == []
    assert trace.retrieval_mode == "hybrid:shadow"
    assert trace.llm_fallback is True


def test_retrieve_uses_get_existing_collection_when_available() -> None:
    hit = SimpleNamespace(
        doc_id="doc-1",
        text="known answer",
        score=0.95,
        metadata={"source": "test"},
    )

    class Registry:
        def __init__(self) -> None:
            self.lookups: list[str] = []

        def get_existing(self, name: str):
            self.lookups.append(name)
            if name == "cell_hub":
                return SimpleNamespace(count=1, search=lambda _vec, k: [hit])
            return None

        def get_or_create(self, name: str):  # pragma: no cover - must not be used
            raise AssertionError(f"unexpected write-path create for {name}")

        def stats(self) -> dict:
            return {}

    registry = Registry()
    service = HybridRetrievalService(
        faiss_registry=registry,
        topology=_Topology(),
        vector_store=None,
        embed_fn=lambda _query: [1.0],
        enabled=True,
    )

    trace = asyncio.run(service.retrieve("hello", k=1))

    assert registry.lookups == ["cell_hub"]
    assert trace.answered_by_layer == "local_faiss"
    assert trace.hits[0].doc_id == "doc-1"


def test_embedding_error_hides_query_and_preserves_global_fallback(caplog) -> None:
    query = "synthetic-private-embedding-query"

    def fail_embed(text):
        assert text == query
        raise RuntimeError(text)

    store = SimpleNamespace(query=AsyncMock(return_value=[
        {"id": "global-1", "text": "synthetic result", "score": 0.9, "metadata": {}}
    ]))
    service = HybridRetrievalService(_LegacyRegistry(), _Topology(), vector_store=store,
                                     embed_fn=fail_embed, enabled=True)
    with caplog.at_level("WARNING"):
        trace = asyncio.run(service.retrieve(query, k=1))

    store.query.assert_awaited_once_with(text=query, n_results=1, collection="waggle_memory")
    assert trace.embeddings_degraded and trace.answered_by_layer == "global_chroma"
    assert trace.hits[0].doc_id == "global-1"
    assert "RuntimeError" in caplog.text and query not in caplog.text


def test_faiss_error_hides_exception_text_and_preserves_global_fallback(caplog) -> None:
    query = "synthetic-private-faiss-query"

    def fail_search(_vector, k):
        raise ValueError(query)

    registry = SimpleNamespace(get_existing=lambda _name: SimpleNamespace(count=1, search=fail_search))
    store = SimpleNamespace(query=AsyncMock(return_value=[
        {"id": "global-2", "text": "synthetic result", "score": 0.9, "metadata": {}}
    ]))
    service = HybridRetrievalService(registry, _Topology(), vector_store=store,
                                     embed_fn=lambda _query: [1.0], enabled=True)
    with caplog.at_level("DEBUG"):
        trace = asyncio.run(service.retrieve(query, k=1))

    store.query.assert_awaited_once_with(text=query, n_results=1, collection="waggle_memory")
    assert trace.answered_by_layer == "global_chroma" and trace.hits[0].doc_id == "global-2"
    assert "ValueError" in caplog.text and query not in caplog.text


def test_global_error_hides_query_and_preserves_degraded_fallback(caplog) -> None:
    query = "synthetic-private-global-query"
    store = SimpleNamespace(query=AsyncMock(side_effect=RuntimeError(query)))
    service = HybridRetrievalService(_LegacyRegistry(), _Topology(), vector_store=store, enabled=False)
    with caplog.at_level("WARNING"):
        trace = asyncio.run(service.retrieve(query, k=1))

    store.query.assert_awaited_once_with(text=query, n_results=1, collection="waggle_memory")
    assert trace.chroma_degraded and trace.llm_fallback and trace.answered_by_layer == "llm"
    assert "RuntimeError" in caplog.text and query not in caplog.text
