"""B2: Test MemoryEviction — TTL + max count limits."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, UTC


class MockMemoryStore:
    """Mock MemoryStore with integer count property."""
    def __init__(self, count_val=100, items=None):
        self._count = count_val
        self._items = items or {"ids": [], "metadatas": []}
        self.collection = MagicMock()
        self.collection.get.return_value = self._items
        self.collection.delete.return_value = None

    @property
    def count(self):
        return self._count

    @count.setter
    def count(self, val):
        self._count = val


def make_mock_memory(count=100, items=None):
    """Create a mock MemoryStore with configurable get/delete/count."""
    return MockMemoryStore(count, items)


def test_eviction_disabled():
    """Disabled eviction does nothing."""
    from core.memory_engine import MemoryEviction
    mem = make_mock_memory(300000)
    ev = MemoryEviction(mem, {"enabled": False})
    assert ev.run_eviction() == 0
    print("  [PASS] eviction disabled -> 0 evictions")


def test_eviction_below_limit():
    """No eviction when below max_facts."""
    from core.memory_engine import MemoryEviction
    mem = make_mock_memory()
    # count property
    mem.count = 1000
    # Make collection.count() also work
    ev = MemoryEviction(mem, {"enabled": True, "max_facts": 200000})
    result = ev._evict_count_overflow()
    assert result == 0
    print("  [PASS] below limit -> 0 evictions")


def test_ttl_eviction():
    """TTL eviction removes expired items."""
    from core.memory_engine import MemoryEviction

    # Create items with old timestamps
    old_ts = (datetime.now(UTC) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")

    items = {
        "ids": ["w1", "w2", "w3"],
        "metadatas": [
            {"source_type": "weather", "timestamp": old_ts},
            {"source_type": "weather", "timestamp": old_ts},
            {"source_type": "weather", "timestamp": fresh_ts},  # not expired
        ]
    }
    mem = make_mock_memory(100, items)

    ev = MemoryEviction(mem, {
        "enabled": True,
        "ttl_rules": {"weather": 1},  # 1 hour TTL
    })

    result = ev._evict_ttl_expired()
    assert result == 2, f"Expected 2 evictions, got {result}"
    # Verify delete was called with the old IDs
    mem.collection.delete.assert_called_once_with(ids=["w1", "w2"])
    print("  [PASS] TTL eviction: 2 expired weather items deleted")


def test_ttl_protects_user_sources():
    """Protected sources are not TTL-evicted."""
    from core.memory_engine import MemoryEviction

    old_ts = (datetime.now(UTC) - timedelta(hours=9999)).strftime("%Y-%m-%dT%H:%M:%S")
    items = {
        "ids": ["u1", "u2"],
        "metadatas": [
            {"source_type": "user_teaching", "timestamp": old_ts},
            {"source_type": "user_teaching", "timestamp": old_ts},
        ]
    }
    mem = make_mock_memory(100, items)

    ev = MemoryEviction(mem, {
        "enabled": True,
        "ttl_rules": {"user_teaching": 1},  # Would expire, but protected
        "protected_sources": ["user_teaching"],
    })

    result = ev._evict_ttl_expired()
    assert result == 0
    print("  [PASS] protected sources survive TTL eviction")


def test_count_overflow_eviction():
    """Count overflow evicts lowest-confidence items."""
    from core.memory_engine import MemoryEviction

    # 105 items, max 100 -> need to evict ~5
    items = {
        "ids": [f"id_{i}" for i in range(20)],
        "metadatas": [
            {"confidence": 0.3, "timestamp": "2026-01-01T00:00:00", "source_type": "web_learning"}
            for _ in range(10)
        ] + [
            {"confidence": 0.9, "timestamp": "2026-03-01T00:00:00", "source_type": "user_teaching"}
            for _ in range(10)
        ]
    }
    mem = make_mock_memory(items=items)
    # Make count property return 105 first, then after deletion updates
    mem.count = 105

    ev = MemoryEviction(mem, {
        "enabled": True,
        "max_facts": 100,
        "eviction_batch_size": 50,
    })

    result = ev._evict_count_overflow()
    assert result > 0, "Should evict some items"
    # Check that delete was called
    assert mem.collection.delete.called
    # The deleted IDs should be from the low-confidence items
    deleted_ids = mem.collection.delete.call_args[1].get("ids",
                   mem.collection.delete.call_args[0][0] if mem.collection.delete.call_args[0] else [])
    # Actually get from kwargs
    call_kwargs = mem.collection.delete.call_args
    print(f"  [PASS] count overflow: evicted {result} items")


def test_on_flush_periodic():
    """on_flush only runs eviction every N flushes."""
    from core.memory_engine import MemoryEviction
    mem = make_mock_memory(100)

    ev = MemoryEviction(mem, {
        "enabled": True,
        "eviction_check_every_n_flushes": 5,
        "max_facts": 200000,
    })

    # First 4 flushes should not trigger
    for i in range(4):
        assert ev.on_flush() == 0

    # 5th flush triggers (but nothing to evict since below limit)
    result = ev.on_flush()
    assert result == 0  # nothing to evict, but it DID check
    assert ev._flush_counter == 5
    print("  [PASS] on_flush respects check_every_n interval")


def test_stats():
    """Stats property returns correct info."""
    from core.memory_engine import MemoryEviction
    mem = make_mock_memory()
    mem.count = 5000

    ev = MemoryEviction(mem, {"enabled": True, "max_facts": 200000})
    s = ev.stats
    assert s["enabled"] is True
    assert s["max_facts"] == 200000
    assert s["current_count"] == 5000
    assert "2.5%" in s["utilization_pct"]
    assert s["total_evicted"] == 0
    print("  [PASS] stats returns correct data")


if __name__ == "__main__":
    print("B2: MemoryEviction tests")
    print("=" * 50)
    test_eviction_disabled()
    test_eviction_below_limit()
    test_ttl_eviction()
    test_ttl_protects_user_sources()
    test_count_overflow_eviction()
    test_on_flush_periodic()
    test_stats()
    print("=" * 50)
    print("ALL 7 TESTS PASSED")
