# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
