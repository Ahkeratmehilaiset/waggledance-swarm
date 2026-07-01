# SPDX-License-Identifier: Apache-2.0
"""BridgeLLMClient — the four-tier fallback runtime LLM client.

R20.2 prototype scope per
``iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md``:

- Interface (LLMRequest / LLMResponse)
- Config loader (llm_config.json)
- Telemetry per call
- Cache placeholder (ExactCacheProvider)
- local-ollama provider (lazy-imported)
- Heuristic fallback (always works offline)
- Provider plugin interface (register_provider)
- Per-call budget enforcement

Profile S (no LLM allowed) flows through at the configuration layer:
the small.json profile sets ``bridge_llm_client.enabled = false``.
The client honors that by short-circuiting to the heuristic tier on
every call. This means no LLM SDK is imported when the client runs
under Profile S.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Sequence

from .budget import BudgetConfig, BudgetExhausted, BudgetTracker, load_budget_config
from .providers.anthropic import AnthropicProvider
from .providers.base import ProviderError, ProviderPlugin, all_providers
from .providers.cache import ExactCacheProvider
from .providers.heuristic import HeuristicProvider
from .providers.ollama import OllamaProvider
from .telemetry import TelemetryLogger
from .types import CallBudget, FallbackLevel, LLMRequest, LLMResponse


log = logging.getLogger(__name__)


# Default fallback chain. Tier names index the provider registry. The
# Tier-3 "cloud" slot resolves to the live AnthropicProvider (registered
# under both its descriptive "anthropic-api" name and the canonical
# "cloud" alias), which replaced the deliberately-unavailable
# CloudStubProvider. With no ANTHROPIC_API_KEY / SDK / working redactor the
# cloud tier reports unavailable and the chain falls through to heuristic.
DEFAULT_CHAIN: tuple[str, ...] = (
    "cache",
    "local-ollama",
    "cloud",
    "heuristic",
)

# Canonical name of the Tier-3 cloud slot in the default chain.
CLOUD_TIER_NAME = "cloud"


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(config: dict) -> dict:
    """Merge profile bootstrap env vars over file/default config."""
    merged = dict(config)
    enabled = _env_flag("WAGGLE_BRIDGE_LLM_ENABLED")
    if enabled is not None:
        merged["enabled"] = enabled

    redaction = _env_flag("WAGGLE_BRIDGE_LLM_REDACTION")
    if redaction is not None:
        merged["redaction_required"] = redaction

    chain = os.environ.get("WAGGLE_FALLBACK_CHAIN")
    if chain is not None:
        parsed = [part.strip() for part in chain.split(",") if part.strip()]
        if parsed:
            merged["fallback_chain"] = parsed
    return merged


def _default_config_path() -> Path:
    root = os.environ.get("AGENT_BRIDGE_RUNTIME_ROOT")
    if root:
        return Path(root) / "llm_config.json"
    return Path("data") / "bridge_llm" / "llm_config.json"


def load_llm_config(path: Path | str | None = None) -> dict:
    """Load llm_config.json or return a safe-default dict."""
    p = Path(path) if path is not None else _default_config_path()
    if not p.is_file():
        return _apply_env_overrides({
            "enabled": True,
            "fallback_chain": list(DEFAULT_CHAIN),
            "redaction_required": True,
        })
    return _apply_env_overrides(json.loads(p.read_text(encoding="utf-8")))


class BridgeLLMClient:
    """Single entry point for runtime LLM augmentation.

    Construction:

        client = BridgeLLMClient.default()       # uses env-driven config
        client = BridgeLLMClient(providers=[...]) # explicit injection

    Calling:

        response = client.run(LLMRequest(
            injection_point="hex.select_origin_cell",
            prompt="Where should I route 'beekeeper diagnostics'?",
        ))
    """

    def __init__(
        self,
        providers: Optional[Sequence[ProviderPlugin]] = None,
        fallback_chain: Optional[Sequence[str]] = None,
        budget: Optional[BudgetTracker] = None,
        telemetry: Optional[TelemetryLogger] = None,
        config: Optional[dict] = None,
    ):
        self._config = config if config is not None else {"enabled": True}
        self._providers: dict[str, ProviderPlugin] = {}
        if providers is None:
            # AnthropicProvider is the live Tier-3 cloud provider; it
            # replaces the deliberately-unavailable CloudStubProvider.
            providers = [
                ExactCacheProvider(),
                OllamaProvider(),
                AnthropicProvider(),
                HeuristicProvider(),
            ]
        for p in providers:
            self._providers[p.name] = p
        for p in all_providers().values():
            self._providers[p.name] = p
        # Alias the canonical "cloud" tier to the real Anthropic provider.
        # The provider keeps its descriptive "anthropic-api" identity (used
        # by Profile L chains that name it directly and by telemetry); the
        # "cloud" alias lets the default chain serve real cloud calls in
        # place of the removed CloudStubProvider. We never clobber an
        # explicitly-registered "cloud" provider (e.g. a caller-injected
        # cloud plugin), and only alias when an anthropic-api provider is
        # actually present.
        if (
            CLOUD_TIER_NAME not in self._providers
            and AnthropicProvider.name in self._providers
        ):
            self._providers[CLOUD_TIER_NAME] = \
                self._providers[AnthropicProvider.name]
        if fallback_chain is None:
            fallback_chain = self._config.get("fallback_chain") or DEFAULT_CHAIN
        self._fallback_chain = tuple(fallback_chain)
        self._budget = budget or BudgetTracker(BudgetConfig())
        self._telemetry = telemetry or TelemetryLogger()

    # ── Constructors ─────────────────────────────────────────────

    @classmethod
    def default(cls) -> "BridgeLLMClient":
        config = load_llm_config()
        budget_cfg_path = os.environ.get("WAGGLE_LLM_BUDGET_PATH")
        budget_cfg = load_budget_config(budget_cfg_path) if budget_cfg_path \
            else BudgetConfig()
        return cls(
            config=config,
            budget=BudgetTracker(budget_cfg),
        )

    @classmethod
    def disabled(cls, reason: str = "profile_s") -> "BridgeLLMClient":
        """Build a client that ALWAYS short-circuits to heuristic.

        Used by Profile S (no LLM allowed). The heuristic provider is
        the only one registered; cache and ollama are not constructed
        so no lazy-import path is reachable.
        """
        client = cls(
            providers=[HeuristicProvider()],
            fallback_chain=("heuristic",),
            config={"enabled": False, "disabled_reason": reason},
        )
        return client

    # ── Entry point ─────────────────────────────────────────────

    @property
    def fallback_chain(self) -> tuple[str, ...]:
        return self._fallback_chain

    @property
    def providers(self) -> dict[str, ProviderPlugin]:
        return dict(self._providers)

    @property
    def telemetry(self) -> TelemetryLogger:
        return self._telemetry

    def is_enabled(self) -> bool:
        return bool(self._config.get("enabled", True))

    def run(self, request: LLMRequest) -> LLMResponse:
        """Serve one request via the configured fallback chain.

        Telemetry is logged whether the call succeeds or falls
        through. Budget enforcement happens before the cloud tier
        (cache + local + heuristic stay free of budget).
        """
        # Profile S short-circuit. Avoid importing any LLM library.
        if not self.is_enabled():
            response = self._call_provider("heuristic", request)
            self._log(request, response)
            return response

        if not self._budget.can_spend(request.injection_point):
            # Budget exhausted -> degrade to heuristic
            response = self._call_provider("heuristic", request)
            response.error_class = "budget_exhausted"
            response.success = False
            self._log(request, response)
            return response

        last_error: str | None = None
        for provider_name in self._fallback_chain:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            # Primary cloud-egress gate: never dispatch to a CLOUD_LLM tier
            # when the request disallows cloud (default deny — see
            # LLMRequest.allows_cloud / CallBudget.allow_cloud). The cloud
            # provider also self-guards (defense in depth), but this skip
            # ensures no CLOUD_LLM-tier provider is even reached.
            if (
                provider.fallback_level == FallbackLevel.CLOUD_LLM
                and not request.allows_cloud()
            ):
                last_error = f"{provider_name}: cloud disabled (allow_cloud=False)"
                continue
            if not provider.is_available():
                last_error = f"{provider_name}: unavailable"
                continue
            try:
                response = self._call_provider(provider_name, request)
            except ProviderError as exc:
                last_error = f"{provider_name}: {exc}"
                continue
            # Best-effort budget bump for cloud-tier calls (none in v1
            # but the hook is in place).
            try:
                self._budget.record(
                    request.injection_point, response.cost_cents
                )
            except BudgetExhausted:
                response.error_class = "budget_exhausted"
            self._log(request, response)
            return response

        # All providers fell through -> emit a failure response
        response = LLMResponse(
            text=f"[no-provider-served] {last_error or 'unknown'}",
            fallback_level=FallbackLevel.HEURISTIC,
            provider="none",
            success=False,
            latency_ms=0.0,
            error_class=last_error or "no_provider",
            call_id=str(uuid.uuid4())[:16],
        )
        self._log(request, response)
        return response

    # ── Internals ────────────────────────────────────────────────

    def _call_provider(
        self, name: str, request: LLMRequest
    ) -> LLMResponse:
        provider = self._providers[name]
        start = time.perf_counter()
        try:
            response = provider.call(request)
            if not response.call_id:
                response.call_id = str(uuid.uuid4())[:16]
            return response
        except ProviderError:
            raise
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return LLMResponse(
                text=f"[provider-crashed] {provider.name}: {exc}",
                fallback_level=provider.fallback_level,
                provider=provider.name,
                success=False,
                latency_ms=elapsed,
                error_class=exc.__class__.__name__,
                call_id=str(uuid.uuid4())[:16],
            )

    def _log(self, request: LLMRequest, response: LLMResponse) -> None:
        try:
            self._telemetry.log_call(
                request, response, self._budget.state_snapshot()
            )
        except Exception as exc:  # never crash the call on telemetry
            log.debug("telemetry write failed: %s", exc)
