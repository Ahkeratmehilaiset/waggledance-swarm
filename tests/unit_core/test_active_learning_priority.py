"""Tests for core.active_learning — ActiveLearningScorer (Phase 4)."""

import unittest

from core.active_learning import (
    ActiveLearningScorer,
    CONFIDENCE_THRESHOLD,
    LearningCandidate,
    TOPIC_GAP_CEILING,
    W_LOW_CONFIDENCE,
    W_REPEATED_FALLBACK,
    W_TOPIC_GAP,
    W_USER_CORRECTION,
)


class TestLearningCandidate(unittest.TestCase):
    def test_defaults(self):
        c = LearningCandidate(query="hello")
        self.assertEqual(c.confidence, 1.0)
        self.assertEqual(c.topic, "")
        self.assertFalse(c.was_corrected)
        self.assertEqual(c.fallback_count, 0)


class TestSignalLowConfidence(unittest.TestCase):
    def test_high_confidence_gives_zero(self):
        s = ActiveLearningScorer._sig_low_confidence(0.9)
        self.assertAlmostEqual(s, 0.0)

    def test_at_threshold_gives_zero(self):
        s = ActiveLearningScorer._sig_low_confidence(CONFIDENCE_THRESHOLD)
        self.assertAlmostEqual(s, 0.0)

    def test_zero_confidence_gives_one(self):
        s = ActiveLearningScorer._sig_low_confidence(0.0)
        self.assertAlmostEqual(s, 1.0)

    def test_mid_confidence(self):
        mid = CONFIDENCE_THRESHOLD / 2.0
        s = ActiveLearningScorer._sig_low_confidence(mid)
        self.assertAlmostEqual(s, 0.5, places=4)


class TestSignalTopicGap(unittest.TestCase):
    def test_unknown_topic_full_gap(self):
        scorer = ActiveLearningScorer(topic_counts={"other": 100})
        self.assertAlmostEqual(scorer._sig_topic_gap("bee_health"), 1.0)

    def test_empty_topic_returns_zero(self):
        scorer = ActiveLearningScorer(topic_counts={"bee_health": 0})
        self.assertAlmostEqual(scorer._sig_topic_gap(""), 0.0)

    def test_saturated_topic_returns_zero(self):
        scorer = ActiveLearningScorer(
            topic_counts={"heating": TOPIC_GAP_CEILING + 5}
        )
        self.assertAlmostEqual(scorer._sig_topic_gap("heating"), 0.0)


class TestSignalUserCorrection(unittest.TestCase):
    def test_corrected(self):
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_user_correction(True), 1.0
        )

    def test_not_corrected(self):
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_user_correction(False), 0.0
        )


class TestSignalRepeatedFallback(unittest.TestCase):
    def test_zero_fallbacks(self):
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_repeated_fallback(0), 0.0
        )

    def test_one_fallback(self):
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_repeated_fallback(1), 0.0
        )

    def test_five_plus_fallbacks_caps_at_one(self):
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_repeated_fallback(5), 1.0
        )

    def test_three_fallbacks(self):
        # (3-1)/4 = 0.5
        self.assertAlmostEqual(
            ActiveLearningScorer._sig_repeated_fallback(3), 0.5
        )


class TestScore(unittest.TestCase):
    def test_perfect_candidate_scores_zero(self):
        """High confidence, saturated topic, no correction, no fallback → 0."""
        scorer = ActiveLearningScorer(topic_counts={"heating": 100})
        c = LearningCandidate(
            query="test", confidence=0.9, topic="heating",
            was_corrected=False, fallback_count=0,
        )
        self.assertAlmostEqual(scorer.score(c), 0.0)

    def test_worst_candidate_scores_high(self):
        """All signals maxed out → near 1.0."""
        scorer = ActiveLearningScorer(topic_counts={})
        c = LearningCandidate(
            query="test", confidence=0.0, topic="unknown_topic",
            was_corrected=True, fallback_count=5,
        )
        score = scorer.score(c)
        # 0.35*1 + 0.25*1 + 0.25*1 + 0.15*1 = 1.0
        self.assertAlmostEqual(score, 1.0)

    def test_score_in_range(self):
        scorer = ActiveLearningScorer(topic_counts={"bee": 2})
        c = LearningCandidate(
            query="q", confidence=0.3, topic="bee",
            was_corrected=False, fallback_count=3,
        )
        score = scorer.score(c)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_correction_only(self):
        """Only the correction signal fires."""
        scorer = ActiveLearningScorer(topic_counts={"t": 100})
        c = LearningCandidate(
            query="q", confidence=0.9, topic="t",
            was_corrected=True, fallback_count=0,
        )
        self.assertAlmostEqual(scorer.score(c), W_USER_CORRECTION)


class TestRank(unittest.TestCase):
    def test_rank_returns_top_k(self):
        scorer = ActiveLearningScorer(topic_counts={})
        candidates = [
            LearningCandidate(query="a", confidence=0.9),
            LearningCandidate(query="b", confidence=0.1),
            LearningCandidate(query="c", confidence=0.5),
        ]
        ranked = scorer.rank(candidates, top_k=2)
        self.assertEqual(len(ranked), 2)
        # b (confidence=0.1) should rank first (lowest confidence → highest score)
        self.assertEqual(ranked[0][1].query, "b")

    def test_rank_descending(self):
        scorer = ActiveLearningScorer(topic_counts={})
        candidates = [
            LearningCandidate(query="x", confidence=0.0, was_corrected=True),
            LearningCandidate(query="y", confidence=0.9),
        ]
        ranked = scorer.rank(candidates)
        self.assertGreaterEqual(ranked[0][0], ranked[1][0])

    def test_rank_empty(self):
        scorer = ActiveLearningScorer()
        self.assertEqual(scorer.rank([]), [])


if __name__ == "__main__":
    unittest.main()
