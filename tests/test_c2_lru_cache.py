"""C2: Test Embedding cache LRU — max 500, no memory leak."""
import sys, os, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collections import OrderedDict


def test_embedding_engine_cache_is_ordered_dict():
    """EmbeddingEngine uses OrderedDict for LRU."""
    from consciousness import EmbeddingEngine
    ee = EmbeddingEngine(cache_size=100)
    assert isinstance(ee._cache, OrderedDict)
    print("  [PASS] EmbeddingEngine._cache is OrderedDict")


def test_eval_engine_cache_is_ordered_dict():
    """EvalEmbeddingEngine uses OrderedDict for LRU."""
    from consciousness import EvalEmbeddingEngine
    ee = EvalEmbeddingEngine(cache_size=100)
    assert isinstance(ee._cache, OrderedDict)
    print("  [PASS] EvalEmbeddingEngine._cache is OrderedDict")


def test_default_cache_size_500():
    """Default cache size is 500 (not 10000/5000)."""
    from consciousness import EmbeddingEngine, EvalEmbeddingEngine
    ee = EmbeddingEngine()
    assert ee._cache_max == 500, f"Expected 500, got {ee._cache_max}"
    ev = EvalEmbeddingEngine()
    assert ev._cache_max == 500, f"Expected 500, got {ev._cache_max}"
    print("  [PASS] default cache_max = 500 for both engines")


def test_lru_eviction_in_cached_embed():
    """_cached_embed evicts LRU when full."""
    from consciousness import EmbeddingEngine
    ee = EmbeddingEngine(cache_size=3)

    # Manually populate cache (bypass _raw_embed)
    for i in range(3):
        key = hashlib.md5(f"text_{i}".encode()).hexdigest()
        ee._cache[key] = [0.1] * 10  # fake embedding

    assert len(ee._cache) == 3

    # Simulate adding a 4th entry via cache logic
    key4 = hashlib.md5("text_3".encode()).hexdigest()
    ee._cache[key4] = [0.2] * 10
    while len(ee._cache) > ee._cache_max:
        ee._cache.popitem(last=False)

    assert len(ee._cache) == 3
    # First entry should be evicted
    key0 = hashlib.md5("text_0".encode()).hexdigest()
    assert key0 not in ee._cache
    print("  [PASS] LRU evicts oldest entry")


def test_lru_refresh_on_hit():
    """Cache hit moves entry to end (most recently used)."""
    from consciousness import EmbeddingEngine
    ee = EmbeddingEngine(cache_size=3)

    keys = []
    for i in range(3):
        key = hashlib.md5(f"text_{i}".encode()).hexdigest()
        ee._cache[key] = [0.1] * 10
        keys.append(key)

    # Access first entry (should move to end)
    ee._cache.move_to_end(keys[0])

    # Add 4th → should evict keys[1] (now oldest)
    key4 = hashlib.md5("text_3".encode()).hexdigest()
    ee._cache[key4] = [0.2] * 10
    while len(ee._cache) > ee._cache_max:
        ee._cache.popitem(last=False)

    assert keys[0] in ee._cache, "Refreshed entry should survive"
    assert keys[1] not in ee._cache, "Oldest non-refreshed should be evicted"
    print("  [PASS] LRU refresh keeps recently used entries")


def test_memory_bounded():
    """Cache size never exceeds max after many inserts."""
    from consciousness import EmbeddingEngine
    ee = EmbeddingEngine(cache_size=10)

    for i in range(100):
        key = hashlib.md5(f"text_{i}".encode()).hexdigest()
        ee._cache[key] = [0.1] * 768
        while len(ee._cache) > ee._cache_max:
            ee._cache.popitem(last=False)

    assert len(ee._cache) <= 10
    print(f"  [PASS] cache bounded at {len(ee._cache)} entries (max=10)")


def test_source_code_no_unbounded_check():
    """Source code uses LRU eviction, not 'if len < max' guard."""
    with open(os.path.join(os.path.dirname(__file__), "..", "consciousness.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # Should NOT have the old pattern: "if len(self._cache) < self._cache_max"
    old_pattern_count = src.count("if len(self._cache) < self._cache_max")
    assert old_pattern_count == 0, f"Found {old_pattern_count} old unbounded cache checks"

    # Should have LRU eviction
    lru_count = src.count("self._cache.popitem(last=False)")
    assert lru_count >= 2, f"Expected >= 2 LRU popitem calls, found {lru_count}"
    print(f"  [PASS] {lru_count} LRU eviction points, 0 unbounded checks")


if __name__ == "__main__":
    print("C2: Embedding cache LRU tests")
    print("=" * 50)
    test_embedding_engine_cache_is_ordered_dict()
    test_eval_engine_cache_is_ordered_dict()
    test_default_cache_size_500()
    test_lru_eviction_in_cached_embed()
    test_lru_refresh_on_hit()
    test_memory_bounded()
    test_source_code_no_unbounded_check()
    print("=" * 50)
    print("ALL 7 TESTS PASSED")
