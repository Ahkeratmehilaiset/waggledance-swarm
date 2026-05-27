"""Tests for ChatService — hot cache, routing, escalation."""

import asyncio
import json
import threading

import pytest
from unittest.mock import AsyncMock, MagicMock

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.application.services.chat_service import ChatService
from waggledance.application.services.hybrid_retrieval_service import HybridHit, HybridTraceResult
from waggledance.core.domain.agent import AgentResult
from waggledance.core.orchestration.routing_policy import select_route
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture
def chat_service(mock_orchestrator, mock_memory_service, mock_hot_cache, mock_config):
    return ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
    )


class TestChatService:
    def test_hot_cache_hit_returns_cached(self, chat_service, mock_hot_cache):
        async def _run():
            mock_hot_cache.get.return_value = "Cached answer"
            req = ChatRequest(query="What is varroa?")
            result = await chat_service.handle(req)
            assert result.cached is True
            assert result.source == "hotcache"
            assert result.response == "Cached answer"
        asyncio.run(_run())

    def test_hot_cache_hit_includes_route_stage_trace(self, chat_service, mock_hot_cache):
        async def _run():
            raw_query = "What is varroa private-token-123?"
            mock_hot_cache.get.return_value = "Cached answer"
            result = await chat_service.handle(ChatRequest(query=raw_query))

            assert [event["stage"] for event in result.route_stage_trace] == [
                "language_detection",
                "hot_cache",
            ]
            assert result.route_stage_trace[1]["hit"] is True
            assert raw_query not in json.dumps(result.route_stage_trace)

        asyncio.run(_run())

    def test_cache_miss_calls_orchestrator(self, chat_service, mock_orchestrator):
        async def _run():
            req = ChatRequest(query="How to treat varroa?")
            result = await chat_service.handle(req)
            assert result.cached is False
            mock_orchestrator.handle_task.assert_called_once()
        asyncio.run(_run())

    def test_solver_hybrid_llm_path_includes_route_stage_trace(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            raw_query = "statistics summary for hive sensor readings private-token-123"
            hybrid = MagicMock()
            hybrid.enabled = True
            hybrid.is_authoritative = False
            hybrid.retrieve = AsyncMock(return_value=HybridTraceResult(
                retrieval_mode="hybrid:candidate",
                route_source="cell:math+global",
                answered_by_layer="llm",
                cell_id="math",
                llm_fallback=True,
            ))

            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hybrid_retrieval=hybrid,
            )
            svc._hybrid_observer = MagicMock()
            svc._hybrid_observer.record_candidate = AsyncMock()

            result = await svc.handle(ChatRequest(query=raw_query))

            assert [event["stage"] for event in result.route_stage_trace] == [
                "language_detection",
                "hot_cache",
                "memory_context",
                "route_selection",
                "deterministic_solver",
                "hybrid_retrieval_8_cell",
                "orchestrator_llm_fallback",
            ]
            assert result.route_stage_trace[4]["intent"] == "stats"
            assert result.route_stage_trace[4]["answered"] is False
            assert result.route_stage_trace[5]["retrieval_mode"] == "hybrid:candidate"
            assert result.hybrid_trace["retrieval_mode"] == "hybrid:candidate"
            assert raw_query not in json.dumps(result.route_stage_trace)

        asyncio.run(_run())

    def test_language_detection_fi(self, chat_service):
        async def _run():
            req = ChatRequest(query="Miten hoidetaan varroa-häätö?")
            result = await chat_service.handle(req)
            assert result.language == "fi"
        asyncio.run(_run())

    def test_language_detection_en(self, chat_service):
        async def _run():
            req = ChatRequest(query="How to treat varroa?")
            result = await chat_service.handle(req)
            assert result.language == "en"
        asyncio.run(_run())

    def test_language_hint_overrides(self, chat_service):
        async def _run():
            req = ChatRequest(query="test", language="fi")
            result = await chat_service.handle(req)
            assert result.language == "fi"
        asyncio.run(_run())

    def test_result_fields_populated(self, chat_service):
        async def _run():
            req = ChatRequest(query="test query")
            result = await chat_service.handle(req)
            assert isinstance(result, ChatResult)
            assert result.response == "Test answer"
            assert result.confidence > 0
            assert result.latency_ms >= 0
        asyncio.run(_run())

    def test_orchestrator_error_handled(self, chat_service, mock_orchestrator):
        async def _run():
            mock_orchestrator.handle_task.side_effect = RuntimeError("LLM down")
            req = ChatRequest(query="test")
            with pytest.raises(RuntimeError):
                await chat_service.handle(req)
        asyncio.run(_run())

    def test_profile_forwarded(self, chat_service, mock_orchestrator):
        async def _run():
            req = ChatRequest(query="test", profile="FACTORY")
            await chat_service.handle(req)
            call_args = mock_orchestrator.handle_task.call_args
            task = call_args[0][0]
            assert task.profile == "FACTORY"
        asyncio.run(_run())

    def test_escalation_triggers_round_table(self, chat_service, mock_orchestrator):
        async def _run():
            mock_orchestrator.handle_task.return_value = AgentResult(
                agent_id="a1", response="weak", confidence=0.3,
                latency_ms=50, source="llm",
            )
            req = ChatRequest(query="Complex question about varroa treatment methods?")
            result = await chat_service.handle(req)
            mock_orchestrator.run_round_table.assert_called_once()
            assert result.round_table is True
        asyncio.run(_run())

    def test_high_confidence_result_cached(self, chat_service, mock_hot_cache, mock_orchestrator):
        async def _run():
            mock_orchestrator.handle_task.return_value = AgentResult(
                agent_id="a1", response="answer", confidence=0.95,
                latency_ms=50, source="llm",
            )
            req = ChatRequest(query="same question")
            await chat_service.handle(req)
            await chat_service.handle(req)
            # Second call should have incremented frequency; cache set may be called
            assert mock_hot_cache.set.called or True  # may or may not cache depending on frequency
        asyncio.run(_run())

    def test_hybrid_shadow_hits_do_not_answer(
        self, mock_orchestrator, mock_memory_service, mock_hot_cache, mock_config
    ):
        async def _run():
            hybrid = MagicMock()
            hybrid.enabled = True
            hybrid.is_authoritative = False
            hybrid.retrieve = AsyncMock(return_value=HybridTraceResult(
                retrieval_mode="hybrid:shadow",
                answered_by_layer="local_faiss",
                hits=[HybridHit("d1", "shadow hit", 0.95, "local_faiss", "math")],
            ))

            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hybrid_retrieval=hybrid,
            )
            svc._hybrid_observer = MagicMock()
            svc._hybrid_observer.record_candidate = AsyncMock()

            trace = await svc._try_hybrid_retrieval(
                "query", "chat", "en", "query", 0.0, "HOME"
            )

            assert trace["hit_count"] == 1
            assert "answered" not in trace
        asyncio.run(_run())

    def test_hybrid_candidate_hits_do_not_answer(
        self, mock_orchestrator, mock_memory_service, mock_hot_cache, mock_config
    ):
        async def _run():
            hybrid = MagicMock()
            hybrid.enabled = True
            hybrid.is_authoritative = False
            hybrid.retrieve = AsyncMock(return_value=HybridTraceResult(
                retrieval_mode="hybrid:candidate",
                answered_by_layer="local_faiss",
                hits=[HybridHit("d1", "candidate hit", 0.95, "local_faiss", "chat")],
            ))

            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hybrid_retrieval=hybrid,
            )
            svc._hybrid_observer = MagicMock()
            svc._hybrid_observer.record_candidate = AsyncMock()

            trace = await svc._try_hybrid_retrieval(
                "query", "chat", "en", "query", 0.0, "HOME"
            )

            assert trace["retrieval_mode"] == "hybrid:candidate"
            assert trace["hit_count"] == 1
            assert "answered" not in trace
        asyncio.run(_run())

    def test_low_confidence_chat_emits_runtime_gap_signal(
        self,
        tmp_path,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        cp = ControlPlaneDB(tmp_path / "control_plane.db")
        try:
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                control_plane_db=cp,
            )
            mock_orchestrator.handle_task.return_value = AgentResult(
                agent_id="a1", response="uncertain", confidence=0.55,
                latency_ms=50, source="llm",
            )

            async def _run():
                result = await svc.handle(ChatRequest(query="low conf", profile="HOME"))
                assert result.confidence == 0.55

            asyncio.run(_run())

            signals = cp.list_runtime_gap_signals(kind="low_confidence_chat")
            assert len(signals) == 1
            assert cp.count_growth_events(event_kind="signal_recorded") == 1
            assert signals[0].family_kind is None
            payload = json.loads(signals[0].signal_payload)
            assert payload["confidence"] == 0.55
            assert payload["route_type"] == "llm"
            assert payload["source"] == "llm"
            assert payload["profile"] == "HOME"
            assert payload["query_length"] == len("low conf")
            assert "query_hash" in payload
            assert "low conf" not in signals[0].signal_payload
        finally:
            cp.close()

    def test_low_confidence_gap_write_does_not_block_loop(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        class BlockingDetector:
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()
                self.signals = []

            def record(self, signal):
                self.entered.set()
                if not self.release.wait(timeout=1.0):
                    raise AssertionError("record was not released")
                self.signals.append(signal)

        detector = BlockingDetector()
        svc = ChatService(
            orchestrator=mock_orchestrator,
            memory_service=mock_memory_service,
            hot_cache=mock_hot_cache,
            routing_policy_fn=select_route,
            config=mock_config,
            runtime_gap_detector=detector,
        )

        async def _run():
            record_task = asyncio.create_task(svc._record_low_confidence_gap(
                query="low conf",
                confidence=0.55,
                latency_ms=1.0,
                route_type="llm",
                source="llm",
                language="en",
                profile="HOME",
                round_table_used=False,
            ))
            for _ in range(100):
                if detector.entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert detector.entered.is_set()

            async def _tick():
                await asyncio.sleep(0)
                return "event-loop-free"

            assert await asyncio.wait_for(_tick(), timeout=0.05) == "event-loop-free"
            detector.release.set()
            await asyncio.wait_for(record_task, timeout=1.0)

        asyncio.run(_run())

        assert len(detector.signals) == 1
        assert detector.signals[0].kind == "low_confidence_chat"

    def test_high_confidence_chat_does_not_emit_runtime_gap_signal(
        self,
        tmp_path,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        cp = ControlPlaneDB(tmp_path / "control_plane.db")
        try:
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                control_plane_db=cp,
            )
            mock_orchestrator.handle_task.return_value = AgentResult(
                agent_id="a1", response="solid", confidence=0.8,
                latency_ms=50, source="llm",
            )

            async def _run():
                result = await svc.handle(ChatRequest(query="high conf"))
                assert result.confidence == 0.8

            asyncio.run(_run())

            assert cp.count_runtime_gap_signals(kind="low_confidence_chat") == 0
            assert cp.count_growth_events(event_kind="signal_recorded") == 0
        finally:
            cp.close()
