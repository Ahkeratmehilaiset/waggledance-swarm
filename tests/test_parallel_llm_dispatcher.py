"""Tests for ParallelLLMDispatcher — P4 test group 1.

Covers:
- Flag OFF = old behavior (passthrough)
- Flag ON = bounded concurrency
- Semaphore limits
- Timeout handling
- Cancellation cleanup
- Dedup identical prompts
- Fallback to sequential/default
- Batch dispatch
- Metrics
"""

import asyncio
import pytest

from waggledance.application.services.parallel_llm_dispatcher import (
    ParallelLLMDispatcher,
    ModelLine,
)


# ── Stubs ────────────────────────────────────────────────────────


class StubSettings:
    """Minimal settings stub for dispatcher tests."""

    def __init__(self, **kwargs):
        self.llm_parallel_enabled = kwargs.get("enabled", False)
        self.llm_parallel_max_concurrent = kwargs.get("max_concurrent", 4)
        self.llm_parallel_max_inflight_per_model = kwargs.get("max_per_model", 2)
        self.llm_parallel_request_timeout_s = kwargs.get("timeout", 120)
        self.llm_parallel_dedupe = kwargs.get("dedupe", True)
        self.llm_parallel_round_table_first_pass = kwargs.get("rt_first", False)
        self.llm_parallel_candidate_lab = kwargs.get("candidate_lab", 1)


class StubLLM:
    """Stub LLM adapter that records calls and returns canned responses."""

    def __init__(self, response="stub response", delay=0.0, fail_after=None):
        self.calls = []
        self.response = response
        self.delay = delay
        self.fail_after = fail_after
        self._call_count = 0

    async def generate(self, prompt, model="default", temperature=0.7, max_tokens=1000):
        self._call_count += 1
        if self.fail_after is not None and self._call_count > self.fail_after:
            raise RuntimeError("Stub LLM failure")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append({
            "prompt": prompt,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return self.response

    async def is_available(self):
        return True

    def get_active_model(self):
        return "stub"


class StubGemmaRouter:
    """Stub Gemma router that records calls."""

    def __init__(self, enabled=False, response="gemma response"):
        self.enabled = enabled
        self.response = response
        self.calls = []

    async def generate(self, prompt, tier=None, temperature=0.7, max_tokens=1000):
        self.calls.append({
            "prompt": prompt,
            "tier": tier,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return self.response


# ── Test Group 1: Flag OFF = old behavior ────────────────────────


class TestFlagOff:
    """When flag is OFF, dispatcher is a transparent passthrough."""

    @pytest.mark.asyncio
    async def test_dispatch_passthrough_when_disabled(self):
        llm = StubLLM(response="hello")
        d = ParallelLLMDispatcher(StubSettings(enabled=False), llm)
        assert not d.enabled
        result = await d.dispatch("test prompt")
        assert result == "hello"
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_batch_sequential_when_disabled(self):
        llm = StubLLM(response="batch response")
        d = ParallelLLMDispatcher(StubSettings(enabled=False), llm)
        results = await d.dispatch_batch([
            ("p1", "default", 0.7, 100),
            ("p2", "default", 0.7, 100),
            ("p3", "default", 0.7, 100),
        ])
        assert len(results) == 3
        assert all(r == "batch response" for r in results)
        assert len(llm.calls) == 3

    @pytest.mark.asyncio
    async def test_metrics_disabled(self):
        llm = StubLLM()
        d = ParallelLLMDispatcher(StubSettings(enabled=False), llm)
        m = d.get_metrics()
        assert m["enabled"] is False

    @pytest.mark.asyncio
    async def test_gemma_router_used_when_disabled_flag(self):
        """Even when parallel is OFF, if gemma router is enabled it's used."""
        llm = StubLLM(response="llm fallback")
        gemma = StubGemmaRouter(enabled=True, response="gemma direct")
        d = ParallelLLMDispatcher(StubSettings(enabled=False), llm, gemma_router=gemma)
        result = await d.dispatch("test")
        # When disabled, direct call goes through gemma if enabled
        assert result == "gemma direct"


# ── Test Group 2: Flag ON = bounded concurrency ─────────────────


class TestFlagOn:
    """When flag is ON, dispatcher enforces bounded concurrency."""

    @pytest.mark.asyncio
    async def test_dispatch_single_call(self):
        llm = StubLLM(response="parallel")
        d = ParallelLLMDispatcher(StubSettings(enabled=True), llm)
        assert d.enabled
        result = await d.dispatch("test prompt")
        assert result == "parallel"

    @pytest.mark.asyncio
    async def test_batch_parallel(self):
        llm = StubLLM(response="batch", delay=0.01)
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, max_concurrent=4), llm)
        results = await d.dispatch_batch([
            ("p1", "default", 0.7, 100),
            ("p2", "default", 0.7, 100),
            ("p3", "default", 0.7, 100),
            ("p4", "default", 0.7, 100),
        ])
        assert len(results) == 4
        assert all(r == "batch" for r in results)

    @pytest.mark.asyncio
    async def test_metrics_enabled(self):
        llm = StubLLM()
        d = ParallelLLMDispatcher(StubSettings(enabled=True), llm)
        await d.dispatch("test")
        m = d.get_metrics()
        assert m["enabled"] is True
        assert m["total_dispatched"] >= 1
        assert m["total_completed"] >= 1

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        llm = StubLLM()
        d = ParallelLLMDispatcher(StubSettings(enabled=True), llm)
        results = await d.dispatch_batch([])
        assert results == []


# ── Test Group 3: Semaphore limits ───────────────────────────────


class TestSemaphoreLimits:
    """Test that semaphores actually bound concurrency."""

    @pytest.mark.asyncio
    async def test_max_concurrent_respected(self):
        """With max_concurrent=2, at most 2 calls run simultaneously."""
        peak_concurrent = 0
        current_concurrent = 0

        class TrackedLLM(StubLLM):
            async def generate(self, prompt, **kwargs):
                nonlocal peak_concurrent, current_concurrent
                current_concurrent += 1
                peak_concurrent = max(peak_concurrent, current_concurrent)
                await asyncio.sleep(0.05)
                current_concurrent -= 1
                return "ok"

        llm = TrackedLLM()
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, max_concurrent=2, max_per_model=2), llm)
        results = await d.dispatch_batch([
            ("p1", "default", 0.7, 100),
            ("p2", "default", 0.7, 100),
            ("p3", "default", 0.7, 100),
            ("p4", "default", 0.7, 100),
        ])
        assert len(results) == 4
        assert peak_concurrent <= 2

    @pytest.mark.asyncio
    async def test_per_model_limit(self):
        """Per-model semaphore limits inflight per line."""
        peak_per_model = 0
        current_per_model = 0

        class TrackedLLM(StubLLM):
            async def generate(self, prompt, **kwargs):
                nonlocal peak_per_model, current_per_model
                current_per_model += 1
                peak_per_model = max(peak_per_model, current_per_model)
                await asyncio.sleep(0.05)
                current_per_model -= 1
                return "ok"

        llm = TrackedLLM()
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, max_concurrent=10, max_per_model=1), llm)
        results = await d.dispatch_batch([
            ("p1", "default", 0.7, 100),
            ("p2", "default", 0.7, 100),
            ("p3", "default", 0.7, 100),
        ])
        assert len(results) == 3
        assert peak_per_model <= 1


# ── Test Group 4: Timeout handling ───────────────────────────────


class TestTimeout:
    """Timeout handling returns empty string, records metric."""

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        llm = StubLLM(response="slow", delay=5.0)
        settings = StubSettings(enabled=True, timeout=120)
        d = ParallelLLMDispatcher(settings, llm)
        # Override the timeout to a very short value for testing
        d._timeout_s = 0.2
        result = await d.dispatch("slow prompt")
        assert result == ""
        assert d.get_metrics()["timeout_count"] >= 1


# ── Test Group 5: Dedup identical prompts ────────────────────────


class TestDedup:
    """Dedup deduplicates identical concurrent prompts."""

    @pytest.mark.asyncio
    async def test_dedup_enabled(self):
        """Two identical prompts dispatched concurrently → LLM called once."""
        call_count = 0

        class CountingLLM(StubLLM):
            async def generate(self, prompt, **kwargs):
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.1)
                return "deduped"

        llm = CountingLLM()
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, dedupe=True, max_concurrent=4), llm)

        # Dispatch two identical requests concurrently
        r1, r2 = await asyncio.gather(
            d.dispatch("same prompt", model_hint="default", temperature=0.7, max_tokens=100),
            d.dispatch("same prompt", model_hint="default", temperature=0.7, max_tokens=100),
        )
        assert r1 == "deduped"
        assert r2 == "deduped"
        # Dedup should have caught the second one (or at least not doubled calls)
        assert d.get_metrics()["deduped_requests"] >= 1

    @pytest.mark.asyncio
    async def test_dedup_disabled(self):
        """When dedupe=false, identical prompts are dispatched separately."""
        llm = StubLLM(response="no dedup", delay=0.05)
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, dedupe=False, max_concurrent=4), llm)
        await asyncio.gather(
            d.dispatch("same prompt"),
            d.dispatch("same prompt"),
        )
        assert d.get_metrics()["deduped_requests"] == 0

    @pytest.mark.asyncio
    async def test_different_prompts_not_deduped(self):
        llm = StubLLM(response="unique", delay=0.05)
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, dedupe=True, max_concurrent=4), llm)
        r1, r2 = await asyncio.gather(
            d.dispatch("prompt A"),
            d.dispatch("prompt B"),
        )
        assert r1 == "unique"
        assert r2 == "unique"
        assert d.get_metrics()["deduped_requests"] == 0


# ── Test Group 6: Fallback to sequential ─────────────────────────


class TestFallback:
    """Errors degrade to sequential direct call."""

    @pytest.mark.asyncio
    async def test_error_degrades_to_direct(self):
        """If guarded call fails, dispatcher falls back to direct call."""

        class FailThenSucceedLLM(StubLLM):
            def __init__(self):
                super().__init__()
                self._call_count = 0

            async def generate(self, prompt, **kwargs):
                self._call_count += 1
                self.calls.append({"prompt": prompt})
                # First call fails (inside guarded), second call succeeds (direct)
                if self._call_count == 1:
                    raise RuntimeError("transient error")
                return "recovered"

        llm = FailThenSucceedLLM()
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True, dedupe=False), llm)
        result = await d.dispatch("test fallback")
        assert result == "recovered"
        assert d.get_metrics()["degrade_to_sequential_count"] >= 1


# ── Test Group 7: ModelLine unit tests ───────────────────────────


class TestModelLine:
    """Test ModelLine semaphore wrapper."""

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        line = ModelLine("test", 2)
        assert line.inflight == 0
        await line.acquire()
        assert line.inflight == 1
        await line.acquire()
        assert line.inflight == 2
        line.release()
        assert line.inflight == 1
        line.release()
        assert line.inflight == 0

    @pytest.mark.asyncio
    async def test_release_never_negative(self):
        line = ModelLine("test", 1)
        line.release()  # release without acquire
        assert line.inflight == 0


# ── Test Group 8: Gemma router integration ───────────────────────


class TestGemmaIntegration:
    """Test dispatcher with Gemma router."""

    @pytest.mark.asyncio
    async def test_gemma_fast_tier(self):
        llm = StubLLM()
        gemma = StubGemmaRouter(enabled=True, response="gemma fast")
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True), llm, gemma_router=gemma)
        result = await d.dispatch("test", tier="fast")
        assert result == "gemma fast"
        assert len(gemma.calls) == 1

    @pytest.mark.asyncio
    async def test_gemma_heavy_tier(self):
        llm = StubLLM()
        gemma = StubGemmaRouter(enabled=True, response="gemma heavy")
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True), llm, gemma_router=gemma)
        result = await d.dispatch("test", tier="heavy")
        assert result == "gemma heavy"

    @pytest.mark.asyncio
    async def test_gemma_disabled_falls_to_llm(self):
        llm = StubLLM(response="llm direct")
        gemma = StubGemmaRouter(enabled=False)
        d = ParallelLLMDispatcher(
            StubSettings(enabled=True), llm, gemma_router=gemma)
        result = await d.dispatch("test")
        assert result == "llm direct"
        assert len(gemma.calls) == 0
