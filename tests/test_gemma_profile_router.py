"""Tests for Gemma 4 dual-tier profile router.

Covers:
- config validation & profile selection
- dual-tier routing logic (fast/heavy/auto)
- graceful degradation when models unavailable
- old fallback path unchanged when Gemma disabled
- status/ops additive fields
- metrics tracking
"""

import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.application.services.gemma_profile_router import (
    GemmaMetrics,
    GemmaProfileRouter,
    GemmaTier,
    _VALID_PROFILES,
)


# ── Fixtures ──────────────────────────────────────────────


@dataclass
class FakeSettings:
    """Minimal settings stub for testing."""
    gemma_enabled: bool = False
    gemma_fast_model: str = "gemma4:e4b"
    gemma_heavy_model: str = "gemma4:26b"
    gemma_active_profile: str = "disabled"
    gemma_heavy_reasoning_only: bool = True
    gemma_degrade_to_default: bool = True
    chat_model: str = "phi4-mini"


def make_llm(response="test response"):
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=response)
    return llm


def make_router(profile="disabled", enabled=True, **kw):
    settings = FakeSettings(
        gemma_enabled=enabled,
        gemma_active_profile=profile,
        **kw,
    )
    llm = make_llm()
    return GemmaProfileRouter(settings=settings, default_llm=llm), llm


# ── Config validation tests ──────────────────────────────


class TestConfigValidation:
    def test_valid_profiles(self):
        assert _VALID_PROFILES == {"disabled", "fast_only", "heavy_only", "dual_tier"}

    def test_disabled_by_default(self):
        router, _ = make_router(profile="disabled", enabled=False)
        assert not router.enabled
        assert router.profile == "disabled"

    def test_enabled_fast_only(self):
        router, _ = make_router(profile="fast_only")
        assert router.enabled
        assert router.profile == "fast_only"

    def test_enabled_heavy_only(self):
        router, _ = make_router(profile="heavy_only")
        assert router.enabled
        assert router.profile == "heavy_only"

    def test_enabled_dual_tier(self):
        router, _ = make_router(profile="dual_tier")
        assert router.enabled
        assert router.profile == "dual_tier"

    def test_invalid_profile_falls_back_to_disabled(self):
        router, _ = make_router(profile="bogus")
        assert not router.enabled
        assert router.profile == "disabled"

    def test_enabled_false_overrides_profile(self):
        router, _ = make_router(profile="dual_tier", enabled=False)
        assert not router.enabled


# ── Profile selection / routing tests ──────────────────────


class TestProfileRouting:
    def test_disabled_returns_empty(self):
        router, _ = make_router(profile="disabled", enabled=False)
        assert router.resolve_model(GemmaTier.FAST) == ""
        assert router.resolve_model(GemmaTier.HEAVY) == ""
        assert router.resolve_model(GemmaTier.AUTO) == ""

    def test_fast_only_fast_tier(self):
        router, _ = make_router(profile="fast_only")
        assert router.resolve_model(GemmaTier.FAST) == "gemma4:e4b"

    def test_fast_only_heavy_tier_returns_empty(self):
        router, _ = make_router(profile="fast_only")
        assert router.resolve_model(GemmaTier.HEAVY) == ""

    def test_fast_only_auto_uses_fast(self):
        router, _ = make_router(profile="fast_only")
        assert router.resolve_model(GemmaTier.AUTO) == "gemma4:e4b"

    def test_heavy_only_heavy_tier(self):
        router, _ = make_router(profile="heavy_only")
        assert router.resolve_model(GemmaTier.HEAVY) == "gemma4:26b"

    def test_heavy_only_fast_tier_returns_empty(self):
        router, _ = make_router(profile="heavy_only")
        assert router.resolve_model(GemmaTier.FAST) == ""

    def test_heavy_only_auto_uses_heavy(self):
        router, _ = make_router(profile="heavy_only")
        assert router.resolve_model(GemmaTier.AUTO) == "gemma4:26b"

    def test_dual_tier_fast(self):
        router, _ = make_router(profile="dual_tier")
        assert router.resolve_model(GemmaTier.FAST) == "gemma4:e4b"

    def test_dual_tier_heavy(self):
        router, _ = make_router(profile="dual_tier")
        assert router.resolve_model(GemmaTier.HEAVY) == "gemma4:26b"

    def test_dual_tier_auto_defaults_to_fast(self):
        router, _ = make_router(profile="dual_tier")
        assert router.resolve_model(GemmaTier.AUTO) == "gemma4:e4b"

    def test_custom_model_tags(self):
        router, _ = make_router(
            profile="dual_tier",
            gemma_fast_model="gemma4:2b",
            gemma_heavy_model="gemma4:12b",
        )
        assert router.resolve_model(GemmaTier.FAST) == "gemma4:2b"
        assert router.resolve_model(GemmaTier.HEAVY) == "gemma4:12b"


# ── Generation tests ──────────────────────────────────────


class TestGeneration:
    @pytest.mark.asyncio
    async def test_disabled_uses_default_llm(self):
        router, llm = make_router(profile="disabled", enabled=False)
        result = await router.generate("hello")
        assert result == "test response"
        llm.generate.assert_called_once_with(
            "hello", temperature=0.7, max_tokens=1000,
        )

    @pytest.mark.asyncio
    async def test_fast_only_passes_model_override(self):
        router, llm = make_router(profile="fast_only")
        result = await router.generate("hello", tier=GemmaTier.FAST)
        assert result == "test response"
        llm.generate.assert_called_once_with(
            "hello", model="gemma4:e4b", temperature=0.7, max_tokens=1000,
        )

    @pytest.mark.asyncio
    async def test_heavy_tier_passes_heavy_model(self):
        router, llm = make_router(profile="dual_tier")
        result = await router.generate("hard problem", tier=GemmaTier.HEAVY)
        assert result == "test response"
        llm.generate.assert_called_once_with(
            "hard problem", model="gemma4:26b", temperature=0.7, max_tokens=1000,
        )

    @pytest.mark.asyncio
    async def test_temperature_and_max_tokens_passthrough(self):
        router, llm = make_router(profile="fast_only")
        await router.generate("q", tier=GemmaTier.FAST, temperature=0.1, max_tokens=50)
        llm.generate.assert_called_once_with(
            "q", model="gemma4:e4b", temperature=0.1, max_tokens=50,
        )


# ── Degradation tests ────────────────────────────────────


class TestDegradation:
    @pytest.mark.asyncio
    async def test_fast_degraded_falls_back_to_default(self):
        router, llm = make_router(profile="fast_only")
        # Simulate degradation
        router._metrics.gemma_fast_degraded = True
        router._metrics._fast_degraded_since = time.monotonic()

        result = await router.generate("hello", tier=GemmaTier.FAST)
        assert result == "test response"
        # Should call default (no model override)
        llm.generate.assert_called_once_with(
            "hello", temperature=0.7, max_tokens=1000,
        )
        assert router._metrics.default_fallback_calls == 1

    @pytest.mark.asyncio
    async def test_heavy_degraded_falls_back_to_default(self):
        router, llm = make_router(profile="dual_tier")
        router._metrics.gemma_heavy_degraded = True
        router._metrics._heavy_degraded_since = time.monotonic()

        result = await router.generate("problem", tier=GemmaTier.HEAVY)
        assert result == "test response"
        llm.generate.assert_called_once_with(
            "problem", temperature=0.7, max_tokens=1000,
        )

    @pytest.mark.asyncio
    async def test_degraded_auto_recovers(self):
        router, llm = make_router(profile="fast_only")
        # Degrade then wait past recovery window
        router._metrics.gemma_fast_degraded = True
        router._metrics._fast_degraded_since = time.monotonic() - 200  # > 120s ago

        model = router.resolve_model(GemmaTier.FAST)
        assert model == "gemma4:e4b"  # Recovered
        assert not router._metrics.gemma_fast_degraded

    @pytest.mark.asyncio
    async def test_exception_marks_degraded_and_falls_back(self):
        router, llm = make_router(profile="fast_only")
        llm.generate = AsyncMock(side_effect=[Exception("model not found"), "fallback ok"])

        result = await router.generate("hello", tier=GemmaTier.FAST)
        assert result == "fallback ok"
        assert router._metrics.gemma_fast_degraded
        assert router._metrics.default_fallback_calls == 1

    @pytest.mark.asyncio
    async def test_empty_response_marks_degraded(self):
        router, llm = make_router(profile="fast_only")
        llm.generate = AsyncMock(side_effect=["", "fallback ok"])

        result = await router.generate("hello", tier=GemmaTier.FAST)
        assert result == "fallback ok"
        assert router._metrics.gemma_fast_degraded

    @pytest.mark.asyncio
    async def test_no_degrade_to_default_returns_empty(self):
        router, llm = make_router(profile="fast_only", gemma_degrade_to_default=False)
        llm.generate = AsyncMock(side_effect=Exception("fail"))

        result = await router.generate("hello", tier=GemmaTier.FAST)
        assert result == ""
        assert router._metrics.default_fallback_calls == 0


# ── Metrics tests ─────────────────────────────────────────


class TestMetrics:
    @pytest.mark.asyncio
    async def test_fast_call_counted(self):
        router, llm = make_router(profile="fast_only")
        await router.generate("a", tier=GemmaTier.FAST)
        m = router.get_metrics()
        assert m["fast_model_calls"] == 1
        assert m["heavy_model_calls"] == 0

    @pytest.mark.asyncio
    async def test_heavy_call_counted_with_reasoning(self):
        router, llm = make_router(profile="dual_tier")
        await router.generate("hard", tier=GemmaTier.HEAVY)
        m = router.get_metrics()
        assert m["heavy_model_calls"] == 1
        assert m["heavy_reasoning_calls"] == 1

    def test_metrics_shape(self):
        router, _ = make_router(profile="dual_tier")
        m = router.get_metrics()
        expected_keys = {
            "enabled", "active_profile", "active_fast_model", "active_heavy_model",
            "fast_model_calls", "heavy_model_calls", "heavy_reasoning_calls",
            "default_fallback_calls", "gemma_fast_degraded", "gemma_heavy_degraded",
        }
        assert set(m.keys()) == expected_keys

    def test_disabled_metrics(self):
        router, _ = make_router(profile="disabled", enabled=False)
        m = router.get_metrics()
        assert not m["enabled"]
        assert m["active_fast_model"] == ""
        assert m["active_heavy_model"] == ""


# ── Old fallback path unchanged ───────────────────────────


class TestOldPathUnchanged:
    @pytest.mark.asyncio
    async def test_default_llm_not_affected_when_gemma_off(self):
        """When Gemma is disabled, default LLM path must be identical."""
        settings = FakeSettings(gemma_enabled=False, gemma_active_profile="disabled")
        llm = make_llm("original response")
        router = GemmaProfileRouter(settings=settings, default_llm=llm)

        result = await router.generate("test query")
        assert result == "original response"
        # Must use default path (no model override)
        llm.generate.assert_called_once_with(
            "test query", temperature=0.7, max_tokens=1000,
        )

    @pytest.mark.asyncio
    async def test_all_tiers_use_default_when_disabled(self):
        settings = FakeSettings(gemma_enabled=False)
        llm = make_llm("default")
        router = GemmaProfileRouter(settings=settings, default_llm=llm)

        for tier in GemmaTier:
            llm.reset_mock()
            result = await router.generate("q", tier=tier)
            assert result == "default"
