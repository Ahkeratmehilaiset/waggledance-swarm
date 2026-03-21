"""Unit tests for HotCache — LRU cache with TTL expiry."""

import time
from unittest.mock import patch

import pytest

from waggledance.adapters.memory.hot_cache import HotCache


class TestHotCacheBasicGetSet:
    """Basic get/set operations."""

    def test_set_and_get_returns_value(self, hot_cache: HotCache) -> None:
        hot_cache.set("key1", "value1")
        assert hot_cache.get("key1") == "value1"

    def test_get_missing_key_returns_none(self, hot_cache: HotCache) -> None:
        assert hot_cache.get("nonexistent") is None

    def test_overwrite_existing_key(self, hot_cache: HotCache) -> None:
        hot_cache.set("key1", "old")
        hot_cache.set("key1", "new")
        assert hot_cache.get("key1") == "new"
        assert hot_cache.stats()["size"] == 1


class TestHotCacheTTL:
    """TTL-based expiration."""

    def test_expired_entry_returns_none(self) -> None:
        cache = HotCache(max_size=10, default_ttl=1)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("k", "v")

        # Advance time past TTL
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 2.0):
            assert cache.get("k") is None

    def test_explicit_ttl_overrides_default(self) -> None:
        cache = HotCache(max_size=10, default_ttl=3600)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("k", "v", ttl=1)

        # After 2 seconds: explicit TTL of 1s should have expired
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 2.0):
            assert cache.get("k") is None

    def test_none_ttl_with_zero_default_means_no_expiration(self) -> None:
        cache = HotCache(max_size=10, default_ttl=0)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("k", "v")  # ttl defaults to None -> uses default_ttl=0 -> no expiry

        # Way into the future: should still exist
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 999999.0):
            assert cache.get("k") == "v"

    def test_default_ttl_applied_when_ttl_not_specified(self) -> None:
        cache = HotCache(max_size=10, default_ttl=5)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("k", "v")

        # Not yet expired at 4 seconds
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 4.0):
            assert cache.get("k") == "v"

        # Expired at 6 seconds
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 6.0):
            assert cache.get("k") is None


class TestHotCacheLRUEviction:
    """LRU eviction when max_size is exceeded."""

    def test_evicts_oldest_when_full(self) -> None:
        cache = HotCache(max_size=3, default_ttl=3600)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        # Cache is full. Adding a new entry evicts "a" (LRU).
        cache.set("d", "4")
        assert cache.get("a") is None
        assert cache.get("b") == "2"
        assert cache.get("d") == "4"

    def test_access_refreshes_lru_position(self) -> None:
        cache = HotCache(max_size=3, default_ttl=3600)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        # Access "a" to make it most recently used
        cache.get("a")
        # Adding "d" should evict "b" (now the LRU), not "a"
        cache.set("d", "4")
        assert cache.get("a") == "1"
        assert cache.get("b") is None


class TestHotCacheEvictExpired:
    """Batch eviction of expired entries."""

    def test_evict_expired_returns_count(self) -> None:
        cache = HotCache(max_size=10, default_ttl=1)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("x", "1")
            cache.set("y", "2")
            cache.set("z", "3")

        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 2.0):
            count = cache.evict_expired()
        assert count == 3

    def test_evict_expired_only_removes_expired(self) -> None:
        cache = HotCache(max_size=10, default_ttl=3600)
        t0 = 1000.0
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0):
            cache.set("long_ttl", "stays")
            cache.set("short_ttl", "goes", ttl=1)

        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 2.0):
            count = cache.evict_expired()
        assert count == 1
        # Verify the non-expired entry is still accessible
        with patch("waggledance.adapters.memory.hot_cache.time.monotonic", return_value=t0 + 2.0):
            assert cache.get("long_ttl") == "stays"


class TestHotCacheStats:
    """Stats tracking: hits, misses, evictions."""

    def test_initial_stats_all_zero(self) -> None:
        cache = HotCache(max_size=5, default_ttl=60)
        s = cache.stats()
        assert s == {"size": 0, "hits": 0, "misses": 0, "evictions": 0}

    def test_stats_track_hits_and_misses(self, hot_cache: HotCache) -> None:
        hot_cache.set("x", "val")
        hot_cache.get("x")       # hit
        hot_cache.get("x")       # hit
        hot_cache.get("nope")    # miss
        s = hot_cache.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert s["size"] == 1

    def test_stats_track_evictions(self) -> None:
        cache = HotCache(max_size=2, default_ttl=3600)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")  # evicts "a"
        cache.set("d", "4")  # evicts "b"
        s = cache.stats()
        assert s["evictions"] == 2
        assert s["size"] == 2
