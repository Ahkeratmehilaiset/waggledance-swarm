"""Tests for core.circuit_breaker — extracted from memory_engine.py (v1.17.0)."""

import time
import unittest
from unittest.mock import patch

from core.circuit_breaker import CircuitBreaker


class TestCircuitBreakerInit(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker("test")
        self.assertEqual(cb.state, CircuitBreaker.CLOSED)

    def test_custom_params(self):
        cb = CircuitBreaker("x", failure_threshold=5, window_s=120, recovery_s=60)
        self.assertEqual(cb.failure_threshold, 5)
        self.assertEqual(cb.window_s, 120)
        self.assertEqual(cb.recovery_s, 60)


class TestCircuitBreakerStateTransitions(unittest.TestCase):
    def test_allows_request_when_closed(self):
        cb = CircuitBreaker("test")
        self.assertTrue(cb.allow_request())

    def test_trips_after_threshold_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3, window_s=60)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.CLOSED)
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.OPEN)

    def test_blocks_requests_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        self.assertFalse(cb.allow_request())

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_s=0.01)
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.OPEN)
        time.sleep(0.02)
        self.assertTrue(cb.allow_request())
        self.assertEqual(cb.state, CircuitBreaker.HALF_OPEN)

    def test_closes_on_success_from_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_s=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # transition to HALF_OPEN
        cb.record_success()
        self.assertEqual(cb.state, CircuitBreaker.CLOSED)

    def test_reopens_on_failure_from_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_s=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.OPEN)


class TestCircuitBreakerWindowPruning(unittest.TestCase):
    def test_old_failures_pruned(self):
        cb = CircuitBreaker("test", failure_threshold=3, window_s=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        cb.record_failure()
        # Old 2 failures pruned, only 1 recent — stays CLOSED
        self.assertEqual(cb.state, CircuitBreaker.CLOSED)


class TestCircuitBreakerStats(unittest.TestCase):
    def test_stats_dict(self):
        cb = CircuitBreaker("myservice")
        stats = cb.stats
        self.assertEqual(stats["name"], "myservice")
        self.assertEqual(stats["state"], "closed")
        self.assertEqual(stats["recent_failures"], 0)
        self.assertEqual(stats["total_trips"], 0)
        self.assertEqual(stats["total_blocked"], 0)

    def test_stats_after_trip(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        cb.allow_request()  # blocked
        stats = cb.stats
        self.assertEqual(stats["total_trips"], 1)
        self.assertEqual(stats["total_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
