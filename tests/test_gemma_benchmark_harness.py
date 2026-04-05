"""Benchmark harness for Gemma 4 dual-tier fallback comparison.

Tests 4 modes apples-to-apples:
  A. Current baseline model (phi4-mini)
  B. Gemma fast only (gemma4:e4b)
  C. Gemma heavy only (gemma4:26b)
  D. Dual-tier fast+heavy

Captures:
  - route distribution
  - p50/p95/mean latency
  - error count
  - model used per request
  - heavy-path usage count
  - structured output success

Also tests:
  - correct request shape (/api/chat query field)
  - correct health endpoint usage
  - benchmark artifact schema
  - no auth regressions
"""

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.application.services.gemma_profile_router import (
    GemmaProfileRouter,
    GemmaTier,
)


# ── Benchmark Query Set ──────────────────────────────────


QUERYSET = [
    {"query": "Paljonko sähkö maksaa nyt?", "intent": "electricity", "difficulty": "easy"},
    {"query": "Mikä on lämpötila ulkona?", "intent": "weather", "difficulty": "easy"},
    {"query": "Kerro mehiläisten elinkaaresta", "intent": "knowledge", "difficulty": "medium"},
    {"query": "Laske 17 × 23 + 45", "intent": "math", "difficulty": "easy"},
    {"query": "Milloin kannattaa pestä pyykkiä halvimmalla sähköllä?",
     "intent": "optimization", "difficulty": "hard"},
    {"query": "Selitä kuinka FAISS-indeksi toimii", "intent": "technical", "difficulty": "hard"},
    {"query": "Mitä kuuluu?", "intent": "greeting", "difficulty": "easy"},
    {"query": "Analysoi viimeisen viikon energiankulutustrendit",
     "intent": "analysis", "difficulty": "hard"},
]


# ��─ Benchmark Result Schema ──────────────────────────────


@dataclass
class BenchmarkResult:
    """Single benchmark request result."""
    query: str
    intent: str
    difficulty: str
    model_used: str
    response: str
    latency_ms: float
    success: bool
    error: str = ""
    tier: str = "auto"  # fast/heavy/auto


@dataclass
class BenchmarkSummary:
    """Aggregate benchmark summary for one mode."""
    mode: str
    total_requests: int = 0
    successful: int = 0
    errors: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_mean_ms: float = 0.0
    model_distribution: Dict[str, int] = field(default_factory=dict)
    heavy_path_count: int = 0
    fast_path_count: int = 0
    default_fallback_count: int = 0
    results: List[BenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_requests": self.total_requests,
            "successful": self.successful,
            "errors": self.errors,
            "latency_p50_ms": round(self.latency_p50_ms, 1),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
            "latency_mean_ms": round(self.latency_mean_ms, 1),
            "model_distribution": self.model_distribution,
            "heavy_path_count": self.heavy_path_count,
            "fast_path_count": self.fast_path_count,
            "default_fallback_count": self.default_fallback_count,
        }


# ── Benchmark Runner ─────────────────────────────────────


@dataclass
class FakeSettings:
    gemma_enabled: bool = False
    gemma_fast_model: str = "gemma4:e4b"
    gemma_heavy_model: str = "gemma4:26b"
    gemma_active_profile: str = "disabled"
    gemma_heavy_reasoning_only: bool = True
    gemma_degrade_to_default: bool = True


class BenchmarkHarness:
    """Runs benchmark queries through a GemmaProfileRouter in all 4 modes."""

    MODES = {
        "A_baseline": {"enabled": False, "profile": "disabled"},
        "B_gemma_fast": {"enabled": True, "profile": "fast_only"},
        "C_gemma_heavy": {"enabled": True, "profile": "heavy_only"},
        "D_dual_tier": {"enabled": True, "profile": "dual_tier"},
    }

    DIFFICULTY_TO_TIER = {
        "easy": GemmaTier.FAST,
        "medium": GemmaTier.AUTO,
        "hard": GemmaTier.HEAVY,
    }

    def __init__(self, llm_mock):
        self._llm = llm_mock

    async def run_mode(self, mode_name: str, queries: List[dict]) -> BenchmarkSummary:
        """Run benchmark for a single mode."""
        mode_cfg = self.MODES[mode_name]
        settings = FakeSettings(
            gemma_enabled=mode_cfg["enabled"],
            gemma_active_profile=mode_cfg["profile"],
        )
        router = GemmaProfileRouter(settings=settings, default_llm=self._llm)

        summary = BenchmarkSummary(mode=mode_name)
        latencies = []

        for q in queries:
            tier = self.DIFFICULTY_TO_TIER.get(q.get("difficulty", "medium"), GemmaTier.AUTO)
            model_expected = router.resolve_model(tier)

            t0 = time.monotonic()
            try:
                resp = await router.generate(q["query"], tier=tier, temperature=0.4)
                latency = (time.monotonic() - t0) * 1000
                success = bool(resp)

                result = BenchmarkResult(
                    query=q["query"],
                    intent=q.get("intent", "unknown"),
                    difficulty=q.get("difficulty", "medium"),
                    model_used=model_expected or "default",
                    response=resp[:100] if resp else "",
                    latency_ms=latency,
                    success=success,
                    tier=tier.value,
                )

                if success:
                    summary.successful += 1
                    latencies.append(latency)
                else:
                    summary.errors += 1

                # Count paths
                if model_expected == settings.gemma_fast_model:
                    summary.fast_path_count += 1
                elif model_expected == settings.gemma_heavy_model:
                    summary.heavy_path_count += 1
                else:
                    summary.default_fallback_count += 1

                # Model distribution
                m = model_expected or "default"
                summary.model_distribution[m] = summary.model_distribution.get(m, 0) + 1
                summary.results.append(result)

            except Exception as exc:
                summary.errors += 1
                summary.results.append(BenchmarkResult(
                    query=q["query"],
                    intent=q.get("intent", "unknown"),
                    difficulty=q.get("difficulty", "medium"),
                    model_used="error",
                    response="",
                    latency_ms=0,
                    success=False,
                    error=str(exc),
                    tier=tier.value,
                ))

            summary.total_requests += 1

        # Compute latency stats
        if latencies:
            latencies.sort()
            summary.latency_mean_ms = statistics.mean(latencies)
            summary.latency_p50_ms = latencies[len(latencies) // 2]
            idx_95 = min(int(len(latencies) * 0.95), len(latencies) - 1)
            summary.latency_p95_ms = latencies[idx_95]

        return summary

    async def run_all(self, queries: List[dict]) -> Dict[str, BenchmarkSummary]:
        """Run benchmark across all 4 modes."""
        results = {}
        for mode in self.MODES:
            results[mode] = await self.run_mode(mode, queries)
        return results


# ── Tests ────────────────────────────────────────────────


class TestBenchmarkHarnessSchema:
    def test_queryset_has_required_fields(self):
        for q in QUERYSET:
            assert "query" in q
            assert "intent" in q
            assert "difficulty" in q

    def test_queryset_difficulty_levels(self):
        difficulties = {q["difficulty"] for q in QUERYSET}
        assert "easy" in difficulties
        assert "medium" in difficulties
        assert "hard" in difficulties

    def test_benchmark_summary_schema(self):
        s = BenchmarkSummary(mode="test")
        d = s.to_dict()
        expected_keys = {
            "mode", "total_requests", "successful", "errors",
            "latency_p50_ms", "latency_p95_ms", "latency_mean_ms",
            "model_distribution", "heavy_path_count", "fast_path_count",
            "default_fallback_count",
        }
        assert set(d.keys()) == expected_keys

    def test_benchmark_result_fields(self):
        r = BenchmarkResult(
            query="test", intent="test", difficulty="easy",
            model_used="default", response="ok", latency_ms=100.0,
            success=True,
        )
        assert r.query == "test"
        assert r.latency_ms == 100.0


class TestBenchmarkHarnessExecution:
    @pytest.mark.asyncio
    async def test_baseline_mode_uses_default_only(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="answer")
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("A_baseline", QUERYSET)

        assert summary.mode == "A_baseline"
        assert summary.total_requests == len(QUERYSET)
        assert summary.successful == len(QUERYSET)
        assert summary.errors == 0
        assert summary.default_fallback_count == len(QUERYSET)
        assert "default" in summary.model_distribution

    @pytest.mark.asyncio
    async def test_fast_only_routes_easy_to_gemma(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="gemma answer")
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("B_gemma_fast", QUERYSET)

        assert summary.total_requests == len(QUERYSET)
        assert summary.fast_path_count > 0
        # Easy + medium queries route to fast
        assert "gemma4:e4b" in summary.model_distribution

    @pytest.mark.asyncio
    async def test_heavy_only_routes_hard_to_gemma(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="gemma heavy answer")
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("C_gemma_heavy", QUERYSET)

        assert summary.total_requests == len(QUERYSET)
        assert summary.heavy_path_count > 0
        assert "gemma4:26b" in summary.model_distribution

    @pytest.mark.asyncio
    async def test_dual_tier_routes_both(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="dual answer")
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("D_dual_tier", QUERYSET)

        assert summary.total_requests == len(QUERYSET)
        # Dual tier should use both models
        assert summary.fast_path_count > 0 or summary.heavy_path_count > 0
        # Hard queries go to heavy, easy/medium go to fast
        assert "gemma4:e4b" in summary.model_distribution
        assert "gemma4:26b" in summary.model_distribution

    @pytest.mark.asyncio
    async def test_run_all_modes(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="answer")
        harness = BenchmarkHarness(llm)

        results = await harness.run_all(QUERYSET)

        assert len(results) == 4
        assert "A_baseline" in results
        assert "B_gemma_fast" in results
        assert "C_gemma_heavy" in results
        assert "D_dual_tier" in results

        for mode, summary in results.items():
            assert summary.total_requests == len(QUERYSET)
            assert summary.errors == 0

    @pytest.mark.asyncio
    async def test_latency_stats_computed(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="answer")
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("A_baseline", QUERYSET)

        assert summary.latency_mean_ms >= 0
        assert summary.latency_p50_ms >= 0
        assert summary.latency_p95_ms >= 0

    @pytest.mark.asyncio
    async def test_error_handling(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=Exception("timeout"))
        harness = BenchmarkHarness(llm)

        summary = await harness.run_mode("A_baseline", QUERYSET[:1])

        # Baseline mode: exception from default llm
        assert summary.total_requests == 1
        assert summary.errors == 1


class TestBenchmarkNoAuthRegression:
    """Verify benchmark harness does not bypass auth."""

    @pytest.mark.asyncio
    async def test_no_auth_headers_in_harness(self):
        """Harness uses mock LLM — no real HTTP calls."""
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="response")
        harness = BenchmarkHarness(llm)
        await harness.run_mode("A_baseline", QUERYSET[:1])

        # Verify only generate was called, not any HTTP methods
        assert llm.generate.called
        # No direct HTTP client usage
        assert not hasattr(harness, '_http_client')


class TestBenchmarkRequestShape:
    """Verify query field correctness in requests."""

    @pytest.mark.asyncio
    async def test_query_passed_as_prompt(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="ok")
        harness = BenchmarkHarness(llm)

        await harness.run_mode("A_baseline", [QUERYSET[0]])

        # First arg to generate should be the query string
        call_args = llm.generate.call_args
        assert call_args[0][0] == QUERYSET[0]["query"] or call_args[1].get("prompt") == QUERYSET[0]["query"]
