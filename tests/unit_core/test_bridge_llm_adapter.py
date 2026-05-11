# SPDX-License-Identifier: BUSL-1.1
"""Audit H49 / D3.4 regression — BridgeLLMAdapter routes via BridgeLLMClient.

Before this fix, container.llm returned OllamaAdapter directly,
bypassing the BridgeLLMClient 4-tier fallback chain along with its
BridgeLLMRedactor (cloud-PII protection). These tests pin:
- BridgeLLMAdapter delegates LLMPort.generate to BridgeLLMClient.run
- Fallback to the direct adapter when BridgeLLMClient is disabled
- Fallback when BridgeLLMClient.run raises
- Fallback when BridgeLLMClient returns empty/whitespace text
- close() forwards to the fallback adapter
- is_available defers to fallback adapter

The adapter never raises; production callers continue using
``await llm.generate(prompt=...)`` exactly as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from waggledance.adapters.llm.bridge_llm_adapter import BridgeLLMAdapter


def _fake_response(text: str = "bridge text"):
    return SimpleNamespace(
        text=text,
        fallback_level=1,
        provider="cache",
        success=True,
        latency_ms=1.0,
    )


def _fake_client(enabled: bool = True, response_text: str = "bridge text",
                 raises: bool = False):
    client = SimpleNamespace()
    client.is_enabled = lambda: enabled
    if raises:
        def _boom(request):
            raise RuntimeError("bridge client failed")
        client.run = _boom
    else:
        client.run = lambda request: _fake_response(response_text)
    return client


def _fake_fallback(response: str = "fallback text"):
    fb = SimpleNamespace()
    fb.generate = AsyncMock(return_value=response)
    fb.is_available = AsyncMock(return_value=True)
    fb.close = AsyncMock()
    return fb


class TestBridgeLlmAdapterRouting:
    @pytest.mark.asyncio
    async def test_enabled_client_response_used(self):
        client = _fake_client(enabled=True, response_text="from bridge")
        fb = _fake_fallback("from fallback")
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        result = await adapter.generate(prompt="hi")
        assert result == "from bridge"
        fb.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_client_falls_back(self):
        client = _fake_client(enabled=False)
        fb = _fake_fallback("from fallback")
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        result = await adapter.generate(prompt="hi")
        assert result == "from fallback"
        fb.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_client_exception_falls_back(self):
        client = _fake_client(enabled=True, raises=True)
        fb = _fake_fallback("rescued")
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        result = await adapter.generate(prompt="hi")
        assert result == "rescued"
        fb.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_bridge_text_falls_back(self):
        client = _fake_client(enabled=True, response_text="   ")
        fb = _fake_fallback("real response")
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        result = await adapter.generate(prompt="hi")
        assert result == "real response"

    @pytest.mark.asyncio
    async def test_generate_kwargs_forwarded_on_fallback(self):
        client = _fake_client(enabled=False)
        fb = _fake_fallback("ok")
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        await adapter.generate(
            prompt="hi", model="phi4-mini",
            temperature=0.42, max_tokens=123,
        )
        call = fb.generate.await_args
        assert call.kwargs == {
            "prompt": "hi", "model": "phi4-mini",
            "temperature": 0.42, "max_tokens": 123,
        }


class TestBridgeLlmAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_close_forwards_to_fallback(self):
        client = _fake_client()
        fb = _fake_fallback()
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        await adapter.close()
        fb.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_available_defers_to_fallback(self):
        client = _fake_client()
        fb = _fake_fallback()
        fb.is_available = AsyncMock(return_value=False)
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        assert await adapter.is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_swallows_fallback_exception(self):
        client = _fake_client()
        fb = _fake_fallback()
        async def _boom():
            raise RuntimeError("ollama down")
        fb.is_available = _boom
        adapter = BridgeLLMAdapter(client=client, fallback_adapter=fb)
        # Returns False rather than raising — readiness path must
        # never crash the /ready endpoint.
        assert await adapter.is_available() is False
