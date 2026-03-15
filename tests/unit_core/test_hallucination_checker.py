"""Tests for core.hallucination_checker — extracted from memory_engine.py (v1.17.0)."""

import unittest

from core.hallucination_checker import HallucinationChecker, HallucinationResult


class TestHallucinationResult(unittest.TestCase):
    def test_default_values(self):
        r = HallucinationResult()
        self.assertEqual(r.relevance, 1.0)
        self.assertEqual(r.keyword_overlap, 1.0)
        self.assertFalse(r.is_suspicious)
        self.assertEqual(r.reason, "")

    def test_suspicious_result(self):
        r = HallucinationResult(relevance=0.2, keyword_overlap=0.0,
                                is_suspicious=True, reason="low")
        self.assertTrue(r.is_suspicious)


class TestCossineSimilarity(unittest.TestCase):
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(HallucinationChecker.cosine_similarity(v, v), 1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(HallucinationChecker.cosine_similarity(a, b), 0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        self.assertAlmostEqual(HallucinationChecker.cosine_similarity(a, b), -1.0)

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        self.assertAlmostEqual(HallucinationChecker.cosine_similarity(a, b), 0.0)


class TestKeywordOverlap(unittest.TestCase):
    def test_full_overlap(self):
        overlap = HallucinationChecker.keyword_overlap(
            "bees make honey", "honey from bees")
        self.assertGreater(overlap, 0.5)

    def test_no_overlap(self):
        overlap = HallucinationChecker.keyword_overlap(
            "quantum physics", "baking cookies recipe")
        self.assertEqual(overlap, 0.0)

    def test_stopwords_filtered(self):
        overlap = HallucinationChecker.keyword_overlap(
            "the and for", "the and for")
        # All stopwords → empty q_words → 0.0
        self.assertEqual(overlap, 0.0)

    def test_partial_overlap(self):
        overlap = HallucinationChecker.keyword_overlap(
            "beekeeping varroa treatment", "varroa mite treatment chemical")
        self.assertGreater(overlap, 0.0)
        self.assertLess(overlap, 1.0)


class TestHallucinationCheckerCheck(unittest.TestCase):
    def setUp(self):
        self.checker = HallucinationChecker()

    def test_no_vectors_not_suspicious(self):
        result = self.checker.check("question about bees", "answer about bees")
        # No vectors → similarity=1.0, only keyword overlap matters
        self.assertFalse(result.is_suspicious)

    def test_suspicious_no_overlap_low_similarity(self):
        # Zero keyword overlap + low embedding similarity → suspicious
        q_vec = [1.0, 0.0, 0.0, 0.0]
        a_vec = [0.0, 0.0, 0.0, 1.0]
        result = self.checker.check(
            "varroa treatment methods",
            "pizza delivery service available",
            q_vec=q_vec, a_vec=a_vec)
        self.assertTrue(result.is_suspicious)

    def test_not_suspicious_with_overlap(self):
        result = self.checker.check(
            "varroa treatment for bees",
            "varroa treatment involves oxalic acid for bees")
        self.assertFalse(result.is_suspicious)

    def test_hard_gate_zero_overlap_low_sim(self):
        # Even if combined might pass, hard gate catches it
        checker = HallucinationChecker(combined_threshold=0.01)
        q_vec = [1.0, 0.0]
        a_vec = [0.2, 0.98]  # low similarity
        result = checker.check("xyz123", "abc789", q_vec=q_vec, a_vec=a_vec)
        self.assertTrue(result.is_suspicious)

    def test_custom_thresholds(self):
        checker = HallucinationChecker(
            combined_threshold=0.99, hard_gate_similarity=0.99)
        # No vectors → similarity=1.0, overlap moderate → combined ~0.7+0.3=high
        # Use words with zero overlap to trigger
        result = checker.check("quantum physics research", "pizza delivery service")
        self.assertTrue(result.is_suspicious)


if __name__ == "__main__":
    unittest.main()
