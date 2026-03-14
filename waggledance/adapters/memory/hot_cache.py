# implements HotCachePort
"""
In-process LRU hot cache with TTL expiry.

Extracted from core/fast_memory.py HotCache and adapted to the
HotCachePort contract (synchronous, string values, TTL-aware).

Thread-safe via threading.Lock. Pure Python, no external dependencies.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict


class HotCache:
    """In-process LRU cache with per-entry TTL for the HotCachePort contract.

    - ``get(key)`` returns the cached string or None (counts as hit/miss).
    - ``set(key, value, ttl)`` stores a string with optional TTL in seconds.
    - ``evict_expired()`` sweeps and removes expired entries.
    - ``stats()`` returns size, hits, misses, evictions counters.

    The cache is bounded by ``max_size``. When full, the least-recently-used
    entry is evicted to make room for new inserts.
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

        # OrderedDict for LRU ordering. Each value is:
        #   {"value": str, "expires_at": float | None}
        self._cache: OrderedDict[str, dict] = OrderedDict()

        # Counters
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def get(self, key: str) -> str | None:
        """Retrieve a cached value by key.

        Returns the string value if found and not expired, otherwise None.
        Expired entries are lazily removed on access.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            # Check TTL expiry
            expires_at = entry["expires_at"]
            if expires_at is not None and time.monotonic() >= expires_at:
                # Expired: remove and count as miss
                del self._cache[key]
                self._evictions += 1
                self._misses += 1
                return None

            # Hit: refresh LRU position
            self._cache.move_to_end(key)
            self._hits += 1
            return entry["value"]

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """Store a string value with optional TTL (seconds).

        If ``ttl`` is None, the ``default_ttl`` from init is used.
        If ``default_ttl`` is also 0 or negative, the entry never expires.
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl

        if effective_ttl > 0:
            expires_at: float | None = time.monotonic() + effective_ttl
        else:
            expires_at = None

        with self._lock:
            # If key exists, remove it first so it gets moved to end
            if key in self._cache:
                del self._cache[key]

            self._cache[key] = {
                "value": value,
                "expires_at": expires_at,
            }
            self._cache.move_to_end(key)

            # Evict LRU entries if over capacity
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
                self._evictions += 1

    def evict_expired(self) -> int:
        """Remove all expired entries and return the count evicted."""
        now = time.monotonic()
        evicted = 0

        with self._lock:
            # Collect keys to remove (cannot mutate during iteration)
            expired_keys: list[str] = []
            for key, entry in self._cache.items():
                expires_at = entry["expires_at"]
                if expires_at is not None and now >= expires_at:
                    expired_keys.append(key)

            for key in expired_keys:
                del self._cache[key]
                evicted += 1

            self._evictions += evicted

        return evicted

    def stats(self) -> dict:
        """Return cache statistics.

        Returns dict with keys: size, hits, misses, evictions.
        """
        with self._lock:
            return {
                "size": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }
