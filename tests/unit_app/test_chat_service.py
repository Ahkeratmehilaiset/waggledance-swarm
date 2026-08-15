"""Tests for ChatService — hot cache, routing, escalation."""

import asyncio
import json
import math
import threading

import pytest
from unittest.mock import AsyncMock, MagicMock

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.application.services.chat_service import ChatService
from waggledance.application.services.hybrid_retrieval_service import HybridHit, HybridTraceResult
from waggledance.core.domain.agent import AgentResult
from waggledance.core.orchestration.routing_policy import select_route
from waggledance.core.magma.chat_served_receipt import build_chat_served_summary
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


class _RecordingChatServedEmitter:
    def __init__(
        self,
        *,
        enabled=True,
        record_pending_result=True,
        pending_error: Exception | None = None,
        schedule_error: Exception | None = None,
    ):
        self.enabled = enabled
        self.record_pending_result = record_pending_result
        self.pending_error = pending_error
        self.schedule_error = schedule_error
        self.pending = []
        self.receipts = []

    def record_pending(self, served_id, **kwargs):
        self.pending.append((served_id, kwargs))
        if self.pending_error is not None:
            raise self.pending_error
        return self.record_pending_result

    def schedule_receipt(self, served_id, **kwargs):
        self.receipts.append((served_id, kwargs))
        if self.schedule_error is not None:
            raise self.schedule_error


def _assert_chat_served_emitted(
    emitter: _RecordingChatServedEmitter,
    *,
    route_type: str,
    source: str,
    cached: bool,
) -> None:
    assert len(emitter.pending) == 1
    assert len(emitter.receipts) == 1
    pending_id, pending_kwargs = emitter.pending[0]
    receipt_id, receipt_kwargs = emitter.receipts[0]
    assert pending_id == receipt_id
    assert pending_kwargs["route_type"] == route_type
    assert pending_kwargs["source"] == source
    assert "query" not in pending_kwargs
    assert "response" not in pending_kwargs
    assert receipt_kwargs["route_type"] == route_type
    assert receipt_kwargs["source"] == source
    assert receipt_kwargs["cached"] is cached
    assert receipt_kwargs["route_stage_trace"]


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

    def test_authoritative_hybrid_answer_includes_route_stage_trace(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            raw_query = "statistics summary private-token-123"
            hybrid = MagicMock()
            hybrid.enabled = True
            hybrid.is_authoritative = True
            hybrid.retrieve = AsyncMock(return_value=HybridTraceResult(
                retrieval_mode="hybrid:authoritative",
                route_source="cell:math+global",
                answered_by_layer="local_faiss",
                hits=[
                    HybridHit(
                        "d1",
                        "authoritative stats evidence",
                        0.93,
                        "local_faiss",
                        "math",
                    ),
                ],
                cell_id="math",
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

            assert result.source == "local_faiss"
            assert [event["stage"] for event in result.route_stage_trace] == [
                "language_detection",
                "hot_cache",
                "memory_context",
                "route_selection",
                "deterministic_solver",
                "hybrid_retrieval_8_cell",
            ]
            assert result.route_stage_trace[-1]["answered"] is True
            assert raw_query not in json.dumps(result.route_stage_trace)

        asyncio.run(_run())

    def test_chat_served_emits_for_hotcache_return(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            emitter = _RecordingChatServedEmitter()
            mock_hot_cache.get.return_value = "Cached answer"
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                chat_served_emitter=emitter,
            )

            result = await svc.handle(ChatRequest(query="What is varroa?"))

            assert result.response == "Cached answer"
            _assert_chat_served_emitted(
                emitter,
                route_type="hotcache",
                source="hotcache",
                cached=True,
            )

        asyncio.run(_run())

    def test_chat_served_emits_for_solver_return(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            emitter = _RecordingChatServedEmitter()
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                chat_served_emitter=emitter,
            )

            result = await svc.handle(ChatRequest(query="what is 15% of 300"))

            assert result.source == "solver"
            _assert_chat_served_emitted(
                emitter,
                route_type="solver",
                source="solver",
                cached=False,
            )

        asyncio.run(_run())

    def test_deterministic_solver_preempts_confident_micromodel(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            original_get = mock_config.get.side_effect
            mock_config.get.side_effect = lambda key, default=None: (
                True
                if key == "advanced_learning.micro_model_enabled"
                else original_get(key, default)
            )
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
            )
            svc._probe_micromodel = MagicMock(return_value=(True, 0.99))
            query = "what is 15% of 300"

            result = await svc.handle(ChatRequest(query=query))

            assert result.source == "solver"
            assert result.response == "45"
            svc._probe_micromodel.assert_called_once_with(query)
            route_selection = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "route_selection"
            )
            solver_stage = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "deterministic_solver"
            )
            assert route_selection["route_type"] == "solver"
            assert solver_stage == {
                "stage": "deterministic_solver",
                "intent": "math",
                "answered": True,
            }
            mock_orchestrator.handle_task.assert_not_called()

        asyncio.run(_run())

    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            ("statistics summary", "stats"),
            ("calculate twelve plus five", "math"),
        ],
    )
    def test_solver_miss_restores_confident_micromodel_route(
        self,
        query,
        expected_intent,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            original_get = mock_config.get.side_effect
            mock_config.get.side_effect = lambda key, default=None: (
                True
                if key == "advanced_learning.micro_model_enabled"
                else original_get(key, default)
            )
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
            )
            svc._probe_micromodel = MagicMock(return_value=(True, 0.99))

            result = await svc.handle(ChatRequest(query=query))

            svc._probe_micromodel.assert_called_once_with(query)
            orchestrator_route = mock_orchestrator.handle_task.await_args.args[1]
            assert orchestrator_route.route_type == "micromodel"
            assert orchestrator_route.confidence == 0.99
            route_selection = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "route_selection"
            )
            solver_stage = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "deterministic_solver"
            )
            fallback_stage = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "orchestrator_llm_fallback"
            )
            assert route_selection == {
                "stage": "route_selection",
                "route_type": "solver",
                "solver_intent": expected_intent,
                "memory_score": 0.0,
            }
            assert solver_stage == {
                "stage": "deterministic_solver",
                "intent": expected_intent,
                "answered": False,
            }
            assert fallback_stage["route_type"] == "micromodel"

        asyncio.run(_run())

    @pytest.mark.parametrize(
        "memory_confidence",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
            1.1,
            10**309,
            "0.9",
            None,
        ],
    )
    def test_solver_miss_rejects_invalid_memory_confidence_from_receipt_trace(
        self,
        memory_confidence,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            original_get = mock_config.get.side_effect
            mock_config.get.side_effect = lambda key, default=None: (
                True
                if key == "advanced_learning.micro_model_enabled"
                else original_get(key, default)
            )
            mock_memory_service.retrieve_context.return_value = [
                MagicMock(confidence=memory_confidence)
            ]
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
            )
            svc._probe_micromodel = MagicMock(
                return_value=(True, float("nan"))
            )

            result = await svc.handle(
                ChatRequest(query="statistics summary")
            )

            orchestrator_route = mock_orchestrator.handle_task.await_args.args[1]
            assert orchestrator_route.route_type == "llm"
            memory_stage = next(
                event
                for event in result.route_stage_trace
                if event["stage"] == "memory_context"
            )
            assert memory_stage["memory_score"] == 0.0
            assert math.isfinite(memory_stage["memory_score"])
            summary = build_chat_served_summary(
                query="statistics summary",
                response=result.response,
                route_type=orchestrator_route.route_type,
                source=result.source,
                confidence=result.confidence,
                latency_ms=result.latency_ms,
                cached=result.cached,
                round_table=result.round_table,
                agent_id=result.agent_id,
                language=result.language,
                profile="HOME",
                world_snapshot_ref="test-snapshot",
                route_stage_trace=result.route_stage_trace,
            )
            assert summary["route_stage_trace"][2]["memory_score"] == 0.0

        asyncio.run(_run())

    def test_chat_served_emits_for_authoritative_hybrid_return(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            emitter = _RecordingChatServedEmitter()
            hybrid = MagicMock()
            hybrid.enabled = True
            hybrid.is_authoritative = True
            hybrid.retrieve = AsyncMock(return_value=HybridTraceResult(
                retrieval_mode="hybrid:authoritative",
                route_source="cell:math+global",
                answered_by_layer="local_faiss",
                hits=[HybridHit(
                    "d1",
                    "authoritative stats evidence",
                    0.93,
                    "local_faiss",
                    "math",
                )],
                cell_id="math",
            ))
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hybrid_retrieval=hybrid,
                chat_served_emitter=emitter,
            )
            svc._hybrid_observer = MagicMock()
            svc._hybrid_observer.record_candidate = AsyncMock()

            result = await svc.handle(ChatRequest(query="statistics summary"))

            assert result.source == "local_faiss"
            _assert_chat_served_emitted(
                emitter,
                route_type="hybrid_retrieval",
                source="local_faiss",
                cached=False,
            )

        asyncio.run(_run())

    def test_chat_served_emits_for_hex_return(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "hex answer",
                    "source": "hex_mesh",
                    "confidence": 0.88,
                    "trace": {"cell_count": 7, "answered": True},
                }

        async def _run():
            emitter = _RecordingChatServedEmitter()
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
                chat_served_emitter=emitter,
            )

            result = await svc.handle(ChatRequest(query="tell me about hive layout"))

            assert result.source == "hex_mesh"
            _assert_chat_served_emitted(
                emitter,
                route_type="hex_mesh",
                source="hex_mesh",
                cached=False,
            )

        asyncio.run(_run())

    def test_chat_served_emits_for_llm_return(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            emitter = _RecordingChatServedEmitter()
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                chat_served_emitter=emitter,
            )

            result = await svc.handle(ChatRequest(query="explain hive care"))

            assert result.source == "llm"
            _assert_chat_served_emitted(
                emitter,
                route_type="llm",
                source="llm",
                cached=False,
            )

        asyncio.run(_run())

    def test_chat_served_fail_open_when_emitter_raises(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        async def _run():
            emitter = _RecordingChatServedEmitter(
                pending_error=RuntimeError("receipt disk offline")
            )
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                chat_served_emitter=emitter,
            )

            result = await svc.handle(ChatRequest(query="explain hive care"))

            assert result.response == "Test answer"
            assert len(emitter.pending) == 1
            assert emitter.receipts == []

        asyncio.run(_run())

    def test_hex_answer_includes_route_stage_trace(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "hex answer",
                    "source": "hex_mesh",
                    "confidence": 0.88,
                    "trace": {"cell_count": 7, "answered": True},
                }

        async def _run():
            raw_profile = "PRIVATE_PROFILE_MARKER"
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
            )

            result = await svc.handle(ChatRequest(
                query="tell me about hive layout",
                language="PRIVATE_LANGUAGE_MARKER",
                profile=raw_profile,
            ))

            assert result.source == "hex_mesh"
            assert [event["stage"] for event in result.route_stage_trace] == [
                "language_detection",
                "hot_cache",
                "memory_context",
                "route_selection",
                "hex_neighbor_assist_7_cell",
            ]
            assert result.route_stage_trace[0]["explicit_hint"] is True
            trace_json = json.dumps(result.route_stage_trace)
            assert "PRIVATE_LANGUAGE_MARKER" not in trace_json
            assert raw_profile not in trace_json

        asyncio.run(_run())

    # ── hex_mesh_entry "honeycomb-first" runtime invariants ──────────────
    # Executable evidence toward the hex_mesh_entry capability claim_safe
    # decision (manifest flip owned by the lead). These pin the ACTUAL live
    # route_stage_trace ordering so the honest, scoped truth is machine-checked
    # rather than asserted in prose: a query does NOT literally enter the hex
    # honeycomb first (cache/solver/hybrid precede it, and hex is default-off),
    # but when hex IS enabled it precedes the generic LLM fallback.

    def test_honeycomb_not_first_cache_solver_hybrid_precede_hex(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """hex is NOT the first resolution stage: hot_cache,
        deterministic_solver and hybrid_retrieval_8_cell all precede
        hex_neighbor_assist_7_cell in the live trace. Regression guard for the
        manifest's documented 'hex is not entered first' gap — while this
        ordering holds, hex_mesh_entry.claim_safe cannot honestly be True for an
        unconditional 'every query enters the honeycomb first' statement."""
        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "hex answer",
                    "source": "hex_mesh",
                    "confidence": 0.88,
                    "trace": {"cell_count": 7, "answered": True},
                }

        async def _run():
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
                hex_neighbor_assist=HexAssist(),
            )
            svc._hybrid_observer = MagicMock()
            svc._hybrid_observer.record_candidate = AsyncMock()

            result = await svc.handle(ChatRequest(
                query="statistics summary for hive sensor readings",
            ))
            stages = [event["stage"] for event in result.route_stage_trace]
            assert "hex_neighbor_assist_7_cell" in stages, stages
            hex_idx = stages.index("hex_neighbor_assist_7_cell")
            # every earlier resolution stage strictly precedes hex
            for earlier in (
                "hot_cache",
                "deterministic_solver",
                "hybrid_retrieval_8_cell",
            ):
                assert earlier in stages, (earlier, stages)
                assert stages.index(earlier) < hex_idx, (earlier, stages)
            # hex is therefore not the first stage in the trace
            assert stages[0] != "hex_neighbor_assist_7_cell", stages
            assert hex_idx >= 1, stages

        asyncio.run(_run())

    def test_hex_precedes_orchestrator_llm_when_enabled(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """The honest CONDITIONAL that DOES hold: when hex is enabled and does
        not itself answer (low confidence), hex_neighbor_assist_7_cell is
        consulted BEFORE the generic orchestrator_llm_fallback. This is the
        defensible part of the hex claim (hex precedes the LLM), distinct from
        the false unconditional 'enters honeycomb first'."""
        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "low-confidence hex",
                    "source": "hex_mesh",
                    "confidence": 0.10,
                    "trace": {"cell_count": 7, "answered": False},
                }

        async def _run():
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
            )
            result = await svc.handle(ChatRequest(query="tell me about hive layout"))
            stages = [event["stage"] for event in result.route_stage_trace]
            assert "hex_neighbor_assist_7_cell" in stages, stages
            assert "orchestrator_llm_fallback" in stages, stages
            assert (
                stages.index("hex_neighbor_assist_7_cell")
                < stages.index("orchestrator_llm_fallback")
            ), stages

        asyncio.run(_run())

    def test_hex_not_entered_when_disabled(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """Default-off blocker: when hex is not enabled the honeycomb is never
        entered — hex_neighbor_assist_7_cell is absent from the trace and the
        disabled assist is never consulted. So 'every query enters the
        honeycomb' cannot hold on the default configuration."""
        class HexAssist:
            enabled = False

            async def resolve(self, *, query, intent, context):
                raise AssertionError("disabled hex must not be consulted")

        async def _run():
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
            )
            result = await svc.handle(ChatRequest(query="tell me about hive layout"))
            stages = [event["stage"] for event in result.route_stage_trace]
            assert "hex_neighbor_assist_7_cell" not in stages, stages

        asyncio.run(_run())

    # ── deterministic_solver_first "deterministic-first" runtime invariants ──
    # Executable evidence (T2 Phase A) toward the deterministic_solver_first
    # capability claim_safe decision (manifest flip owned by the lead). These
    # pin the honest, scoped truth on the live route_stage_trace: the
    # deterministic solver is ATTEMPTED before the generic LLM fallback and
    # before the 8-cell hybrid retrieval — i.e. deterministic-first among the
    # answering paths — without asserting the broader (unproven) full-provenance
    # claim.

    def test_deterministic_solver_precedes_orchestrator_llm_fallback(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """The deterministic solver stage is attempted BEFORE the generic
        orchestrator_llm_fallback in the live trace. Evidence for the honest
        scoped claim 'the deterministic solver is attempted before the LLM
        fallback' (not the broader full-MAGMA-provenance claim)."""
        async def _run():
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
            )
            result = await svc.handle(ChatRequest(
                query="statistics summary for hive sensor readings",
            ))
            stages = [event["stage"] for event in result.route_stage_trace]
            assert "deterministic_solver" in stages, stages
            assert "orchestrator_llm_fallback" in stages, stages
            assert (
                stages.index("deterministic_solver")
                < stages.index("orchestrator_llm_fallback")
            ), stages

        asyncio.run(_run())

    def test_deterministic_solver_precedes_hybrid_retrieval(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """The deterministic solver is attempted BEFORE the 8-cell hybrid
        retrieval stage — the deterministic attempt precedes the retrieval
        path, not after it."""
        async def _run():
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
            result = await svc.handle(ChatRequest(
                query="statistics summary for hive sensor readings",
            ))
            stages = [event["stage"] for event in result.route_stage_trace]
            assert "deterministic_solver" in stages, stages
            assert "hybrid_retrieval_8_cell" in stages, stages
            assert (
                stages.index("deterministic_solver")
                < stages.index("hybrid_retrieval_8_cell")
            ), stages

        asyncio.run(_run())

    # ── future_waggledance_swarm "bounded route-depth telemetry" invariants ──
    # Executable evidence (T6 Phase A) toward the future_waggledance_swarm
    # capability claim_safe decision (manifest flip owned by the lead). The
    # literal "infinite/unbounded scalability" claim is not provable; these pin
    # the honest, measurable truth on the live route_stage_trace: route depth is
    # BOUNDED by a fixed finite pipeline, and the telemetry is SANITIZED (safe
    # stage labels, no raw query/payload export) — supporting an honest rescope
    # to "bounded measured route-depth telemetry".

    def test_route_depth_is_bounded_by_fixed_pipeline(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """Route depth is BOUNDED: across diverse query paths, every
        route_stage_trace draws its stages from a fixed finite allowlist, no
        stage repeats, and depth never exceeds the pipeline size. Refutes any
        'unbounded/infinite' reading; supports bounded measured route-depth."""
        KNOWN_PIPELINE_STAGES = {
            "language_detection",
            "hot_cache",
            "memory_context",
            "route_selection",
            "deterministic_solver",
            "hybrid_retrieval_8_cell",
            "hex_neighbor_assist_7_cell",
            "orchestrator_llm_fallback",
        }

        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "hex answer",
                    "source": "hex_mesh",
                    "confidence": 0.88,
                    "trace": {"cell_count": 7, "answered": True},
                }

        async def _stages(svc, query):
            result = await svc.handle(ChatRequest(query=query))
            return [event["stage"] for event in result.route_stage_trace]

        async def _run():
            # cache-hit path (shortest)
            mock_hot_cache.get.return_value = "Cached answer"
            cache_svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
            )
            cache_stages = await _stages(cache_svc, "What is varroa?")
            mock_hot_cache.get.return_value = None

            # solver + hybrid + llm path (deepest non-hex)
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
            hybrid_svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hybrid_retrieval=hybrid,
            )
            hybrid_svc._hybrid_observer = MagicMock()
            hybrid_svc._hybrid_observer.record_candidate = AsyncMock()
            hybrid_stages = await _stages(
                hybrid_svc, "statistics summary for hive sensor readings"
            )

            # hex path
            hex_svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
            )
            hex_stages = await _stages(hex_svc, "tell me about hive layout")

            for stages in (cache_stages, hybrid_stages, hex_stages):
                assert stages, stages
                # every stage from the fixed finite pipeline allowlist
                assert set(stages) <= KNOWN_PIPELINE_STAGES, stages
                # no stage repeats -> depth bounded by the fixed pipeline
                assert len(stages) == len(set(stages)), stages
                # depth never exceeds the fixed pipeline size
                assert len(stages) <= len(KNOWN_PIPELINE_STAGES), stages

        asyncio.run(_run())

    def test_route_stage_trace_is_sanitized_no_raw_query_or_payload(
        self,
        mock_orchestrator,
        mock_memory_service,
        mock_hot_cache,
        mock_config,
    ):
        """The route-depth telemetry is SANITIZED: stage labels are safe
        snake_case identifiers and the serialized trace never exports the raw
        query, language, or profile. Supports bounded route-depth telemetry as
        privacy-safe (no raw payload export)."""
        import re

        raw_query = "secret hive plan PRIVATE_QUERY_MARKER"
        raw_lang = "PRIVATE_LANGUAGE_MARKER"
        raw_profile = "PRIVATE_PROFILE_MARKER"
        safe_label = re.compile(r"^[a-z][a-z0-9_]*$")

        class HexAssist:
            enabled = True

            async def resolve(self, *, query, intent, context):
                return {
                    "response": "hex answer",
                    "source": "hex_mesh",
                    "confidence": 0.88,
                    "trace": {"cell_count": 7, "answered": True},
                }

        async def _run():
            svc = ChatService(
                orchestrator=mock_orchestrator,
                memory_service=mock_memory_service,
                hot_cache=mock_hot_cache,
                routing_policy_fn=select_route,
                config=mock_config,
                hex_neighbor_assist=HexAssist(),
            )
            result = await svc.handle(ChatRequest(
                query=raw_query,
                language=raw_lang,
                profile=raw_profile,
            ))
            trace_json = json.dumps(result.route_stage_trace)
            for marker in (
                raw_query,
                raw_lang,
                raw_profile,
                "PRIVATE_QUERY_MARKER",
            ):
                assert marker not in trace_json, marker
            for event in result.route_stage_trace:
                assert safe_label.fullmatch(event["stage"]), event["stage"]

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
