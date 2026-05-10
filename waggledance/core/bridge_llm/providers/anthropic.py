# SPDX-License-Identifier: Apache-2.0
"""AnthropicProvider — first cloud provider plugin (Tier 3).

Per R21.3 / operator decision 4: cloud-bound prompts MUST go through
``BridgeLLMRedactor`` before transmission. ``AcceptPiiToCloud=False``
is the hard default; the caller may opt in per call.

Lazy-imports the ``anthropic`` SDK inside ``call()`` so importing this
module does NOT import the SDK (Profile S compatibility). The
``is_available()`` check uses ``importlib.util.find_spec`` to stay
clean.
"""
from __future__ import annotations

import importlib.util
import os
import time

from ..redactor import BridgeLLMRedactor, RedactionResult
from ..types import LLMRequest, LLMResponse, FallbackLevel
from .base import ProviderError, ProviderPlugin


# claude-haiku-4-5-20251001 published pricing (per million tokens).
# R22.0 finding F3: hardcoded for v1 so cost_cents is non-zero in
# telemetry + budget tracker can enforce $-budget. Override per-call
# via request.metadata if a different model is used.
_HAIKU_INPUT_USD_PER_M_TOKENS = 0.25
_HAIKU_OUTPUT_USD_PER_M_TOKENS = 1.25


def _estimate_cost_cents(tokens_in: int, tokens_out: int,
                          input_per_m_usd: float = _HAIKU_INPUT_USD_PER_M_TOKENS,
                          output_per_m_usd: float = _HAIKU_OUTPUT_USD_PER_M_TOKENS,
                          ) -> int:
    """Approximate cost in cents (rounded up) for a haiku call."""
    usd = (tokens_in / 1_000_000.0) * input_per_m_usd
    usd += (tokens_out / 1_000_000.0) * output_per_m_usd
    cents = usd * 100.0
    # Round up to next cent so the budget tracker is conservative.
    return int(cents) + (1 if cents > int(cents) else 0)


class AnthropicProvider(ProviderPlugin):
    """Anthropic Claude API plugin (cloud, tier 3).

    Configuration:

    - API key from env var ``ANTHROPIC_API_KEY`` at call time. If
      unset, ``is_available()`` returns False so the chain falls
      through to heuristic.
    - Default model: ``claude-haiku-4-5-20251001`` (fast, cheap; the
      caller may override per-call via request.metadata['model']).
    - Redactor is mandatory per operator decision 4. The provider
      refuses to dispatch a cloud call if the redactor signals
      ``failed=True`` (R22.0 finding F2: trust the structured flag,
      not text-equality of the sentinel string, so a malicious
      prompt can't spoof fail-closed).
    - R22.0 finding F3: ``request.budget.max_latency_ms`` is honored
      via ``timeout=`` on ``client.messages.create()`` so a slow
      provider can't hang the call indefinitely.

    The ``accept_pii_to_cloud`` flag in request.metadata gates the
    per-call opt-in. Default False.
    """

    name = "anthropic-api"
    fallback_level = FallbackLevel.CLOUD_LLM

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 256,
        redactor: BridgeLLMRedactor | None = None,
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._redactor = redactor or BridgeLLMRedactor()

    def is_available(self) -> bool:
        # Profile S clean: importlib.util.find_spec doesn't import.
        if importlib.util.find_spec("anthropic") is None:
            return False
        # API key required at call time.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        return True

    def call(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            raise ProviderError(
                "anthropic SDK or ANTHROPIC_API_KEY missing; "
                "falling through to next tier"
            )

        accept_pii = bool(request.metadata.get("accept_pii_to_cloud", False))
        redaction = self._redactor.redact(
            request.prompt, accept_pii_to_cloud=accept_pii,
        )

        # Fail closed (R22.0 finding F2): trust the structured
        # `failed` flag, NOT text equality with the sentinel string.
        # A user prompt containing the literal "<REDACTOR_FAILED>"
        # would spoof a text-equality check.
        if redaction.failed:
            raise ProviderError(
                "redactor failed; refusing to dispatch cloud call "
                "(fail-closed per operator decision 4)"
            )

        # R22.0 finding F3: honor the per-call latency budget so a
        # slow provider can't hang the call. Convert ms → seconds for
        # the SDK; default 30s if the budget is unset / very large.
        timeout_s = max(0.5, min(request.budget.max_latency_ms / 1000.0, 30.0))

        # Lazy import — keeps Profile S import boundary clean.
        import anthropic  # type: ignore[import-not-found]

        client = anthropic.Anthropic(timeout=timeout_s)
        start = time.perf_counter()
        try:
            model = request.metadata.get("model", self._model)
            response = client.messages.create(
                model=model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": redaction.text}],
            )
            text_blocks = [
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            ]
            text = "".join(text_blocks)
            tokens_in = int(getattr(response.usage, "input_tokens", 0))
            tokens_out = int(getattr(response.usage, "output_tokens", 0))
            # R22.0 finding F3: compute non-zero cost so budget
            # tracker can enforce $-budget. v1 uses haiku pricing; if
            # the model override is not haiku the estimate may drift
            # — caller can pass explicit pricing in a follow-up.
            cost_cents = _estimate_cost_cents(tokens_in, tokens_out)
            latency_ms = (time.perf_counter() - start) * 1000
            return LLMResponse(
                text=text,
                fallback_level=FallbackLevel.CLOUD_LLM,
                provider=self.name,
                success=True,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_cents=cost_cents,
                redaction_applied=redaction.applied,
            )
        except Exception as exc:
            raise ProviderError(f"anthropic call failed: {exc}") from exc
