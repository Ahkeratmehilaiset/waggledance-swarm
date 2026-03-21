"""Fast Memory core module — test suite (~15 tests).

Tests: HotCache init, put/get/miss, LRU eviction, stats, clear,
SEASONAL_RULES dict structure — all pure in-memory, no external deps.
BilingualMemoryStore and FiFastStore are tested only at the structural
level (syntax + constants) since they need a live ChromaDB client.
"""
import sys, os, ast, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_fast_memory():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "fast_memory.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/fast_memory.py syntax valid")


# ── 2. SEASONAL_RULES constants ──────────────────────────────────────────

def test_seasonal_rules_dict_exists():
    from core.fast_memory import SEASONAL_RULES
    assert isinstance(SEASONAL_RULES, dict)
    assert len(SEASONAL_RULES) >= 5
    print("  [PASS] SEASONAL_RULES dict exists with entries")


def test_seasonal_rules_structure():
    from core.fast_memory import SEASONAL_RULES
    for key, rule in SEASONAL_RULES.items():
        assert "patterns" in rule, f"{key} missing 'patterns'"
        assert "valid_months" in rule, f"{key} missing 'valid_months'"
        assert "reason" in rule, f"{key} missing 'reason'"
        assert isinstance(rule["patterns"], list)
        assert isinstance(rule["valid_months"], list)
        assert all(1 <= m <= 12 for m in rule["valid_months"]), \
            f"{key} has invalid month numbers"
    print("  [PASS] SEASONAL_RULES entries have correct structure")


def test_seasonal_rules_oxalic_winter():
    from core.fast_memory import SEASONAL_RULES
    # Oxalic acid is a winter treatment (Oct-Jan)
    assert "oxalic_treatment" in SEASONAL_RULES
    rule = SEASONAL_RULES["oxalic_treatment"]
    assert 11 in rule["valid_months"] or 12 in rule["valid_months"]
    print("  [PASS] SEASONAL_RULES oxalic_treatment is a winter activity")


# ── 3. HotCache init ─────────────────────────────────────────────────────

def test_hot_cache_init_default():
    from core.fast_memory import HotCache
    cache = HotCache()
    assert cache.size == 0
    assert cache._max_size == 500
    assert cache._total_hits == 0
    assert cache._total_misses == 0
    print("  [PASS] HotCache default init OK")


def test_hot_cache_init_custom_size():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=10)
    assert cache._max_size == 10
    print("  [PASS] HotCache custom max_size OK")


# ── 4. HotCache put / get / miss ─────────────────────────────────────────

def test_hot_cache_put_and_get():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.put("hunaja", "Honey is sweet.", score=0.9)
    result = cache.get("hunaja")
    assert result is not None
    assert result["answer"] == "Honey is sweet."
    assert result["score"] == 0.9
    print("  [PASS] HotCache put and get OK")


def test_hot_cache_miss_returns_none():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    result = cache.get("totally unknown query xyz")
    assert result is None
    print("  [PASS] HotCache miss returns None")


def test_hot_cache_hit_increments_counter():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.put("varroa hoito", "Treat with oxalic acid.", score=0.85)
    cache.get("varroa hoito")
    cache.get("varroa hoito")
    assert cache._total_hits == 2
    print("  [PASS] HotCache hit increments _total_hits")


def test_hot_cache_miss_increments_counter():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.get("not present")
    cache.get("also not present")
    assert cache._total_misses == 2
    print("  [PASS] HotCache miss increments _total_misses")


# ── 5. HotCache LRU eviction ─────────────────────────────────────────────

def test_hot_cache_lru_eviction():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=3)
    cache.put("entry_a", "Answer A", score=0.9)
    cache.put("entry_b", "Answer B", score=0.8)
    cache.put("entry_c", "Answer C", score=0.7)
    assert cache.size == 3

    # Touch entry_a to refresh it as MRU
    cache.get("entry_a")

    # Adding a 4th entry should evict the LRU (entry_b, since entry_a was touched)
    cache.put("entry_d", "Answer D", score=0.6)
    assert cache.size == 3
    # entry_b should be evicted (was LRU after entry_a was refreshed)
    assert cache.get("entry_b") is None
    # entry_a should still be present (was recently used)
    assert cache.get("entry_a") is not None
    print("  [PASS] HotCache LRU eviction evicts least recently used entry")


def test_hot_cache_max_size_never_exceeded():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=5)
    for i in range(20):
        cache.put("query_{0}".format(i), "answer_{0}".format(i), score=0.9)
    assert cache.size <= 5
    print("  [PASS] HotCache size never exceeds max_size")


# ── 6. HotCache stats ────────────────────────────────────────────────────

def test_hot_cache_stats_structure():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.put("test", "answer", score=0.9)
    cache.get("test")           # hit
    cache.get("not there")      # miss
    stats = cache.stats
    assert "size" in stats
    assert "max_size" in stats
    assert "hit_rate" in stats
    assert "total_hits" in stats
    assert "total_misses" in stats
    assert stats["size"] == 1
    assert stats["max_size"] == 100
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 1
    assert 0.0 <= stats["hit_rate"] <= 1.0
    print("  [PASS] HotCache stats structure correct")


def test_hot_cache_hit_rate_calculation():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.put("q", "a", score=0.9)
    cache.get("q")   # hit
    cache.get("q")   # hit
    cache.get("x")   # miss
    cache.get("y")   # miss
    stats = cache.stats
    # 2 hits / 4 total = 0.5
    assert abs(stats["hit_rate"] - 0.5) < 0.01
    print("  [PASS] HotCache hit_rate calculated correctly")


# ── 7. HotCache clear ────────────────────────────────────────────────────

def test_hot_cache_clear():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    cache.put("q1", "a1", score=0.9)
    cache.put("q2", "a2", score=0.8)
    assert cache.size == 2
    cache.clear()
    assert cache.size == 0
    assert cache.get("q1") is None
    print("  [PASS] HotCache clear empties cache")


# ── 8. HotCache normalize_key ────────────────────────────────────────────

def test_hot_cache_normalize_key_returns_string():
    from core.fast_memory import HotCache
    cache = HotCache()
    key = cache.normalize_key("Varroa hoito oksaalihapolla?")
    assert isinstance(key, str)
    # Should be lowercase (normalizer lowercases)
    assert key == key.lower()
    print("  [PASS] normalize_key returns lowercase string")


def test_hot_cache_empty_key_skips_put():
    from core.fast_memory import HotCache
    cache = HotCache(max_size=100)
    # Empty query should produce empty key and be skipped
    cache.put("", "answer", score=0.9)
    assert cache.size == 0
    print("  [PASS] HotCache skips put for empty query key")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_fast_memory,
    test_seasonal_rules_dict_exists,
    test_seasonal_rules_structure,
    test_seasonal_rules_oxalic_winter,
    test_hot_cache_init_default,
    test_hot_cache_init_custom_size,
    test_hot_cache_put_and_get,
    test_hot_cache_miss_returns_none,
    test_hot_cache_hit_increments_counter,
    test_hot_cache_miss_increments_counter,
    test_hot_cache_lru_eviction,
    test_hot_cache_max_size_never_exceeded,
    test_hot_cache_stats_structure,
    test_hot_cache_hit_rate_calculation,
    test_hot_cache_clear,
    test_hot_cache_normalize_key_returns_string,
    test_hot_cache_empty_key_skips_put,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/fast_memory.py -- {0} tests".format(len(ALL_TESTS)))
    print("=" * 60 + "\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print("  [FAIL] {0}: {1}".format(test.__name__, e))

    print("\n" + "=" * 60)
    print("Result: {0}/{1} passed, {2} failed".format(passed, passed + failed, failed))
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print("  - {0}: {1}".format(name, err))
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
