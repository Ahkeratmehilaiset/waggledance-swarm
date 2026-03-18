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
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field

# v1.17.0: Extracted modules — re-exported for backward compatibility
from core.circuit_breaker import CircuitBreaker  # noqa: F401
from core.embedding_cache import EmbeddingEngine, EvalEmbeddingEngine  # noqa: F401
from core.hallucination_checker import HallucinationChecker, HallucinationResult  # noqa: F401
from core.math_solver import MathSolver  # noqa: F401
# v1.18.0: Further extractions
from core.memory_eviction import MemoryEviction  # noqa: F401
from core.opus_mt_adapter import OpusMTAdapter  # noqa: F401
from core.learning_task_queue import LearningTaskQueue, SEASONAL_BOOST, DOMAIN_TOPICS  # noqa: F401

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
            from core.disk_guard import check_disk_space
            check_disk_space(".", label="ChromaDB store")
        except (ImportError, OSError):
            pass  # DiskSpaceError propagates; ImportError/OSError ignored
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



# MemoryEviction extracted to core/memory_eviction.py (v1.18.0)


# OpusMTAdapter extracted to core/opus_mt_adapter.py (v1.18.0)


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

        # Learn queue for batch flush (capped to prevent unbounded growth)
        self._learn_queue: List[tuple] = []
        self._flush_threshold = 10
        self._learn_queue_maxlen = 1000

        # M6: Lock for shared state (learn queue, counters)
        import threading as _threading
        self._state_lock = _threading.Lock()

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

        # v2.0: SafeActionBus write proxy (set by runtime when autonomy active)
        self._write_proxy = None  # Optional[SafeActionBus]

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
        # Try full orchestrator first, then fallback to standalone V1 engine
        _mm = self.micro_model or getattr(self, '_v1_engine', None)
        if _mm:
            mm_result = _mm.predict(message)
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

        # Queue for batch flush (M6: thread-safe, capped)
        with self._state_lock:
            if len(self._learn_queue) >= self._learn_queue_maxlen:
                log.warning("learn_queue full (%d), dropping oldest entry",
                            self._learn_queue_maxlen)
                self._learn_queue.pop(0)
            self._learn_queue.append((text, {
                "agent_id": agent_id,
                "source_type": source_type,
                "confidence": confidence,
                "validated": validated,
                **(metadata or {}),
            }))
            should_flush = len(self._learn_queue) >= self._flush_threshold

        if should_flush:
            self._flush_learn_queue()

        return True

    def set_write_proxy(self, action_bus):
        """Set SafeActionBus as write proxy for autonomy mode.

        When set, all memory writes go through the action bus for
        policy evaluation before storage.
        """
        self._write_proxy = action_bus
        log.info("v2.0: SafeActionBus write proxy set on Consciousness")

    def _learn_single(self, text, agent_id="system", source_type="heartbeat",
                      confidence=0.5, validated=False, metadata=None):
        """Original single-item learn logic: translate → embed → dedup → store.

        v2.0: When _write_proxy is set, wraps the store operation through
        SafeActionBus for policy evaluation.
        """
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

        # v2.0: Route through SafeActionBus if write proxy is set
        if self._write_proxy is not None:
            try:
                from waggledance.core.domain.autonomy import Action
                action = Action(capability_id="memory.store")
                # Create a minimal capability for policy check
                from waggledance.core.domain.autonomy import CapabilityContract, CapabilityCategory
                cap = CapabilityContract(
                    capability_id="memory.store",
                    category=CapabilityCategory.ACT,
                    description="Memory store write operation",
                )
                result = self._write_proxy.submit(
                    action, cap,
                    quality_path="silver" if confidence >= 0.8 else "bronze",
                    context={"obs_id": obs_id, "text": combined[:200]},
                )
                if not result.decision.approved:
                    log.info("v2.0: Memory write denied by policy: %s",
                             result.decision.reason)
                    return False
            except Exception as _wp_err:
                log.debug("v2.0: Write proxy check failed, proceeding: %s", _wp_err)

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

        # Copy and clear queue atomically (M6: thread-safe)
        with self._state_lock:
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


# SEASONAL_BOOST, DOMAIN_TOPICS, LearningTaskQueue extracted to
# core/learning_task_queue.py (v1.18.0)
#
# Standalone test block removed — run via pytest instead.
