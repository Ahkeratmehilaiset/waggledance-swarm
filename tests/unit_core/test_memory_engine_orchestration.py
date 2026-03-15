"""Tests for memory_engine.py orchestration — backward compatibility after extraction (v1.17.0)."""

import unittest


class TestBackwardCompatImports(unittest.TestCase):
    """Verify that importing from core.memory_engine still works after extraction."""

    def test_import_circuit_breaker(self):
        from core.memory_engine import CircuitBreaker
        cb = CircuitBreaker("test")
        self.assertEqual(cb.state, "closed")

    def test_import_embedding_engine(self):
        from core.memory_engine import EmbeddingEngine
        self.assertTrue(hasattr(EmbeddingEngine, 'embed_query'))

    def test_import_eval_embedding_engine(self):
        from core.memory_engine import EvalEmbeddingEngine
        self.assertTrue(hasattr(EvalEmbeddingEngine, 'embed'))

    def test_import_hallucination_result(self):
        from core.memory_engine import HallucinationResult
        hr = HallucinationResult(relevance=0.5)
        self.assertEqual(hr.relevance, 0.5)

    def test_import_math_solver(self):
        from core.memory_engine import MathSolver
        self.assertEqual(MathSolver.solve("calculate 2+2"), "4")

    def test_import_hallucination_checker(self):
        from core.memory_engine import HallucinationChecker
        checker = HallucinationChecker()
        result = checker.check("bees", "bees make honey")
        self.assertIsNotNone(result)


class TestDirectImports(unittest.TestCase):
    """Verify that importing directly from extracted modules works."""

    def test_circuit_breaker_direct(self):
        from core.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("direct")
        self.assertEqual(cb.state, "closed")

    def test_embedding_cache_direct(self):
        from core.embedding_cache import EmbeddingEngine, EvalEmbeddingEngine
        self.assertTrue(hasattr(EmbeddingEngine, 'PREFIX_DOCUMENT'))
        self.assertFalse(hasattr(EvalEmbeddingEngine, 'PREFIX_DOCUMENT'))

    def test_hallucination_checker_direct(self):
        from core.hallucination_checker import HallucinationChecker, HallucinationResult
        self.assertTrue(hasattr(HallucinationChecker, 'check'))
        hr = HallucinationResult()
        self.assertFalse(hr.is_suspicious)

    def test_math_solver_direct(self):
        from core.math_solver import MathSolver
        self.assertTrue(MathSolver.is_math("2+2"))


class TestMemoryEngineClassesStillPresent(unittest.TestCase):
    """Verify that non-extracted classes are still in memory_engine."""

    def test_memory_match(self):
        from core.memory_engine import MemoryMatch
        mm = MemoryMatch(text="test", score=0.9)
        self.assertEqual(mm.text, "test")

    def test_prefilter_result(self):
        from core.memory_engine import PreFilterResult
        pf = PreFilterResult(handled=True, method="cache")
        self.assertTrue(pf.handled)

    def test_memory_store_class_exists(self):
        from core.memory_engine import MemoryStore
        self.assertTrue(callable(MemoryStore))

    def test_memory_eviction_class_exists(self):
        from core.memory_engine import MemoryEviction
        self.assertTrue(callable(MemoryEviction))

    def test_opus_mt_adapter_class_exists(self):
        from core.memory_engine import OpusMTAdapter
        self.assertTrue(callable(OpusMTAdapter))

    def test_consciousness_class_exists(self):
        from core.memory_engine import Consciousness
        self.assertTrue(callable(Consciousness))


if __name__ == "__main__":
    unittest.main()
