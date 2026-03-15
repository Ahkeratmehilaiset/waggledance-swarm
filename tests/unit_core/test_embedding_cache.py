"""Tests for core.embedding_cache — extracted from memory_engine.py (v1.17.0)."""

import unittest
from unittest.mock import patch, MagicMock

from core.embedding_cache import EmbeddingEngine, EvalEmbeddingEngine


class TestEmbeddingEngineInit(unittest.TestCase):
    def test_default_model(self):
        eng = EmbeddingEngine()
        self.assertEqual(eng.model, "nomic-embed-text")

    def test_custom_cache_size(self):
        eng = EmbeddingEngine(cache_size=100)
        self.assertEqual(eng._cache_max, 100)

    def test_initial_stats(self):
        eng = EmbeddingEngine()
        self.assertEqual(eng.cache_hits, 0)
        self.assertEqual(eng.cache_misses, 0)
        self.assertEqual(eng.avg_latency_ms, 0)
        self.assertEqual(eng.cache_hit_rate, 0)


class TestEmbeddingEngineCacheLRU(unittest.TestCase):
    def test_cache_hit(self):
        eng = EmbeddingEngine(cache_size=10)
        eng._available = True
        mock_vec = [0.1, 0.2, 0.3]
        with patch.object(eng, '_raw_embed', return_value=mock_vec):
            v1 = eng.embed_query("hello")
            v2 = eng.embed_query("hello")
        self.assertEqual(v1, mock_vec)
        self.assertEqual(v2, mock_vec)
        self.assertEqual(eng.cache_hits, 1)
        self.assertEqual(eng.cache_misses, 1)

    def test_cache_eviction(self):
        eng = EmbeddingEngine(cache_size=2)
        eng._available = True
        with patch.object(eng, '_raw_embed', side_effect=lambda t: [hash(t) % 100]):
            eng.embed_query("a")
            eng.embed_query("b")
            eng.embed_query("c")  # evicts "a"
        self.assertEqual(len(eng._cache), 2)

    def test_cache_hit_rate(self):
        eng = EmbeddingEngine(cache_size=10)
        eng._available = True
        with patch.object(eng, '_raw_embed', return_value=[1.0]):
            eng.embed_query("x")
            eng.embed_query("x")
            eng.embed_query("x")
        self.assertAlmostEqual(eng.cache_hit_rate, 2/3)


class TestEmbeddingEnginePrefix(unittest.TestCase):
    def test_document_prefix(self):
        eng = EmbeddingEngine()
        eng._available = True
        calls = []
        def mock_raw(text):
            calls.append(text)
            return [0.5]
        with patch.object(eng, '_raw_embed', side_effect=mock_raw):
            eng.embed_document("test text")
        self.assertTrue(calls[0].startswith("search_document: "))

    def test_query_prefix(self):
        eng = EmbeddingEngine()
        eng._available = True
        calls = []
        def mock_raw(text):
            calls.append(text)
            return [0.5]
        with patch.object(eng, '_raw_embed', side_effect=mock_raw):
            eng.embed_query("test text")
        self.assertTrue(calls[0].startswith("search_query: "))


class TestEmbeddingEngineCircuitBreaker(unittest.TestCase):
    def test_returns_none_when_unavailable(self):
        eng = EmbeddingEngine()
        eng._available = False
        result = eng._raw_embed("test")
        self.assertIsNone(result)

    def test_returns_none_when_breaker_open(self):
        eng = EmbeddingEngine()
        eng._available = True
        eng.breaker.state = "open"
        eng.breaker._opened_at = float('inf')  # never recover
        result = eng._raw_embed("test")
        self.assertIsNone(result)


class TestEvalEmbeddingEngineInit(unittest.TestCase):
    def test_default_model(self):
        eng = EvalEmbeddingEngine()
        self.assertEqual(eng.model, "all-minilm")

    def test_no_prefix(self):
        """EvalEmbeddingEngine is symmetric — no prefix."""
        eng = EvalEmbeddingEngine()
        eng._available = True
        calls = []
        def mock_raw(text):
            calls.append(text)
            return [0.5]
        with patch.object(eng, '_raw_embed', side_effect=mock_raw):
            eng.embed("test text")
        self.assertEqual(calls[0], "test text")  # no prefix


class TestEvalEmbeddingEngineBatch(unittest.TestCase):
    def test_empty_batch(self):
        eng = EvalEmbeddingEngine()
        eng._available = True
        result = eng.embed_batch([])
        self.assertEqual(result, [])

    def test_unavailable_returns_nones(self):
        eng = EvalEmbeddingEngine()
        eng._available = False
        result = eng.embed_batch(["a", "b"])
        self.assertEqual(result, [None, None])


if __name__ == "__main__":
    unittest.main()
