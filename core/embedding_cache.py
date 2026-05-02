"""Embedding engines with LRU cache and circuit breaker.

Extracted from memory_engine.py (v1.17.0).
Contains EmbeddingEngine (nomic-embed-text, asymmetric with prefix)
and EvalEmbeddingEngine (all-minilm, symmetric, no prefix).
"""

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Optional, List

from core.circuit_breaker import CircuitBreaker

log = logging.getLogger("consciousness")


class EmbeddingEngine:
    PREFIX_DOCUMENT = "search_document: "
    PREFIX_QUERY = "search_query: "

    FALLBACK_MODELS = ["nomic-embed-text", "all-minilm"]

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
        self._cache: OrderedDict = OrderedDict()
        self._cache_max = cache_size
        self.cache_hits = 0
        self.cache_misses = 0
        # M3: Fallback is same model retry (different-dimension models
        # would break ChromaDB collections fixed to primary's dimension)
        self._fallback_model = None

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

    def _call_ollama_embed(self, text: str, model: str) -> Optional[List[float]]:
        """Single Ollama embed call for a given model."""
        try:
            import requests
            t0 = time.perf_counter()
            r = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text},
                timeout=30
            )
            ms = (time.perf_counter() - t0) * 1000
            self._latency_sum += ms
            self._latency_count += 1
            if r.status_code == 200:
                return r.json()["embeddings"][0]
            log.error(f"Embed HTTP {r.status_code} for {model}")
            return None
        except Exception as e:
            log.error(f"Embed error ({model}): {e}")
            return None

    def _raw_embed(self, text: str) -> Optional[List[float]]:
        if not self.available:
            return None
        if not self.breaker.allow_request():
            # Circuit open on primary — try fallback directly
            if self._fallback_model:
                log.info(f"Embed fallback: {self.model} circuit open, trying {self._fallback_model}")
                return self._call_ollama_embed(text, self._fallback_model)
            return None
        result = self._call_ollama_embed(text, self.model)
        if result is not None:
            self.breaker.record_success()
            return result
        self.breaker.record_failure()
        # Primary failed — try fallback
        if self._fallback_model:
            log.info(f"Embed fallback: {self.model} failed, trying {self._fallback_model}")
            return self._call_ollama_embed(text, self._fallback_model)
        return None

    def _cached_embed(self, text: str) -> Optional[List[float]]:
        key = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
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
            key = hashlib.md5(prefixed.encode(), usedforsecurity=False).hexdigest()
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
                            key = hashlib.md5(chunk[j].encode(), usedforsecurity=False).hexdigest()
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
        key = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
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
            key = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
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
                            key = hashlib.md5(chunk[j].encode(), usedforsecurity=False).hexdigest()
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
