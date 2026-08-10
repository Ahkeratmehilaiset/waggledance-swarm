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


def test_getter_less_registry_is_unavailable_and_never_creates_on_read() -> None:
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
    assert service.requested_enabled is True
    assert service.enabled is False
    assert service.faiss_available is False
    assert service.is_authoritative is False
    assert trace.retrieval_mode == "global_only"
    assert trace.faiss_degraded is True
    assert trace.faiss_degraded_reason == "faiss_registry_protocol_unavailable"
    assert trace.llm_fallback is True


def test_missing_faiss_dependency_is_global_only_and_degraded() -> None:
    service = HybridRetrievalService(
        faiss_registry=None,
        topology=_Topology(),
        vector_store=None,
        embed_fn=lambda _query: [1.0],
        enabled=True,
        mode="authoritative",
    )

    trace = asyncio.run(service.retrieve("hello", k=1))

    assert service.requested_enabled is True
    assert service.enabled is False
    assert service.faiss_available is False
    assert service.is_authoritative is False
    assert trace.retrieval_mode == "global_only"
    assert trace.faiss_degraded is True
    assert trace.faiss_degraded_reason == "faiss_dependency_unavailable"


def test_disabled_missing_faiss_is_unavailable_but_not_degraded() -> None:
    service = HybridRetrievalService(
        faiss_registry=None,
        topology=_Topology(),
        vector_store=None,
        enabled=False,
    )

    trace = asyncio.run(service.retrieve("hello", k=1))

    assert service.requested_enabled is False
    assert service.enabled is False
    assert service.faiss_available is False
    assert service.faiss_degraded is False
    assert service.faiss_degraded_reason is None
    assert trace.faiss_degraded is False
    assert trace.faiss_degraded_reason is None


def test_enabled_setter_cannot_activate_unavailable_authoritative_mode() -> None:
    service = HybridRetrievalService(
        faiss_registry=None,
        topology=_Topology(),
        enabled=False,
        mode="authoritative",
    )

    service.enabled = True

    assert service.requested_enabled is True
    assert service.enabled is False
    assert service.is_authoritative is False
    assert service.faiss_degraded is True

    service.enabled = False
    assert service.requested_enabled is False
    assert service.faiss_degraded is False


def test_missing_collection_is_not_a_faiss_degradation() -> None:
    class Registry:
        def get_existing(self, _name: str):
            return None

    service = HybridRetrievalService(
        faiss_registry=Registry(),
        topology=_Topology(),
        vector_store=None,
        embed_fn=lambda _query: [1.0],
        enabled=True,
    )

    trace = asyncio.run(service.retrieve("hello", k=1))

    assert service.enabled is True
    assert service.faiss_available is True
    assert trace.retrieval_mode == "hybrid:shadow"
    assert trace.faiss_degraded is False
    assert trace.faiss_degraded_reason is None


def test_faiss_search_failure_is_visible_without_exposing_exception_text() -> None:
    class Registry:
        def get_existing(self, _name: str):
            raise RuntimeError("private backend detail")

    service = HybridRetrievalService(
        faiss_registry=Registry(),
        topology=_Topology(),
        vector_store=None,
        embed_fn=lambda _query: [1.0],
        enabled=True,
    )

    trace = asyncio.run(service.retrieve("hello", k=1))
    public = trace.to_dict()

    assert trace.retrieval_mode == "hybrid:shadow"
    assert trace.faiss_degraded is True
    assert trace.faiss_degraded_reason == "faiss_search_failed"
    assert "private backend detail" not in str(public)


def test_stats_supports_minimal_read_registry_without_stats_method() -> None:
    class Registry:
        def get_existing(self, _name: str):
            return None

    service = HybridRetrievalService(
        faiss_registry=Registry(),
        topology=_Topology(),
        enabled=True,
    )

    stats = service.stats()

    assert stats["requested_enabled"] is True
    assert stats["enabled"] is True
    assert stats["effective_enabled"] is True
    assert stats["faiss_available"] is True
    assert stats["faiss_degraded"] is False
    assert stats["faiss_degraded_reason"] is None
    assert stats["stats_degraded"] is False
    assert stats["stats_degraded_sources"] == []
    assert stats["faiss_stats"] == {}


def test_stats_preserves_truth_when_topology_stats_fails() -> None:
    class Registry:
        def get_existing(self, _name: str):
            return None

    class BrokenTopology(_Topology):
        def stats(self) -> dict:
            raise RuntimeError("private topology failure")

    service = HybridRetrievalService(
        faiss_registry=Registry(),
        topology=BrokenTopology(),
        enabled=True,
    )

    stats = service.stats()

    assert stats["requested_enabled"] is True
    assert stats["enabled"] is True
    assert stats["faiss_available"] is True
    assert stats["stats_degraded"] is True
    assert stats["stats_degraded_sources"] == ["topology"]
    assert stats["cell_stats"] == {}
    assert "private topology failure" not in str(stats)


def test_stats_preserves_truth_when_registry_stats_fails() -> None:
    class Registry:
        def get_existing(self, _name: str):
            return None

        def stats(self) -> dict:
            raise RuntimeError("private registry failure")

    service = HybridRetrievalService(
        faiss_registry=Registry(),
        topology=_Topology(),
        enabled=True,
    )

    stats = service.stats()

    assert stats["requested_enabled"] is True
    assert stats["enabled"] is True
    assert stats["faiss_available"] is True
    assert stats["stats_degraded"] is True
    assert stats["stats_degraded_sources"] == ["faiss_registry"]
    assert stats["faiss_stats"] == {}
    assert "private registry failure" not in str(stats)


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
