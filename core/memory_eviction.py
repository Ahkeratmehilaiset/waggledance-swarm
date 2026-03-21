"""Memory eviction strategies — extracted from memory_engine.py v1.18.0.

Prevents unbounded ChromaDB growth via TTL and count-based eviction.
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


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

    def __init__(self, memory, config: dict = None):
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
        now = datetime.now(timezone.utc)
        evicted = 0

        for source_type, ttl_hours in self.ttl_rules.items():
            if source_type in self.protected_sources:
                continue
            cutoff = now - timedelta(hours=ttl_hours)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

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
        to_evict = min(overflow + self.batch_size, overflow * 2)
        evicted = 0

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
            protection_bonus = 0.5 if source in self.protected_sources else 0.0
            scored.append((conf + protection_bonus, ts, oid))

        scored.sort(key=lambda x: (x[0], x[1]))

        ids_to_delete = [s[2] for s in scored[:to_evict]]
        if ids_to_delete:
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
