# implements VectorStorePort
"""
ChromaDB-backed VectorStorePort implementation.

Ported from core/memory_engine.py MemoryStore class.
Collections: waggle_memory, swarm_facts, corrections, episodes.
Scoring formula: 1.0 - (dist / 2.0) (cosine distance to similarity).
Circuit breaker prevents cascading failures when ChromaDB is unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Circuit Breaker Constants ─────────────────────────────────
OPEN_THRESHOLD = 3
WINDOW_SECONDS = 60.0
RECOVERY_SECONDS = 30.0

# ── Known Collections ─────────────────────────────────────────
KNOWN_COLLECTIONS = frozenset({
    "waggle_memory", "swarm_facts", "corrections", "episodes",
})


class _CircuitBreaker:
    """Internal circuit breaker for ChromaDB calls.

    States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing recovery).
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = OPEN_THRESHOLD,
        window_s: float = WINDOW_SECONDS,
        recovery_s: float = RECOVERY_SECONDS,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.recovery_s = recovery_s
        self.state = self.CLOSED
        self._failures: list[float] = []
        self._opened_at: float = 0.0
        self._total_trips: int = 0
        self._total_blocked: int = 0

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_s:
                self.state = self.HALF_OPEN
                log.info("CircuitBreaker[%s]: HALF_OPEN (testing recovery)", self.name)
                return True
            self._total_blocked += 1
            return False
        # HALF_OPEN: allow one test request
        return True

    def record_success(self) -> None:
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
            self._failures.clear()
            log.info("CircuitBreaker[%s]: CLOSED (recovered)", self.name)

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures.append(now)
        cutoff = now - self.window_s
        self._failures = [t for t in self._failures if t > cutoff]

        if self.state == self.HALF_OPEN:
            self._trip()
            return

        if len(self._failures) >= self.failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self.state = self.OPEN
        self._opened_at = time.monotonic()
        self._total_trips += 1
        log.warning(
            "CircuitBreaker[%s]: OPEN (%d trips, recovering in %.0fs)",
            self.name, self._total_trips, self.recovery_s,
        )

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "recent_failures": len(self._failures),
            "total_trips": self._total_trips,
            "total_blocked": self._total_blocked,
        }


class ChromaVectorStore:
    """Production VectorStorePort backed by ChromaDB.

    Manages four collections (waggle_memory, swarm_facts, corrections, episodes)
    using chromadb.PersistentClient with cosine distance space.

    Embeds text via Ollama /api/embed before upsert and query.
    Scoring: ``1.0 - (cosine_distance / 2.0)`` converts distance to similarity.
    """

    def __init__(
        self,
        persist_directory: str = "./chroma_data",
        embedding_model: str = "nomic-embed-text",
        ollama_base_url: str = "http://localhost:11434",
        embed_timeout: float = 30.0,
    ) -> None:
        import chromadb

        self._persist_directory = persist_directory
        self._embedding_model = embedding_model
        self._ollama_base_url = ollama_base_url.rstrip("/")
        self._embed_timeout = embed_timeout

        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_directory)

        # Pre-create all known collections with cosine distance
        self._collections: dict[str, Any] = {}
        for coll_name in KNOWN_COLLECTIONS:
            self._collections[coll_name] = self._client.get_or_create_collection(
                name=coll_name,
                metadata={"hnsw:space": "cosine"},
            )

        self._breaker = _CircuitBreaker("chromadb")

        # Embedding cache (LRU)
        from collections import OrderedDict
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embed_cache_max = 500

        log.info(
            "ChromaVectorStore initialized: %s (%s)",
            persist_directory,
            ", ".join(f"{n}={c.count()}" for n, c in self._collections.items()),
        )

    def _get_collection(self, name: str) -> Any:
        """Get or lazily create a ChromaDB collection by name."""
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def _embed_text(self, text: str, prefix: str = "search_document: ") -> list[float] | None:
        """Compute embedding via Ollama /api/embed with caching."""
        prefixed = prefix + text
        cache_key = hashlib.md5(prefixed.encode()).hexdigest()

        if cache_key in self._embed_cache:
            self._embed_cache.move_to_end(cache_key)
            return self._embed_cache[cache_key]

        try:
            import requests
            resp = requests.post(
                f"{self._ollama_base_url}/api/embed",
                json={"model": self._embedding_model, "input": prefixed},
                timeout=self._embed_timeout,
            )
            if resp.status_code != 200:
                log.error("Ollama embed HTTP %d", resp.status_code)
                return None
            embedding = resp.json()["embeddings"][0]
            self._embed_cache[cache_key] = embedding
            while len(self._embed_cache) > self._embed_cache_max:
                self._embed_cache.popitem(last=False)
            return embedding
        except Exception as exc:
            # Timeouts during startup/load are expected — log at warning, not error
            exc_str = str(exc)
            if "timed out" in exc_str.lower() or "timeout" in exc_str.lower():
                log.warning("Ollama embed timeout (model=%s): %s", self._embedding_model, exc)
            else:
                log.error("Ollama embed error: %s", exc)
            return None

    async def upsert(
        self,
        id: str,
        text: str,
        metadata: dict,
        collection: str = "waggle_memory",
    ) -> None:
        """Upsert a document with its embedding into the named collection."""
        if not self._breaker.allow_request():
            log.warning("ChromaDB upsert blocked by circuit breaker")
            return

        embedding = await asyncio.to_thread(self._embed_text, text, "search_document: ")
        if embedding is None:
            log.warning("Skipping upsert for id=%s: embedding failed", id)
            return

        coll = self._get_collection(collection)
        try:
            await asyncio.to_thread(
                coll.upsert,
                ids=[id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[metadata],
            )
            self._breaker.record_success()
        except Exception as exc:
            log.error("ChromaDB upsert error (collection=%s): %s", collection, exc)
            self._breaker.record_failure()

    async def query(
        self,
        text: str,
        n_results: int = 5,
        collection: str = "waggle_memory",
        where: dict | None = None,
    ) -> list[dict]:
        """Query the named collection and return scored results.

        Returns list of dicts with keys: id, text, metadata, score.
        Score formula: 1.0 - (cosine_distance / 2.0).
        """
        if not self._breaker.allow_request():
            log.warning("ChromaDB query blocked by circuit breaker")
            return []

        embedding = await asyncio.to_thread(self._embed_text, text, "search_query: ")
        if embedding is None:
            log.warning("Query embedding failed for text: %s", text[:80])
            return []

        coll = self._get_collection(collection)
        count = coll.count()
        if count == 0:
            return []

        effective_n = min(n_results, count)

        kwargs: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": effective_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = await asyncio.to_thread(coll.query, **kwargs)
            self._breaker.record_success()
        except Exception as exc:
            log.error("ChromaDB query error (collection=%s): %s", collection, exc)
            self._breaker.record_failure()
            return []

        if not results.get("documents") or not results["documents"][0]:
            return []

        output: list[dict] = []
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]
        ids = results["ids"][0]

        for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            score = 1.0 - (dist / 2.0)
            output.append({
                "id": doc_id,
                "text": doc,
                "metadata": meta,
                "score": score,
            })

        output.sort(key=lambda r: r["score"], reverse=True)
        return output

    async def is_ready(self) -> bool:
        """Check if ChromaDB client is responsive and primary collection exists."""
        try:
            coll = self._get_collection("waggle_memory")
            count = await asyncio.to_thread(coll.count)
            return count >= 0
        except Exception as exc:
            log.error("ChromaDB readiness check failed: %s", exc)
            return False

    @property
    def breaker_stats(self) -> dict:
        """Expose circuit breaker stats for observability."""
        return self._breaker.stats
