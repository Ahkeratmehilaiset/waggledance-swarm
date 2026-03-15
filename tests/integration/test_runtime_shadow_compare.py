"""Tests for tools/runtime_shadow_compare.py — RuntimeShadowCompare tool."""

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.runtime_shadow_compare import (
    CompareRequest,
    CompareResult,
    RuntimeShadowCompare,
    ShadowReport,
)


# ---------------------------------------------------------------------------
# Mock handlers
# ---------------------------------------------------------------------------

async def mock_old_handler(query: str) -> dict:
    """Simulates the old runtime handler."""
    return {
        "response": f"old: {query}",
        "route_type": "llm_reasoning",
        "confidence": 0.85,
    }


async def mock_new_handler(query: str) -> dict:
    """Simulates the new runtime handler — same routes as old."""
    return {
        "response": f"new: {query}",
        "route_type": "llm_reasoning",
        "confidence": 0.90,
    }


async def mock_new_handler_different_route(query: str) -> dict:
    """New handler that returns a different route."""
    return {
        "response": f"new: {query}",
        "route_type": "retrieval",
        "confidence": 0.70,
    }


async def mock_identical_handler(query: str) -> dict:
    """Handler that returns identical response for both old and new."""
    return {
        "response": f"answer: {query}",
        "route_type": "llm_reasoning",
        "confidence": 0.80,
    }


async def mock_error_handler(query: str) -> dict:
    """Handler that always raises an exception."""
    raise RuntimeError("handler crashed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestShadowModeInit(unittest.TestCase):
    """test_shadow_mode_init — verify constructor and valid modes."""

    def test_default_mode(self):
        sc = RuntimeShadowCompare()
        self.assertEqual(sc.mode, "shadow")

    def test_canary_mode(self):
        sc = RuntimeShadowCompare(mode="canary")
        self.assertEqual(sc.mode, "canary")

    def test_validate_mode(self):
        sc = RuntimeShadowCompare(mode="validate")
        self.assertEqual(sc.mode, "validate")

    def test_invalid_mode_raises(self):
        with self.assertRaises(AssertionError):
            RuntimeShadowCompare(mode="bogus")

    def test_results_empty_on_init(self):
        sc = RuntimeShadowCompare()
        self.assertEqual(sc._results, [])


class TestCompareSingleMatching(unittest.TestCase):
    """test_compare_single_matching — routes match."""

    def test_routes_match(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="What is varroa?")
        result = asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        self.assertTrue(result.match)
        self.assertEqual(result.old_route, "llm_reasoning")
        self.assertEqual(result.new_route, "llm_reasoning")

    def test_responses_populated(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="hello")
        result = asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        self.assertEqual(result.old_response, "old: hello")
        self.assertEqual(result.new_response, "new: hello")

    def test_latencies_positive(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="test")
        result = asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        self.assertGreater(result.old_latency_ms, 0)
        self.assertGreater(result.new_latency_ms, 0)

    def test_confidences(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="test")
        result = asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        self.assertAlmostEqual(result.old_confidence, 0.85)
        self.assertAlmostEqual(result.new_confidence, 0.90)

    def test_result_appended_to_internal_list(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="q1")
        asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        self.assertEqual(len(sc._results), 1)
        self.assertEqual(sc._results[0].query, "q1")


class TestCompareSingleMismatch(unittest.TestCase):
    """test_compare_single_mismatch — routes differ."""

    def test_routes_differ(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="What is varroa?")
        result = asyncio.run(
            sc.compare_single(req, mock_old_handler, mock_new_handler_different_route)
        )
        self.assertFalse(result.match)
        self.assertEqual(result.old_route, "llm_reasoning")
        self.assertEqual(result.new_route, "retrieval")

    def test_no_error_on_mismatch(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="mismatch test")
        result = asyncio.run(
            sc.compare_single(req, mock_old_handler, mock_new_handler_different_route)
        )
        self.assertEqual(result.error, "")


class TestRunCorpus(unittest.TestCase):
    """test_run_corpus — full corpus execution and report."""

    def test_report_totals(self):
        sc = RuntimeShadowCompare()
        queries = [
            CompareRequest(query="q1"),
            CompareRequest(query="q2"),
            CompareRequest(query="q3"),
        ]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        self.assertEqual(report.total_requests, 3)
        self.assertEqual(report.route_matches, 3)
        self.assertEqual(report.errors, 0)
        self.assertEqual(len(report.results), 3)

    def test_response_matches_with_identical_handlers(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1"), CompareRequest(query="q2")]
        report = asyncio.run(
            sc.run_corpus(queries, mock_identical_handler, mock_identical_handler)
        )
        self.assertEqual(report.response_matches, 2)
        self.assertAlmostEqual(report.response_match_rate, 1.0)

    def test_response_mismatches_counted(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1")]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        # old returns "old: q1", new returns "new: q1" -- different text
        self.assertEqual(report.response_matches, 0)

    def test_avg_latencies_positive(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1"), CompareRequest(query="q2")]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        self.assertGreater(report.avg_old_latency_ms, 0)
        self.assertGreater(report.avg_new_latency_ms, 0)

    def test_route_match_rate(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1")]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        self.assertAlmostEqual(report.route_match_rate, 1.0)

    def test_empty_corpus(self):
        sc = RuntimeShadowCompare()
        report = asyncio.run(sc.run_corpus([], mock_old_handler, mock_new_handler))
        self.assertEqual(report.total_requests, 0)
        self.assertAlmostEqual(report.route_match_rate, 0.0)
        self.assertAlmostEqual(report.response_match_rate, 0.0)


class TestRouteDistribution(unittest.TestCase):
    """test_route_distribution — verify route bucketing."""

    def test_single_route(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1"), CompareRequest(query="q2")]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        self.assertEqual(report.route_distribution, {"llm_reasoning": 2})

    def test_mixed_routes(self):
        sc = RuntimeShadowCompare()
        # First query goes to matching handler, second to different route handler
        queries = [CompareRequest(query="q1")]
        report1 = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))
        self.assertIn("llm_reasoning", report1.route_distribution)

        sc2 = RuntimeShadowCompare()
        queries2 = [CompareRequest(query="q2")]
        report2 = asyncio.run(
            sc2.run_corpus(queries2, mock_old_handler, mock_new_handler_different_route)
        )
        self.assertIn("retrieval", report2.route_distribution)


class TestHealthCheck(unittest.TestCase):
    """test_health_check — health_check returns correct status."""

    def test_health_check_initial(self):
        sc = RuntimeShadowCompare()
        hc = sc.health_check()
        self.assertEqual(hc["mode"], "shadow")
        self.assertEqual(hc["results_collected"], 0)
        self.assertEqual(hc["status"], "ready")

    def test_health_check_after_compare(self):
        sc = RuntimeShadowCompare(mode="canary")
        req = CompareRequest(query="test")
        asyncio.run(sc.compare_single(req, mock_old_handler, mock_new_handler))
        hc = sc.health_check()
        self.assertEqual(hc["mode"], "canary")
        self.assertEqual(hc["results_collected"], 1)


class TestSaveReport(unittest.TestCase):
    """test_save_report — report serialization to JSON."""

    def test_save_and_load(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1"), CompareRequest(query="q2")]
        report = asyncio.run(sc.run_corpus(queries, mock_old_handler, mock_new_handler))

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "report.json")
            RuntimeShadowCompare.save_report(report, path)
            self.assertTrue(os.path.exists(path))

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["mode"], "shadow")
            self.assertEqual(data["total_requests"], 2)
            self.assertEqual(data["route_matches"], 2)
            self.assertEqual(len(data["results"]), 2)
            self.assertEqual(data["results"][0]["query"], "q1")

    def test_save_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep_path = os.path.join(tmp, "a", "b", "c", "report.json")
            report = ShadowReport()
            RuntimeShadowCompare.save_report(report, deep_path)
            self.assertTrue(os.path.exists(deep_path))


class TestErrorHandling(unittest.TestCase):
    """test_error_handling — handler exceptions captured, not raised."""

    def test_old_handler_error(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="boom")
        result = asyncio.run(
            sc.compare_single(req, mock_error_handler, mock_new_handler)
        )
        self.assertIn("old_handler", result.error)
        self.assertIn("handler crashed", result.error)
        # new handler still ran successfully
        self.assertEqual(result.new_response, "new: boom")

    def test_new_handler_error(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="boom")
        result = asyncio.run(
            sc.compare_single(req, mock_old_handler, mock_error_handler)
        )
        self.assertIn("new_handler", result.error)
        self.assertIn("handler crashed", result.error)
        # old handler still ran successfully
        self.assertEqual(result.old_response, "old: boom")

    def test_corpus_counts_errors(self):
        sc = RuntimeShadowCompare()
        queries = [CompareRequest(query="q1"), CompareRequest(query="q2")]
        report = asyncio.run(
            sc.run_corpus(queries, mock_error_handler, mock_new_handler)
        )
        self.assertEqual(report.errors, 2)

    def test_both_handlers_error(self):
        sc = RuntimeShadowCompare()
        req = CompareRequest(query="double boom")
        result = asyncio.run(
            sc.compare_single(req, mock_error_handler, mock_error_handler)
        )
        # The error field will contain the last error (new_handler overwrites)
        self.assertIn("new_handler", result.error)


if __name__ == "__main__":
    unittest.main()
