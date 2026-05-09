"""R20.2 — BridgeLLMClient prototype tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Imports + Profile S import discipline ───────────────────────

def test_bridge_llm_client_module_imports_without_llm_libs():
    """Importing waggledance.core.bridge_llm must NOT pull in any LLM
    SDK. Provider plugins lazy-import their backing libraries inside
    .call() so Profile S stays clean."""
    script = textwrap.dedent('''
        import sys
        # Path manipulation so the subprocess finds the package
        sys.path.insert(0, %r)
        from waggledance.core.bridge_llm import (
            BridgeLLMClient, LLMRequest, LLMResponse,
            FallbackLevel, ProviderError,
        )
        leaked = [n for n in ("anthropic", "openai", "ollama",
                                "vertexai", "cohere", "groq")
                   if n in sys.modules]
        if leaked:
            print(f"LEAKED: {leaked}")
            sys.exit(1)
        print("BRIDGE_LLM_IMPORT_CLEAN")
    ''') % (str(REPO_ROOT),)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "BRIDGE_LLM_IMPORT_CLEAN" in proc.stdout


# ─── Cache + heuristic providers ─────────────────────────────────

def test_cache_provider_stores_and_returns_hit():
    from waggledance.core.bridge_llm.providers.cache import ExactCacheProvider
    from waggledance.core.bridge_llm.types import LLMRequest, FallbackLevel
    cache = ExactCacheProvider()
    req = LLMRequest(injection_point="test.point", prompt="hello")
    cache.store(req, "world")
    response = cache.call(req)
    assert response.text == "world"
    assert response.fallback_level == FallbackLevel.CACHE
    assert response.cached is True
    assert response.success is True


def test_cache_provider_miss_raises_provider_error(tmp_path):
    from waggledance.core.bridge_llm.providers.cache import ExactCacheProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest
    cache = ExactCacheProvider()
    req = LLMRequest(injection_point="test.point", prompt="hello")
    with pytest.raises(ProviderError, match="cache miss"):
        cache.call(req)


def test_heuristic_provider_always_works():
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    from waggledance.core.bridge_llm.types import LLMRequest, FallbackLevel
    h = HeuristicProvider()
    assert h.is_available() is True
    req = LLMRequest(
        injection_point="hex.select_origin_cell",
        prompt="Where to route?",
        intent="route",
    )
    response = h.call(req)
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.provider == "heuristic"
    assert response.success is True
    assert "hex.select_origin_cell" in response.text
    assert "route" in response.text


def test_ollama_provider_is_available_returns_bool():
    """Provider availability check uses importlib.util.find_spec
    rather than importing the package, so it stays cheap and Profile-S-clean."""
    from waggledance.core.bridge_llm.providers.ollama import OllamaProvider
    o = OllamaProvider()
    # Availability depends on whether ollama Python package is installed
    # locally; either way the result must be a bool.
    assert isinstance(o.is_available(), bool)


def test_ollama_provider_call_raises_provider_error_when_unavailable(monkeypatch):
    """When the ollama package is not importable (or the daemon not
    reachable), the provider must raise ProviderError so the client
    falls through, not crash with ImportError."""
    from waggledance.core.bridge_llm.providers import ollama as ollama_mod
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest
    o = ollama_mod.OllamaProvider()
    # Force is_available() to return False so .call() raises
    monkeypatch.setattr(o, "is_available", lambda: False)
    with pytest.raises(ProviderError):
        o.call(LLMRequest(injection_point="x", prompt="hello"))


# ─── BridgeLLMClient end-to-end ──────────────────────────────────

def test_client_disabled_short_circuits_to_heuristic():
    """Profile S contract: BridgeLLMClient.disabled() never imports
    any LLM library and always returns a heuristic response."""
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    client = BridgeLLMClient.disabled(reason="profile_s")
    assert not client.is_enabled()
    assert client.fallback_chain == ("heuristic",)
    response = client.run(LLMRequest(
        injection_point="x.y", prompt="hello",
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.success is True


def test_client_serves_from_cache_when_seeded(tmp_path, monkeypatch):
    """Cache hit short-circuits the chain at tier 1."""
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path))
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.cache import ExactCacheProvider
    cache = ExactCacheProvider()
    req = LLMRequest(injection_point="cache.test", prompt="seed-prompt")
    cache.store(req, "cached-answer")
    client = BridgeLLMClient(
        providers=[cache],
        fallback_chain=("cache",),
    )
    response = client.run(req)
    assert response.text == "cached-answer"
    assert response.fallback_level == FallbackLevel.CACHE


def test_client_falls_through_to_heuristic_when_cache_misses(tmp_path, monkeypatch):
    """Cache miss falls to next tier — eventually heuristic."""
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path))
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.cache import ExactCacheProvider
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    client = BridgeLLMClient(
        providers=[ExactCacheProvider(), HeuristicProvider()],
        fallback_chain=("cache", "heuristic"),
    )
    response = client.run(LLMRequest(
        injection_point="fallthrough.test", prompt="never-cached"
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.success is True


def test_client_telemetry_writes_one_jsonl_line_per_call(tmp_path, monkeypatch):
    """Every call appends one JSONL line to the telemetry log."""
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path))
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest
    client = BridgeLLMClient.disabled(reason="test")
    for i in range(3):
        client.run(LLMRequest(
            injection_point=f"telemetry.test.{i}",
            prompt=f"prompt {i}",
        ))
    telemetry_path = tmp_path / "bridge_llm_telemetry.jsonl"
    assert telemetry_path.is_file()
    lines = telemetry_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    # Each line must be parseable JSON with required fields
    for line in lines:
        record = json.loads(line)
        for field in (
            "call_id", "timestamp", "injection_point", "provider",
            "prompt_hash", "latency_ms", "fallback_level", "success",
        ):
            assert field in record, f"missing telemetry field: {field}"


def test_client_load_llm_config_uses_env_var(tmp_path, monkeypatch):
    """load_llm_config reads <AGENT_BRIDGE_RUNTIME_ROOT>/llm_config.json."""
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path))
    cfg_path = tmp_path / "llm_config.json"
    cfg_path.write_text(json.dumps({
        "enabled": True,
        "fallback_chain": ["cache", "heuristic"],
        "redaction_required": True,
    }), encoding="utf-8")
    from waggledance.core.bridge_llm.client import load_llm_config
    cfg = load_llm_config()
    assert cfg["enabled"] is True
    assert cfg["fallback_chain"] == ["cache", "heuristic"]


# ─── Budget enforcement ──────────────────────────────────────────

def test_budget_tracker_blocks_when_calls_exhausted():
    from waggledance.core.bridge_llm.budget import BudgetTracker, BudgetConfig
    tracker = BudgetTracker(BudgetConfig(max_calls_per_day=2))
    assert tracker.can_spend("ip") is True
    tracker.record("ip")
    tracker.record("ip")
    assert tracker.can_spend("ip") is False


def test_client_degrades_to_heuristic_when_budget_exhausted(tmp_path, monkeypatch):
    """Budget exhaustion -> heuristic, with error_class=budget_exhausted."""
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path))
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.budget import BudgetTracker, BudgetConfig
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider

    tracker = BudgetTracker(BudgetConfig(max_calls_per_day=1))
    # Pre-spend the budget
    tracker.record("budget.test")

    client = BridgeLLMClient(
        providers=[HeuristicProvider()],
        fallback_chain=("heuristic",),
        budget=tracker,
        config={"enabled": True},
    )
    response = client.run(LLMRequest(
        injection_point="budget.test", prompt="hello"
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.success is False
    assert response.error_class == "budget_exhausted"


# ─── Provider plugin registry ────────────────────────────────────

def test_register_provider_makes_plugin_globally_visible():
    from waggledance.core.bridge_llm.providers.base import (
        ProviderPlugin, register_provider, get_provider,
    )
    from waggledance.core.bridge_llm.types import (
        LLMRequest, LLMResponse, FallbackLevel,
    )

    class SignalProvider(ProviderPlugin):
        name = "test-signal"
        fallback_level = FallbackLevel.HEURISTIC

        def is_available(self) -> bool:
            return True

        def call(self, request):
            return LLMResponse(
                text="signal", fallback_level=FallbackLevel.HEURISTIC,
                provider=self.name, success=True, latency_ms=0.0,
            )

    register_provider(SignalProvider())
    assert get_provider("test-signal") is not None
    assert get_provider("test-signal").name == "test-signal"
