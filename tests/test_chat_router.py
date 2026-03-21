"""Tests for unified ChatRouter."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.chat_router import ChatRouter, ChatResult


@pytest.mark.asyncio(loop_scope="function")
async def test_autonomy_first():
    """Autonomy path used when it returns a result."""
    auto = MagicMock()
    auto.handle_query.return_value = {
        "executed": True, "error": None,
        "result": {"answer": "35 astetta"}, "quality_path": "gold",
        "capability": "hive_temp"}
    router = ChatRouter(autonomy_service=auto)
    result = await router.route("Mikä on lämpötila?")
    assert result.method == "autonomy"
    assert result.response == "35 astetta"
    assert result.confidence == 0.9


@pytest.mark.asyncio(loop_scope="function")
async def test_fallback_to_legacy():
    """When autonomy fails, legacy is tried."""
    auto = MagicMock()
    auto.handle_query.side_effect = RuntimeError("Ollama down")
    legacy = MagicMock()
    legacy._do_chat = AsyncMock(return_value="Legacy vastaus")
    legacy._last_chat_agent_id = "beekeeper"
    legacy._last_chat_method = "memory_fast"
    router = ChatRouter(autonomy_service=auto, legacy_handler=legacy)
    result = await router.route("Mikä on lämpötila?")
    assert result.method == "legacy"
    assert result.fallback_used is True


@pytest.mark.asyncio(loop_scope="function")
async def test_final_fallback():
    """When all paths fail, returns error message."""
    router = ChatRouter()  # no services
    result = await router.route("Mikä on lämpötila?")
    assert result.method == "fallback"
    assert result.confidence == 0.0
    assert "Anteeksi" in result.response


@pytest.mark.asyncio(loop_scope="function")
async def test_stats_tracking():
    router = ChatRouter()
    await router.route("test1")
    await router.route("test2")
    assert router.stats["total_requests"] == 2
    assert router.stats["fallback_count"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_autonomy_empty_answer_falls_through():
    """Autonomy that returns empty answer should fall through."""
    auto = MagicMock()
    auto.handle_query.return_value = {
        "executed": True, "error": None,
        "result": {"answer": ""}, "quality_path": "bronze",
        "capability": "unknown"}
    router = ChatRouter(autonomy_service=auto)
    result = await router.route("test")
    assert result.method == "fallback"
