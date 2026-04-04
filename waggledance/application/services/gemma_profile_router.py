"""Gemma 4 dual-tier profile router — optional model routing for Gemma fast/heavy.

Routes LLM requests to the appropriate Gemma model based on:
- Active profile (disabled / fast_only / heavy_only / dual_tier)
- Request tier hint (fast / heavy / auto)
- Model availability (graceful degradation to default)

When disabled, the router is a transparent pass-through to the default LLM.

Metrics are exposed via get_metrics() for /api/status and /api/ops.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

_VALID_PROFILES = frozenset({"disabled", "fast_only", "heavy_only", "dual_tier"})


class GemmaTier(str, Enum):
    FAST = "fast"
    HEAVY = "heavy"
    AUTO = "auto"


@dataclass
class GemmaMetrics:
    """Runtime metrics for Gemma profile routing."""
    fast_model_calls: int = 0
    heavy_model_calls: int = 0
    heavy_reasoning_calls: int = 0
    default_fallback_calls: int = 0
    gemma_fast_degraded: bool = False
    gemma_heavy_degraded: bool = False
    active_fast_model: str = ""
    active_heavy_model: str = ""
    active_profile: str = "disabled"
    last_fast_error: str = ""
    last_heavy_error: str = ""
    _fast_degraded_since: float = 0.0
    _heavy_degraded_since: float = 0.0


class GemmaProfileRouter:
    """Routes LLM calls through Gemma fast/heavy models when enabled.

    Usage::

        router = GemmaProfileRouter(settings, default_llm)
        # Fast path (tool/agent/fallback):
        response = await router.generate(prompt, tier=GemmaTier.FAST)
        # Heavy path (reasoning/candidate-lab/verifier):
        response = await router.generate(prompt, tier=GemmaTier.HEAVY)
        # Auto (router decides based on profile):
        response = await router.generate(prompt, tier=GemmaTier.AUTO)
    """

    # Seconds before retrying a degraded model
    DEGRADED_RECOVERY_SECONDS = 120

    def __init__(self, settings, default_llm):
        self._settings = settings
        self._default_llm = default_llm
        self._metrics = GemmaMetrics()

        # Resolve profile
        profile = getattr(settings, "gemma_active_profile", "disabled")
        if profile not in _VALID_PROFILES:
            logger.warning("Invalid gemma_active_profile '%s', using 'disabled'", profile)
            profile = "disabled"

        self._enabled = getattr(settings, "gemma_enabled", False) and profile != "disabled"
        self._profile = profile
        self._fast_model = getattr(settings, "gemma_fast_model", "gemma4:e4b")
        self._heavy_model = getattr(settings, "gemma_heavy_model", "gemma4:26b")
        self._heavy_reasoning_only = getattr(settings, "gemma_heavy_reasoning_only", True)
        self._degrade_to_default = getattr(settings, "gemma_degrade_to_default", True)

        # Populate metrics identity
        self._metrics.active_profile = self._profile
        self._metrics.active_fast_model = self._fast_model if self._enabled else ""
        self._metrics.active_heavy_model = self._heavy_model if self._enabled else ""

        if self._enabled:
            logger.info(
                "GemmaProfileRouter enabled: profile=%s fast=%s heavy=%s",
                self._profile, self._fast_model, self._heavy_model,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def profile(self) -> str:
        return self._profile

    def resolve_model(self, tier: GemmaTier = GemmaTier.AUTO) -> str:
        """Resolve which model name to use for a given tier hint.

        Returns the Ollama model tag string, or empty string to use default.
        """
        if not self._enabled:
            return ""  # Use default

        if tier == GemmaTier.FAST:
            return self._resolve_fast()
        elif tier == GemmaTier.HEAVY:
            return self._resolve_heavy()
        else:  # AUTO
            return self._resolve_auto()

    def _resolve_fast(self) -> str:
        if self._profile in ("fast_only", "dual_tier"):
            if not self._is_fast_degraded():
                return self._fast_model
            if self._degrade_to_default:
                self._metrics.default_fallback_calls += 1
                return ""  # Fall back to default
        return ""

    def _resolve_heavy(self) -> str:
        if self._profile in ("heavy_only", "dual_tier"):
            if not self._is_heavy_degraded():
                return self._heavy_model
            if self._degrade_to_default:
                self._metrics.default_fallback_calls += 1
                return ""
        return ""

    def _resolve_auto(self) -> str:
        if self._profile == "fast_only":
            return self._resolve_fast()
        elif self._profile == "heavy_only":
            return self._resolve_heavy()
        elif self._profile == "dual_tier":
            # Default auto behavior: use fast model
            # Heavy is only used when explicitly requested
            return self._resolve_fast()
        return ""

    async def generate(
        self,
        prompt: str,
        tier: GemmaTier = GemmaTier.AUTO,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Generate via the appropriate model based on tier and profile.

        Falls back to default LLM when Gemma is disabled or degraded.
        """
        model = self.resolve_model(tier)

        if not model:
            # Use default LLM
            return await self._default_llm.generate(
                prompt, temperature=temperature, max_tokens=max_tokens,
            )

        # Use Gemma model via default LLM adapter (model override)
        try:
            result = await self._default_llm.generate(
                prompt, model=model, temperature=temperature, max_tokens=max_tokens,
            )
            # Record success
            if model == self._fast_model:
                self._metrics.fast_model_calls += 1
                self._clear_fast_degraded()
            elif model == self._heavy_model:
                self._metrics.heavy_model_calls += 1
                if tier == GemmaTier.HEAVY:
                    self._metrics.heavy_reasoning_calls += 1
                self._clear_heavy_degraded()

            if not result:
                # Empty response — mark degraded
                self._mark_degraded(model, "empty response")
                if self._degrade_to_default:
                    self._metrics.default_fallback_calls += 1
                    return await self._default_llm.generate(
                        prompt, temperature=temperature, max_tokens=max_tokens,
                    )
            return result

        except Exception as exc:
            self._mark_degraded(model, str(exc))
            logger.warning("Gemma model %s failed: %s, falling back", model, exc)
            if self._degrade_to_default:
                self._metrics.default_fallback_calls += 1
                return await self._default_llm.generate(
                    prompt, temperature=temperature, max_tokens=max_tokens,
                )
            return ""

    def _mark_degraded(self, model: str, error: str):
        now = time.monotonic()
        if model == self._fast_model:
            self._metrics.gemma_fast_degraded = True
            self._metrics._fast_degraded_since = now
            self._metrics.last_fast_error = error
            logger.warning("Gemma fast model degraded: %s", error)
        elif model == self._heavy_model:
            self._metrics.gemma_heavy_degraded = True
            self._metrics._heavy_degraded_since = now
            self._metrics.last_heavy_error = error
            logger.warning("Gemma heavy model degraded: %s", error)

    def _clear_fast_degraded(self):
        if self._metrics.gemma_fast_degraded:
            self._metrics.gemma_fast_degraded = False
            self._metrics._fast_degraded_since = 0.0
            self._metrics.last_fast_error = ""
            logger.info("Gemma fast model recovered")

    def _clear_heavy_degraded(self):
        if self._metrics.gemma_heavy_degraded:
            self._metrics.gemma_heavy_degraded = False
            self._metrics._heavy_degraded_since = 0.0
            self._metrics.last_heavy_error = ""
            logger.info("Gemma heavy model recovered")

    def _is_fast_degraded(self) -> bool:
        if not self._metrics.gemma_fast_degraded:
            return False
        # Auto-recover after DEGRADED_RECOVERY_SECONDS
        if (time.monotonic() - self._metrics._fast_degraded_since
                > self.DEGRADED_RECOVERY_SECONDS):
            self._clear_fast_degraded()
            return False
        return True

    def _is_heavy_degraded(self) -> bool:
        if not self._metrics.gemma_heavy_degraded:
            return False
        if (time.monotonic() - self._metrics._heavy_degraded_since
                > self.DEGRADED_RECOVERY_SECONDS):
            self._clear_heavy_degraded()
            return False
        return True

    def get_metrics(self) -> dict:
        """Return metrics for /api/status and /api/ops."""
        return {
            "enabled": self._enabled,
            "active_profile": self._profile,
            "active_fast_model": self._metrics.active_fast_model,
            "active_heavy_model": self._metrics.active_heavy_model,
            "fast_model_calls": self._metrics.fast_model_calls,
            "heavy_model_calls": self._metrics.heavy_model_calls,
            "heavy_reasoning_calls": self._metrics.heavy_reasoning_calls,
            "default_fallback_calls": self._metrics.default_fallback_calls,
            "gemma_fast_degraded": self._metrics.gemma_fast_degraded,
            "gemma_heavy_degraded": self._metrics.gemma_heavy_degraded,
        }
