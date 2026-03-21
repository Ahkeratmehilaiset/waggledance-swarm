"""Adapter wrapping legacy core.fast_memory.HotCache as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_HotCache = None


def _get_class():
    global _HotCache
    if _HotCache is None:
        try:
            from core.fast_memory import HotCache
            _HotCache = HotCache
        except ImportError:
            _HotCache = None
    return _HotCache


class HotCacheAdapter:
    """Capability adapter for legacy HotCache (RAM LRU Finnish answer cache).

    Exposes HotCache.get() and HotCache.put() through the autonomy
    capability interface as a fast retrieval path.
    """

    CAPABILITY_ID = "retrieve.hot_cache"

    def __init__(self, hot_cache=None):
        cls = _get_class()
        self._cache = hot_cache or (cls() if cls else None)
        self._call_count = 0
        self._hit_count = 0

    @property
    def available(self) -> bool:
        return self._cache is not None

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        """Look up a Finnish query in the HotCache.

        Args:
            query: Finnish text to look up
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._cache:
            return {"success": False, "error": "HotCache not available",
                    "capability_id": self.CAPABILITY_ID}

        hit = self._cache.get(query)
        elapsed = (time.monotonic() - t0) * 1000

        if hit is not None:
            self._hit_count += 1
            return {
                "success": True,
                "answer": hit.get("answer", ""),
                "score": hit.get("score", 0.0),
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        return {
            "success": False,
            "error": "cache_miss",
            "capability_id": self.CAPABILITY_ID,
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        cache_stats = self._cache.stats if self._cache else {}
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "hits": self._hit_count,
            "hit_rate": (self._hit_count / self._call_count
                         if self._call_count else 0.0),
            "cache": cache_stats,
        }
