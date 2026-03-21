"""Tests for core.canary_promoter — micromodel canary scenarios (Phase 4)."""

import unittest

from core.canary_promoter import CanaryPromoter, ROLLBACK_THRESHOLD


class TestMicromodelCanaryPromotion(unittest.TestCase):
    def test_micromodel_promoted(self):
        cp = CanaryPromoter(min_samples=10, min_improvement=0.03)
        cp.start_canary("micro-phi4-v2", baseline_score=0.65)
        # Simulate 10 eval scores slightly above baseline + threshold
        for _ in range(10):
            cp.record_sample("micro-phi4-v2", 0.72)
        result = cp.evaluate("micro-phi4-v2")
        self.assertTrue(result.promoted)
        self.assertAlmostEqual(result.improvement, 0.07, places=4)
        self.assertEqual(result.samples_seen, 10)

    def test_micromodel_not_promoted(self):
        cp = CanaryPromoter(min_samples=10, min_improvement=0.10)
        cp.start_canary("micro-phi4-v2", baseline_score=0.65)
        for _ in range(10):
            cp.record_sample("micro-phi4-v2", 0.68)
        result = cp.evaluate("micro-phi4-v2")
        self.assertFalse(result.promoted)


class TestMicromodelRollback(unittest.TestCase):
    def test_should_rollback_on_regression(self):
        cp = CanaryPromoter(min_samples=5, min_improvement=0.05)
        cp.start_canary("micro-bad", baseline_score=0.70)
        # Much worse than baseline
        for _ in range(5):
            cp.record_sample("micro-bad", 0.55)
        self.assertTrue(cp.should_rollback("micro-bad"))

    def test_no_rollback_when_close(self):
        cp = CanaryPromoter(min_samples=5, min_improvement=0.05)
        cp.start_canary("micro-ok", baseline_score=0.70)
        for _ in range(5):
            cp.record_sample("micro-ok", 0.68)
        # 0.68 - 0.70 = -0.02, within ROLLBACK_THRESHOLD (-0.05)
        self.assertFalse(cp.should_rollback("micro-ok"))

    def test_no_rollback_unknown(self):
        cp = CanaryPromoter()
        self.assertFalse(cp.should_rollback("nonexistent"))

    def test_no_rollback_empty_scores(self):
        cp = CanaryPromoter()
        cp.start_canary("empty", baseline_score=0.70)
        self.assertFalse(cp.should_rollback("empty"))

    def test_evaluate_regression_reason(self):
        cp = CanaryPromoter(min_samples=3, min_improvement=0.05)
        cp.start_canary("regress", baseline_score=0.80)
        for _ in range(3):
            cp.record_sample("regress", 0.60)
        result = cp.evaluate("regress")
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "regression_detected")


if __name__ == "__main__":
    unittest.main()
