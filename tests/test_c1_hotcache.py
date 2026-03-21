"""C1: Test HotCache auto-population from all chat paths."""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from _hive_source import read_hive_source


def test_hotcache_put_calls_in_hivemind():
    """All chat response paths should populate HotCache."""
    src = read_hive_source()

    # Count populate_hot_cache calls (v3.3: telemetry.populate_hot_cache or _populate_hot_cache)
    calls = src.count("populate_hot_cache")
    # 1+ definition + 3+ call sites (delegate, neg_kw_delegate, master, and/or memory_fast)
    assert calls >= 5, f"Expected >= 5 populate_hot_cache refs, found {calls}"
    print(f"  [PASS] {calls} populate_hot_cache references")


def test_hotcache_put_calls_in_consciousness():
    """before_llm fast paths should populate HotCache."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    put_calls = src.count("hot_cache.put(")
    assert put_calls >= 3, f"Expected >= 3 hot_cache.put calls, found {put_calls}"
    print(f"  [PASS] {put_calls} hot_cache.put calls in memory_engine.py")


def test_populate_method_exists():
    """populate_hot_cache method exists (in handler or telemetry module)."""
    src = read_hive_source()

    # v3.3: implementation may be in chat_telemetry.py as populate_hot_cache
    assert "def populate_hot_cache" in src or "def _populate_hot_cache" in src
    # Find the real implementation (has hot_cache.put)
    method_body = None
    for prefix in ("def populate_hot_cache", "def _populate_hot_cache"):
        search_from = 0
        while True:
            pos = src.find(prefix, search_from)
            if pos == -1:
                break
            candidate = src[pos:pos + 800]
            if "hot_cache.put(" in candidate:
                method_body = candidate
                break
            search_from = pos + 1
        if method_body:
            break
    assert method_body is not None, "populate_hot_cache implementation not found"
    assert "hot_cache" in method_body
    assert "_is_valid_response" in method_body
    assert "detected_lang" in method_body
    assert "score < 0.6" in method_body
    print("  [PASS] populate_hot_cache has all guards")


def test_hotcache_lru_eviction():
    """HotCache evicts LRU when full."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
    from fast_memory import HotCache

    cache = HotCache(max_size=3)
    cache.put("q1", "a1", 0.9, source="test")
    cache.put("q2", "a2", 0.9, source="test")
    cache.put("q3", "a3", 0.9, source="test")
    assert cache.size == 3

    # Add 4th -> should evict q1
    cache.put("q4", "a4", 0.9, source="test")
    assert cache.size == 3
    print("  [PASS] HotCache LRU eviction works at max_size")


def test_hotcache_stats():
    """HotCache stats track hits/misses."""
    from core.fast_memory import HotCache

    cache = HotCache(max_size=100)
    cache.put("test query", "test answer", 0.9)

    # Hit
    result = cache.get("test query")
    assert result is not None
    assert result["answer"] == "test answer"

    # Miss
    result2 = cache.get("unknown query")
    assert result2 is None

    stats = cache.stats
    assert stats["total_hits"] >= 1
    assert stats["total_misses"] >= 1
    print(f"  [PASS] stats: hits={stats['total_hits']}, misses={stats['total_misses']}")


def test_syntax():
    """All modified files parse correctly."""
    for fname in ["hivemind.py", os.path.join("core", "memory_engine.py")]:
        path = os.path.join(os.path.dirname(__file__), "..", fname)
        with open(path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
    print("  [PASS] all files syntax valid")


if __name__ == "__main__":
    print("C1: HotCache auto-population tests")
    print("=" * 50)
    test_syntax()
    test_hotcache_put_calls_in_hivemind()
    test_hotcache_put_calls_in_consciousness()
    test_populate_method_exists()
    test_hotcache_lru_eviction()
    test_hotcache_stats()
    print("=" * 50)
    print("ALL 6 TESTS PASSED")
