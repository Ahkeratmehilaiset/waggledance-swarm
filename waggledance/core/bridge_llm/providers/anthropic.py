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


class AnthropicProvider(ProviderPlugin):
    """Anthropic Claude API plugin (cloud, tier 3).

    Configuration:

    - API key from env var ``ANTHROPIC_API_KEY`` at call time. If
      unset, ``is_available()`` returns False so the chain falls
      through to heuristic.
    - Default model: ``claude-haiku-4-5-20251001`` (fast, cheap; the
      caller may override per-call via request.metadata['model']).
    - Redactor is mandatory per operator decision 4. The provider
      refuses to dispatch a cloud call if the redactor returns the
      ``<REDACTOR_FAILED>`` sentinel — this is the "fail closed when
      redaction is unavailable" requirement.

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

        # Fail closed: if redactor returned the failure sentinel, do
        # NOT dispatch the cloud call.
        if redaction.text == "<REDACTOR_FAILED>":
            raise ProviderError(
                "redactor failed; refusing to dispatch cloud call "
                "(fail-closed per operator decision 4)"
            )

        # Lazy import — keeps Profile S import boundary clean.
        import anthropic  # type: ignore[import-not-found]

        client = anthropic.Anthropic()
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
            latency_ms = (time.perf_counter() - start) * 1000
            return LLMResponse(
                text=text,
                fallback_level=FallbackLevel.CLOUD_LLM,
                provider=self.name,
                success=True,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                redaction_applied=redaction.applied,
            )
        except Exception as exc:
            raise ProviderError(f"anthropic call failed: {exc}") from exc
