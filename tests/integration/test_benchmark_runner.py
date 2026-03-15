"""Tests for tools.benchmark_harness — BenchmarkHarness, reports, YAML loading."""

import asyncio
import json
import os
import tempfile
import unittest

from tools.benchmark_harness import (
    BenchmarkHarness,
    BenchmarkQuery,
    BenchmarkReport,
    BenchmarkResult,
)


async def _mock_handler(query: str) -> dict:
    """Simulated handler that echoes the query back with a fixed route."""
    return {"response": f"answer to: {query}", "route_type": "llm_reasoning", "confidence": 0.85}


async def _error_handler(query: str) -> dict:
    """Handler that always raises."""
    raise RuntimeError("model unavailable")


async def _route_matching_handler(query: str) -> dict:
    """Handler that returns model_based for honey queries, llm_reasoning otherwise."""
    if "honey" in query.lower():
        return {"response": "honey answer", "route_type": "model_based", "confidence": 0.9}
    return {"response": "generic", "route_type": "llm_reasoning", "confidence": 0.5}


class TestBenchmarkHarnessRun(unittest.TestCase):
    """Test the core run() method with mock handlers."""

    def test_run_all_queries_collected(self):
        queries = [
            BenchmarkQuery(id="q1", query="hello", domain="fallback"),
            BenchmarkQuery(id="q2", query="world", domain="fallback"),
        ]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_mock_handler))
        self.assertEqual(report.total, 2)
        self.assertEqual(len(report.results), 2)

    def test_run_pass_when_no_expected_route(self):
        """Queries without expected_route should always count as passed."""
        queries = [BenchmarkQuery(id="q1", query="test", domain="fallback")]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_mock_handler))
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 0)

    def test_run_fail_when_route_mismatch(self):
        queries = [
            BenchmarkQuery(id="q1", query="test", domain="bee_qa", expected_route="model_based"),
        ]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_mock_handler))  # returns llm_reasoning
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.passed, 0)

    def test_run_error_handling(self):
        queries = [BenchmarkQuery(id="q1", query="boom", domain="fallback")]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_error_handler))
        self.assertEqual(report.errors, 1)
        self.assertIn("model unavailable", report.results[0].error)

    def test_latency_recorded(self):
        queries = [BenchmarkQuery(id="q1", query="hi", domain="fallback")]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_mock_handler))
        self.assertGreater(report.avg_latency_ms, 0)
        self.assertGreater(report.results[0].latency_ms, 0)

    def test_mixed_pass_fail(self):
        queries = [
            BenchmarkQuery(id="q1", query="honey yield", domain="bee_qa", expected_route="model_based"),
            BenchmarkQuery(id="q2", query="random stuff", domain="fallback", expected_route="model_based"),
        ]
        harness = BenchmarkHarness(queries=queries)
        report = asyncio.run(harness.run(_route_matching_handler))
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 1)


class TestBenchmarkPassRate(unittest.TestCase):
    def test_pass_rate_all_passed(self):
        report = BenchmarkReport(total=5, passed=5)
        self.assertAlmostEqual(report.pass_rate(), 1.0)

    def test_pass_rate_none_passed(self):
        report = BenchmarkReport(total=5, passed=0)
        self.assertAlmostEqual(report.pass_rate(), 0.0)

    def test_pass_rate_empty(self):
        report = BenchmarkReport(total=0, passed=0)
        self.assertAlmostEqual(report.pass_rate(), 0.0)


class TestBenchmarkCompare(unittest.TestCase):
    def test_compare_reports(self):
        before = BenchmarkReport(total=10, passed=7, avg_latency_ms=100.0)
        after = BenchmarkReport(total=10, passed=9, avg_latency_ms=80.0)
        diff = BenchmarkHarness.compare(before, after)
        self.assertAlmostEqual(diff["pass_rate_delta"], 0.2)
        self.assertAlmostEqual(diff["latency_delta_ms"], -20.0)
        self.assertEqual(diff["before_passed"], 7)
        self.assertEqual(diff["after_passed"], 9)


class TestBenchmarkSaveReport(unittest.TestCase):
    def test_save_and_load(self):
        report = BenchmarkReport(total=2, passed=1, failed=1, avg_latency_ms=42.0)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "report.json")
            BenchmarkHarness.save_report(report, path)
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["passed"], 1)
            self.assertAlmostEqual(data["avg_latency_ms"], 42.0)


class TestBenchmarkFromYaml(unittest.TestCase):
    def test_from_yaml_loads_queries(self):
        yaml_content = """\
queries:
  - id: test1
    query: "What is honey?"
    domain: bee_qa
    expected_route: model_based
    language: en
  - id: test2
    query: "Paljonko?"
    domain: bilingual
    language: fi
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        try:
            harness = BenchmarkHarness.from_yaml(path)
            self.assertEqual(len(harness.queries), 2)
            self.assertEqual(harness.queries[0].id, "test1")
            self.assertEqual(harness.queries[0].domain, "bee_qa")
            self.assertEqual(harness.queries[1].language, "fi")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
