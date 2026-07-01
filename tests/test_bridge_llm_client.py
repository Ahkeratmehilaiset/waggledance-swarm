"""R20.2 — BridgeLLMClient prototype tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import types
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


def test_cache_provider_keys_generation_params():
    from waggledance.core.bridge_llm.providers.cache import ExactCacheProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest

    cache = ExactCacheProvider()
    req_a = LLMRequest(
        injection_point="test.point",
        prompt="same prompt",
        model="model-a",
        temperature=0.0,
        max_tokens=128,
    )
    req_b = LLMRequest(
        injection_point="test.point",
        prompt="same prompt",
        model="model-b",
        temperature=0.0,
        max_tokens=128,
    )
    cache.store(req_a, "model-a-answer")
    assert cache.call(req_a).text == "model-a-answer"
    with pytest.raises(ProviderError, match="cache miss"):
        cache.call(req_b)


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


def test_ollama_provider_passes_latency_budget_to_sdk_timeout(monkeypatch):
    from waggledance.core.bridge_llm.providers import ollama as ollama_mod
    from waggledance.core.bridge_llm.types import LLMRequest, CallBudget

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def chat(self, **kwargs):
            captured["chat_kwargs"] = kwargs
            return {
                "message": {"content": "ok"},
                "prompt_eval_count": 1,
                "eval_count": 2,
            }

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(
        Client=FakeClient,
    ))
    provider = ollama_mod.OllamaProvider(host="http://fake")
    monkeypatch.setattr(provider, "is_available", lambda: True)

    provider.call(LLMRequest(
        injection_point="x",
        prompt="hello",
        budget=CallBudget(max_latency_ms=1234),
    ))

    assert captured["client_kwargs"]["host"] == "http://fake"
    assert captured["client_kwargs"]["timeout"] == pytest.approx(1.234)
    assert captured["chat_kwargs"]["options"]["num_predict"] == 256


def test_ollama_provider_uses_request_generation_params(monkeypatch):
    from waggledance.core.bridge_llm.providers import ollama as ollama_mod
    from waggledance.core.bridge_llm.types import LLMRequest

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def chat(self, **kwargs):
            captured["chat_kwargs"] = kwargs
            return {
                "message": {"content": "ok"},
                "prompt_eval_count": 1,
                "eval_count": 2,
            }

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(
        Client=FakeClient,
    ))
    provider = ollama_mod.OllamaProvider()
    monkeypatch.setattr(provider, "is_available", lambda: True)

    provider.call(LLMRequest(
        injection_point="x",
        prompt="hello",
        model="custom-ollama",
        temperature=0.0,
        max_tokens=512,
    ))

    assert captured["chat_kwargs"]["model"] == "custom-ollama"
    assert captured["chat_kwargs"]["options"]["temperature"] == 0.0
    assert captured["chat_kwargs"]["options"]["num_predict"] == 512


def test_anthropic_provider_uses_request_generation_params(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.types import CallBudget, LLMRequest

    captured_create = {}

    class StubUsage:
        input_tokens = 1
        output_tokens = 2

    class StubBlock:
        type = "text"
        text = "ok"

    class StubResponse:
        content = [StubBlock()]
        usage = StubUsage()

    class StubAnthropic:
        def __init__(self, timeout=None, **kwargs):
            pass

        @property
        def messages(self):
            class _M:
                @staticmethod
                def create(**kwargs):
                    captured_create.update(kwargs)
                    return StubResponse()
            return _M()

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = StubAnthropic  # type: ignore[attr-defined]

    provider = AnthropicProvider()
    monkeypatch.setattr(provider, "is_available", lambda: True)
    response = provider.call(LLMRequest(
        injection_point="x",
        prompt="hello",
        model="claude-test",
        temperature=0.0,
        max_tokens=321,
        budget=CallBudget(allow_cloud=True),
    ))

    assert response.text == "ok"
    assert captured_create["model"] == "claude-test"
    assert captured_create["temperature"] == 0.0
    assert captured_create["max_tokens"] == 321


def test_ollama_provider_honors_max_retries_budget(monkeypatch):
    from waggledance.core.bridge_llm.providers import ollama as ollama_mod
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest, CallBudget

    attempts = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def chat(self, **kwargs):
            attempts.append(kwargs)
            raise RuntimeError("daemon still loading")

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(
        Client=FakeClient,
    ))
    provider = ollama_mod.OllamaProvider()
    monkeypatch.setattr(provider, "is_available", lambda: True)

    with pytest.raises(ProviderError, match="daemon still loading"):
        provider.call(LLMRequest(
            injection_point="x",
            prompt="hello",
            budget=CallBudget(max_retries=2),
        ))

    assert len(attempts) == 2


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


def test_llm_request_effective_generation_params_preserve_metadata_compat():
    from waggledance.core.bridge_llm.types import LLMRequest

    legacy = LLMRequest(
        injection_point="x",
        prompt="hello",
        metadata={
            "model": "legacy-model",
            "temperature": "0",
            "max_tokens": "512",
        },
    )
    assert legacy.effective_model("provider-default") == "legacy-model"
    assert legacy.effective_temperature(0.7) == 0.0
    assert legacy.effective_max_tokens(256) == 512

    explicit = LLMRequest(
        injection_point="x",
        prompt="hello",
        model="explicit-model",
        temperature=0.2,
        max_tokens=128,
        metadata={
            "model": "legacy-model",
            "temperature": 0.9,
            "max_tokens": 999,
        },
    )
    assert explicit.effective_model("provider-default") == "explicit-model"
    assert explicit.effective_temperature(0.7) == 0.2
    assert explicit.effective_max_tokens(256) == 128


@pytest.mark.asyncio
async def test_bridge_llm_adapter_lifts_generation_params_into_request():
    from waggledance.adapters.llm.bridge_llm_adapter import BridgeLLMAdapter
    from waggledance.core.bridge_llm.types import FallbackLevel, LLMResponse

    captured = {}

    class CaptureClient:
        def is_enabled(self):
            return True

        def run(self, request):
            captured["request"] = request
            return LLMResponse(
                text="bridge ok",
                fallback_level=FallbackLevel.HEURISTIC,
                provider="capture",
                success=True,
                latency_ms=0.0,
            )

    class FallbackAdapter:
        async def generate(self, **kwargs):
            raise AssertionError("fallback should not be used")

    adapter = BridgeLLMAdapter(
        CaptureClient(),
        FallbackAdapter(),
        injection_point="test.adapter",
    )

    text = await adapter.generate(
        "hello",
        model="custom-model",
        temperature=0.0,
        max_tokens=512,
    )

    assert text == "bridge ok"
    request = captured["request"]
    assert request.injection_point == "test.adapter"
    assert request.prompt == "hello"
    assert request.model == "custom-model"
    assert request.temperature == 0.0
    assert request.max_tokens == 512
    assert request.metadata["model"] == "custom-model"
    assert request.metadata["temperature"] == 0.0
    assert request.metadata["max_tokens"] == 512


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


def test_default_client_honors_profile_env_disable(monkeypatch):
    """Profile S bootstrap env must disable the default client path."""
    monkeypatch.delenv("AGENT_BRIDGE_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("WAGGLE_BRIDGE_LLM_ENABLED", "0")
    monkeypatch.setenv("WAGGLE_FALLBACK_CHAIN", "heuristic")
    monkeypatch.setenv("WAGGLE_BRIDGE_LLM_REDACTION", "1")

    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest
    from waggledance.core.bridge_llm.types import FallbackLevel

    client = BridgeLLMClient.default()
    assert client.is_enabled() is False
    assert client.fallback_chain == ("heuristic",)
    assert {"cache", "local-ollama", "cloud", "heuristic"}.issubset(
        set(client.providers)
    )

    response = client.run(LLMRequest(
        injection_point="profile_s.env", prompt="must stay offline",
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.provider == "heuristic"


def test_default_client_profile_l_env_registers_anthropic_provider(monkeypatch):
    """Profile L env should route to the real Anthropic provider name,
    not the unavailable cloud placeholder."""
    monkeypatch.delenv("AGENT_BRIDGE_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("WAGGLE_BRIDGE_LLM_ENABLED", "1")
    monkeypatch.setenv(
        "WAGGLE_FALLBACK_CHAIN",
        "cache,local-ollama,anthropic-api,heuristic",
    )
    monkeypatch.setenv("WAGGLE_BRIDGE_LLM_REDACTION", "1")

    from waggledance.core.bridge_llm import BridgeLLMClient

    client = BridgeLLMClient.default()
    assert client.is_enabled() is True
    assert client.fallback_chain == (
        "cache", "local-ollama", "anthropic-api", "heuristic",
    )
    assert "anthropic-api" in client.providers
    assert client.providers["anthropic-api"].__class__.__name__ == (
        "AnthropicProvider"
    )


def test_default_client_profile_l_env_does_not_import_anthropic_sdk():
    """Constructing the default client with Profile L's cloud provider
    registered must stay SDK-clean until an actual cloud call."""
    script = textwrap.dedent('''
        import os, sys
        sys.path.insert(0, %r)
        os.environ.pop("AGENT_BRIDGE_RUNTIME_ROOT", None)
        os.environ["WAGGLE_BRIDGE_LLM_ENABLED"] = "1"
        os.environ["WAGGLE_FALLBACK_CHAIN"] = (
            "cache,local-ollama,anthropic-api,heuristic"
        )
        os.environ["WAGGLE_BRIDGE_LLM_REDACTION"] = "1"
        from waggledance.core.bridge_llm import BridgeLLMClient
        client = BridgeLLMClient.default()
        assert "anthropic-api" in client.providers
        if "anthropic" in sys.modules:
            print("ANTHROPIC_SDK_LEAKED")
            sys.exit(1)
        print("ANTHROPIC_PROVIDER_REGISTERED_WITHOUT_SDK_IMPORT")
    ''') % (str(REPO_ROOT),)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "ANTHROPIC_PROVIDER_REGISTERED_WITHOUT_SDK_IMPORT" in proc.stdout


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

    from waggledance.core.bridge_llm import BridgeLLMClient

    client = BridgeLLMClient(providers=[], fallback_chain=("test-signal",))
    response = client.run(LLMRequest(
        injection_point="plugin.registry", prompt="hello",
    ))
    assert response.provider == "test-signal"
    assert response.text == "signal"


# ─── Lane #7: real Anthropic cloud tier replaces CloudStubProvider ──

def test_default_client_wires_anthropic_as_cloud_tier(monkeypatch):
    """The default client's Tier-3 'cloud' slot now resolves to the live
    AnthropicProvider (same instance also reachable as 'anthropic-api'),
    replacing the retired, always-unavailable CloudStubProvider."""
    monkeypatch.delenv("AGENT_BRIDGE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("WAGGLE_FALLBACK_CHAIN", raising=False)
    monkeypatch.delenv("WAGGLE_BRIDGE_LLM_ENABLED", raising=False)
    from waggledance.core.bridge_llm import BridgeLLMClient

    client = BridgeLLMClient.default()
    assert "cloud" in client.providers
    assert client.providers["cloud"].__class__.__name__ == "AnthropicProvider"
    # The "cloud" alias and the descriptive "anthropic-api" name point at
    # the SAME provider instance.
    assert client.providers["cloud"] is client.providers["anthropic-api"]
    # Default chain still names the canonical cloud tier.
    assert "cloud" in client.fallback_chain
    # The retired stub is no longer serving the cloud tier.
    assert client.providers["cloud"].__class__.__name__ != "CloudStubProvider"


def test_client_cloud_tier_falls_through_to_heuristic_when_redactor_fails(monkeypatch):
    """Security fall-through: when the cloud tier is reached but redaction
    fails, the provider raises ProviderError and the chain degrades to the
    local heuristic tier WITHOUT contacting the cloud."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor
    from waggledance.core.bridge_llm.types import CallBudget

    class RaisingRedactor(BridgeLLMRedactor):
        def redact(self, prompt, *, accept_pii_to_cloud=False):
            raise RuntimeError("redactor backend down")

    class ExplodingAnthropic:
        def __init__(self, *a, **kw):
            raise AssertionError("cloud must NOT be contacted on redaction failure")

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = ExplodingAnthropic  # type: ignore[attr-defined]

    cloud = AnthropicProvider(redactor=RaisingRedactor())
    # Force the tier to be 'attempted' so call() runs and fails closed,
    # exercising the chain fall-through.
    monkeypatch.setattr(cloud, "is_available", lambda: True)

    client = BridgeLLMClient(
        providers=[cloud, HeuristicProvider()],
        fallback_chain=("anthropic-api", "heuristic"),
        config={"enabled": True},
    )
    # allow_cloud=True so the client gate admits the cloud tier; the
    # fall-through here is driven by the redaction failure, not the gate.
    response = client.run(LLMRequest(
        injection_point="x", prompt="alice@example.org leaked?",
        budget=CallBudget(allow_cloud=True),
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.provider == "heuristic"
    assert response.success is True


def test_client_skips_cloud_tier_when_no_api_key(monkeypatch):
    """No ANTHROPIC_API_KEY -> cloud tier reports unavailable -> the chain
    skips it cleanly and serves heuristic (no fake cloud serving)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    from waggledance.core.bridge_llm.types import CallBudget

    cloud = AnthropicProvider()
    assert cloud.is_available() is False
    client = BridgeLLMClient(
        providers=[cloud, HeuristicProvider()],
        fallback_chain=("anthropic-api", "heuristic"),
        config={"enabled": True},
    )
    # allow_cloud=True so the skip here is driven by missing-key
    # unavailability, not the allow_cloud gate.
    response = client.run(LLMRequest(
        injection_point="x", prompt="hello",
        budget=CallBudget(allow_cloud=True),
    ))
    assert response.fallback_level == FallbackLevel.HEURISTIC
    assert response.provider == "heuristic"


# ─── Lane #7 (#1449 review): honor allow_cloud before cloud dispatch ──

def test_llm_request_defaults_deny_cloud():
    """The authoritative default: a bare request must NOT opt in to cloud.
    CallBudget.allow_cloud defaults to False, so allows_cloud() is False.
    Fail-closed: a None budget also denies."""
    from waggledance.core.bridge_llm.types import CallBudget, LLMRequest
    assert CallBudget().allow_cloud is False
    req = LLMRequest(injection_point="x", prompt="p")
    assert req.budget.allow_cloud is False
    assert req.allows_cloud() is False
    assert LLMRequest(
        injection_point="x", prompt="p",
        budget=CallBudget(allow_cloud=True),
    ).allows_cloud() is True
    # Fail-closed robustness: a torn-down/None budget denies cloud.
    req.budget = None  # type: ignore[assignment]
    assert req.allows_cloud() is False


def test_client_does_not_dispatch_cloud_when_allow_cloud_false(monkeypatch):
    """#1449 review repro: a request that opts OUT of cloud must never reach
    a CLOUD_LLM-tier provider, even when that tier is available (key +
    redactor present). The chain skips it and serves heuristic;
    cloud_calls == 0. Covers BOTH explicit allow_cloud=False AND the bare
    (default-deny) request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.base import ProviderPlugin
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    from waggledance.core.bridge_llm.types import CallBudget, LLMResponse

    class SpyCloudProvider(ProviderPlugin):
        # Same name the default chain aliases to 'cloud'; CLOUD_LLM tier.
        name = "anthropic-api"
        fallback_level = FallbackLevel.CLOUD_LLM

        def __init__(self):
            self.calls = 0

        def is_available(self) -> bool:
            return True  # available, yet must NOT be dispatched

        def call(self, request) -> LLMResponse:
            self.calls += 1
            raise AssertionError(
                "cloud provider was dispatched despite allow_cloud=False"
            )

    spy = SpyCloudProvider()
    client = BridgeLLMClient(
        providers=[spy, HeuristicProvider()],
        fallback_chain=("anthropic-api", "heuristic"),
        config={"enabled": True},
    )

    # Explicit opt-out.
    resp = client.run(LLMRequest(
        injection_point="x", prompt="secret",
        budget=CallBudget(allow_cloud=False),
    ))
    assert resp.provider == "heuristic"
    assert resp.fallback_level == FallbackLevel.HEURISTIC
    assert spy.calls == 0

    # Bare request → default-deny → same outcome.
    resp2 = client.run(LLMRequest(injection_point="x", prompt="secret"))
    assert resp2.provider == "heuristic"
    assert spy.calls == 0


def test_client_dispatches_and_redacts_cloud_when_allow_cloud_true(monkeypatch):
    """allow_cloud=True (with key + working redactor): the chain DOES
    dispatch to the cloud tier, and the payload that reaches the SDK is
    redacted — proving the gate opens only on opt-in and the redaction
    boundary still holds end-to-end through the client."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm import BridgeLLMClient, LLMRequest, FallbackLevel
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    from waggledance.core.bridge_llm.types import CallBudget

    captured_create: dict = {}

    class StubUsage:
        input_tokens = 3
        output_tokens = 4

    class StubBlock:
        type = "text"
        text = "cloud-ok"

    class StubResponse:
        content = [StubBlock()]
        usage = StubUsage()

    class StubAnthropic:
        def __init__(self, timeout=None, **kwargs):
            pass

        @property
        def messages(self):
            class _M:
                @staticmethod
                def create(**kwargs):
                    captured_create.update(kwargs)
                    return StubResponse()
            return _M()

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = StubAnthropic  # type: ignore[attr-defined]

    cloud = AnthropicProvider()  # real BridgeLLMRedactor
    monkeypatch.setattr(cloud, "is_available", lambda: True)
    client = BridgeLLMClient(
        providers=[cloud, HeuristicProvider()],
        fallback_chain=("anthropic-api", "heuristic"),
        config={"enabled": True},
    )

    response = client.run(LLMRequest(
        injection_point="x", prompt="email alice@example.org now",
        budget=CallBudget(allow_cloud=True),
    ))

    # Cloud tier served (not heuristic).
    assert response.provider == "anthropic-api"
    assert response.fallback_level == FallbackLevel.CLOUD_LLM
    assert response.text == "cloud-ok"
    # ...and the wire payload was redacted.
    sent = captured_create["messages"][0]["content"]
    assert "alice@example.org" not in sent
    assert "<EMAIL_1>" in sent
