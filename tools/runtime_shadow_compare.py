"""Runtime shadow compare — validate new runtime against old by running both."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Awaitable, Optional

log = logging.getLogger(__name__)


@dataclass
class CompareRequest:
    query: str
    language: str = "auto"
    profile: str = "COTTAGE"


@dataclass
class CompareResult:
    query: str
    old_response: str = ""
    new_response: str = ""
    old_route: str = ""
    new_route: str = ""
    old_latency_ms: float = 0.0
    new_latency_ms: float = 0.0
    old_confidence: float = 0.0
    new_confidence: float = 0.0
    match: bool = False
    error: str = ""


@dataclass
class ShadowReport:
    mode: str = "shadow"  # "shadow", "canary", "validate"
    total_requests: int = 0
    route_matches: int = 0
    response_matches: int = 0
    errors: int = 0
    avg_old_latency_ms: float = 0.0
    avg_new_latency_ms: float = 0.0
    route_distribution: dict[str, int] = field(default_factory=dict)
    results: list[CompareResult] = field(default_factory=list)

    @property
    def route_match_rate(self) -> float:
        return self.route_matches / self.total_requests if self.total_requests else 0.0

    @property
    def response_match_rate(self) -> float:
        return self.response_matches / self.total_requests if self.total_requests else 0.0


class RuntimeShadowCompare:
    """Compare old and new runtime side-by-side."""

    def __init__(self, mode: str = "shadow"):
        assert mode in ("shadow", "canary", "validate"), f"Invalid mode: {mode}"
        self.mode = mode
        self._results: list[CompareResult] = []

    async def compare_single(
        self,
        request: CompareRequest,
        old_handler: Callable[[str], Awaitable[dict]],
        new_handler: Callable[[str], Awaitable[dict]],
    ) -> CompareResult:
        """Run a single query through both handlers and compare."""
        result = CompareResult(query=request.query)

        try:
            t0 = time.monotonic()
            old_resp = await old_handler(request.query)
            result.old_latency_ms = (time.monotonic() - t0) * 1000
            result.old_response = old_resp.get("response", "")
            result.old_route = old_resp.get("route_type", "")
            result.old_confidence = old_resp.get("confidence", 0.0)
        except Exception as e:
            result.error = f"old_handler: {e}"

        try:
            t0 = time.monotonic()
            new_resp = await new_handler(request.query)
            result.new_latency_ms = (time.monotonic() - t0) * 1000
            result.new_response = new_resp.get("response", "")
            result.new_route = new_resp.get("route_type", "")
            result.new_confidence = new_resp.get("confidence", 0.0)
        except Exception as e:
            result.error = f"new_handler: {e}"

        result.match = (result.old_route == result.new_route)
        self._results.append(result)
        return result

    async def run_corpus(
        self,
        queries: list[CompareRequest],
        old_handler: Callable[[str], Awaitable[dict]],
        new_handler: Callable[[str], Awaitable[dict]],
    ) -> ShadowReport:
        """Run a full test corpus and generate report."""
        report = ShadowReport(mode=self.mode, total_requests=len(queries))

        for q in queries:
            result = await self.compare_single(q, old_handler, new_handler)
            report.results.append(result)

            if result.error:
                report.errors += 1
            if result.match:
                report.route_matches += 1
            if result.old_response == result.new_response:
                report.response_matches += 1

            route = result.new_route or "unknown"
            report.route_distribution[route] = report.route_distribution.get(route, 0) + 1

        old_lats = [r.old_latency_ms for r in report.results if r.old_latency_ms > 0]
        new_lats = [r.new_latency_ms for r in report.results if r.new_latency_ms > 0]
        report.avg_old_latency_ms = sum(old_lats) / len(old_lats) if old_lats else 0
        report.avg_new_latency_ms = sum(new_lats) / len(new_lats) if new_lats else 0

        return report

    def health_check(self) -> dict:
        """Return health status of the comparison tool."""
        return {
            "mode": self.mode,
            "results_collected": len(self._results),
            "status": "ready",
        }

    @staticmethod
    def save_report(report: ShadowReport, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)
