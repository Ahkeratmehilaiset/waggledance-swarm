"""Tests for core.route_telemetry — RouteStats, RouteTelemetry recording and summary."""

import unittest

from core.route_telemetry import RouteStats, RouteTelemetry


class TestRouteStatsInit(unittest.TestCase):
    def test_default_values(self):
        s = RouteStats(route_type="llm")
        self.assertEqual(s.count, 0)
        self.assertEqual(s.success_count, 0)
        self.assertEqual(s.total_latency_ms, 0.0)
        self.assertEqual(s.latencies, [])
        self.assertEqual(s.fallback_count, 0)

    def test_success_rate_zero_when_empty(self):
        s = RouteStats(route_type="llm")
        self.assertAlmostEqual(s.success_rate, 0.0)

    def test_avg_latency_zero_when_empty(self):
        s = RouteStats(route_type="llm")
        self.assertAlmostEqual(s.avg_latency_ms, 0.0)


class TestRouteTelemetryRecord(unittest.TestCase):
    def test_record_creates_stats(self):
        rt = RouteTelemetry()
        rt.record("llm", 50.0, success=True)
        stats = rt.get_stats("llm")
        self.assertIsNotNone(stats)
        self.assertEqual(stats.count, 1)
        self.assertEqual(stats.success_count, 1)

    def test_record_multiple(self):
        rt = RouteTelemetry()
        rt.record("llm", 50.0, success=True)
        rt.record("llm", 100.0, success=False)
        rt.record("llm", 75.0, success=True, was_fallback=True)
        stats = rt.get_stats("llm")
        self.assertEqual(stats.count, 3)
        self.assertEqual(stats.success_count, 2)
        self.assertEqual(stats.fallback_count, 1)
        self.assertAlmostEqual(stats.total_latency_ms, 225.0)

    def test_record_separate_routes(self):
        rt = RouteTelemetry()
        rt.record("llm", 50.0, success=True)
        rt.record("model_based", 30.0, success=True)
        self.assertEqual(len(rt.all_stats()), 2)
        self.assertEqual(rt.get_stats("llm").count, 1)
        self.assertEqual(rt.get_stats("model_based").count, 1)

    def test_latency_truncation(self):
        rt = RouteTelemetry(max_latency_samples=5)
        for i in range(10):
            rt.record("llm", float(i), success=True)
        stats = rt.get_stats("llm")
        self.assertEqual(len(stats.latencies), 5)
        # Should keep the last 5 (indices 5..9)
        self.assertEqual(stats.latencies, [5.0, 6.0, 7.0, 8.0, 9.0])

    def test_get_stats_returns_none_for_unknown(self):
        rt = RouteTelemetry()
        self.assertIsNone(rt.get_stats("nonexistent"))


class TestRouteStatsPercentile(unittest.TestCase):
    def test_percentile_empty(self):
        s = RouteStats(route_type="llm")
        self.assertAlmostEqual(s.percentile(50), 0.0)

    def test_percentile_single(self):
        s = RouteStats(route_type="llm", latencies=[42.0])
        self.assertAlmostEqual(s.percentile(50), 42.0)
        self.assertAlmostEqual(s.percentile(99), 42.0)

    def test_percentile_multiple(self):
        s = RouteStats(route_type="llm", latencies=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
        p50 = s.percentile(50)
        p99 = s.percentile(99)
        # p50 should be around the middle
        self.assertGreaterEqual(p50, 50.0)
        self.assertLessEqual(p50, 60.0)
        # p99 should be near the max
        self.assertGreaterEqual(p99, 90.0)


class TestRouteTelemetrySummary(unittest.TestCase):
    def test_summary_structure(self):
        rt = RouteTelemetry()
        rt.record("llm", 50.0, success=True)
        rt.record("llm", 100.0, success=False, was_fallback=True)
        summary = rt.summary()
        self.assertIn("llm", summary)
        entry = summary["llm"]
        self.assertEqual(entry["count"], 2)
        self.assertAlmostEqual(entry["success_rate"], 0.5)
        self.assertAlmostEqual(entry["avg_latency_ms"], 75.0)
        self.assertAlmostEqual(entry["fallback_rate"], 0.5)
        self.assertIn("p50_ms", entry)
        self.assertIn("p99_ms", entry)

    def test_summary_empty(self):
        rt = RouteTelemetry()
        self.assertEqual(rt.summary(), {})


if __name__ == "__main__":
    unittest.main()
