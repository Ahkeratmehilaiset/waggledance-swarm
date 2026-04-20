"""Benchmark harness — run predefined queries and measure system performance."""

import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

@dataclass
class BenchmarkQuery:
    id: str
    query: str
    domain: str  # "bee_qa", "bilingual", "correction", "sensor", "fallback"
    expected_route: str = ""
    expected_keywords: list[str] = field(default_factory=list)
    language: str = "auto"

@dataclass
class BenchmarkResult:
    query_id: str
    response: str = ""
    route_type: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    matched_expected: bool = False
    error: str = ""

@dataclass
class BenchmarkReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    avg_latency_ms: float = 0.0
    results: list[BenchmarkResult] = field(default_factory=list)

    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

class BenchmarkHarness:
    def __init__(self, queries: list[BenchmarkQuery] | None = None):
        self.queries = queries or []

    @classmethod
    def from_yaml(cls, path: str) -> "BenchmarkHarness":
        """Load queries from a YAML config file."""
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        queries = [BenchmarkQuery(**q) for q in data.get("queries", [])]
        return cls(queries=queries)

    async def run(self, handler: Callable[[str], Awaitable[dict]]) -> BenchmarkReport:
        """Run all queries through the handler and collect results."""
        report = BenchmarkReport(total=len(self.queries))
        for q in self.queries:
            t0 = time.perf_counter()
            try:
                resp = await handler(q.query)
                ms = (time.perf_counter() - t0) * 1000
                route = resp.get("route_type", "")
                confidence = resp.get("confidence", 0.0)
                matched = route == q.expected_route if q.expected_route else True
                result = BenchmarkResult(
                    query_id=q.id, response=resp.get("response", ""),
                    route_type=route, confidence=confidence,
                    latency_ms=ms, matched_expected=matched)
                if matched:
                    report.passed += 1
                else:
                    report.failed += 1
            except Exception as e:
                ms = (time.perf_counter() - t0) * 1000
                result = BenchmarkResult(query_id=q.id, latency_ms=ms, error=str(e))
                report.errors += 1
            report.results.append(result)

        latencies = [r.latency_ms for r in report.results]
        report.avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0
        return report

    @staticmethod
    def compare(before: BenchmarkReport, after: BenchmarkReport) -> dict:
        """Compare two benchmark reports."""
        return {
            "pass_rate_delta": after.pass_rate() - before.pass_rate(),
            "latency_delta_ms": after.avg_latency_ms - before.avg_latency_ms,
            "before_passed": before.passed,
            "after_passed": after.passed,
        }

    @staticmethod
    def save_report(report: BenchmarkReport, path: str) -> None:
        """Save report as JSON."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)
