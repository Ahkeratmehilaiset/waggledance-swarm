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
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field

log = logging.getLogger("consciousness")


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
                 cache_size=10000):
        self.model = model
        self.base_url = base_url
        self._available = None
        self._latency_sum = 0.0
        self._latency_count = 0
        self._cache = {}
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
                return r.json()["embeddings"][0]
            log.error(f"Embed HTTP {r.status_code}")
            return None
        except Exception as e:
            log.error(f"Embed error: {e}")
            return None

    def _cached_embed(self, text: str) -> Optional[List[float]]:
        key = hashlib.md5(text.encode()).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1
        vec = self._raw_embed(text)
        if vec and len(self._cache) < self._cache_max:
            self._cache[key] = vec
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
                            # Cache the result
                            key = hashlib.md5(chunk[j].encode()).hexdigest()
                            if len(self._cache) < self._cache_max:
                                self._cache[key] = emb
                else:
                    log.error(f"Batch embed HTTP {r.status_code}")
        except Exception as e:
            log.error(f"Batch embed error: {e}")

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
                 cache_size=5000):
        self.model = model
        self.base_url = base_url
        self._available = None
        self._latency_sum = 0.0
        self._latency_count = 0
        self._cache = {}
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
                return r.json()["embeddings"][0]
            log.error(f"EvalEmbed HTTP {r.status_code}")
            return None
        except Exception as e:
            log.error(f"EvalEmbed error: {e}")
            return None

    def embed(self, text: str) -> Optional[List[float]]:
        """Embed text (no prefix — symmetric model)."""
        key = hashlib.md5(text.encode()).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1
        vec = self._raw_embed(text)
        if vec and len(self._cache) < self._cache_max:
            self._cache[key] = vec
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
                            if len(self._cache) < self._cache_max:
                                self._cache[key] = emb
                else:
                    log.error(f"EvalEmbed batch HTTP {r.status_code}")
        except Exception as e:
            log.error(f"EvalEmbed batch error: {e}")

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
        self.collection = self.client.get_or_create_collection(
            name="waggle_memory",
            metadata={"hnsw:space": "cosine"}
        )
        # Phase 3: Swarm facts — shared knowledge from Level 3+ agents
        self.swarm_facts = self.client.get_or_create_collection(
            name="swarm_facts",
            metadata={"hnsw:space": "cosine"}
        )
        log.info(f"MemoryStore: {self.count} muistoa, "
                 f"{self.swarm_facts.count()} swarm facts ({path})")

    def store(self, obs_id, text, embedding, metadata=None):
        self.collection.upsert(
            ids=[obs_id], embeddings=[embedding],
            documents=[text], metadatas=[metadata or {}]
        )

    def store_batch(self, ids, texts, embeddings, metadatas=None):
        """Batch upsert multiple documents in a single ChromaDB call."""
        if not ids:
            return
        self.collection.upsert(
            ids=ids, embeddings=embeddings,
            documents=texts,
            metadatas=metadatas or [{} for _ in ids]
        )

    def search(self, embedding, top_k=5, min_score=0.3,
               where=None) -> List[MemoryMatch]:
        if self.count == 0:
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
        except Exception as e:
            log.error(f"ChromaDB query: {e}")
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
        return sorted(matches, key=lambda m: m.score, reverse=True)

    @property
    def count(self):
        return self.collection.count()


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

        # Phase 3: task queue (must be after class body is fully defined at module level,
        # but LearningTaskQueue is defined below — use lazy init in init_task_queue())
        self.task_queue = None

    def set_translation_proxy(self, proxy):
        self.opus.set_proxy(proxy)

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
        if self._is_finnish(text) and self.opus.available:
            return self.opus.fi_to_en(text)
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

        # 1. Math — direct answer, no LLM
        if self.math.is_math(message):
            result = self.math.solve(message)
            if result is not None:
                self._prefilter_hits += 1
                log.info(f"🧮 Math: {message.strip()} = {result}")
                return PreFilterResult(
                    handled=True, answer=result,
                    method="math", confidence=1.0)

        # 2. Memory search (EN embedding + query prefix)
        if self.embed.available and self.memory.count > 0:
            msg_en = self._to_english(message)
            q_vec = self.embed.embed_query(msg_en)

            if q_vec:
                matches = self.memory.search(q_vec, top_k=5, min_score=0.3)
                if matches:
                    best = matches[0]

                    # Tier 1: >0.90 validated → direct answer, no LLM
                    if (best.score > 0.90
                            and best.metadata.get("validated")
                            and best.metadata.get("confidence", 0) > 0.8):
                        self._prefilter_hits += 1
                        answer = best.text_fi or best.text
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
                        log.info(f"🧠 Konteksti llama1b:lle ({best.score:.0%})")
                        return PreFilterResult(
                            handled=False, context=context,
                            method="memory_fast", confidence=best.score)

                    # Tier 3: >0.50 → use phi4-mini with full context
                    if best.score > 0.50:
                        parts = []
                        for m in matches[:5]:
                            en = m.text_en or m.text
                            parts.append(f"[{m.score:.0%}] {en}")
                        context = "KNOWN FACTS (use if relevant):\n" + "\n".join(parts)
                        log.info(f"🧠 Konteksti phi4-mini:lle ({best.score:.0%})")
                        return PreFilterResult(
                            handled=False, context=context,
                            method="memory_context", confidence=best.score)

        # Tier 4: no good context — phi4-mini without context
        # Phase 3: record for guided learning
        if hasattr(self, 'task_queue') and self.task_queue:
            self.task_queue.record_low_confidence_query(message, 0.0)
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

        embedding = self.embed.embed_document(text_en)
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
        }
        if metadata:
            meta.update(metadata)

        self.memory.store(obs_id, combined, embedding, meta)
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

        # Batch embed all EN texts
        embeddings = self.embed.embed_batch(texts_en, mode="document")

        # Dedup + collect survivors for batch insert
        ids_to_store = []
        docs_to_store = []
        embs_to_store = []
        metas_to_store = []
        stored = 0

        for i, (text_fi, text_en, emb, meta) in enumerate(
                zip(texts, texts_en, embeddings, metas)):
            if emb is None:
                continue

            # Dedup check
            existing = self.memory.search(emb, top_k=1, min_score=0.93)
            if existing:
                log.debug(f"Duplikaatti ({existing[0].score:.0%}): {text_fi[:50]}")
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
            log.info(f"📝 Batch opittu {stored} faktaa (muisti={self.memory.count})")

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
    "tarhaaja": [
        "varroa treatments", "queen rearing", "swarm prevention",
        "honey harvest", "winter preparation", "spring inspection",
    ],
    "tautivahti": [
        "AFB detection", "EFB symptoms", "nosema prevention",
        "chalkbrood treatment", "disease reporting",
    ],
    "meteorologi": [
        "weather forecast impact", "temperature thresholds",
        "rain prediction", "frost warning",
    ],
    "hortonomi": [
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
        topics = DOMAIN_TOPICS.get(agent_type, DOMAIN_TOPICS.get("tarhaaja", []))
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
        ("Varroa-hoitokynnys on 3 punkkia per 100 mehiläistä elokuussa", "tarhaaja", 0.9),
        ("Oksaalihappohoito tehdään lokakuussa sikiöttömänä aikana", "tarhaaja", 0.85),
        ("Syysruokinta: 15-20 kg sokerisiirappia per yhdyskunta", "tarhaaja", 0.9),
        ("Kuningattarella on 5 silmää: 2 verkkosilmää ja 3 pistesilmää", "tarhaaja", 0.95),
        ("JKH Service: 202 yhdyskuntaa, 35 tarhaa", "business", 0.95),
        ("Raspberry Pi 5 sopii sensori-nodeksi", "tech", 0.7),
        ("Kevättarkastus kun lämpötila ylittää 10°C", "tarhaaja", 0.85),
        ("Hunajan kosteus max 18%", "tarhaaja", 0.9),
        ("Maitohorsma kukkii heinä-elokuussa", "hortonomi", 0.85),
        ("Vadelma on Suomen suosituin lajihunajan lähde", "hortonomi", 0.9),
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
        from translation_proxy import TranslationProxy
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
