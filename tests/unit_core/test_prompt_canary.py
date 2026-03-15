"""Tests for core.canary_promoter — prompt canary scenarios (Phase 4)."""

import unittest

from core.canary_promoter import CanaryPromoter, CanaryResult


class TestPromptCanaryBasic(unittest.TestCase):
    def test_start_and_evaluate_need_samples(self):
        cp = CanaryPromoter(min_samples=10, min_improvement=0.05)
        cp.start_canary("prompt-v2", baseline_score=0.70)
        for _ in range(5):
            cp.record_sample("prompt-v2", 0.80)
        result = cp.evaluate("prompt-v2")
        self.assertFalse(result.promoted)
        self.assertEqual(result.samples_seen, 5)
        self.assertIn("need_more_samples", result.reason)

    def test_promotion_when_improved(self):
        cp = CanaryPromoter(min_samples=5, min_improvement=0.05)
        cp.start_canary("prompt-v2", baseline_score=0.70)
        for _ in range(5):
            cp.record_sample("prompt-v2", 0.80)
        result = cp.evaluate("prompt-v2")
        self.assertTrue(result.promoted)
        self.assertEqual(result.reason, "promoted")
        self.assertAlmostEqual(result.improvement, 0.10, places=4)

    def test_not_promoted_insufficient_improvement(self):
        cp = CanaryPromoter(min_samples=5, min_improvement=0.10)
        cp.start_canary("prompt-v2", baseline_score=0.70)
        for _ in range(5):
            cp.record_sample("prompt-v2", 0.73)
        result = cp.evaluate("prompt-v2")
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "insufficient_improvement")

    def test_disabled_promoter(self):
        cp = CanaryPromoter(enabled=False)
        cp.start_canary("prompt-v2", baseline_score=0.70)
        cp.record_sample("prompt-v2", 0.90)
        result = cp.evaluate("prompt-v2")
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "canary_disabled")

    def test_unknown_experiment(self):
        cp = CanaryPromoter()
        result = cp.evaluate("nonexistent")
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "unknown_experiment")


class TestPromptCanaryEdgeCases(unittest.TestCase):
    def test_restart_resets_experiment(self):
        cp = CanaryPromoter(min_samples=3, min_improvement=0.05)
        cp.start_canary("exp", baseline_score=0.50)
        cp.record_sample("exp", 0.80)
        cp.record_sample("exp", 0.80)
        # Reset by starting again
        cp.start_canary("exp", baseline_score=0.70)
        result = cp.evaluate("exp")
        self.assertEqual(result.samples_seen, 0)
        self.assertIn("need_more_samples", result.reason)

    def test_record_sample_unknown_experiment_ignored(self):
        cp = CanaryPromoter()
        # Should not raise
        cp.record_sample("ghost", 0.5)


if __name__ == "__main__":
    unittest.main()
