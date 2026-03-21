"""Tests for core.route_telemetry — bounded threshold tuning."""

import unittest

from core.route_telemetry import RouteTelemetry


class TestTuneThresholdBasics(unittest.TestCase):
    def test_no_change_below_min_samples(self):
        """Tuning requires at least 10 samples; should return current unchanged."""
        rt = RouteTelemetry()
        for _ in range(9):
            rt.record("llm", 50.0, success=True)
        result = rt.tune_threshold("llm", current=0.6)
        self.assertAlmostEqual(result, 0.6)

    def test_lowers_threshold_on_low_success(self):
        """When success rate is below target, threshold should decrease."""
        rt = RouteTelemetry()
        # 3 successes out of 20 = 15% success rate, well below 80% target
        for _ in range(3):
            rt.record("llm", 50.0, success=True)
        for _ in range(17):
            rt.record("llm", 50.0, success=False)
        result = rt.tune_threshold("llm", current=0.6)
        self.assertLess(result, 0.6)

    def test_raises_threshold_on_high_success(self):
        """When success rate exceeds target + 0.1, threshold should increase."""
        rt = RouteTelemetry()
        # 20 successes out of 20 = 100% >> 80% + 10%
        for _ in range(20):
            rt.record("llm", 50.0, success=True)
        result = rt.tune_threshold("llm", current=0.6)
        self.assertGreater(result, 0.6)

    def test_no_change_at_target(self):
        """When success rate equals target exactly, no adjustment."""
        rt = RouteTelemetry()
        # 16 successes out of 20 = 80% = target
        for _ in range(16):
            rt.record("llm", 50.0, success=True)
        for _ in range(4):
            rt.record("llm", 50.0, success=False)
        result = rt.tune_threshold("llm", current=0.6)
        # success_rate == 0.8, not < 0.8 and not > 0.9, so delta = 0
        self.assertAlmostEqual(result, 0.6)


class TestTuneThresholdBounds(unittest.TestCase):
    def test_max_adjustment_cap(self):
        """Single tune call cannot change by more than MAX_ADJUSTMENT (0.05)."""
        rt = RouteTelemetry()
        for _ in range(20):
            rt.record("llm", 50.0, success=False)
        result = rt.tune_threshold("llm", current=0.6)
        # delta = -0.01 which is within bounds
        self.assertAlmostEqual(result, 0.59)

    def test_threshold_floor(self):
        """Threshold should never go below 0.1."""
        rt = RouteTelemetry()
        for _ in range(20):
            rt.record("llm", 50.0, success=False)
        result = rt.tune_threshold("llm", current=0.1)
        # max(0.1, 0.1 - 0.01) = 0.1
        self.assertGreaterEqual(result, 0.1)

    def test_threshold_ceiling(self):
        """Threshold should never exceed 0.95."""
        rt = RouteTelemetry()
        for _ in range(20):
            rt.record("llm", 50.0, success=True)
        result = rt.tune_threshold("llm", current=0.95)
        # min(0.95, 0.95 + 0.01) = 0.95
        self.assertLessEqual(result, 0.95)


class TestTuneThresholdDisabled(unittest.TestCase):
    def test_disabled_returns_current(self):
        """When tuning is disabled, should return current threshold unchanged."""
        rt = RouteTelemetry(tuning_enabled=False)
        for _ in range(20):
            rt.record("llm", 50.0, success=False)
        result = rt.tune_threshold("llm", current=0.7)
        self.assertAlmostEqual(result, 0.7)


class TestGetThreshold(unittest.TestCase):
    def test_default_threshold(self):
        rt = RouteTelemetry()
        self.assertAlmostEqual(rt.get_threshold("unknown"), 0.6)

    def test_custom_default(self):
        rt = RouteTelemetry()
        self.assertAlmostEqual(rt.get_threshold("unknown", default=0.5), 0.5)

    def test_stored_threshold_after_tune(self):
        rt = RouteTelemetry()
        for _ in range(20):
            rt.record("llm", 50.0, success=True)
        rt.tune_threshold("llm", current=0.6)
        stored = rt.get_threshold("llm")
        self.assertAlmostEqual(stored, 0.61)


if __name__ == "__main__":
    unittest.main()
