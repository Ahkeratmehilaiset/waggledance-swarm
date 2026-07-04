# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import asyncio
import json

from waggledance.application.dto.chat_dto import ChatRequest
from waggledance.application.services.chat_service import ChatService
from waggledance.core.magma.chat_served_emitter import ChatServedEvidenceEmitter
from waggledance.core.orchestration.routing_policy import select_route


def test_chat_service_emits_served_evidence_for_llm_result(
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    captured: list[dict] = []
    emitter = ChatServedEvidenceEmitter(sink=lambda evidence: captured.append(dict(evidence)))
    svc = ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
        chat_served_emitter=emitter,
    )

    async def _run() -> None:
        result = await svc.handle(ChatRequest(query="private chat DO_NOT_LEAK"))
        assert result.source == "llm"

    asyncio.run(_run())

    assert len(captured) == 1
    evidence = captured[0]
    assert evidence["source"] == "llm"
    assert evidence["cached"] is False
    assert evidence["route_stage_names"][-1] == "orchestrator_llm_fallback"
    serialized = json.dumps(evidence, sort_keys=True)
    assert "private chat" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_chat_service_emits_served_evidence_for_hotcache_result(
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    captured: list[dict] = []
    mock_hot_cache.get.return_value = "cached answer"
    svc = ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
        chat_served_emitter=ChatServedEvidenceEmitter(
            sink=lambda evidence: captured.append(dict(evidence))
        ),
    )

    async def _run() -> None:
        result = await svc.handle(ChatRequest(query="cached private DO_NOT_LEAK"))
        assert result.cached is True

    asyncio.run(_run())

    mock_orchestrator.handle_task.assert_not_called()
    assert len(captured) == 1
    assert captured[0]["source"] == "hotcache"
    assert captured[0]["cached"] is True
    assert captured[0]["route_stage_names"] == ["language_detection", "hot_cache"]
    assert "DO_NOT_LEAK" not in json.dumps(captured[0], sort_keys=True)


def test_chat_service_served_evidence_failure_does_not_break_chat(
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    class BrokenEmitter:
        def emit(self, **_kwargs):
            raise RuntimeError("evidence sink unavailable")

    svc = ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
        chat_served_emitter=BrokenEmitter(),
    )

    async def _run() -> None:
        result = await svc.handle(ChatRequest(query="still served"))
        assert result.response == "Test answer"

    asyncio.run(_run())
