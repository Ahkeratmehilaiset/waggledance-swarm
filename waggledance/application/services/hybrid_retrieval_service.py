"""
Hybrid retrieval service — orchestrates cell-local FAISS → neighbor FAISS →
global ChromaDB retrieval with full telemetry.

Feature-flagged: when hybrid is disabled, falls through to global Chroma only.
When embeddings or FAISS are unavailable, degrades gracefully.

Retrieval order (when hybrid enabled):
  1. Local FAISS cell (cell assigned by HexCellTopology)
  2. Ring-1 neighbor FAISS cells
  3. (Optional) Ring-2 neighbor FAISS cells (only if ring-1 insufficient)
  4. Global ChromaDB
  5. (LLM fallback handled by caller, not by this service)

Each retrieval attempt is timed and recorded in HybridTraceResult.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class HybridHit:
    """A single retrieval hit from any layer."""
    doc_id: str
    text: str
    score: float
    source_layer: str  # "local_faiss" | "neighbor_faiss" | "global_chroma"
    cell_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridTraceResult:
    """Full telemetry trace for a hybrid retrieval request."""
    retrieval_mode: str = "hybrid"     # "hybrid" | "global_only" | "disabled"
    route_source: str = ""             # which layer answered
    answered_by_layer: str = ""        # solver | local_faiss | neighbor_faiss | global_chroma | llm
    cell_id: str = ""                  # assigned cell
    neighbor_hops_used: int = 0        # 0=local, 1=ring-1, 2=ring-2
    local_hit: bool = False
    neighbor_hit: bool = False
    global_hit: bool = False
    llm_fallback: bool = False
    hits: List[HybridHit] = field(default_factory=list)

    # Timing (milliseconds)
    local_faiss_ms: float = 0.0
    neighbor_faiss_ms: float = 0.0
    global_chroma_ms: float = 0.0
    total_ms: float = 0.0

    # Degraded flags
    embeddings_degraded: bool = False
    faiss_degraded: bool = False
    chroma_degraded: bool = False

    # Counts
    local_candidates: int = 0
    neighbor_candidates: int = 0
    global_candidates: int = 0

    def to_dict(self) -> dict:
        return {
            "retrieval_mode": self.retrieval_mode,
            "route_source": self.route_source,
            "answered_by_layer": self.answered_by_layer,
            "cell_id": self.cell_id,
            "neighbor_hops_used": self.neighbor_hops_used,
            "local_hit": self.local_hit,
            "neighbor_hit": self.neighbor_hit,
            "global_hit": self.global_hit,
            "llm_fallback": self.llm_fallback,
            "local_faiss_ms": round(self.local_faiss_ms, 2),
            "neighbor_faiss_ms": round(self.neighbor_faiss_ms, 2),
            "global_chroma_ms": round(self.global_chroma_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "embeddings_degraded": self.embeddings_degraded,
            "faiss_degraded": self.faiss_degraded,
            "chroma_degraded": self.chroma_degraded,
            "local_candidates": self.local_candidates,
            "neighbor_candidates": self.neighbor_candidates,
            "global_candidates": self.global_candidates,
            "hit_count": len(self.hits),
        }


# Minimum score threshold for considering a hit useful
_MIN_SCORE = 0.35
# Maximum hits to return from any single layer
_MAX_HITS_PER_LAYER = 5
# Score threshold above which we don't need further layers
_SUFFICIENT_SCORE = 0.70


class HybridRetrievalService:
    """Orchestrates hybrid retrieval across cell-local FAISS and global ChromaDB.

    Args:
        faiss_registry: FaissRegistry instance for cell-local indices
        topology: HexCellTopology for cell assignment and neighbor lookup
        vector_store: VectorStorePort (ChromaDB) for global retrieval
        embed_fn: callable(text) -> numpy array or None (embedding function)
        enabled: whether hybrid mode is active (feature flag)
        ring2_enabled: whether to search ring-2 neighbors (default: False)
    """

    def __init__(
        self,
        faiss_registry,
        topology,
        vector_store=None,
        embed_fn=None,
        enabled: bool = False,
        ring2_enabled: bool = False,
        mode: str = "shadow",          # v3 §1.1 — shadow | candidate | authoritative
        min_score: float = 0.35,       # v3 +Phase D v2 — score threshold for off-domain rejection
    ):
        self._faiss_registry = faiss_registry
        self._topology = topology
        self._vector_store = vector_store
        self._embed_fn = embed_fn
        self._enabled = enabled
        self._ring2_enabled = ring2_enabled
        self._mode = mode if mode in ("shadow", "candidate", "authoritative") else "shadow"
        self._min_score = float(min_score)

        # Counters
        self._total_queries = 0
        self._local_hits = 0
        self._neighbor_hits = 0
        self._global_hits = 0
        self._llm_fallbacks = 0
        self._candidates_logged = 0     # v3 §1.1 — candidate-mode trace count
        self._off_domain_rejections = 0 # Phase D v2 — score < min_score events

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def min_score(self) -> float:
        return self._min_score

    @property
    def is_authoritative(self) -> bool:
        """Whether this retrieval should drive production routing decisions.

        Per v3 §1.1: authoritative ⇒ chooses production solver path.
        shadow + candidate ⇒ trace only; production uses old route.
        """
        return self._enabled and self._mode == "authoritative"

    async def retrieve(
        self,
        query: str,
        intent: str = "chat",
        k: int = 5,
    ) -> HybridTraceResult:
        """Execute hybrid retrieval for a query.

        Returns HybridTraceResult with hits and full telemetry.
        If hybrid is disabled, only global ChromaDB is searched.
        """
        t0 = time.perf_counter()
        self._total_queries += 1

        trace = HybridTraceResult()

        if not self._enabled:
            trace.retrieval_mode = "global_only"
            # Fall through to global ChromaDB only
            await self._search_global(query, k, trace)
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return trace

        # Hybrid mode — branch on activation mode (v3 §1.1)
        # shadow:        compute candidates, never drives production
        # candidate:     compute candidates, visible to verifier, may be overridden
        # authoritative: chosen as production solver path
        trace.retrieval_mode = f"hybrid:{self._mode}"

        # Assign cell
        assignment = self._topology.assign_cell(intent, query)
        trace.cell_id = assignment.cell_id

        # Get embedding vector
        query_vec = None
        try:
            if self._embed_fn:
                query_vec = self._embed_fn(query)
        except Exception as e:
            log.warning("Embedding failed for hybrid retrieval: %s", e)
            trace.embeddings_degraded = True

        if query_vec is None:
            trace.embeddings_degraded = True
            # Fallback to global-only (no FAISS without embeddings)
            await self._search_global(query, k, trace)
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return trace

        # 1. Local cell FAISS
        t_local = time.perf_counter()
        local_hits = self._search_faiss_cell(assignment.cell_id, query_vec, k)
        trace.local_faiss_ms = (time.perf_counter() - t_local) * 1000
        trace.local_candidates = len(local_hits)

        if local_hits:
            trace.local_hit = True
            trace.hits.extend(local_hits)
            self._local_hits += 1

        # Check if local results are sufficient
        if self._sufficient(trace.hits, k):
            trace.answered_by_layer = "local_faiss"
            trace.route_source = f"cell:{assignment.cell_id}"
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return trace

        # 2. Ring-1 neighbor cells
        t_neighbor = time.perf_counter()
        remaining = k - len(trace.hits)
        for neighbor_id in assignment.neighbors_ring1:
            n_hits = self._search_faiss_cell(neighbor_id, query_vec, remaining)
            if n_hits:
                trace.hits.extend(n_hits)
                trace.neighbor_hit = True
                remaining = k - len(trace.hits)
                if remaining <= 0:
                    break
        trace.neighbor_faiss_ms = (time.perf_counter() - t_neighbor) * 1000
        trace.neighbor_candidates = len(trace.hits) - trace.local_candidates

        if trace.neighbor_hit:
            trace.neighbor_hops_used = 1
            self._neighbor_hits += 1

        if self._sufficient(trace.hits, k):
            trace.answered_by_layer = "neighbor_faiss"
            trace.route_source = f"cell:{assignment.cell_id}+ring1"
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return trace

        # 3. Ring-2 (optional)
        if self._ring2_enabled and assignment.neighbors_ring2:
            remaining = k - len(trace.hits)
            for nn_id in assignment.neighbors_ring2:
                n_hits = self._search_faiss_cell(nn_id, query_vec, remaining)
                if n_hits:
                    trace.hits.extend(n_hits)
                    trace.neighbor_hit = True
                    remaining = k - len(trace.hits)
                    if remaining <= 0:
                        break
            if trace.neighbor_hops_used < 2 and trace.neighbor_hit:
                trace.neighbor_hops_used = 2

            if self._sufficient(trace.hits, k):
                trace.answered_by_layer = "neighbor_faiss"
                trace.route_source = f"cell:{assignment.cell_id}+ring2"
                trace.total_ms = (time.perf_counter() - t0) * 1000
                return trace

        # 4. Global ChromaDB
        await self._search_global(query, k, trace)

        # Determine answered_by_layer based on best source
        if not trace.hits:
            trace.llm_fallback = True
            trace.answered_by_layer = "llm"
            self._llm_fallbacks += 1
        elif trace.global_hit and not trace.local_hit and not trace.neighbor_hit:
            trace.answered_by_layer = "global_chroma"
            self._global_hits += 1
        elif trace.local_hit:
            trace.answered_by_layer = "local_faiss"
        elif trace.neighbor_hit:
            trace.answered_by_layer = "neighbor_faiss"
        else:
            trace.answered_by_layer = "global_chroma"
            self._global_hits += 1

        trace.route_source = f"cell:{assignment.cell_id}+global"
        trace.total_ms = (time.perf_counter() - t0) * 1000

        # Sort all hits by score, deduplicate, truncate
        trace.hits = self._dedupe_and_sort(trace.hits, k)

        return trace

    async def ingest(
        self,
        doc_id: str,
        text: str,
        vector,
        intent: str = "chat",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Ingest a document into the correct cell-local FAISS index.

        Returns the cell_id it was assigned to, or None if hybrid is disabled.
        Does NOT replace the global ChromaDB ingest — both should happen.
        """
        if not self._enabled:
            return None

        assignment = self._topology.assign_cell(intent, text)
        cell_id = assignment.cell_id
        collection_name = f"cell_{cell_id}"

        try:
            col = self._faiss_registry.get_or_create(collection_name)
            col.add(doc_id, text, vector, metadata or {})
            log.debug("Ingested doc %s into cell %s", doc_id, cell_id)
            return cell_id
        except Exception as e:
            log.warning("Hybrid ingest failed for cell %s: %s", cell_id, e)
            return None

    def stats(self) -> dict:
        """Return hybrid retrieval statistics."""
        total = self._total_queries or 1
        return {
            "enabled": self._enabled,
            "total_queries": self._total_queries,
            "local_hits": self._local_hits,
            "neighbor_hits": self._neighbor_hits,
            "global_hits": self._global_hits,
            "llm_fallbacks": self._llm_fallbacks,
            "local_hit_rate": round(self._local_hits / total, 4),
            "neighbor_hit_rate": round(self._neighbor_hits / total, 4),
            "global_hit_rate": round(self._global_hits / total, 4),
            "llm_fallback_rate": round(self._llm_fallbacks / total, 4),
            "ring2_enabled": self._ring2_enabled,
            "cell_stats": self._topology.stats() if self._topology else {},
            "faiss_stats": self._faiss_registry.stats() if self._faiss_registry else {},
        }

    # ── Private helpers ───────────────────────────────────────

    def _search_faiss_cell(self, cell_id: str, query_vec, k: int) -> List[HybridHit]:
        """Search a single cell's FAISS index."""
        collection_name = f"cell_{cell_id}"
        try:
            col = self._faiss_registry.get_or_create(collection_name)
            if col.count == 0:
                return []
            results = col.search(query_vec, k=k)
            return [
                HybridHit(
                    doc_id=r.doc_id,
                    text=r.text,
                    score=r.score,
                    source_layer="local_faiss" if cell_id else "neighbor_faiss",
                    cell_id=cell_id,
                    metadata=r.metadata,
                )
                for r in results
                if r.score >= self._min_score   # Phase D v2 — instance threshold (was hardcoded _MIN_SCORE)
            ]
        except Exception as e:
            log.debug("FAISS cell %s search failed: %s", cell_id, e)
            return []

    async def _search_global(self, query: str, k: int, trace: HybridTraceResult) -> None:
        """Search global ChromaDB and append hits to trace."""
        if self._vector_store is None:
            trace.chroma_degraded = True
            return

        t_global = time.perf_counter()
        try:
            results = await self._vector_store.query(
                text=query,
                n_results=k,
                collection="waggle_memory",
            )
            trace.global_chroma_ms = (time.perf_counter() - t_global) * 1000
            trace.global_candidates = len(results)

            for r in results:
                score = r.get("score", 0.0)
                if score >= self._min_score:   # Phase D v2 — instance threshold
                    trace.hits.append(HybridHit(
                        doc_id=r.get("id", ""),
                        text=r.get("text", ""),
                        score=score,
                        source_layer="global_chroma",
                        metadata=r.get("metadata", {}),
                    ))
                    trace.global_hit = True
        except Exception as e:
            log.warning("Global ChromaDB search failed: %s", e)
            trace.chroma_degraded = True
            trace.global_chroma_ms = (time.perf_counter() - t_global) * 1000

    @staticmethod
    def _sufficient(hits: List[HybridHit], k: int) -> bool:
        """Check if we have enough high-quality hits to stop searching."""
        good = [h for h in hits if h.score >= _SUFFICIENT_SCORE]
        return len(good) >= min(k, 3)

    @staticmethod
    def _dedupe_and_sort(hits: List[HybridHit], k: int) -> List[HybridHit]:
        """Deduplicate hits by doc_id, sort by score desc, truncate to k."""
        seen: set = set()
        unique: List[HybridHit] = []
        for h in sorted(hits, key=lambda x: x.score, reverse=True):
            if h.doc_id not in seen:
                seen.add(h.doc_id)
                unique.append(h)
        return unique[:k]
