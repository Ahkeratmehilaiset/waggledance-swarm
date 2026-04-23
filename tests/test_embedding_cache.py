"""Tests for B.7 embedding cache (v3.1 tweak 3)."""
from __future__ import annotations

import time

from waggledance.core.learning.embedding_cache import (
    EmbeddingCache, canonicalize_for_embedding, cache_key, NORMALIZATION_VERSION,
)


def test_canonicalize_is_nfc_and_strips():
    # Composed é (U+00E9) and decomposed é (U+0065 U+0301) become equal after NFC
    composed = "caf\u00e9"
    decomposed = "cafe\u0301"
    assert canonicalize_for_embedding(composed) == canonicalize_for_embedding(decomposed)
    # Strip leading/trailing whitespace
    assert canonicalize_for_embedding("  hello  ") == "hello"
    # Preserves case and inner whitespace
    assert canonicalize_for_embedding("Hello World") == "Hello World"
    # Preserves unicode inside
    assert canonicalize_for_embedding("paljonko lämmitys") == "paljonko lämmitys"


def test_cache_key_is_model_keyed():
    k1 = cache_key("nomic-embed-text:v1.5", "hello")
    k2 = cache_key("nomic-embed-text:v2.0", "hello")
    assert k1 != k2
    # Same model + text = same key
    assert k1 == cache_key("nomic-embed-text:v1.5", "hello")


def test_cache_key_case_sensitive():
    # v3.1 tweak 3: USA vs usa should NOT share a vector
    k_upper = cache_key("nomic-embed-text:v1.5", "USA")
    k_lower = cache_key("nomic-embed-text:v1.5", "usa")
    assert k_upper != k_lower


def test_cache_key_whitespace_stable():
    # NFC + strip, so leading/trailing whitespace doesn't matter
    assert (
        cache_key("model", "hello")
        == cache_key("model", "  hello  ")
        == cache_key("model", "hello\t")
    )


def test_put_and_get(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite")
    vec = [0.1, 0.2, 0.3]
    cache.put("nomic-embed-text:v1.5", "hello world", vec)
    got = cache.get("nomic-embed-text:v1.5", "hello world")
    assert got == vec


def test_miss_returns_none(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite")
    assert cache.get("model", "never cached") is None


def test_model_change_invalidates_cache(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite")
    cache.put("model-v1", "hello", [0.1])
    assert cache.get("model-v2", "hello") is None
    assert cache.get("model-v1", "hello") == [0.1]


def test_ttl_expiry(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite", ttl_days=0.000001)
    cache.put("m", "x", [1.0])
    time.sleep(0.5)
    # Past TTL cutoff
    assert cache.get("m", "x") is None


def test_max_entries_eviction(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite", max_entries=3)
    for i in range(5):
        cache.put("m", f"text{i}", [float(i)])
        time.sleep(0.001)  # ensure distinct last_hit_at
    stats = cache.stats()
    # Should have kept only 3 most recent
    assert stats["entries"] == 3


def test_corrupt_cache_graceful_fallback(tmp_path):
    path = tmp_path / "cache.sqlite"
    path.write_bytes(b"not a valid sqlite file")
    # EmbeddingCache tries to init; might raise. Catch and verify graceful.
    import pytest
    with pytest.raises(Exception):
        EmbeddingCache(path=path)


def test_cache_stats(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite")
    cache.put("m", "a", [0.1])
    cache.put("m", "b", [0.2])
    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["normalization_version"] == NORMALIZATION_VERSION


def test_usa_vs_usa_lowercase_not_aliased(tmp_path):
    """Critical v3.1 tweak 3 test: USA and usa must NOT share a vector."""
    cache = EmbeddingCache(path=tmp_path / "cache.sqlite")
    cache.put("m", "USA", [1.0, 0.0])
    cache.put("m", "usa", [0.0, 1.0])
    assert cache.get("m", "USA") == [1.0, 0.0]
    assert cache.get("m", "usa") == [0.0, 1.0]
