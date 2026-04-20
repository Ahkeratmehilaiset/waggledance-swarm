"""Parallel LLM Dispatcher — bounded concurrent LLM call orchestration.

Provides a feature-flagged parallel dispatch layer over the existing LLM/Gemma
infrastructure. When disabled, all calls fall through to the underlying LLM
adapter sequentially (zero behavior change).

When enabled:
- Global asyncio semaphore bounds total concurrent LLM calls
- Per-model semaphores (fast/heavy/default) bound per-line concurrency
- Identical prompt deduplication avoids redundant work
- Per-request timeout with proper cancellation cleanup
- Metrics exposed for /api/status and /api/ops
- Graceful degradation to sequential on any failure

Design principles:
- Additive only: old behavior is the default
- Solver-first is untouched: parallelism only applies to independent LLM calls
- No new nondeterministic side effects on production routing
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DispatchMetrics:
    """Runtime metrics for parallel LLM dispatch."""
    enabled: bool = False
    inflight_total: int = 0
    inflight_fast: int = 0
    inflight_heavy: int = 0
    inflight_default: int = 0
    completed_parallel_batches: int = 0
    total_dispatched: int = 0
    total_completed: int = 0
    timeout_count: int = 0
    cancelled_count: int = 0
    deduped_requests: int = 0
    degrade_to_sequential_count: int = 0
    queue_depth: int = 0


class ModelLine:
    """Tracks per-model concurrency via a named semaphore."""

    FAST = "fast"
    HEAVY = "heavy"
    DEFAULT = "default"

    def __init__(self, name: str, max_inflight: int):
        self.name = name
        self.max_inflight = max_inflight
        self._semaphore = asyncio.Semaphore(max_inflight)
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._inflight += 1

    def release(self) -> None:
        self._inflight = max(0, self._inflight - 1)
        self._semaphore.release()


class ParallelLLMDispatcher:
    """Bounded concurrent LLM dispatch with per-model lines and dedup.

    Usage::

        dispatcher = ParallelLLMDispatcher(settings, llm, gemma_router)

        # Single call (respects semaphores/timeout):
        result = await dispatcher.dispatch(prompt, model_hint="fast")

        # Parallel batch (bounded concurrency):
        results = await dispatcher.dispatch_batch([
            ("prompt1", "fast", 0.7, 500),
            ("prompt2", "heavy", 0.3, 1000),
        ])

    When disabled (feature flag OFF), dispatch() calls the underlying LLM
    directly with no overhead. dispatch_batch() runs sequentially.
    """

    def __init__(self, settings, llm, gemma_router=None):
        self._settings = settings
        self._llm = llm
        self._gemma_router = gemma_router
        self._metrics = DispatchMetrics()

        # Feature flag
        self._enabled = getattr(settings, "llm_parallel_enabled", False)
        self._metrics.enabled = self._enabled

        if not self._enabled:
            logger.info("ParallelLLMDispatcher: disabled (feature flag OFF)")
            return

        # Global semaphore
        max_concurrent = max(1, getattr(settings, "llm_parallel_max_concurrent", 4))
        self._global_semaphore = asyncio.Semaphore(max_concurrent)

        # Per-model lines
        max_per_model = max(1, getattr(settings, "llm_parallel_max_inflight_per_model", 2))
        self._lines = {
            ModelLine.FAST: ModelLine(ModelLine.FAST, max_per_model),
            ModelLine.HEAVY: ModelLine(ModelLine.HEAVY, max_per_model),
            ModelLine.DEFAULT: ModelLine(ModelLine.DEFAULT, max_per_model),
        }

        # Config
        self._timeout_s = max(10, getattr(settings, "llm_parallel_request_timeout_s", 120))
        self._dedupe = getattr(settings, "llm_parallel_dedupe", True)

        # Dedup tracking: hash -> Future
        self._pending_dedup: dict[str, asyncio.Future] = {}

        logger.info(
            "ParallelLLMDispatcher: enabled (max_concurrent=%d, per_model=%d, "
            "timeout=%ds, dedupe=%s)",
            max_concurrent, max_per_model, self._timeout_s, self._dedupe,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_metrics(self) -> dict:
        """Return metrics dict for /api/status and /api/ops."""
        d = {
            "enabled": self._enabled,
            "queue_depth": self._metrics.queue_depth,
            "inflight_total": self._metrics.inflight_total,
            "inflight_fast": 0,
            "inflight_heavy": 0,
            "inflight_default": 0,
            "completed_parallel_batches": self._metrics.completed_parallel_batches,
            "total_dispatched": self._metrics.total_dispatched,
            "total_completed": self._metrics.total_completed,
            "timeout_count": self._metrics.timeout_count,
            "cancelled_count": self._metrics.cancelled_count,
            "deduped_requests": self._metrics.deduped_requests,
            "degrade_to_sequential_count": self._metrics.degrade_to_sequential_count,
        }
        if self._enabled:
            d["inflight_fast"] = self._lines[ModelLine.FAST].inflight
            d["inflight_heavy"] = self._lines[ModelLine.HEAVY].inflight
            d["inflight_default"] = self._lines[ModelLine.DEFAULT].inflight
        return d

    # ------------------------------------------------------------------ #
    #  Single dispatch                                                     #
    # ------------------------------------------------------------------ #

    async def dispatch(
        self,
        prompt: str,
        model_hint: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
        tier: str = "auto",
    ) -> str:
        """Dispatch a single LLM call.

        When disabled: direct passthrough to LLM/Gemma with no overhead.
        When enabled: bounded by global + per-model semaphores, with
        timeout, dedup, and metrics.

        Args:
            prompt: The prompt text.
            model_hint: Model line hint ("fast", "heavy", "default").
            temperature: Generation temperature.
            max_tokens: Max output tokens.
            tier: Gemma tier hint ("fast", "heavy", "auto") — only used
                  when gemma_router is enabled.

        Returns:
            Generated text, or "" on failure/timeout.
        """
        if not self._enabled:
            return await self._direct_call(prompt, temperature, max_tokens, tier)

        self._metrics.total_dispatched += 1

        line = self._resolve_line(model_hint)

        # Dedup: register future synchronously BEFORE any await, so concurrent
        # dispatches from asyncio.gather() can see each other's entry. Earlier
        # versions registered the future inside _guarded_call (after multiple
        # awaits), producing a race where Python 3.11's asyncio scheduling ran
        # both tasks past the dedup check before either registered.
        own_future: Optional[asyncio.Future] = None
        own_dedup_key: Optional[str] = None
        if self._dedupe:
            dedup_key = self._dedup_key(prompt, line.name, temperature, max_tokens)
            existing = self._pending_dedup.get(dedup_key)
            if existing is not None and not existing.done():
                self._metrics.deduped_requests += 1
                logger.debug("Dedup hit for prompt hash %s", dedup_key[:8])
                try:
                    return await asyncio.shield(existing)
                except (asyncio.CancelledError, Exception):
                    # If the original was cancelled/failed, fall through to new call
                    pass
            # First caller: register our future so a concurrent dispatch can see it
            own_future = asyncio.get_event_loop().create_future()
            own_dedup_key = dedup_key
            self._pending_dedup[dedup_key] = own_future

        self._metrics.queue_depth += 1

        try:
            result = await asyncio.wait_for(
                self._guarded_call(prompt, line, temperature, max_tokens, tier, own_future),
                timeout=self._timeout_s,
            )
            self._metrics.total_completed += 1
            return result
        except asyncio.TimeoutError:
            self._metrics.timeout_count += 1
            logger.warning(
                "Parallel dispatch timeout (%ds) for model_hint=%s",
                self._timeout_s, model_hint,
            )
            return ""
        except asyncio.CancelledError:
            self._metrics.cancelled_count += 1
            raise
        except Exception as exc:
            logger.warning("Parallel dispatch error: %s", exc)
            # Degrade to direct sequential call
            self._metrics.degrade_to_sequential_count += 1
            return await self._direct_call(prompt, temperature, max_tokens, tier)
        finally:
            self._metrics.queue_depth = max(0, self._metrics.queue_depth - 1)

    async def _guarded_call(
        self,
        prompt: str,
        line: ModelLine,
        temperature: float,
        max_tokens: int,
        tier: str,
        future: Optional[asyncio.Future] = None,
    ) -> str:
        """Execute an LLM call guarded by global + per-model semaphores.

        The `future` (if any) was registered in dispatch() before any await,
        so concurrent dispatches can observe it for dedup. We resolve it here
        and remove it from the pending map when done.
        """
        try:
            await self._global_semaphore.acquire()
            try:
                await line.acquire()
                self._metrics.inflight_total = sum(
                    l.inflight for l in self._lines.values()
                )
                try:
                    result = await self._direct_call(
                        prompt, temperature, max_tokens, tier,
                    )
                    if future is not None and not future.done():
                        future.set_result(result)
                    return result
                except Exception as exc:
                    if future is not None and not future.done():
                        future.set_exception(exc)
                    raise
                finally:
                    line.release()
                    self._metrics.inflight_total = sum(
                        l.inflight for l in self._lines.values()
                    )
            finally:
                self._global_semaphore.release()
        finally:
            if future is not None:
                # Remove from pending map (owner cleanup)
                for k, v in list(self._pending_dedup.items()):
                    if v is future:
                        self._pending_dedup.pop(k, None)
                        break

    # ------------------------------------------------------------------ #
    #  Batch dispatch                                                      #
    # ------------------------------------------------------------------ #

    async def dispatch_batch(
        self,
        requests: List[Tuple[str, str, float, int]],
        tier: str = "auto",
    ) -> List[str]:
        """Dispatch a batch of LLM calls, bounded by concurrency limits.

        Each request is a tuple: (prompt, model_hint, temperature, max_tokens).

        When disabled: runs sequentially (identical behavior to old code).
        When enabled: runs concurrently bounded by semaphores.

        Returns list of results in same order as requests.
        """
        if not requests:
            return []

        if not self._enabled:
            # Sequential fallback — old behavior
            results = []
            for prompt, model_hint, temp, tokens in requests:
                r = await self._direct_call(prompt, temp, tokens, tier)
                results.append(r)
            return results

        # Parallel dispatch
        tasks = []
        for prompt, model_hint, temp, tokens in requests:
            tasks.append(
                self.dispatch(
                    prompt, model_hint=model_hint,
                    temperature=temp, max_tokens=tokens, tier=tier,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to empty strings
        final = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Batch item failed: %s", r)
                final.append("")
            else:
                final.append(r)

        self._metrics.completed_parallel_batches += 1
        return final

    # ------------------------------------------------------------------ #
    #  Direct call (passthrough)                                           #
    # ------------------------------------------------------------------ #

    async def _direct_call(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        tier: str = "auto",
    ) -> str:
        """Direct LLM call — uses Gemma router if available and enabled."""
        if self._gemma_router and self._gemma_router.enabled:
            from waggledance.application.services.gemma_profile_router import GemmaTier
            tier_map = {
                "fast": GemmaTier.FAST,
                "heavy": GemmaTier.HEAVY,
                "auto": GemmaTier.AUTO,
            }
            gemma_tier = tier_map.get(tier, GemmaTier.AUTO)
            return await self._gemma_router.generate(
                prompt, tier=gemma_tier,
                temperature=temperature, max_tokens=max_tokens,
            )
        return await self._llm.generate(
            prompt, temperature=temperature, max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _resolve_line(self, model_hint: str) -> ModelLine:
        """Resolve model_hint to a ModelLine."""
        if model_hint in self._lines:
            return self._lines[model_hint]
        return self._lines[ModelLine.DEFAULT]

    @staticmethod
    def _dedup_key(prompt: str, model_hint: str, temperature: float, max_tokens: int) -> str:
        """Create a dedup key from request parameters."""
        raw = f"{prompt}|{model_hint}|{temperature}|{max_tokens}"
        return hashlib.sha256(raw.encode()).hexdigest()
