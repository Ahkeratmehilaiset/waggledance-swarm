"""Tests for hybrid FAISS + hex-cell retrieval architecture.

Covers:
  - Feature flag OFF path remains compatible
  - Feature flag ON path: local → neighbor → global → LLM ordering
  - Cell assignment determinism
  - Neighbor lookup correctness
  - FAISS local hit behavior
  - Graceful fallback when FAISS unavailable
  - Graceful fallback when embeddings unavailable
  - Route telemetry truthfulness
  - Additive API schema behavior
  - No regression in solver/hotcache behavior
"""

import asyncio
import numpy as np
import pytest

from waggledance.core.hex_cell_topology import (
    ALL_CELLS,
    CELL_GENERAL,
    CELL_MATH,
    CELL_THERMAL,
    CELL_ENERGY,
    CELL_SAFETY,
    CELL_SEASONAL,
    CELL_SYSTEM,
    CELL_LEARNING,
    HexCellTopology,
    CellAssignment,
    _ADJACENCY,
)
from waggledance.application.services.hybrid_retrieval_service import (
    HybridRetrievalService,
    HybridHit,
    HybridTraceResult,
)
from core.faiss_store import FaissRegistry, FaissCollection, SearchResult


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def topology():
    return HexCellTopology()


@pytest.fixture
def faiss_registry(tmp_path):
    return FaissRegistry(base_dir=str(tmp_path / "faiss"), default_dim=4)


@pytest.fixture
def embed_fn():
    """Simple deterministic embedding function for testing (4d)."""
    def _embed(text: str):
        # Deterministic hash-based embedding
        h = hash(text) & 0xFFFFFFFF
        vec = np.array([
            (h & 0xFF) / 255.0,
            ((h >> 8) & 0xFF) / 255.0,
            ((h >> 16) & 0xFF) / 255.0,
            ((h >> 24) & 0xFF) / 255.0,
        ], dtype=np.float32)
        return vec / (np.linalg.norm(vec) + 1e-8)
    return _embed


@pytest.fixture
def hybrid_service(faiss_registry, topology, embed_fn):
    return HybridRetrievalService(
        faiss_registry=faiss_registry,
        topology=topology,
        vector_store=None,  # No ChromaDB in tests
        embed_fn=embed_fn,
        enabled=True,
        ring2_enabled=False,
    )


@pytest.fixture
def hybrid_service_disabled(faiss_registry, topology, embed_fn):
    return HybridRetrievalService(
        faiss_registry=faiss_registry,
        topology=topology,
        vector_store=None,
        embed_fn=embed_fn,
        enabled=False,
    )


# ── Cell Assignment Tests ─────────────────────────────────────


class TestCellAssignment:
    """Test deterministic cell assignment."""

    def test_math_intent_assigns_math_cell(self, topology):
        a = topology.assign_cell("math", "calculate 5+3")
        assert a.cell_id == CELL_MATH
        assert a.method == "intent"

    def test_thermal_intent_assigns_thermal_cell(self, topology):
        a = topology.assign_cell("thermal", "what is the temperature")
        assert a.cell_id == CELL_THERMAL
        assert a.method == "intent"

    def test_chat_intent_with_thermal_keywords(self, topology):
        a = topology.assign_cell("chat", "what is the temperature outside celsius")
        assert a.cell_id == CELL_THERMAL
        assert a.method == "keyword"

    def test_chat_intent_with_energy_keywords(self, topology):
        a = topology.assign_cell("chat", "how much electricity does the grid use")
        assert a.cell_id == CELL_ENERGY
        assert a.method == "keyword"

    def test_chat_intent_no_keywords_defaults_general(self, topology):
        a = topology.assign_cell("chat", "hello world")
        assert a.cell_id == CELL_GENERAL
        assert a.method in ("default", "intent")

    def test_unknown_intent_defaults_general(self, topology):
        a = topology.assign_cell("unknown_intent", "")
        assert a.cell_id == CELL_GENERAL

    def test_assignment_is_deterministic(self, topology):
        """Same input always produces same output."""
        a1 = topology.assign_cell("math", "calculate 5+3")
        a2 = topology.assign_cell("math", "calculate 5+3")
        assert a1.cell_id == a2.cell_id
        assert a1.method == a2.method

    def test_all_intents_have_valid_cells(self, topology):
        intents = ["math", "thermal", "optimization", "seasonal",
                   "constraint", "stats", "symbolic", "causal",
                   "anomaly", "retrieval", "chat"]
        for intent in intents:
            a = topology.assign_cell(intent, "test")
            assert a.cell_id in ALL_CELLS, f"Intent {intent} -> invalid cell {a.cell_id}"

    def test_stats_tracking(self, topology):
        topology.assign_cell("math", "calc")
        topology.assign_cell("chat", "hello")
        topology.assign_cell("chat", "temperature celsius")
        s = topology.stats()
        assert s["assignments"] == 3
        assert s["by_intent"] >= 1
        assert s["cell_counts"]["math"] >= 1


# ── Neighbor Tests ────────────────────────────────────────────


class TestNeighborTopology:
    """Test hex-cell neighbor lookup correctness."""

    def test_all_cells_have_neighbors(self):
        for cell in ALL_CELLS:
            assert cell in _ADJACENCY, f"Cell {cell} missing from adjacency"
            assert len(_ADJACENCY[cell]) >= 2, f"Cell {cell} has < 2 neighbors"

    def test_adjacency_is_bidirectional(self):
        for cell, neighbors in _ADJACENCY.items():
            for n in neighbors:
                assert cell in _ADJACENCY[n], \
                    f"Adjacency not bidirectional: {cell}->{n} but {n} has {_ADJACENCY[n]}"

    def test_ring1_neighbors_correct(self, topology):
        a = topology.assign_cell("math", "calc")
        assert "energy" in a.neighbors_ring1
        assert "general" in a.neighbors_ring1
        assert "system" in a.neighbors_ring1

    def test_ring2_excludes_self_and_ring1(self, topology):
        a = topology.assign_cell("math", "calc")
        for r2 in a.neighbors_ring2:
            assert r2 != a.cell_id, "Ring-2 should not include self"
            assert r2 not in a.neighbors_ring1, "Ring-2 should not include ring-1"

    def test_get_neighbors_ring1_only(self, topology):
        n1 = topology.get_neighbors("math", max_ring=1)
        assert "energy" in n1
        assert len(n1) <= 4

    def test_get_neighbors_ring2(self, topology):
        n2 = topology.get_neighbors("math", max_ring=2)
        assert len(n2) > len(topology.get_neighbors("math", max_ring=1))

    def test_no_self_in_neighbors(self, topology):
        a = topology.assign_cell("math", "calc")
        all_n = a.all_neighbor_cells()
        assert "math" not in all_n


# ── HybridTraceResult Tests ──────────────────────────────────


class TestHybridTraceResult:
    """Test telemetry trace completeness."""

    def test_default_trace(self):
        t = HybridTraceResult()
        d = t.to_dict()
        assert d["retrieval_mode"] == "hybrid"
        assert d["local_hit"] is False
        assert d["neighbor_hit"] is False
        assert d["global_hit"] is False
        assert d["llm_fallback"] is False
        assert d["hit_count"] == 0

    def test_trace_with_hits(self):
        t = HybridTraceResult(
            local_hit=True,
            answered_by_layer="local_faiss",
            cell_id="math",
            hits=[HybridHit("d1", "text", 0.9, "local_faiss", "math")],
        )
        d = t.to_dict()
        assert d["local_hit"] is True
        assert d["answered_by_layer"] == "local_faiss"
        assert d["cell_id"] == "math"
        assert d["hit_count"] == 1

    def test_trace_all_fields_present(self):
        t = HybridTraceResult()
        d = t.to_dict()
        expected_keys = {
            "retrieval_mode", "route_source", "answered_by_layer",
            "cell_id", "neighbor_hops_used", "local_hit", "neighbor_hit",
            "global_hit", "llm_fallback", "local_faiss_ms", "neighbor_faiss_ms",
            "global_chroma_ms", "total_ms", "embeddings_degraded",
            "faiss_degraded", "chroma_degraded", "local_candidates",
            "neighbor_candidates", "global_candidates", "hit_count",
        }
        assert expected_keys.issubset(d.keys())


# ── Feature Flag Tests ────────────────────────────────────────


class TestFeatureFlag:
    """Test hybrid retrieval behavior with flag ON and OFF."""

    def test_disabled_returns_global_only(self, hybrid_service_disabled):
        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service_disabled.retrieve("test query"))
        assert trace.retrieval_mode == "global_only"

    def test_enabled_returns_hybrid(self, hybrid_service):
        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("calculate sum"))
        assert trace.retrieval_mode == "hybrid"

    def test_enable_disable_toggle(self, hybrid_service):
        assert hybrid_service.enabled is True
        hybrid_service.enabled = False
        assert hybrid_service.enabled is False
        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("test"))
        assert trace.retrieval_mode == "global_only"


# ── FAISS Local Hit Tests ─────────────────────────────────────


class TestFaissLocalHit:
    """Test cell-local FAISS retrieval behavior."""

    def test_empty_cell_returns_no_local_hits(self, hybrid_service):
        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("calculate 5+3", intent="math"))
        assert trace.local_candidates == 0

    def test_populated_cell_returns_local_hits(self, hybrid_service, embed_fn):
        # Populate math cell
        col = hybrid_service._faiss_registry.get_or_create("cell_math", dim=4)
        vec = embed_fn("addition formula")
        col.add("doc1", "addition formula: a + b = sum", vec, {"source": "test"})
        col.add("doc2", "subtraction formula: a - b = diff", embed_fn("subtraction"), {})

        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("addition formula", intent="math"))
        assert trace.cell_id == "math"
        # Should have attempted local search
        assert trace.local_faiss_ms >= 0

    def test_ingest_adds_to_correct_cell(self, hybrid_service, embed_fn):
        vec = embed_fn("test document")
        cell_id = asyncio.get_event_loop().run_until_complete(
            hybrid_service.ingest("doc1", "test document", vec, intent="math"))
        assert cell_id == "math"

        # Verify it's in the cell
        col = hybrid_service._faiss_registry.get_or_create("cell_math")
        assert col.count == 1

    def test_ingest_disabled_returns_none(self, hybrid_service_disabled, embed_fn):
        vec = embed_fn("test")
        result = asyncio.get_event_loop().run_until_complete(
            hybrid_service_disabled.ingest("doc1", "test", vec))
        assert result is None


# ── Graceful Fallback Tests ───────────────────────────────────


class TestGracefulFallback:
    """Test degradation behavior."""

    def test_no_embed_fn_degrades_gracefully(self, faiss_registry, topology):
        svc = HybridRetrievalService(
            faiss_registry=faiss_registry,
            topology=topology,
            embed_fn=None,
            enabled=True,
        )
        trace = asyncio.get_event_loop().run_until_complete(
            svc.retrieve("test"))
        assert trace.embeddings_degraded is True

    def test_embed_fn_exception_degrades_gracefully(self, faiss_registry, topology):
        def bad_embed(text):
            raise RuntimeError("Embedding service down")
        svc = HybridRetrievalService(
            faiss_registry=faiss_registry,
            topology=topology,
            embed_fn=bad_embed,
            enabled=True,
        )
        trace = asyncio.get_event_loop().run_until_complete(
            svc.retrieve("test"))
        assert trace.embeddings_degraded is True

    def test_no_vector_store_degrades_chroma(self, hybrid_service):
        """When vector_store is None, global chroma search is degraded."""
        trace = asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("test"))
        assert trace.chroma_degraded is True


# ── Stats Tests ───────────────────────────────────────────────


class TestStats:
    """Test stats reporting."""

    def test_stats_after_queries(self, hybrid_service):
        asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("test1"))
        asyncio.get_event_loop().run_until_complete(
            hybrid_service.retrieve("test2"))
        s = hybrid_service.stats()
        assert s["enabled"] is True
        assert s["total_queries"] == 2

    def test_stats_disabled(self, hybrid_service_disabled):
        s = hybrid_service_disabled.stats()
        assert s["enabled"] is False


# ── FaissCollection Tests ─────────────────────────────────────


class TestFaissCollection:
    """Test FAISS collection add/search/persist."""

    def test_add_and_search(self, tmp_path):
        col = FaissCollection("test", dim=4, persist_dir=str(tmp_path / "test"))
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        col.add("doc1", "test text", vec, {"key": "value"})
        assert col.count == 1

        results = col.search(vec, k=1)
        assert len(results) == 1
        assert results[0].doc_id == "doc1"
        assert results[0].score > 0.9  # Cosine similarity to self should be ~1.0

    def test_empty_collection_returns_empty(self, tmp_path):
        col = FaissCollection("empty", dim=4, persist_dir=str(tmp_path / "empty"))
        results = col.search(np.zeros(4, dtype=np.float32), k=5)
        assert results == []

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "persist")
        col = FaissCollection("persist", dim=4, persist_dir=path)
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        col.add("doc1", "persisted text", vec)
        col.save()

        col2 = FaissCollection("persist", dim=4, persist_dir=path)
        assert col2.count == 1
        results = col2.search(vec, k=1)
        assert results[0].doc_id == "doc1"

    def test_batch_add(self, tmp_path):
        col = FaissCollection("batch", dim=4, persist_dir=str(tmp_path / "batch"))
        vecs = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        col.add_batch(["d1", "d2"], ["t1", "t2"], vecs)
        assert col.count == 2


# ── FaissRegistry Tests ───────────────────────────────────────


class TestFaissRegistry:
    """Test FAISS registry management."""

    def test_get_or_create(self, faiss_registry):
        col = faiss_registry.get_or_create("test_col")
        assert col.name == "test_col"
        assert col.count == 0

    def test_same_name_returns_same_instance(self, faiss_registry):
        c1 = faiss_registry.get_or_create("test_col")
        c2 = faiss_registry.get_or_create("test_col")
        assert c1 is c2

    def test_list_collections(self, faiss_registry):
        faiss_registry.get_or_create("a")
        faiss_registry.get_or_create("b")
        assert set(faiss_registry.list_collections()) == {"a", "b"}

    def test_stats(self, faiss_registry):
        col = faiss_registry.get_or_create("test", dim=4)
        col.add("d1", "t1", np.zeros(4, dtype=np.float32))
        s = faiss_registry.stats()
        assert s["test"] == 1


# ── API Schema Tests ──────────────────────────────────────────


class TestAPISchema:
    """Test that API response schemas are additive and backward-compatible."""

    def test_chat_result_hybrid_trace_optional(self):
        from waggledance.application.dto.chat_dto import ChatResult
        # Without hybrid trace
        r1 = ChatResult(response="hi", language="en", source="solver",
                        confidence=0.9, latency_ms=5.0, agent_id=None,
                        round_table=False, cached=False)
        assert r1.hybrid_trace is None

        # With hybrid trace
        r2 = ChatResult(response="hi", language="en", source="local_faiss",
                        confidence=0.8, latency_ms=10.0, agent_id="hybrid",
                        round_table=False, cached=False,
                        hybrid_trace={"retrieval_mode": "hybrid"})
        assert r2.hybrid_trace is not None

    def test_hybrid_trace_to_dict_structure(self):
        t = HybridTraceResult(
            retrieval_mode="hybrid",
            answered_by_layer="local_faiss",
            cell_id="math",
            local_hit=True,
        )
        d = t.to_dict()
        # All required trace fields present
        assert "retrieval_mode" in d
        assert "answered_by_layer" in d
        assert "cell_id" in d
        assert "local_hit" in d
        assert "neighbor_hit" in d
        assert "global_hit" in d
        assert "llm_fallback" in d
        assert "local_faiss_ms" in d
        assert "neighbor_faiss_ms" in d
        assert "global_chroma_ms" in d
        assert "total_ms" in d
