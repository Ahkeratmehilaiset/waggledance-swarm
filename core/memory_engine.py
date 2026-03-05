"""
WaggleDance — Tietoisuuskerros v2.0

Korjattu v1:n 3 kriittistä bugia:
  BUG 1: nomic-embed EI saanut task-prefixiä → kaikki scoret 75-85%
  BUG 2: Suomenkielinen embedding → malli ei ymmärrä → ei erottele
  BUG 3: Ei warmup → 2136ms cold start joka kerta

Uudet ominaisuudet:
  - Opus-MT integraatio: käännä EN:ksi ennen embeddingiä
  - Task prefix: "search_document:" / "search_query:"
  - Warmup startupissa → 10ms per embed
  - Kaksikielinen tallennus (FI + EN)
  - Hallusinaatiotarkistus EN:ksi + keyword overlap
  - Embedding cache (50% vähemmän GPU-kutsuja)
  - Parannettu MathSolver
  - Confidence-pohjainen routing
"""

import math
import time
import hashlib
import json
import logging
import random
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field

log = logging.getLogger("consciousness")


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER — degraded mode when services are slow/down
# ═══════════════════════════════════════════════════════════════

class CircuitBreaker:
    """B3: Generic circuit breaker for external service calls.

    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)

    When a service fails `failure_threshold` times within `window_s` seconds,
    the breaker opens and all calls return the fallback for `recovery_s` seconds.
    After recovery time, one test call is allowed (half-open).
    If it succeeds → close. If it fails → re-open.
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, failure_threshold: int = 3,
                 window_s: float = 60.0, recovery_s: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.recovery_s = recovery_s
        self.state = self.CLOSED
        self._failures: list = []  # timestamps of recent failures
        self._opened_at: float = 0.0
        self._total_trips = 0
        self._total_blocked = 0

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_s:
                self.state = self.HALF_OPEN
                log.info(f"CircuitBreaker[{self.name}]: HALF_OPEN (testing recovery)")
                return True
            self._total_blocked += 1
            return False
        # HALF_OPEN: allow one test request
        return True

    def record_success(self):
        """Call after a successful service call."""
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
            self._failures.clear()
            log.info(f"CircuitBreaker[{self.name}]: CLOSED (recovered)")

    def record_failure(self):
        """Call after a failed service call."""
        now = time.monotonic()
        self._failures.append(now)
        # Prune old failures outside window
        cutoff = now - self.window_s
        self._failures = [t for t in self._failures if t > cutoff]

        if self.state == self.HALF_OPEN:
            self._trip()
            return

        if len(self._failures) >= self.failure_threshold:
            self._trip()

    def _trip(self):
        """Open the circuit breaker."""
        self.state = self.OPEN
        self._opened_at = time.monotonic()
        self._total_trips += 1
        log.warning(f"CircuitBreaker[{self.name}]: OPEN "
                    f"({self._total_trips} trips, recovering in {self.recovery_s}s)")

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "recent_failures": len(self._failures),
            "total_trips": self._total_trips,
            "total_blocked": self._total_blocked,
        }


# ═══════════════════════════════════════════════════════════════
# DATALUOKAT
# ═══════════════════════════════════════════════════════════════

@dataclass
class MemoryMatch:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)
    text_fi: str = ""
    text_en: str = ""


@dataclass
class PreFilterResult:
    handled: bool = False
    answer: Optional[str] = None
    method: str = "none"
    context: str = ""
    confidence: float = 0.0


@dataclass
class HallucinationResult:
    relevance: float = 1.0
    keyword_overlap: float = 1.0
    is_suspicious: bool = False
    reason: str = ""


# ═══════════════════════════════════════════════════════════════
# EMBEDDING ENGINE — nomic-embed-text + cache + prefix
# ═══════════════════════════════════════════════════════════════

class EmbeddingEngine:
    PREFIX_DOCUMENT = "search_document: "
    PREFIX_QUERY = "search_query: "

    def __init__(self, model="nomic-embed-text",
                 base_url="http://localhost:11434",
                 cache_size=500):
        self.model = model
        self.base_url = base_url
        self._available = None
        self._latency_sum = 0.0
        self._latency_count = 0
        self.breaker = CircuitBreaker(f"embed_{model}", failure_threshold=3,
                                      window_s=60, recovery_s=30)
        from collections import OrderedDict
        self._cache: OrderedDict = OrderedDict()
        self._cache_max = cache_size
        self.cache_hits = 0
        self.cache_misses = 0

    @property
    def available(self) -> bool:
        if self._available is None:
            self._check_available()
        return self._available

    def _check_available(self):
        try:
            import requests
            r = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": "test"},
                timeout=5
            )
            self._available = (r.status_code == 200)
            if self._available:
                log.info(f"Embedding: {self.model} ✅")
            else:
                log.warning(f"Embedding: {self.model} ❌ ({r.status_code})")
        except Exception as e:
            self._available = False
            log.warning(f"Embedding: {self.model} ❌ ({e})")

    def warmup(self) -> float:
        t0 = time.perf_counter()
        self.embed_query("warmup")
        ms = (time.perf_counter() - t0) * 1000
        log.info(f"Embedding warmup: {ms:.0f}ms")
        return ms

    def _raw_embed(self, text: str) -> Optional[List[float]]:
        if not self.available:
            return None
        if not self.breaker.allow_request():
            return None
        try:
            import requests
            t0 = time.perf_counter()
            r = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
                timeout=30
            )
            ms = (time.perf_counter() - t0) * 1000
            self._latency_sum += ms
            self._latency_count += 1
            if r.status_code == 200:
                self.breaker.record_success()
                return r.json()["embeddings"][0]
            log.error(f"Embed HTTP {r.status_code}")
            self.breaker.record_failure()
            return None
        except Exception as e:
            log.error(f"Embed error: {e}")
            self.breaker.record_failure()
            return None

    def _cached_embed(self, text: str) -> Optional[List[float]]:
        key = hashlib.md5(text.encode()).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            self._cache.move_to_end(key)  # LRU refresh
            return self._cache[key]
        self.cache_misses += 1
        vec = self._raw_embed(text)
        if vec:
            self._cache[key] = vec
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)  # Evict LRU
        return vec

    def embed_document(self, text: str) -> Optional[List[float]]:
        return self._cached_embed(self.PREFIX_DOCUMENT + text)

    def embed_query(self, text: str) -> Optional[List[float]]:
        return self._cached_embed(self.PREFIX_QUERY + text)

    def embed_batch(self, texts: List[str], mode: str = "document",
                    max_batch: int = 50) -> List[Optional[List[float]]]:
        """Batch embed multiple texts. Returns aligned list (None for failures).

        Args:
            texts: List of texts to embed
            mode: "document" or "query" (determines prefix)
            max_batch: Max texts per Ollama call (chunk larger)
        """
        if not self.available or not texts:
            return [None] * len(texts)

        prefix = self.PREFIX_DOCUMENT if mode == "document" else self.PREFIX_QUERY
        results: List[Optional[List[float]]] = [None] * len(texts)

        # Check cache first, collect uncached indices
        uncached_indices = []
        prefixed_texts = []
        for i, text in enumerate(texts):
            prefixed = prefix + text
            key = hashlib.md5(prefixed.encode()).hexdigest()
            if key in self._cache:
                results[i] = self._cache[key]
                self._cache.move_to_end(key)  # LRU refresh
                self.cache_hits += 1
            else:
                uncached_indices.append(i)
                prefixed_texts.append(prefixed)
                self.cache_misses += 1

        if not uncached_indices:
            return results

        # Batch embed uncached texts in chunks
        try:
            import requests
            for chunk_start in range(0, len(prefixed_texts), max_batch):
                chunk = prefixed_texts[chunk_start:chunk_start + max_batch]
                chunk_indices = uncached_indices[chunk_start:chunk_start + max_batch]

                t0 = time.perf_counter()
                r = requests.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": chunk},
                    timeout=60
                )
                ms = (time.perf_counter() - t0) * 1000
                self._latency_sum += ms
                self._latency_count += 1

                if r.status_code == 200:
                    embeddings = r.json().get("embeddings", [])
                    for j, emb in enumerate(embeddings):
                        if j < len(chunk_indices):
                            idx = chunk_indices[j]
                            results[idx] = emb
                            key = hashlib.md5(chunk[j].encode()).hexdigest()
                            self._cache[key] = emb
                else:
                    log.error(f"Batch embed HTTP {r.status_code}")
        except Exception as e:
            log.error(f"Batch embed error: {e}")

        # LRU eviction after batch
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

        return results

    @property
    def avg_latency_ms(self) -> float:
        if self._latency_count == 0:
            return 0
        return self._latency_sum / self._latency_count

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0


# ═══════════════════════════════════════════════════════════════
# EVAL EMBEDDING ENGINE — all-minilm (symmetric, no prefix)
# ═══════════════════════════════════════════════════════════════

class EvalEmbeddingEngine:
    """Symmetric embedding engine using all-minilm for evaluation tasks.

    Used for: hallucination check (Q vs A similarity), dedup, clustering.
    Unlike nomic-embed-text, all-minilm is symmetric — NO prefix needed.
    """

    def __init__(self, model="all-minilm",
                 base_url="http://localhost:11434",
                 cache_size=500):
        self.model = model
        self.base_url = base_url
        self._available = None
        self._latency_sum = 0.0
        self._latency_count = 0
        from collections import OrderedDict
        self._cache: OrderedDict = OrderedDict()
        self._cache_max = cache_size
        self.cache_hits = 0
        self.cache_misses = 0
        self.breaker = CircuitBreaker(f"eval_embed_{model}", failure_threshold=3,
                                      window_s=60, recovery_s=30)

    @property
    def available(self) -> bool:
        if self._available is None:
            self._check_available()
        return self._available

    def _check_available(self):
        try:
            import requests
            r = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": "test"},
                timeout=5
            )
            if r.status_code == 200:
                self._available = True
                log.info(f"EvalEmbed: {self.model} ✅")
            else:
                # Model not loaded — try to pull it
                log.info(f"EvalEmbed: {self.model} not found, pulling...")
                try:
                    rp = requests.post(
                        f"{self.base_url}/api/pull",
                        json={"name": self.model},
                        timeout=300
                    )
                    if rp.status_code == 200:
                        # Verify after pull
                        r2 = requests.post(
                            f"{self.base_url}/api/embed",
                            json={"model": self.model, "input": "test"},
                            timeout=10
                        )
                        self._available = (r2.status_code == 200)
                    else:
                        self._available = False
                except Exception:
                    self._available = False
                if self._available:
                    log.info(f"EvalEmbed: {self.model} pulled ✅")
                else:
                    log.warning(f"EvalEmbed: {self.model} pull failed ❌")
        except Exception as e:
            self._available = False
            log.warning(f"EvalEmbed: {self.model} ❌ ({e})")

    def warmup(self) -> float:
        t0 = time.perf_counter()
        self.embed("warmup")
        ms = (time.perf_counter() - t0) * 1000
        log.info(f"EvalEmbed warmup: {ms:.0f}ms")
        return ms

    def _raw_embed(self, text: str) -> Optional[List[float]]:
        if not self.available:
            return None
        if not self.breaker.allow_request():
            return None
        try:
            import requests
            t0 = time.perf_counter()
            r = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
                timeout=30
            )
            ms = (time.perf_counter() - t0) * 1000
            self._latency_sum += ms
            self._latency_count += 1
            if r.status_code == 200:
                self.breaker.record_success()
                return r.json()["embeddings"][0]
            log.error(f"EvalEmbed HTTP {r.status_code}")
            self.breaker.record_failure()
            return None
        except Exception as e:
            log.error(f"EvalEmbed error: {e}")
            self.breaker.record_failure()
            return None

    def embed(self, text: str) -> Optional[List[float]]:
        """Embed text (no prefix — symmetric model)."""
        key = hashlib.md5(text.encode()).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            self._cache.move_to_end(key)  # LRU refresh
            return self._cache[key]
        self.cache_misses += 1
        vec = self._raw_embed(text)
        if vec:
            self._cache[key] = vec
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)  # Evict LRU
        return vec

    def embed_batch(self, texts: List[str], max_batch: int = 50) -> List[Optional[List[float]]]:
        """Batch embed multiple texts (no prefix)."""
        if not self.available or not texts:
            return [None] * len(texts)

        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices = []
        uncached_texts = []

        for i, text in enumerate(texts):
            key = hashlib.md5(text.encode()).hexdigest()
            if key in self._cache:
                results[i] = self._cache[key]
                self._cache.move_to_end(key)  # LRU refresh
                self.cache_hits += 1
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)
                self.cache_misses += 1

        if not uncached_indices:
            return results

        try:
            import requests
            for chunk_start in range(0, len(uncached_texts), max_batch):
                chunk = uncached_texts[chunk_start:chunk_start + max_batch]
                chunk_indices = uncached_indices[chunk_start:chunk_start + max_batch]

                t0 = time.perf_counter()
                r = requests.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": chunk},
                    timeout=60
                )
                ms = (time.perf_counter() - t0) * 1000
                self._latency_sum += ms
                self._latency_count += 1

                if r.status_code == 200:
                    embeddings = r.json().get("embeddings", [])
                    for j, emb in enumerate(embeddings):
                        if j < len(chunk_indices):
                            idx = chunk_indices[j]
                            results[idx] = emb
                            key = hashlib.md5(chunk[j].encode()).hexdigest()
                            self._cache[key] = emb
                else:
                    log.error(f"EvalEmbed batch HTTP {r.status_code}")
        except Exception as e:
            log.error(f"EvalEmbed batch error: {e}")

        # LRU eviction after batch
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

        return results

    @property
    def avg_latency_ms(self) -> float:
        if self._latency_count == 0:
            return 0
        return self._latency_sum / self._latency_count

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0


# ═══════════════════════════════════════════════════════════════
# MEMORY STORE — ChromaDB
# ═══════════════════════════════════════════════════════════════

class MemoryStore:
    def __init__(self, path="data/chroma_db"):
        import chromadb
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=path)
        self.breaker = CircuitBreaker("chromadb", failure_threshold=3,
                                      window_s=60, recovery_s=15)
        self.collection = self.client.get_or_create_collection(
            name="waggle_memory",
            metadata={"hnsw:space": "cosine"}
        )
        # Phase 3: Swarm facts — shared knowledge from Level 3+ agents
        self.swarm_facts = self.client.get_or_create_collection(
            name="swarm_facts",
            metadata={"hnsw:space": "cosine"}
        )
        # Phase 4: Corrections — contrastive learning from user corrections
        self.corrections = self.client.get_or_create_collection(
            name="corrections",
            metadata={"hnsw:space": "cosine"}
        )
        # Phase 4: Episodes — conversation turn chains
        self.episodes = self.client.get_or_create_collection(
            name="episodes",
            metadata={"hnsw:space": "cosine"}
        )
        log.info(f"MemoryStore: {self.count} muistoa, "
                 f"{self.swarm_facts.count()} swarm facts, "
                 f"{self.corrections.count()} corrections, "
                 f"{self.episodes.count()} episodes ({path})")

    def store(self, obs_id, text, embedding, metadata=None):
        if not self.breaker.allow_request():
            log.warning("ChromaDB store blocked by circuit breaker")
            return
        try:
            self.collection.upsert(
                ids=[obs_id], embeddings=[embedding],
                documents=[text], metadatas=[metadata or {}]
            )
            self.breaker.record_success()
        except Exception as e:
            log.error(f"ChromaDB store: {e}")
            self.breaker.record_failure()

    def store_batch(self, ids, texts, embeddings, metadatas=None):
        """Batch upsert multiple documents in a single ChromaDB call."""
        if not ids:
            return
        if not self.breaker.allow_request():
            log.warning("ChromaDB store_batch blocked by circuit breaker")
            return
        try:
            self.collection.upsert(
                ids=ids, embeddings=embeddings,
                documents=texts,
                metadatas=metadatas or [{} for _ in ids]
            )
            self.breaker.record_success()
        except Exception as e:
            log.error(f"ChromaDB store_batch: {e}")
            self.breaker.record_failure()

    def search(self, embedding, top_k=5, min_score=0.3,
               where=None, seasonal_boost=None) -> List[MemoryMatch]:
        if self.count == 0:
            return []
        if not self.breaker.allow_request():
            log.warning("ChromaDB search blocked by circuit breaker")
            return []
        kwargs = {
            "query_embeddings": [embedding],
            "n_results": min(top_k, self.count),
            "include": ["documents", "metadatas", "distances"]
        }
        if where:
            kwargs["where"] = where
        try:
            results = self.collection.query(**kwargs)
            self.breaker.record_success()
        except Exception as e:
            log.error(f"ChromaDB query: {e}")
            self.breaker.record_failure()
            return []
        if not results["documents"] or not results["documents"][0]:
            return []
        matches = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        ):
            score = 1.0 - (dist / 2.0)
            if score >= min_score:
                matches.append(MemoryMatch(
                    text=doc, score=score, metadata=meta,
                    text_fi=meta.get("text_fi", ""),
                    text_en=meta.get("text_en", doc)
                ))
        matches = sorted(matches, key=lambda m: m.score, reverse=True)
        # Phase 4: Seasonal scoring boost (1.2x for seasonal keywords)
        if seasonal_boost and matches:
            if isinstance(seasonal_boost, list):
                boost_kws = [kw.lower() for kw in seasonal_boost]
            else:
                boost_kws = seasonal_boost.lower().split()
            for match in matches:
                text_lower = (match.text or "").lower()
                if any(kw in text_lower for kw in boost_kws):
                    match.score = min(match.score * 1.2, 1.0)
            matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    @property
    def count(self):
        return self.collection.count()


# ═══════════════════════════════════════════════════════════════
# MEMORY EVICTION — TTL + max count limits
# ═══════════════════════════════════════════════════════════════

class MemoryEviction:
    """B2: Prevents unbounded ChromaDB growth.

    Two eviction strategies:
    1. TTL-based: source_type-specific expiry (weather=1h, web=90d, user=never)
    2. Count-based: when >max_facts, evict lowest-confidence oldest first

    Protected sources (user_teaching, yaml_scan, etc.) are never TTL-evicted
    but CAN be evicted by count overflow if they're the lowest quality.
    """

    DEFAULT_TTL_RULES = {
        "weather": 1,
        "electricity": 1,
        "electricity_optimization": 6,
        "camera_event": 168,
        "audio_event": 168,
        "news": 720,
        "self_enrichment": 2160,
        "web_learning": 2160,
    }
    DEFAULT_PROTECTED = {
        "user_teaching", "user_correction", "yaml_scan",
        "conversation", "round_table", "distillation",
    }

    def __init__(self, memory: MemoryStore, config: dict = None):
        self.memory = memory
        cfg = config or {}
        self.enabled = cfg.get("enabled", True)
        self.max_facts = cfg.get("max_facts", 200_000)
        self.check_every_n = cfg.get("eviction_check_every_n_flushes", 10)
        self.batch_size = cfg.get("eviction_batch_size", 500)
        self.ttl_rules = cfg.get("ttl_rules", self.DEFAULT_TTL_RULES)
        self.protected_sources = set(
            cfg.get("protected_sources", self.DEFAULT_PROTECTED))
        self._flush_counter = 0
        self._total_evicted = 0

    def on_flush(self) -> int:
        """Called after each batch flush. Returns evicted count (0 most of the time)."""
        if not self.enabled:
            return 0
        self._flush_counter += 1
        if self._flush_counter % self.check_every_n != 0:
            return 0
        return self.run_eviction()

    def run_eviction(self) -> int:
        """Run full eviction cycle: TTL first, then count overflow."""
        evicted = 0
        evicted += self._evict_ttl_expired()
        evicted += self._evict_count_overflow()
        self._total_evicted += evicted
        if evicted > 0:
            log.info(f"MemoryEviction: evicted {evicted} facts "
                     f"(total={self.memory.count}, lifetime_evicted={self._total_evicted})")
        return evicted

    def _evict_ttl_expired(self) -> int:
        """Remove facts whose source_type has a TTL and timestamp is expired."""
        now = datetime.utcnow()
        evicted = 0

        for source_type, ttl_hours in self.ttl_rules.items():
            if source_type in self.protected_sources:
                continue
            cutoff = now - timedelta(hours=ttl_hours)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

            # ChromaDB where filter: source_type match + timestamp older than cutoff
            try:
                results = self.memory.collection.get(
                    where={"source_type": source_type},
                    include=["metadatas"],
                    limit=self.batch_size,
                )
            except Exception as e:
                log.debug(f"TTL eviction query for {source_type}: {e}")
                continue

            if not results["ids"]:
                continue

            ids_to_delete = []
            for oid, meta in zip(results["ids"], results["metadatas"] or []):
                ts = (meta or {}).get("timestamp", "")
                if isinstance(ts, str) and ts < cutoff_str:
                    ids_to_delete.append(oid)

            if ids_to_delete:
                self.memory.collection.delete(ids=ids_to_delete)
                evicted += len(ids_to_delete)
                log.debug(f"TTL evict: {len(ids_to_delete)} {source_type} "
                          f"(older than {ttl_hours}h)")

        return evicted

    def _evict_count_overflow(self) -> int:
        """If collection exceeds max_facts, remove lowest-confidence oldest items."""
        current = self.memory.count
        if current <= self.max_facts:
            return 0

        overflow = current - self.max_facts
        # Evict in batches to avoid huge single operations
        to_evict = min(overflow + self.batch_size, overflow * 2)
        evicted = 0

        # Strategy: fetch a sample, sort by (confidence ASC, timestamp ASC),
        # delete the worst ones. ChromaDB doesn't support ORDER BY, so we
        # fetch a larger batch and sort in Python.
        fetch_size = min(to_evict * 3, 10000, current)
        try:
            results = self.memory.collection.get(
                include=["metadatas"],
                limit=fetch_size,
            )
        except Exception as e:
            log.warning(f"Count overflow fetch: {e}")
            return 0

        if not results["ids"]:
            return 0

        # Score each item: lower = more evictable
        scored = []
        for oid, meta in zip(results["ids"], results["metadatas"] or []):
            meta = meta or {}
            conf = meta.get("confidence", 0.5)
            if isinstance(conf, str):
                try:
                    conf = float(conf)
                except (ValueError, TypeError):
                    conf = 0.5
            ts = meta.get("timestamp", "2020-01-01T00:00:00")
            source = meta.get("source_type", "")
            # Protected sources get a boost to survive longer
            protection_bonus = 0.5 if source in self.protected_sources else 0.0
            scored.append((conf + protection_bonus, ts, oid))

        # Sort: lowest confidence first, then oldest timestamp
        scored.sort(key=lambda x: (x[0], x[1]))

        # Delete the worst ones
        ids_to_delete = [s[2] for s in scored[:to_evict]]
        if ids_to_delete:
            # Delete in chunks of 500 (ChromaDB batch limit)
            for i in range(0, len(ids_to_delete), 500):
                chunk = ids_to_delete[i:i + 500]
                self.memory.collection.delete(ids=chunk)
                evicted += len(chunk)

        return evicted

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_facts": self.max_facts,
            "current_count": self.memory.count,
            "utilization_pct": f"{self.memory.count / self.max_facts * 100:.1f}%",
            "total_evicted": self._total_evicted,
            "flush_counter": self._flush_counter,
        }


# ═══════════════════════════════════════════════════════════════
# MATH SOLVER — laajennettu
# ═══════════════════════════════════════════════════════════════

class MathSolver:
    SAFE_NAMES = {
        "sqrt": math.sqrt, "abs": abs, "round": round,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10, "log2": math.log2,
        "pi": math.pi, "e": math.e,
        "pow": pow, "min": min, "max": max,
        "ceil": math.ceil, "floor": math.floor,
    }
    MATH_TRIGGERS = [
        "calculate", "laske", "paljonko on", "paljonko",
        "compute", "what is", "mikä on", "kuinka paljon",
        "montako", "how much", "eval",
    ]
    # Suomenkieliset operaattorit
    FI_MATH_REPLACEMENTS = [
        (r'neliojuuri\s*(\d+)', r'sqrt(\1)'),
        (r'neliöjuuri\s*(\d+)', r'sqrt(\1)'),
        (r'(\d+)\s*potenssiin\s*(\d+)', r'\1**\2'),
        (r'(\d+)\s*kertaa\s*(\d+)', r'\1*\2'),
        (r'(\d+)\s*jaettuna\s*(\d+)', r'\1/\2'),
        (r'(\d+)\s*plus\s*(\d+)', r'\1+\2'),
        (r'(\d+)\s*miinus\s*(\d+)', r'\1-\2'),
    ]
    UNIT_CONVERSIONS = {
        r'(\d+\.?\d*)\s*°?[cC]\s*(fahrenheit|fahrenheitiksi|to\s*f)':
            lambda m: f"{float(m.group(1)) * 9/5 + 32:.1f}°F",
        r'(\d+\.?\d*)\s*°?[fF]\s*(celsius|celsiukseksi|to\s*c)':
            lambda m: f"{(float(m.group(1)) - 32) * 5/9:.1f}°C",
        r'(\d+\.?\d*)\s*kg\s*(lbs?|paunoiksi|to\s*lbs?)':
            lambda m: f"{float(m.group(1)) * 2.20462:.1f} lbs",
    }

    @classmethod
    def is_math(cls, text):
        clean = text.strip().lower()
        for pattern in cls.UNIT_CONVERSIONS:
            if re.search(pattern, clean):
                return True
        # Suomenkielinen matikka?
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, _ in cls.FI_MATH_REPLACEMENTS:
                if re.search(pattern, clean):
                    return True
        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):
            clean = clean.replace(w, "")
        clean = clean.strip().rstrip("?=")
        if not clean or len(clean) < 2:
            return False
        has_digit = bool(re.search(r'\d', clean))
        has_operator = bool(re.search(r'[+\-*/^%×÷()]', clean))
        has_func = any(fn in clean for fn in
                       ["sqrt", "sin", "cos", "log", "pow", "abs"])
        return (has_digit and has_operator) or has_func

    @classmethod
    def solve(cls, text):
        clean = text.strip().lower()
        for pattern, converter in cls.UNIT_CONVERSIONS.items():
            m = re.search(pattern, clean)
            if m:
                return converter(m)
        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):
            clean = clean.replace(w, "")
        clean = clean.strip().rstrip("?=")
        # Suomenkieliset operaattorit
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, repl in cls.FI_MATH_REPLACEMENTS:
                clean = re.sub(pattern, repl, clean)
        clean = clean.replace("^", "**").replace("×", "*")
        clean = clean.replace("÷", "/").replace(",", ".")
        clean = re.sub(r'\s*(kg|g|ml|l|€|eur|kpl|pcs)\s*$', '', clean)
        try:
            result = eval(clean, {"__builtins__": {}}, cls.SAFE_NAMES)
            if isinstance(result, float):
                if result == int(result):
                    return str(int(result))
                return f"{result:.6g}"
            return str(result)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# OPUS-MT ADAPTER
# ═══════════════════════════════════════════════════════════════

class OpusMTAdapter:
    def __init__(self):
        self._proxy = None
        self._direct_fi_en = None
        self._direct_en_fi = None
        self._available = None

    def set_proxy(self, translation_proxy):
        self._proxy = translation_proxy
        self._available = True

    @property
    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from transformers import MarianMTModel, MarianTokenizer
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def fi_to_en(self, text):
        if not text or not text.strip():
            return text
        if self._proxy and hasattr(self._proxy, 'fi_to_en'):
            try:
                result = self._proxy.fi_to_en(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)
            except Exception:
                pass
        return self._direct_translate(text, "fi", "en")

    def en_to_fi(self, text):
        if not text or not text.strip():
            return text
        if self._proxy and hasattr(self._proxy, 'en_to_fi'):
            try:
                result = self._proxy.en_to_fi(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)
            except Exception:
                pass
        return self._direct_translate(text, "en", "fi")

    def _direct_translate(self, text, src, tgt):
        try:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
            if src == "fi" and tgt == "en":
                if self._direct_fi_en is None:
                    tok = MarianTokenizer.from_pretrained(model_name)
                    mdl = MarianMTModel.from_pretrained(model_name)
                    self._direct_fi_en = (tok, mdl)
                tok, mdl = self._direct_fi_en
            else:
                if self._direct_en_fi is None:
                    tok = MarianTokenizer.from_pretrained(model_name)
                    mdl = MarianMTModel.from_pretrained(model_name)
                    self._direct_en_fi = (tok, mdl)
                tok, mdl = self._direct_en_fi
            inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
            outputs = mdl.generate(**inputs, max_length=512)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            log.error(f"Direct translate {src}->{tgt}: {e}")
            return text


# ═══════════════════════════════════════════════════════════════
# CONSCIOUSNESS v2
# ═══════════════════════════════════════════════════════════════

class Consciousness:
    def __init__(self, db_path="data/chroma_db",
                 ollama_url="http://localhost:11434",
                 embed_model="nomic-embed-text",
                 eval_embed_model="all-minilm",
                 translation_proxy=None):
        self.embed = EmbeddingEngine(model=embed_model, base_url=ollama_url)
        self.eval_embed = EvalEmbeddingEngine(model=eval_embed_model, base_url=ollama_url)
        self.memory = MemoryStore(path=db_path)
        self.math = MathSolver()
        self.opus = OpusMTAdapter()
        if translation_proxy:
            self.opus.set_proxy(translation_proxy)

        self._insight_counter = 0
        self._hallucination_count = 0
        self._prefilter_hits = 0
        self._total_queries = 0

        # Learn queue for batch flush
        self._learn_queue: List[tuple] = []
        self._flush_threshold = 10

        if self.embed.available:
            self.embed.warmup()

        if self.eval_embed.available:
            self.eval_embed.warmup()

        log.info(
            f"🧠 Tietoisuus v2: embed={'✅' if self.embed.available else '❌'}, "
            f"eval_embed={'✅' if self.eval_embed.available else '❌'}, "
            f"opus={'✅' if self.opus.available else '❌'}, "
            f"muisti={self.memory.count}, math=✅"
        )

        # Phase 4: correction tracking
        self._corrections_count = 0
        self._active_learning_count = 0

        # Phase 4: episodic memory
        self._episode_counter = 0
        self._current_session_id = f"session_{int(time.time())}"

        # Phase 4: embedding augmentation synonyms (load once)
        self._domain_synonyms = self._load_domain_synonyms()

        # Phase 4i/4j: Bilingual index + Hot cache + fi_fast
        try:
            from core.fast_memory import BilingualMemoryStore, HotCache, FiFastStore
            _al_cfg = self._load_advanced_learning_config()
            if _al_cfg.get("bilingual_index", True):
                self.bilingual = BilingualMemoryStore(self.memory, self.embed)
            else:
                self.bilingual = None
            _cache_size = _al_cfg.get("hot_cache_size", 500)
            self.hot_cache = HotCache(max_size=_cache_size)
            # fi_fast: all-minilm Finnish vector search (~18ms)
            if (_al_cfg.get("fi_fast_enabled", True)
                    and self.eval_embed.available):
                self.fi_fast = FiFastStore(self.memory, self.eval_embed)
                self.fi_fast.seasonal_prewarm()
            else:
                self.fi_fast = None
        except Exception as e:
            log.warning(f"Phase 4i/4j init: {e}")
            self.bilingual = None
            self.hot_cache = None
            self.fi_fast = None

        # B2: Memory eviction — TTL + max 200K facts
        try:
            _ev_al_cfg = self._load_advanced_learning_config()
            _ev_cfg = _ev_al_cfg.get("memory_limits", {})
            self.eviction = MemoryEviction(self.memory, _ev_cfg)
            if self.eviction.enabled:
                log.info(f"MemoryEviction: max={self.eviction.max_facts}, "
                         f"current={self.memory.count}, "
                         f"utilization={self.eviction.stats['utilization_pct']}")
        except Exception as e:
            log.warning(f"MemoryEviction init: {e}")
            self.eviction = None

        # Phase 10: Micro-model orchestrator (wired by hivemind)
        self.micro_model = None

        # Phase 3: task queue (must be after class body is fully defined at module level,
        # but LearningTaskQueue is defined below — use lazy init in init_task_queue())
        self.task_queue = None

    def wire_audit(self, audit_log, replay_store=None):
        """MAGMA Layer 3: Wire audit trail into learning pipeline."""
        self._audit_log = audit_log
        self._replay_store = replay_store
        log.info("MAGMA: audit wired to consciousness")

    def wire_graph(self, cognitive_graph):
        """MAGMA: Wire cognitive graph into learning pipeline."""
        self._cognitive_graph = cognitive_graph
        log.info("MAGMA: cognitive graph wired to consciousness")

    def set_translation_proxy(self, proxy):
        self.opus.set_proxy(proxy)

    def _load_advanced_learning_config(self) -> dict:
        """Load advanced_learning config section from settings.yaml."""
        try:
            import yaml as _yaml
            path = Path("configs/settings.yaml")
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    cfg = _yaml.safe_load(f) or {}
                return cfg.get("advanced_learning", {})
        except Exception:
            pass
        return {}

    # ── Phase 4: Domain Synonyms ──────────────────────────────

    def _load_domain_synonyms(self) -> dict:
        """Load beekeeping-specific FI→EN synonym mappings for embedding augmentation.

        Returns dict mapping Finnish term → augmented English text.
        Only domain-specific terms (not common words).
        """
        synonyms = {}
        # Built-in beekeeping domain synonyms
        _builtin = {
            "toukkamätä": "Foulbrood | AFB | American Foulbrood | Paenibacillus larvae",
            "eurooppalainen toukkamätä": "EFB | European Foulbrood | Melissococcus plutonius",
            "nosema": "Nosema | Nosema ceranae | Nosema apis | microsporidian",
            "varroa": "Varroa destructor | varroa mite | parasitic mite",
            "kalkkisikiötauti": "Chalkbrood | Ascosphaera apis | fungal disease",
            "oksaalihappo": "Oxalic acid | OA treatment | varroa treatment",
            "muurahaishappo": "Formic acid | MAQS | varroa treatment",
            "kuningatar": "Queen bee | queen | mated queen | virgin queen",
            "pesäkatsaus": "Hive inspection | colony inspection",
            "kevättarkastus": "Spring inspection | spring check",
            "syysruokinta": "Autumn feeding | fall feeding | sugar syrup",
            "talvehtiminen": "Wintering | overwintering | winter cluster",
            "linkous": "Honey extraction | harvesting | uncapping",
            "parveilu": "Swarming | swarm | swarm prevention",
            "yhdyskunta": "Colony | bee colony | hive",
            "siitepöly": "Pollen | bee pollen | pollen collection",
            "mesikausi": "Nectar flow | honey flow | main flow",
            "mehiläisvaha": "Beeswax | wax | comb wax",
        }
        synonyms.update(_builtin)

        # Load from translation proxy dictionary if available
        if (self.opus._proxy and hasattr(self.opus._proxy, 'dict_fi_en')
                and self.opus._proxy.dict_fi_en):
            # Only add beekeeping-specific terms (> 5 chars, not common words)
            _common = {"ja", "on", "ei", "tai", "kun", "miten", "mikä", "se",
                       "tämä", "niin", "kuin", "hän", "minä", "sinä", "he",
                       "olla", "tulla", "saada", "voida", "pitää", "myös"}
            for fi_term, en_term in self.opus._proxy.dict_fi_en.items():
                if (len(fi_term) > 5 and fi_term not in _common
                        and fi_term not in synonyms):
                    synonyms[fi_term] = en_term

        # Load extra synonyms from file if present
        _syn_path = Path("data/domain_synonyms.json")
        if _syn_path.exists():
            try:
                with open(_syn_path, encoding="utf-8") as f:
                    extra = json.load(f)
                if isinstance(extra, dict):
                    synonyms.update(extra)
            except Exception:
                pass

        log.info(f"Phase 4: {len(synonyms)} domain synonyms loaded")
        return synonyms

    # ── Phase 4: Contrastive Learning ─────────────────────────

    def store_correction(self, query, bad_answer, good_answer, agent_id="unknown"):
        """Store user correction in corrections collection."""
        text_en = (f"Q: {self._to_english(query)} "
                   f"BAD: {self._to_english(bad_answer[:300])} "
                   f"GOOD: {self._to_english(good_answer[:300])}")
        embedding = self.embed.embed_document(text_en)
        if not embedding:
            return False
        self._corrections_count += 1
        obs_id = f"correction_{agent_id}_{int(time.time())}_{self._corrections_count:04d}"
        # Determine error type for failure twin analysis
        _ba_lower = bad_answer.lower()
        if len(bad_answer) < 50:
            _error_type = "too_brief"
        elif "en tiedä" in _ba_lower or "en osaa" in _ba_lower:
            _error_type = "knowledge_gap"
        else:
            _error_type = "wrong_content"
        self.memory.corrections.upsert(
            ids=[obs_id], embeddings=[embedding], documents=[text_en],
            metadatas=[{
                "query": query[:200], "bad_answer": bad_answer[:300],
                "good_answer": good_answer[:300], "agent_id": agent_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "error_type": _error_type,
            }])
        # Also learn the correct answer with high confidence
        self.learn(f"Q: {query} → A: {good_answer}", agent_id=agent_id,
                   source_type="user_correction", confidence=0.9,
                   validated=True, immediate=True)
        log.info(f"📝 Correction stored: {query[:60]} → {good_answer[:60]}")
        return True

    def check_previous_corrections(self, query, top_k=2):
        """Check if this query has prior corrections — inject warning context."""
        if self.memory.corrections.count() == 0:
            return ""
        q_en = self._to_english(query)
        q_vec = self.embed.embed_query(q_en)
        if not q_vec:
            return ""
        results = self.memory.corrections.query(
            query_embeddings=[q_vec], n_results=top_k,
            include=["metadatas", "distances"])
        if not results["distances"] or not results["distances"][0]:
            return ""
        corrections = []
        for i, dist in enumerate(results["distances"][0]):
            if dist < 0.3:  # cosine distance < 0.3 → high similarity
                meta = results["metadatas"][0][i]
                corrections.append(
                    f"Previously wrong: {meta.get('bad_answer', '')}. "
                    f"Correct: {meta.get('good_answer', '')}")
        if corrections:
            log.info(f"Corrections injected for query: {query[:60]} "
                     f"({len(corrections)} match(es))")
        return "\n".join(corrections)

    def get_agent_error_patterns(self, agent_id: str, query: str, top_k: int = 3) -> str:
        """Search corrections collection for known mistakes by this specific agent.

        Returns formatted warning string if similar query found, else empty string.
        """
        if self.memory.corrections.count() == 0:
            return ""
        # Check config
        try:
            _cfg = self._load_advanced_learning_config()
            if not _cfg.get("failure_twin_enabled", True):
                return ""
            threshold = _cfg.get("failure_twin_threshold", 0.60)
        except Exception:
            threshold = 0.60

        q_en = self._to_english(query)
        q_vec = self.embed.embed_query(q_en)
        if not q_vec:
            return ""

        try:
            results = self.memory.corrections.query(
                query_embeddings=[q_vec], n_results=min(top_k * 2, 10),
                include=["metadatas", "distances"])
        except Exception:
            return ""

        if not results["distances"] or not results["distances"][0]:
            return ""

        warnings = []
        for i, dist in enumerate(results["distances"][0]):
            score = 1.0 - (dist / 2.0)
            if score < threshold:
                continue
            meta = results["metadatas"][0][i]
            if meta.get("agent_id") != agent_id:
                continue
            bad = meta.get("bad_answer", "")[:150]
            good = meta.get("good_answer", "")[:150]
            warnings.append(f"- Previously gave wrong answer: '{bad}'. Correct was: '{good}'")

        if not warnings:
            return ""

        return ("FAILURE TWIN WARNING — Avoid repeating these known mistakes:\n"
                + "\n".join(warnings[:top_k]))

    # ── Phase 4: Active Learning ──────────────────────────────

    def detect_user_teaching(self, message, prev_method=None):
        """Return True if this message is a teaching response after active_learning."""
        if prev_method != "active_learning":
            return False
        # Short messages ("ei", "joo") are not teaching
        if len(message.strip()) < 20:
            return False
        # Negation patterns — user saying "no" not teaching
        _neg = {"en tiedä", "en osaa", "ei kiinnosta", "skip", "ohita"}
        msg_lower = message.lower().strip()
        if any(neg in msg_lower for neg in _neg):
            return False
        return True

    def learn_from_user(self, teaching_text, original_query):
        """Store user-taught fact with high confidence."""
        fact = f"Q: {original_query} → A: {teaching_text}"
        self._active_learning_count += 1
        return self.learn(fact, agent_id="user", source_type="user_teaching",
                          confidence=0.9, validated=True, immediate=True)

    # ── Phase 4: Embedding Augmentation ───────────────────────

    def _augment_text_for_embedding(self, text_en, text_fi_original=""):
        """Append domain synonyms to text for richer embedding vectors."""
        if not self._domain_synonyms:
            return text_en
        text_lower = (text_fi_original or text_en).lower()
        augmented_terms = []
        for fi_term, en_synonyms in self._domain_synonyms.items():
            if fi_term in text_lower:
                augmented_terms.append(en_synonyms)
        if augmented_terms:
            return f"{text_en} | {' | '.join(augmented_terms[:5])}"
        return text_en

    # ── Phase 4: Multi-hop RAG ────────────────────────────────

    def multi_hop_search(self, query, max_hops=2):
        """2-hop search: first results → extract entities → second search → merge."""
        q_en = self._to_english(query)
        q_vec = self.embed.embed_query(q_en)
        if not q_vec:
            return []

        # Get seasonal boost for current month
        _month = datetime.now().month
        _seasonal = SEASONAL_BOOST.get(_month, "")

        # Hop 1: standard search
        hop1 = self.memory.search(q_vec, top_k=3, min_score=0.3,
                                  seasonal_boost=_seasonal)
        if not hop1 or hop1[0].score > 0.70:
            return hop1  # Good enough, no need for hop 2

        # Extract entities from hop 1 results
        entities = self._extract_entities(hop1)
        if not entities:
            return hop1

        # Hop 2: search for each entity
        hop2_results = []
        seen_ids = set()
        for m in hop1:
            _oid = m.metadata.get("obs_id", "") if m.metadata else ""
            if _oid:
                seen_ids.add(_oid)

        for entity in entities[:3]:
            e_vec = self.embed.embed_query(entity)
            if not e_vec:
                continue
            matches = self.memory.search(e_vec, top_k=2, min_score=0.4,
                                         seasonal_boost=_seasonal)
            for m in matches:
                oid = m.metadata.get("obs_id", "") if m.metadata else ""
                if oid and oid not in seen_ids:
                    hop2_results.append(m)
                    seen_ids.add(oid)
                elif not oid:
                    hop2_results.append(m)

        # Merge and rank by score
        all_results = hop1 + hop2_results
        all_results.sort(key=lambda m: m.score, reverse=True)
        return all_results[:5]

    def _extract_entities(self, matches):
        """Simple entity extraction from memory match text.

        Finds capitalized terms, domain-specific nouns, quoted terms.
        """
        entities = []
        seen = set()
        for m in matches:
            text = m.text_en or m.text or ""
            # Find capitalized multi-word terms (e.g., "Varroa destructor")
            caps = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', text)
            for cap in caps:
                if len(cap) > 3 and cap.lower() not in seen:
                    entities.append(cap)
                    seen.add(cap.lower())
            # Find domain terms in parentheses or after colons
            parens = re.findall(r'\(([^)]+)\)', text)
            for p in parens:
                p = p.strip()
                if len(p) > 3 and p.lower() not in seen:
                    entities.append(p)
                    seen.add(p.lower())
            # Find terms after ":" or "→"
            after_colon = re.findall(r'[→:]\s*([A-Za-z][A-Za-z\s]{3,30})', text)
            for ac in after_colon:
                ac = ac.strip()
                if ac.lower() not in seen:
                    entities.append(ac)
                    seen.add(ac.lower())
        return entities[:6]

    # ── Phase 4: Episodic Memory ──────────────────────────────

    def store_episode(self, query, response, session_id=None,
                      prev_episode_id=None, quality=0.7, resolved=True):
        """Store conversation turn as episode with chain linking."""
        sid = session_id or self._current_session_id
        self._episode_counter += 1
        ep_id = f"ep_{sid}_{self._episode_counter:06d}"
        text_en = (f"Q: {self._to_english(query[:200])} "
                   f"A: {self._to_english(response[:300])}")
        embedding = self.embed.embed_document(text_en)
        if not embedding:
            return None
        self.memory.episodes.upsert(
            ids=[ep_id], embeddings=[embedding], documents=[text_en],
            metadatas=[{
                "session_id": sid,
                "prev_episode_id": prev_episode_id or "",
                "query": query[:200], "response": response[:300],
                "quality": quality, "resolved": resolved,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }])
        return ep_id

    def get_episode_chain(self, episode_id, max_depth=10):
        """Follow prev_episode_id links to retrieve full conversation chain."""
        chain = []
        current_id = episode_id
        for _ in range(max_depth):
            if not current_id:
                break
            try:
                result = self.memory.episodes.get(
                    ids=[current_id],
                    include=["metadatas", "documents"])
                if not result["ids"]:
                    break
                meta = result["metadatas"][0] if result["metadatas"] else {}
                doc = result["documents"][0] if result["documents"] else ""
                chain.append({"id": current_id, "text": doc, "metadata": meta})
                current_id = meta.get("prev_episode_id", "")
            except Exception:
                break
        chain.reverse()  # oldest first
        return chain

    @staticmethod
    def _is_finnish(text):
        fi_chars = set("äöåÄÖÅ")
        fi_words = {"on", "ja", "ei", "tai", "kun", "mikä", "miten",
                    "kuinka", "milloin", "missä", "paljonko", "onko",
                    "mitä", "kuka", "miksi", "minä", "sinä", "hän",
                    "tämä", "se", "niin", "kanssa", "myös"}
        has_fi_char = any(c in fi_chars for c in text)
        words = set(text.lower().split())
        return has_fi_char or len(words & fi_words) >= 2

    def _to_english(self, text):
        """Translate Finnish to English. LAZY: Don't load models during startup."""
        if not self._is_finnish(text):
            return text
        if not self.opus.available:
            return text

        # Check if models are ACTUALLY loaded (not just available)
        if self.opus._proxy:
            # Check if Opus-MT models are loaded in the proxy
            opus_loaded = (
                hasattr(self.opus._proxy, 'opus') and
                self.opus._proxy.opus and
                (self.opus._proxy.opus._fi_en is not None or
                 self.opus._proxy.opus._en_fi is not None)
            )
            if opus_loaded:
                # Models already loaded - safe to translate
                return self.opus.fi_to_en(text)

        # Models not loaded yet - skip translation to avoid loading during startup
        # YAML seeding will work fine with mixed FI/EN facts
        return text

    # ── A) PRE-FILTER ─────────────────────────────────────────

    def before_llm(self, message):
        """PHASE1 TASK4: Smart Router — confidence-based model selection.
        Returns PreFilterResult with method indicating routing tier:
          - "math": direct answer, no LLM (confidence=1.0)
          - "memory_direct": score>0.90 validated → direct answer, no LLM
          - "memory_fast": score>0.70 → use llama1b with context
          - "memory_context": score>0.50 → use phi4-mini with context
          - "none": no good context → phi4-mini without context
        """
        self._total_queries += 1

        # Phase 10: Layer -1 — Micro-Model (0.01-1ms)
        if hasattr(self, 'micro_model') and self.micro_model:
            mm_result = self.micro_model.predict(message)
            if mm_result and mm_result.get("confidence", 0) > 0.85:
                self._prefilter_hits += 1
                log.info(f"🤖 MicroModel {mm_result.get('method', 'v1')} "
                         f"({mm_result['confidence']:.0%}): "
                         f"{mm_result['answer'][:80]}")
                return PreFilterResult(
                    handled=True, answer=mm_result["answer"],
                    method=f"micro_{mm_result.get('method', 'v1')}",
                    confidence=mm_result["confidence"])

        # Phase 4j: Layer 0 — Hot Cache (5ms, zero GPU)
        if self.hot_cache:
            cache_hit = self.hot_cache.get(message)
            if cache_hit and cache_hit["score"] > 0.85:
                self._prefilter_hits += 1
                log.info(f"🔥 Hot cache ({cache_hit['score']:.0%}): "
                         f"{cache_hit['answer'][:80]}")
                return PreFilterResult(
                    handled=True, answer=cache_hit["answer"],
                    method="hot_cache", confidence=cache_hit["score"])

        # 1. Math — direct answer, no LLM
        if self.math.is_math(message):
            result = self.math.solve(message)
            if result is not None:
                self._prefilter_hits += 1
                log.info(f"🧮 Math: {message.strip()} = {result}")
                return PreFilterResult(
                    handled=True, answer=result,
                    method="math", confidence=1.0)

        # fi_fast: Layer 1.5 — all-minilm Finnish search (~18ms, no translation)
        if (hasattr(self, 'fi_fast') and self.fi_fast
                and self._is_finnish(message)
                and self.fi_fast.fi_fast_count > 0):
            _month_ff = datetime.now().month
            _seasonal_ff = SEASONAL_BOOST.get(_month_ff, "")
            ff_matches = self.fi_fast.search(
                message, top_k=5, min_score=0.3,
                seasonal_boost=_seasonal_ff)
            if ff_matches and ff_matches[0].score > 0.90:
                best_ff = ff_matches[0]
                if (best_ff.metadata.get("validated")
                        and best_ff.metadata.get("confidence", 0) > 0.8):
                    self._prefilter_hits += 1
                    answer = best_ff.text_fi or best_ff.text
                    # Auto-populate hot cache
                    if self.hot_cache:
                        self.hot_cache.put(message, answer,
                                           best_ff.score, source="fi_fast")
                    log.info(f"⚡ fi_fast ({best_ff.score:.0%}): "
                             f"{answer[:80]}")
                    return PreFilterResult(
                        handled=True, answer=answer,
                        method="fi_fast", confidence=best_ff.score)

        # Phase 4i: Layer 2 — FI-direct ChromaDB (55ms, skip translation)
        if (self.bilingual and self._is_finnish(message)
                and self.bilingual.fi_count > 0):
            _month_fi = datetime.now().month
            _seasonal_fi = SEASONAL_BOOST.get(_month_fi, "")
            fi_matches = self.bilingual.search_fi(
                message, top_k=5, min_score=0.3,
                seasonal_boost=_seasonal_fi)
            if fi_matches and fi_matches[0].score > 0.90:
                best_fi = fi_matches[0]
                if (best_fi.metadata.get("validated")
                        and best_fi.metadata.get("confidence", 0) > 0.8):
                    self._prefilter_hits += 1
                    answer = best_fi.text_fi or best_fi.text
                    # Auto-populate hot cache
                    if self.hot_cache:
                        self.hot_cache.put(message, answer,
                                           best_fi.score, source="fi_direct")
                    log.info(f"🇫🇮 FI-direct ({best_fi.score:.0%}): "
                             f"{answer[:80]}")
                    return PreFilterResult(
                        handled=True, answer=answer,
                        method="fi_direct", confidence=best_fi.score)

        # 2. Memory search (EN embedding + query prefix)
        if self.embed.available and self.memory.count > 0:
            msg_en = self._to_english(message)
            q_vec = self.embed.embed_query(msg_en)

            if q_vec:
                # Phase 4: seasonal boost for current month
                _month = datetime.now().month
                _seasonal = SEASONAL_BOOST.get(_month, "")
                matches = self.memory.search(q_vec, top_k=5, min_score=0.3,
                                             seasonal_boost=_seasonal)

                # Phase 4: inject corrections context if any
                _corrections_ctx = self.check_previous_corrections(message)

                if matches:
                    best = matches[0]

                    # Tier 1: >0.90 validated → direct answer, no LLM
                    if (best.score > 0.90
                            and best.metadata.get("validated")
                            and best.metadata.get("confidence", 0) > 0.8):
                        self._prefilter_hits += 1
                        answer = best.text_fi or best.text
                        # Phase 4j: auto-populate hot cache
                        if self.hot_cache:
                            self.hot_cache.put(message, answer,
                                               best.score, source="memory_direct")
                        log.info(f"🧠 Muistista suoraan ({best.score:.0%}): {answer[:80]}")
                        return PreFilterResult(
                            handled=True, answer=answer,
                            method="memory_direct", confidence=best.score)

                    # Tier 2: >0.70 → use llama1b (fast) with context
                    if best.score > 0.70:
                        parts = []
                        for m in matches[:3]:
                            en = m.text_en or m.text
                            parts.append(f"[{m.score:.0%}] {en}")
                        context = "KNOWN FACTS (use if relevant):\n" + "\n".join(parts)
                        if _corrections_ctx:
                            context += f"\n\nCORRECTIONS (avoid repeating):\n{_corrections_ctx}"
                        log.info(f"🧠 Konteksti llama1b:lle ({best.score:.0%})")
                        return PreFilterResult(
                            handled=False, context=context,
                            method="memory_fast", confidence=best.score)

                    # Tier 3: >0.50 → use phi4-mini with full context
                    if best.score > 0.50:
                        # Phase 4: try multi-hop for better context
                        multi_results = self.multi_hop_search(message)
                        if multi_results and multi_results[0].score > best.score:
                            matches = multi_results

                        parts = []
                        for m in matches[:5]:
                            en = m.text_en or m.text
                            parts.append(f"[{m.score:.0%}] {en}")
                        context = "KNOWN FACTS (use if relevant):\n" + "\n".join(parts)
                        if _corrections_ctx:
                            context += f"\n\nCORRECTIONS (avoid repeating):\n{_corrections_ctx}"
                        log.info(f"🧠 Konteksti phi4-mini:lle ({best.score:.0%})")
                        return PreFilterResult(
                            handled=False, context=context,
                            method="memory_context", confidence=best.score)

        # Tier 4: no good context
        # Phase 3: record for guided learning
        if hasattr(self, 'task_queue') and self.task_queue:
            self.task_queue.record_low_confidence_query(message, 0.0)

        # Phase 4: Active Learning — ask user instead of hallucinating
        if self.memory.count > 100:
            self._active_learning_count += 1
            log.info(f"🎓 Active learning: {message[:60]}")
            return PreFilterResult(
                handled=True,
                answer="En ole varma tästä aiheesta. Tiedätkö mikä on vastaus? Voin oppia sinulta!",
                method="active_learning", confidence=0.0)

        return PreFilterResult(handled=False, method="none")

    def get_context(self, message, top_k=3):
        if not self.embed.available or self.memory.count == 0:
            return ""
        msg_en = self._to_english(message)
        q_vec = self.embed.embed_query(msg_en)
        if not q_vec:
            return ""
        matches = self.memory.search(q_vec, top_k=top_k, min_score=0.6)
        if not matches:
            return ""
        parts = [f"- {m.text_en or m.text}" for m in matches]
        return "Known facts:\n" + "\n".join(parts)

    # ── B) HALLUCINATION CHECK ────────────────────────────────

    def check_hallucination(self, question, answer):
        if not self.embed.available:
            return HallucinationResult(relevance=1.0)

        q_en = self._to_english(question)
        a_en = self._to_english(answer[:500])

        # Use eval_embed (symmetric all-minilm) if available — better for Q vs A comparison
        if self.eval_embed.available:
            q_vec = self.eval_embed.embed(q_en)
            a_vec = self.eval_embed.embed(a_en)
        else:
            q_vec = self.embed.embed_query(q_en)
            a_vec = self.embed.embed_document(a_en)

        if not q_vec or not a_vec:
            return HallucinationResult(relevance=1.0)

        dot = sum(a * b for a, b in zip(q_vec, a_vec))
        norm_q = sum(x * x for x in q_vec) ** 0.5
        norm_a = sum(x * x for x in a_vec) ** 0.5
        similarity = dot / (norm_q * norm_a) if norm_q and norm_a else 0

        # Keyword overlap
        stops = {"the", "and", "for", "are", "but", "not", "you",
                 "all", "can", "her", "was", "one", "our", "out",
                 "has", "have", "with", "this", "that", "from",
                 "they", "been", "said", "each", "which", "their",
                 "what", "how", "who", "when", "where", "why",
                 "does", "did", "will", "would", "could", "should"}
        q_words = set(re.findall(r'\b\w{3,}\b', q_en.lower())) - stops
        a_words = set(re.findall(r'\b\w{3,}\b', a_en.lower())) - stops
        # FIX: empty q_words after stopword removal → 0.0 (was 1.0 free pass)
        overlap = len(q_words & a_words) / len(q_words) if q_words else 0.0

        # FIX: keyword is primary signal (0.7), embedding secondary (0.3)
        combined = 0.3 * similarity + 0.7 * overlap
        # FIX: threshold 0.45 (was 0.30)
        is_suspicious = combined < 0.45
        # FIX: hard gate — no keyword overlap + low similarity → always suspicious
        if overlap == 0.0 and similarity < 0.65:
            is_suspicious = True
        reason = ""
        if is_suspicious:
            self._hallucination_count += 1
            reason = f"embed={similarity:.0%}, keyword={overlap:.0%}, combined={combined:.0%}"
            log.warning(f"⚠️ Hallusinaatio? {reason}")

        return HallucinationResult(
            relevance=similarity, keyword_overlap=overlap,
            is_suspicious=is_suspicious, reason=reason)

    # ── C) LEARNING ───────────────────────────────────────────

    def learn(self, text, agent_id="system", source_type="heartbeat",
              confidence=0.5, validated=False, metadata=None,
              immediate=False):
        """Learn a fact. By default queues for batch flush.

        Args:
            immediate: If True, store immediately (old behavior).
                       If False, queue for batch flush at threshold.
        """
        if not self.embed.available:
            return False
        if not text or len(text.strip()) < 10:
            return False
        if confidence < 0.2:
            return False

        if immediate:
            return self._learn_single(text, agent_id, source_type,
                                      confidence, validated, metadata)

        # Queue for batch flush
        self._learn_queue.append((text, {
            "agent_id": agent_id,
            "source_type": source_type,
            "confidence": confidence,
            "validated": validated,
            **(metadata or {}),
        }))

        if len(self._learn_queue) >= self._flush_threshold:
            self._flush_learn_queue()

        return True

    def _learn_single(self, text, agent_id="system", source_type="heartbeat",
                      confidence=0.5, validated=False, metadata=None):
        """Original single-item learn logic: translate → embed → dedup → store."""
        text_fi = text
        text_en = self._to_english(text)
        combined = f"{text_fi} | {text_en}" if text_en != text_fi else text

        # Phase 4: embedding augmentation — append domain synonyms
        text_for_embed = self._augment_text_for_embedding(text_en, text_fi)
        embedding = self.embed.embed_document(text_for_embed)
        if not embedding:
            return False

        existing = self.memory.search(embedding, top_k=1, min_score=0.93)
        if existing:
            log.debug(f"Duplikaatti ({existing[0].score:.0%}): {text[:50]}")
            return False

        self._insight_counter += 1
        obs_id = f"{source_type}_{agent_id}_{int(time.time())}_{self._insight_counter:04d}"

        meta = {
            "agent_id": agent_id, "source_type": source_type,
            "confidence": confidence, "validated": validated,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "text_fi": text_fi, "text_en": text_en,
            "_provenance_id": obs_id,
        }
        if metadata:
            meta.update(metadata)

        self.memory.store(obs_id, combined, embedding, meta)

        # MAGMA: audit + replay
        _al = getattr(self, '_audit_log', None)
        if _al:
            import hashlib as _hl
            _hash = _hl.sha256(combined.encode()).hexdigest()[:16]
            _al.record(
                "store", obs_id, agent_id=agent_id,
                content_hash=_hash, details=combined,
            )
            _rs = getattr(self, '_replay_store', None)
            if _rs:
                _rs.store(obs_id, combined, _hash, meta)

        # MAGMA: cognitive graph node + edge
        _cg = getattr(self, '_cognitive_graph', None)
        if _cg:
            try:
                _cg.add_node(obs_id, agent_id=agent_id, source_type=source_type)
                _src_doc = (metadata or {}).get("enrichment_source")
                if _src_doc:
                    _cg.add_edge(_src_doc, obs_id, link_type="derived_from")
            except Exception:
                pass

        # Phase 4i: store in bilingual FI collection
        if self.bilingual:
            self.bilingual.store_bilingual(
                obs_id, text_fi, text_en, embedding, meta)

        # fi_fast: store with all-minilm embedding
        if hasattr(self, 'fi_fast') and self.fi_fast:
            self.fi_fast.store(obs_id, text_fi, meta)

        log.info(f"📝 Opittu #{self.memory.count}: [{agent_id}] {text[:60]}")
        return True

    def _flush_learn_queue(self):
        """Batch flush queued learn items: translate → embed → dedup → store."""
        if not self._learn_queue:
            return 0

        # Copy and clear queue atomically
        queue = self._learn_queue[:]
        self._learn_queue.clear()

        texts = [item[0] for item in queue]
        metas = [item[1] for item in queue]

        # Detect Finnish texts and batch translate
        texts_en = []
        for text in texts:
            if self._is_finnish(text) and self.opus.available:
                if self.opus._proxy and hasattr(self.opus._proxy, 'batch_fi_to_en'):
                    # Will be handled in batch below
                    texts_en.append(None)  # placeholder
                else:
                    texts_en.append(self.opus.fi_to_en(text))
            else:
                texts_en.append(text)

        # Batch translate Finnish texts if proxy supports it
        fi_indices = [i for i, en in enumerate(texts_en) if en is None]
        if fi_indices and self.opus._proxy and hasattr(self.opus._proxy, 'batch_fi_to_en'):
            fi_texts = [texts[i] for i in fi_indices]
            try:
                batch_results = self.opus._proxy.batch_fi_to_en(fi_texts, force_opus=True)
                for j, idx in enumerate(fi_indices):
                    if j < len(batch_results) and batch_results[j]:
                        r = batch_results[j]
                        texts_en[idx] = r.text if hasattr(r, 'text') else str(r)
                    else:
                        texts_en[idx] = texts[idx]  # fallback to original
            except Exception:
                # Fallback: translate individually
                for idx in fi_indices:
                    texts_en[idx] = self.opus.fi_to_en(texts[idx])

        # Phase 4: embedding augmentation before batch embed
        texts_for_embed = []
        for text_fi, text_en in zip(texts, texts_en):
            augmented = self._augment_text_for_embedding(text_en, text_fi)
            texts_for_embed.append(augmented)

        # Batch embed all EN texts (augmented)
        embeddings = self.embed.embed_batch(texts_for_embed, mode="document")

        # C3: Batch dedup — one ChromaDB query instead of N
        valid_indices = [i for i, emb in enumerate(embeddings) if emb is not None]
        if not valid_indices:
            return 0

        is_dup = [False] * len(texts)
        if self.memory.count > 0:
            valid_embs_for_dedup = [embeddings[i] for i in valid_indices]
            try:
                dedup_results = self.memory.collection.query(
                    query_embeddings=valid_embs_for_dedup,
                    n_results=1,
                    include=["distances"],
                )
                for j, idx in enumerate(valid_indices):
                    if (dedup_results["distances"]
                            and j < len(dedup_results["distances"])
                            and dedup_results["distances"][j]):
                        dist = dedup_results["distances"][j][0]
                        score = 1.0 - (dist / 2.0)
                        if score >= 0.93:
                            is_dup[idx] = True
                            log.debug(f"Duplikaatti ({score:.0%}): {texts[idx][:50]}")
            except Exception as e:
                log.warning(f"Batch dedup query failed: {e}")

        # Collect survivors for batch insert
        ids_to_store = []
        docs_to_store = []
        embs_to_store = []
        metas_to_store = []
        stored = 0

        for i, (text_fi, text_en, emb, meta) in enumerate(
                zip(texts, texts_en, embeddings, metas)):
            if emb is None or is_dup[i]:
                continue

            combined = f"{text_fi} | {text_en}" if text_en != text_fi else text_fi

            self._insight_counter += 1
            agent_id = meta.get("agent_id", "system")
            source_type = meta.get("source_type", "heartbeat")
            obs_id = f"{source_type}_{agent_id}_{int(time.time())}_{self._insight_counter:04d}"

            full_meta = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "text_fi": text_fi,
                "text_en": text_en,
                **meta,
            }

            ids_to_store.append(obs_id)
            docs_to_store.append(combined)
            embs_to_store.append(emb)
            metas_to_store.append(full_meta)
            stored += 1

        # Batch insert survivors
        if ids_to_store:
            self.memory.store_batch(ids_to_store, docs_to_store,
                                    embs_to_store, metas_to_store)

            # MAGMA: batch audit + replay
            _al = getattr(self, '_audit_log', None)
            if _al:
                import hashlib as _hl
                _rs = getattr(self, '_replay_store', None)
                for _i, _oid in enumerate(ids_to_store):
                    _doc = docs_to_store[_i]
                    _hash = _hl.sha256(_doc.encode()).hexdigest()[:16]
                    _m = metas_to_store[_i]
                    _al.record(
                        "store", _oid,
                        agent_id=_m.get("agent_id", "system"),
                        content_hash=_hash, details=_doc,
                    )
                    if _rs:
                        _rs.store(_oid, _doc, _hash, _m)

            # Phase 4i: batch store in FI collection
            if self.bilingual:
                fi_texts = [m.get("text_fi", "") for m in metas_to_store]
                fi_embeddings = self.embed.embed_batch(fi_texts, mode="document")
                valid = [(i, e) for i, e in enumerate(fi_embeddings)
                         if e is not None and fi_texts[i]]
                if valid:
                    v_ids = [ids_to_store[i] for i, _ in valid]
                    v_fi = [fi_texts[i] for i, _ in valid]
                    v_embs = [e for _, e in valid]
                    v_metas = [metas_to_store[i] for i, _ in valid]
                    self.bilingual.store_bilingual_batch(
                        v_ids, v_fi, v_embs, v_metas)

            # fi_fast: batch store with all-minilm
            if hasattr(self, 'fi_fast') and self.fi_fast:
                fi_texts_ff = [m.get("text_fi", "") for m in metas_to_store]
                self.fi_fast.store_batch(
                    ids_to_store, fi_texts_ff, metas_to_store)

            log.info(f"📝 Batch opittu {stored} faktaa (muisti={self.memory.count})")

        # B2: Periodic eviction check (runs every N flushes)
        if self.eviction:
            self.eviction.on_flush()

        return stored

    def flush(self):
        """Force-flush remaining queued learn items. Call at shutdown."""
        if self._learn_queue:
            return self._flush_learn_queue()
        return 0

    def learn_conversation(self, question, answer,
                           agent_id="chat", quality_score=0.7):
        if quality_score < 0.5:
            return False
        q_en = self._to_english(question[:100])
        a_en = self._to_english(answer[:300])
        combined_fi = f"K: {question[:100]} → V: {answer[:200]}"
        combined_en = f"Q: {q_en} → A: {a_en}"

        embedding = self.embed.embed_document(combined_en)
        if not embedding:
            return False

        existing = self.memory.search(embedding, top_k=1, min_score=0.93)
        if existing:
            return False

        self._insight_counter += 1
        obs_id = f"chat_{agent_id}_{int(time.time())}_{self._insight_counter:04d}"
        self.memory.store(obs_id, f"{combined_fi} | {combined_en}", embedding, {
            "agent_id": agent_id, "source_type": "conversation",
            "confidence": quality_score, "validated": False,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "text_fi": combined_fi, "text_en": combined_en,
        })
        return True

    def init_task_queue(self):
        """Initialize LearningTaskQueue (call after class is defined)."""
        self.task_queue = LearningTaskQueue(self)

    @property
    def stats(self):
        return {
            "memories": self.memory.count,
            "swarm_facts": self.memory.swarm_facts.count(),
            "corrections": self.memory.corrections.count(),
            "episodes": self.memory.episodes.count(),
            "embed_available": self.embed.available,
            "eval_embed_available": self.eval_embed.available,
            "embed_latency_ms": f"{self.embed.avg_latency_ms:.1f}",
            "eval_embed_latency_ms": f"{self.eval_embed.avg_latency_ms:.1f}",
            "cache_hit_rate": f"{self.embed.cache_hit_rate:.0%}",
            "prefilter_hits": self._prefilter_hits,
            "total_queries": self._total_queries,
            "hallucinations_caught": self._hallucination_count,
            "insights_stored": self._insight_counter,
            "learn_queue_size": len(self._learn_queue),
            "active_learning_count": self._active_learning_count,
            "domain_synonyms": len(self._domain_synonyms),
            "hot_cache": self.hot_cache.stats if self.hot_cache else {},
            "bilingual_fi_count": (self.bilingual.fi_count
                                   if self.bilingual else 0),
            "fi_fast": (self.fi_fast.stats
                        if hasattr(self, 'fi_fast') and self.fi_fast
                        else {}),
            "micro_model": (self.micro_model.stats
                            if hasattr(self, 'micro_model')
                            and self.micro_model else {}),
            "eviction": (self.eviction.stats
                         if hasattr(self, 'eviction') and self.eviction
                         else {}),
            "circuit_breakers": {
                "embed": self.embed.breaker.stats if self.embed else {},
                "eval_embed": self.eval_embed.breaker.stats if self.eval_embed else {},
                "chromadb": self.memory.breaker.stats if self.memory else {},
            },
        }


# ═══════════════════════════════════════════════════════════════
# SEASONAL BOOST — month → relevant keywords (FI + EN)
# ═══════════════════════════════════════════════════════════════

SEASONAL_BOOST = {
    1: ["talvehtiminen", "wintering", "talviruokinta", "winter feeding"],
    2: ["kevättarkastus", "spring inspection", "nosema", "emojen tarkistus"],
    3: ["keväthoito", "spring management", "siitepöly", "pollen"],
    4: ["yhdistäminen", "combining", "emojen kasvatus", "queen rearing"],
    5: ["rakennuskehä", "foundation", "parven esto", "swarm prevention"],
    6: ["linkoaminen", "honey extraction", "lisäkorotus", "super"],
    7: ["linkous", "extraction", "hunaja", "honey", "mesikausi"],
    8: ["varroa", "treatment", "muurahaishappo", "formic acid"],
    9: ["syyshoito", "autumn management", "oksaalihappo", "oxalic acid"],
    10: ["talvivalmistelut", "winter preparation", "syysruokinta", "autumn feeding"],
    11: ["talvehtiminen", "wintering", "eristys", "insulation"],
    12: ["talvilepo", "winter rest", "lumitilanne", "monitoring"],
}

# Domain topics by agent type for random exploration
DOMAIN_TOPICS = {
    "beekeeper": [
        "varroa treatments", "queen rearing", "swarm prevention",
        "honey harvest", "winter preparation", "spring inspection",
    ],
    "disease_monitor": [
        "AFB detection", "EFB symptoms", "nosema prevention",
        "chalkbrood treatment", "disease reporting",
    ],
    "meteorologist": [
        "weather forecast impact", "temperature thresholds",
        "rain prediction", "frost warning",
    ],
    "horticulturist": [
        "nectar plants", "pollen sources", "bloom calendar",
        "landscape planning", "wildflower meadows",
    ],
    "business": [
        "honey pricing", "VAT rules", "marketing strategy",
        "sales channels", "food safety",
    ],
}


# ═══════════════════════════════════════════════════════════════
# LEARNING TASK QUEUE — Guided heartbeat tasks
# ═══════════════════════════════════════════════════════════════

class LearningTaskQueue:
    """Provides guided tasks for heartbeat learning instead of random thinking.

    Priority order:
    1. Unread YAML sections
    2. Low-coverage topics (sparse in memory)
    3. Recent low-confidence user queries
    4. Seasonal topics
    5. Random domain exploration
    """

    def __init__(self, consciousness: 'Consciousness',
                 scan_progress_path: str = "data/scan_progress.json"):
        self._consciousness = consciousness
        self._scan_progress_path = Path(scan_progress_path)
        self._low_confidence_queries: deque = deque(maxlen=50)
        self._last_tasks: deque = deque(maxlen=20)  # avoid repeats

    def record_low_confidence_query(self, query: str, confidence: float):
        """Record a query that had no good memory match."""
        self._low_confidence_queries.append({
            "query": query,
            "confidence": confidence,
            "timestamp": time.time(),
        })

    def next_task(self, agent_id: str = None,
                  agent_type: str = None) -> Optional[dict]:
        """Get next guided learning task. Returns dict with task info or None."""
        # Try each priority in order
        for fn in [
            self._unread_yaml_task,
            self._low_confidence_task,
            self._seasonal_task,
            lambda at: self._random_task(at),
        ]:
            try:
                task = fn(agent_type)
                if task and task.get("topic") not in self._last_tasks:
                    self._last_tasks.append(task.get("topic", ""))
                    return task
            except Exception:
                continue
        return None

    def _unread_yaml_task(self, agent_type: str = None) -> Optional[dict]:
        """Check scan_progress.json for unscanned YAML files."""
        if not self._scan_progress_path.exists():
            return None
        try:
            with open(self._scan_progress_path, encoding="utf-8") as f:
                progress = json.load(f)
            scanned = set(progress.get("scanned_files", []))
        except Exception:
            return None

        # Find YAML files not yet scanned
        knowledge_dir = Path("knowledge")
        if not knowledge_dir.exists():
            return None
        yaml_files = list(knowledge_dir.rglob("*.yaml")) + list(knowledge_dir.rglob("*.yml"))
        unscanned = [f for f in yaml_files if str(f) not in scanned]
        if not unscanned:
            return None

        target = unscanned[0]
        return {
            "type": "yaml_scan",
            "topic": str(target),
            "prompt": f"Read and learn from knowledge file: {target.name}",
            "priority": 1,
            "source": "yaml_scanner",
        }

    def _low_confidence_task(self, agent_type: str = None) -> Optional[dict]:
        """Return a recent query the system couldn't answer well."""
        if not self._low_confidence_queries:
            return None
        # Get the most recent unanswered query
        entry = self._low_confidence_queries.popleft()
        query = entry["query"]
        return {
            "type": "research",
            "topic": query[:80],
            "prompt": (f"A user asked: '{query}' but we had no good answer. "
                       f"Research this topic and provide a factual answer."),
            "priority": 3,
            "source": "low_confidence",
        }

    def _seasonal_task(self, agent_type: str = None) -> Optional[dict]:
        """Return a seasonally relevant learning topic."""
        month = datetime.now().month
        keywords = SEASONAL_BOOST.get(month, [])
        if not keywords:
            return None
        topic = random.choice(keywords)
        return {
            "type": "seasonal",
            "topic": topic,
            "prompt": (f"It is month {month}. Research the seasonal topic: '{topic}'. "
                       f"What should a Finnish beekeeper know about this right now?"),
            "priority": 4,
            "source": "seasonal",
        }

    def _random_task(self, agent_type: str = None) -> Optional[dict]:
        """Random domain topic for agent's specialty."""
        topics = DOMAIN_TOPICS.get(agent_type, DOMAIN_TOPICS.get("beekeeper", []))
        if not topics:
            return None
        topic = random.choice(topics)
        return {
            "type": "exploration",
            "topic": topic,
            "prompt": (f"Research and share one new practical fact about: '{topic}'. "
                       f"Focus on Finnish beekeeping conditions."),
            "priority": 5,
            "source": "random_exploration",
        }


# ═══════════════════════════════════════════════════════════════
# STANDALONE TESTI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    print("=" * 60)
    print("  🧠 WaggleDance Consciousness v2 Test")
    print("=" * 60)

    c = Consciousness(db_path="data/test_consciousness_v2")

    # ── Math ──
    print("\n[1] MATEMAATIKKA (laajennettu)")
    tests = [
        ("2+2", "4"),
        ("laske 15*20", "300"),
        ("sqrt(144)", "12"),
        ("calculate 100/3", "33.3333"),
        ("paljonko on 2^10", "1024"),
        ("paljonko on 2**10", "1024"),
        ("3*20", "60"),
        ("20°C fahrenheitiksi", "68.0°F"),
        ("moi", None),
    ]
    ok = 0
    for expr, expected in tests:
        pre = c.before_llm(expr)
        if expected is not None:
            if pre.handled and pre.answer == expected:
                print(f"  ✅ {expr} = {pre.answer}")
                ok += 1
            else:
                print(f"  ❌ {expr}: odotus={expected}, tulos={pre.answer if pre.handled else 'LLM'}")
        else:
            if not pre.handled:
                print(f"  ✅ {expr} → LLM:lle (oikein)")
                ok += 1
            else:
                print(f"  ❌ {expr}: ei pitäisi olla matikkaa")
    print(f"  Tulos: {ok}/{len(tests)}")

    # ── Oppiminen ──
    print("\n[2] OPPIMINEN (kaksikielinen, EN-embedding)")
    facts = [
        ("Varroa-hoitokynnys on 3 punkkia per 100 mehiläistä elokuussa", "beekeeper", 0.9),
        ("Oksaalihappohoito tehdään lokakuussa sikiöttömänä aikana", "beekeeper", 0.85),
        ("Syysruokinta: 15-20 kg sokerisiirappia per yhdyskunta", "beekeeper", 0.9),
        ("Kuningattarella on 5 silmää: 2 verkkosilmää ja 3 pistesilmää", "beekeeper", 0.95),
        ("JKH Service: 202 yhdyskuntaa, 35 tarhaa", "business", 0.95),
        ("Raspberry Pi 5 sopii sensori-nodeksi", "tech", 0.7),
        ("Kevättarkastus kun lämpötila ylittää 10°C", "beekeeper", 0.85),
        ("Hunajan kosteus max 18%", "beekeeper", 0.9),
        ("Maitohorsma kukkii heinä-elokuussa", "horticulturist", 0.85),
        ("Vadelma on Suomen suosituin lajihunajan lähde", "horticulturist", 0.9),
    ]
    for text, agent, conf in facts:
        stored = c.learn(text, agent_id=agent, confidence=conf, validated=True,
                         immediate=True)  # immediate for test (need results in [3])
        print(f"  {'✅' if stored else '⏭️'} {text[:60]}")

    # ── Muistihaku ──
    print("\n[3] MUISTIHAKU (EN-embedding + task prefix)")
    queries = [
        ("mikä on varroa-kynnys", "varroa", True),
        ("milloin happohoito", "oxalic", True),
        ("kuinka monta silmää mehiläisellä", "eye", True),
        ("paljonko sokeria syysruokintaan", "sugar", True),
        ("mikä on sään ennuste", None, False),
    ]
    search_ok = 0
    for q, keyword, should_find in queries:
        pre = c.before_llm(q)
        if pre.context:
            first = pre.context.split("\n")[1] if "\n" in pre.context else ""
            score_m = re.search(r'\[(\d+)%\]', first)
            score = int(score_m.group(1)) if score_m else 0
            if should_find and keyword:
                found = keyword.lower() in first.lower()
                icon = "✅" if found else "❌"
                print(f"  {icon} {q}")
                print(f"     → {first[:80]}")
                if found:
                    search_ok += 1
            elif not should_find:
                if score < 70:
                    print(f"  ✅ {q} → matala score ({score}%), OK")
                    search_ok += 1
                else:
                    print(f"  ⚠️ {q} → score {score}%")
        else:
            if not should_find:
                print(f"  ✅ {q} → ei osumaa, OK")
                search_ok += 1
            else:
                print(f"  ❌ {q} → ei osumaa!")
    print(f"  Tulos: {search_ok}/{len(queries)}")

    # ── Hallusinaatio ──
    print("\n[4] HALLUSINAATIOTUNNISTUS (EN + keyword overlap)")
    pairs = [
        ("kuinka monta silmää mehiläisellä",
         "Mehiläisellä on 5 silmää, 2 verkkosilmää ja 3 pistesilmää",
         False, "oikea vastaus"),
        ("kuinka monta silmää mehiläisellä",
         "Jani Korpi on sähköurakoitsija JKH Servicessä",
         True, "irrelevantti"),
        ("varroa hoitokynnys",
         "Hoitokynnys on 3 punkkia per 100 mehiläistä",
         False, "oikea vastaus"),
        ("varroa hoitokynnys",
         "Myrskyisä savi karhu päällä kolme kukkaruukkua",
         True, "hallusinaatio"),
    ]
    hall_ok = 0
    for q, a, should_flag, desc in pairs:
        h = c.check_hallucination(q, a)
        correct = (h.is_suspicious == should_flag)
        icon = "✅" if correct else "❌"
        flag = "🚨 FLAGGED" if h.is_suspicious else "✓ OK"
        print(f"  {icon} [{desc}] {flag}")
        print(f"     embed={h.relevance:.0%}, keyword={h.keyword_overlap:.0%}")
        if correct:
            hall_ok += 1
    print(f"  Tulos: {hall_ok}/{len(pairs)}")

    # ── Tilastot ──
    print(f"\n[5] TILASTOT")
    for k, v in c.stats.items():
        print(f"  {k}: {v}")

    # ── Batch Embedding ──
    print("\n[6] BATCH EMBEDDING")
    batch_texts = [f"Test fact number {i} about beekeeping" for i in range(10)]
    t0 = time.perf_counter()
    batch_vecs = c.embed.embed_batch(batch_texts, mode="document")
    batch_ms = (time.perf_counter() - t0) * 1000
    batch_ok = sum(1 for v in batch_vecs if v is not None)
    print(f"  Embed 10 texts: {batch_ms:.0f}ms ({batch_ms/10:.1f}ms/item)")
    if batch_ok == 10:
        print(f"  ✅ All {batch_ok}/10 vectors OK")
    else:
        print(f"  ❌ Only {batch_ok}/10 vectors OK")
    if batch_ms < 400:
        print(f"  ✅ Under 400ms target")
    else:
        print(f"  ⚠️ Over 400ms target ({batch_ms:.0f}ms)")

    # ── Eval Embedding ──
    print("\n[7] EVAL EMBEDDING (all-minilm)")
    if c.eval_embed.available:
        eval_vec = c.eval_embed.embed("test embedding for evaluation")
        if eval_vec:
            print(f"  ✅ eval_embed available, dim={len(eval_vec)}")
        else:
            print(f"  ❌ eval_embed returned None")
        # Test batch too
        eval_batch = c.eval_embed.embed_batch(["test one", "test two", "test three"])
        eval_batch_ok = sum(1 for v in eval_batch if v is not None)
        print(f"  ✅ eval_embed batch: {eval_batch_ok}/3 OK")
    else:
        print(f"  ⚠️ eval_embed not available (all-minilm not installed)")

    # ── Learn Queue + Batch Flush ──
    print("\n[8] LEARN QUEUE + BATCH FLUSH")
    count_before = c.memory.count
    queue_facts = [
        f"Batch test fact {i}: beekeeping knowledge about topic {i}"
        for i in range(25)
    ]
    for i, fact in enumerate(queue_facts):
        c.learn(fact, agent_id="batch_test", confidence=0.8, validated=True)
        # Check auto-flush at threshold
        if i == 9:
            q_after_10 = len(c._learn_queue)
        if i == 19:
            q_after_20 = len(c._learn_queue)

    # Should have auto-flushed at 10 and 20
    print(f"  Queue after 10 items: {q_after_10} (expected 0 after flush)")
    print(f"  Queue after 20 items: {q_after_20} (expected 0 after flush)")
    remaining = len(c._learn_queue)
    print(f"  Queue before final flush: {remaining} (expected 5)")

    flushed = c.flush()
    print(f"  Final flush: {flushed} items stored")
    count_after = c.memory.count
    total_new = count_after - count_before
    print(f"  Total new memories: {total_new} (from 25 facts, minus dedup)")
    if total_new > 0:
        print(f"  ✅ Learn queue + batch flush working")
    else:
        print(f"  ❌ No new memories stored")

    # ── Batch Translation ──
    print("\n[9] BATCH TRANSLATION")
    try:
        from core.translation_proxy import TranslationProxy
        tp = TranslationProxy()
        if tp.opus.available:
            test_fi = [
                "Mehiläishoito on tärkeää",
                "Varroa-punkkien torjunta",
                "Hunajan laatu on hyvä",
            ]
            results = tp.batch_fi_to_en(test_fi)
            tr_ok = sum(1 for r in results if r and hasattr(r, 'text') and r.text)
            print(f"  ✅ batch_fi_to_en: {tr_ok}/3 results have .text")
            for r in results:
                print(f"     → {r.text[:60]} ({r.method})")
            tp.close()
        else:
            print(f"  ⚠️ Opus-MT not available, skipping batch translation test")
    except Exception as e:
        print(f"  ⚠️ Batch translation test error: {e}")

    # ── Final stats ──
    print(f"\n[10] FINAL TILASTOT")
    for k, v in c.stats.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 60)

    import shutil
    try:
        shutil.rmtree("data/test_consciousness_v2")
    except:
        pass
