"""
WaggleDance — Phase 4i/4j/4k: Fast Memory Extensions
======================================================
BilingualMemoryStore: FI-direct ChromaDB collection (skip translation, 55ms)
HotCache: RAM LRU cache for top 500 Finnish answers (5ms, zero GPU)
FactEnrichmentEngine: Autonomous knowledge generation via dual-model consensus
"""

import json
import logging
import random
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional, List

log = logging.getLogger("fast_memory")


# ═══════════════════════════════════════════════════════════════
# SEASONAL RULES — Temporal sanity for fact enrichment
# ═══════════════════════════════════════════════════════════════

SEASONAL_RULES = {
    "flying_activity": {
        "patterns": ["lentää", "lennolla", "flying", "flight activity", "lentoaukko aktiivisuus"],
        "valid_months": [4, 5, 6, 7, 8, 9],
        "reason": "Bees only fly Apr-Sep in Finland",
    },
    "honey_extraction": {
        "patterns": ["linkous", "lingotaan", "extraction", "harvest honey", "linkoa hunajaa"],
        "valid_months": [6, 7, 8],
        "reason": "Honey extraction Jun-Aug",
    },
    "swarming": {
        "patterns": ["parveilu", "parveil", "swarming", "swarm prevention"],
        "valid_months": [5, 6, 7],
        "reason": "Swarming season May-Jul",
    },
    "oxalic_treatment": {
        "patterns": ["oksaalihappo", "oxalic", "OA treatment", "tihkutus"],
        "valid_months": [10, 11, 12, 1],
        "reason": "Oxalic acid used Oct-Jan (broodless period)",
    },
    "formic_treatment": {
        "patterns": ["muurahaishappo", "formic", "MAQS"],
        "valid_months": [7, 8, 9],
        "reason": "Formic acid treatment Jul-Sep",
    },
    "spring_inspection": {
        "patterns": ["kevättarkastus", "spring inspection", "ensitarkastus"],
        "valid_months": [3, 4, 5],
        "reason": "Spring inspections Mar-May",
    },
    "winter_feeding": {
        "patterns": ["syysruokinta", "talviruokinta", "autumn feeding", "winter feeding", "sokeriliuos"],
        "valid_months": [8, 9, 10],
        "reason": "Winter feeding Aug-Oct",
    },
    "queen_rearing": {
        "patterns": ["emottaminen", "queen rearing", "emocelli", "emon kasvatus"],
        "valid_months": [5, 6, 7],
        "reason": "Queen rearing May-Jul",
    },
}


# ═══════════════════════════════════════════════════════════════
# HOT CACHE — RAM LRU cache for high-confidence Finnish answers
# ═══════════════════════════════════════════════════════════════

# Common Finnish suffixes for stemming normalization
_FI_SUFFIXES = [
    "kään", "kaan",  # longer first
    "ssä", "ssa", "stä", "sta", "llä", "lla", "lle", "ltä", "lta",
    "kin", "kö", "ko",
    "an", "en", "in", "on", "un", "yn", "än", "ön",
]


class HotCache:
    """RAM cache for high-confidence Finnish answers. 5ms lookups, zero GPU."""

    def __init__(self, max_size: int = 500):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._total_hits = 0
        self._total_misses = 0

    def normalize_key(self, text_fi: str) -> str:
        """Finnish stemming normalization for cache key matching.

        - Lowercase, strip whitespace
        - Remove punctuation: ? ! .
        - Remove common Finnish suffixes
        - Sort words alphabetically for order-independent matching
        """
        text = text_fi.lower().strip()
        # Remove trailing punctuation
        text = text.rstrip("?!.")
        text = text.strip()
        # Split to words, strip each
        words = text.split()
        stemmed = []
        for w in words:
            w = re.sub(r'[^\wäöåÄÖÅ]', '', w)
            if not w:
                continue
            # Remove common Finnish suffixes (longest match first)
            for suffix in _FI_SUFFIXES:
                if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                    w = w[:-len(suffix)]
                    break
            if w:
                stemmed.append(w)
        stemmed.sort()
        return " ".join(stemmed)

    def get(self, query_fi: str) -> Optional[dict]:
        """Lookup. Returns {answer, score} or None."""
        key = self.normalize_key(query_fi)
        if not key:
            self._total_misses += 1
            return None
        if key in self._cache:
            entry = self._cache[key]
            entry["hits"] += 1
            self._total_hits += 1
            self._cache.move_to_end(key)  # LRU refresh
            return {"answer": entry["answer"], "score": entry["score"]}
        self._total_misses += 1
        return None

    def put(self, query_fi: str, answer_fi: str, score: float,
            source: str = "auto"):
        """Store answer. Auto-evicts LRU if full."""
        key = self.normalize_key(query_fi)
        if not key or not answer_fi:
            return
        self._cache[key] = {
            "answer": answer_fi, "score": score, "hits": 1,
            "source": source, "timestamp": time.time(),
        }
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)  # Evict LRU

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def stats(self) -> dict:
        total = self._total_hits + self._total_misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hit_rate": self._total_hits / max(total, 1),
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
        }


# ═══════════════════════════════════════════════════════════════
# BILINGUAL MEMORY STORE — FI-direct ChromaDB collection
# ═══════════════════════════════════════════════════════════════

class BilingualMemoryStore:
    """FI-direct ChromaDB collection alongside existing EN collection.

    Finnish queries search FI collection directly — skip translation.
    Uses same nomic-embed-text model for FI embeddings.
    """

    def __init__(self, memory_store, embed_engine):
        self.memory = memory_store  # existing MemoryStore (EN primary)
        self.embed = embed_engine   # existing EmbeddingEngine
        # Create FI collection in same ChromaDB client
        self.fi_collection = memory_store.client.get_or_create_collection(
            name="waggle_memory_fi",
            metadata={"hnsw:space": "cosine"})
        log.info(f"BilingualMemoryStore: FI collection={self.fi_count} items")

    def store_bilingual(self, obs_id: str, text_fi: str, text_en: str,
                        embedding_en, metadata: dict):
        """Store in FI collection (EN already handled by memory_store)."""
        if not text_fi or not text_fi.strip():
            return
        fi_embedding = self.embed.embed_document(text_fi)
        if fi_embedding:
            fi_meta = {k: v for k, v in metadata.items()
                       if isinstance(v, (str, int, float, bool))}
            fi_meta["obs_id_en"] = obs_id
            fi_meta["text_en"] = (text_en or "")[:500]
            self.fi_collection.upsert(
                ids=[f"fi_{obs_id}"],
                embeddings=[fi_embedding],
                documents=[text_fi],
                metadatas=[fi_meta])

    def store_bilingual_batch(self, ids: list, texts_fi: list,
                              embeddings_fi: list, metadatas: list):
        """Batch store into FI collection."""
        if not ids:
            return
        fi_ids = [f"fi_{oid}" for oid in ids]
        # Filter to only valid entries
        valid_ids = []
        valid_docs = []
        valid_embs = []
        valid_metas = []
        for i in range(len(ids)):
            if (i < len(embeddings_fi) and embeddings_fi[i] is not None
                    and i < len(texts_fi) and texts_fi[i]):
                valid_ids.append(fi_ids[i])
                valid_docs.append(texts_fi[i])
                valid_embs.append(embeddings_fi[i])
                meta = metadatas[i] if i < len(metadatas) else {}
                # ChromaDB requires simple types in metadata
                clean_meta = {k: v for k, v in meta.items()
                              if isinstance(v, (str, int, float, bool))}
                valid_metas.append(clean_meta)
        if valid_ids:
            self.fi_collection.upsert(
                ids=valid_ids, embeddings=valid_embs,
                documents=valid_docs, metadatas=valid_metas)

    def search_fi(self, query_fi: str, top_k: int = 5,
                  min_score: float = 0.3,
                  seasonal_boost=None) -> list:
        """Search FI collection directly — skip translation."""
        from consciousness import MemoryMatch
        if self.fi_collection.count() == 0:
            return []
        fi_vec = self.embed.embed_query(query_fi)
        if not fi_vec:
            return []
        n = min(top_k * 2, self.fi_collection.count())
        results = self.fi_collection.query(
            query_embeddings=[fi_vec],
            n_results=n,
            include=["documents", "metadatas", "distances"])
        if not results["documents"] or not results["documents"][0]:
            return []
        matches = []
        for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]):
            score = 1.0 - (dist / 2.0)
            if score >= min_score:
                matches.append(MemoryMatch(
                    text=doc, score=score, metadata=meta,
                    text_fi=doc,
                    text_en=meta.get("text_en", doc)))
        matches.sort(key=lambda m: m.score, reverse=True)
        # Apply seasonal boost
        if seasonal_boost and matches:
            if isinstance(seasonal_boost, list):
                boost_kws = [kw.lower() for kw in seasonal_boost]
            else:
                boost_kws = seasonal_boost.lower().split()
            for m in matches:
                if any(kw in (m.text or "").lower() for kw in boost_kws):
                    m.score = min(m.score * 1.2, 1.0)
            matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:top_k]

    @property
    def fi_count(self) -> int:
        return self.fi_collection.count()

    @property
    def stats(self) -> dict:
        return {
            "fi_count": self.fi_count,
            "en_count": self.memory.count,
        }


# ═══════════════════════════════════════════════════════════════
# FACT ENRICHMENT ENGINE — autonomous knowledge generation
# ═══════════════════════════════════════════════════════════════

class FactEnrichmentEngine:
    """Generates new knowledge during night mode via dual-model consensus.

    Process: find knowledge gap → generate facts with llama1b →
    validate with phi4-mini → store only if both models agree.
    """

    def __init__(self, consciousness, llm_heartbeat, llm_chat):
        self.consciousness = consciousness
        self.llm_fast = llm_heartbeat    # llama3.2:1b — generates
        self.llm_validate = llm_chat     # phi4-mini — validates
        self._facts_generated = 0
        self._facts_validated = 0
        self._facts_rejected = 0

    async def enrichment_cycle(self, throttle=None):
        """One enrichment cycle: find gap -> generate -> validate -> store.

        Returns number of facts stored (0-3).
        """
        gap = self.find_knowledge_gap()
        if not gap:
            return 0

        # Generate with llama1b (cheap, fast)
        gen_prompt = (
            f"Generate 3 factual statements about '{gap['topic']}' "
            f"for Finnish beekeeping. Be specific and practical. "
            f"One statement per line. English only.")

        try:
            if throttle:
                async with throttle:
                    gen_resp = await self.llm_fast.generate(
                        gen_prompt, max_tokens=200)
            else:
                gen_resp = await self.llm_fast.generate(
                    gen_prompt, max_tokens=200)
        except Exception as e:
            log.error(f"Enrichment generate error: {e}")
            return 0

        if not gen_resp or (hasattr(gen_resp, 'error') and gen_resp.error):
            return 0

        content = gen_resp.content if hasattr(gen_resp, 'content') else str(gen_resp)
        candidates = [line.strip() for line in content.strip().split("\n")
                      if len(line.strip()) > 20]
        if not candidates:
            return 0

        # Temporal sanity check — reject out-of-season "current" claims
        if self._should_check_temporal():
            candidates = [c for c in candidates
                          if self._check_temporal_sanity(c)[0]]
        if not candidates:
            return 0

        # Validate each with phi4-mini (smarter, slower)
        stored = 0
        for fact in candidates[:3]:
            self._facts_generated += 1
            val_prompt = (
                f"Fact-check this beekeeping statement:\n\"{fact}\"\n\n"
                f"Reply ONLY 'VALID' or 'INVALID'. "
                f"VALID = factually correct for Finnish beekeeping.")

            try:
                if throttle:
                    async with throttle:
                        val_resp = await self.llm_validate.generate(
                            val_prompt, max_tokens=20)
                else:
                    val_resp = await self.llm_validate.generate(
                        val_prompt, max_tokens=20)
            except Exception as e:
                log.error(f"Enrichment validate error: {e}")
                continue

            if not val_resp or (hasattr(val_resp, 'error') and val_resp.error):
                continue

            val_content = (val_resp.content if hasattr(val_resp, 'content')
                           else str(val_resp))
            val_upper = val_content.upper()

            if "VALID" in val_upper and "INVALID" not in val_upper:
                self.consciousness.learn(
                    fact, agent_id="enrichment",
                    source_type="self_enrichment", confidence=0.80,
                    validated=True, immediate=True,
                    metadata={"gap_topic": gap["topic"],
                              "validation": "dual_model_consensus"})
                stored += 1
                self._facts_validated += 1
                log.info(f"✨ Enrichment: stored '{fact[:60]}'")
            else:
                self._facts_rejected += 1
                log.debug(f"❌ Enrichment rejected: '{fact[:60]}'")
        return stored

    def _check_temporal_sanity(self, fact: str) -> tuple:
        """Check if a generated fact makes a seasonally inappropriate claim.

        Only rejects facts that imply CURRENT action (contains temporal markers
        like "nyt", "now", "currently", "today", etc.) AND reference an activity
        that is out of season.  General knowledge statements always pass.

        Returns:
            (True, "")        — fact is OK
            (False, reason)   — fact is temporally invalid
        """
        fact_lower = fact.lower()
        current_month = datetime.now().month

        # Temporal markers that imply the fact is about RIGHT NOW
        temporal_markers = [
            "nyt", "now", "currently", "tänään", "today",
            "juuri nyt", "this week", "tällä viikolla",
            "tällä hetkellä", "at the moment", "right now",
            "parhaillaan", "meneillään",
        ]

        has_temporal = any(marker in fact_lower for marker in temporal_markers)
        if not has_temporal:
            # General knowledge statement — always passes
            return (True, "")

        for rule_name, rule in SEASONAL_RULES.items():
            pattern_matched = any(
                pat.lower() in fact_lower for pat in rule["patterns"]
            )
            if pattern_matched and current_month not in rule["valid_months"]:
                reason = (
                    f"Temporal reject [{rule_name}]: month {current_month} "
                    f"not in {rule['valid_months']} — {rule['reason']}"
                )
                log.debug(f"Temporal sanity: {reason} | fact='{fact[:80]}'")
                return (False, reason)

        return (True, "")

    def _should_check_temporal(self) -> bool:
        """Check config to see if temporal sanity checking is enabled."""
        try:
            import yaml as _yaml
            path = Path("configs/settings.yaml")
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    cfg = _yaml.safe_load(f) or {}
                return cfg.get("advanced_learning", {}).get(
                    "enrichment_temporal_check", True)
        except Exception:
            pass
        return True

    def find_knowledge_gap(self) -> Optional[dict]:
        """Find a topic where knowledge is weak."""
        strategies = [
            self._gap_from_failed_queries,
            self._gap_from_seasonal,
            self._gap_from_domain_categories,
        ]
        for strategy in strategies:
            try:
                gap = strategy()
                if gap:
                    return gap
            except Exception:
                continue
        return None

    def _gap_from_failed_queries(self) -> Optional[dict]:
        """Topics where active learning was triggered."""
        path = Path("data/failed_queries.jsonl")
        if not path.exists():
            return None
        entries = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return None
        if not entries:
            return None
        entry = random.choice(entries[-20:])
        return {"topic": entry.get("query", ""), "source": "failed_queries"}

    def _gap_from_seasonal(self) -> Optional[dict]:
        """Current month's seasonal topics."""
        try:
            from consciousness import SEASONAL_BOOST
        except ImportError:
            return None
        month = datetime.now().month
        keywords = SEASONAL_BOOST.get(month, [])
        if not keywords:
            return None
        topic = random.choice(keywords)
        return {"topic": topic, "source": "seasonal"}

    def _gap_from_domain_categories(self) -> Optional[dict]:
        """Pick from beekeeping domain categories."""
        categories = [
            "varroa treatment methods", "queen rearing",
            "honey extraction", "winter preparation",
            "spring inspection", "swarm prevention",
            "disease detection", "feeding bees",
            "hive equipment", "pollination management",
        ]
        return {"topic": random.choice(categories), "source": "domain"}

    @property
    def stats(self) -> dict:
        return {
            "generated": self._facts_generated,
            "validated": self._facts_validated,
            "rejected": self._facts_rejected,
            "success_rate": (self._facts_validated
                             / max(self._facts_generated, 1)),
        }
