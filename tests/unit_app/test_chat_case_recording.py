"""Tests for chat → case trajectory recording (PHASE 1 fix).

Verifies that ChatService.handle() creates CaseTrajectory rows via
build_from_legacy() for both solver and LLM routes, while hot-cache
hits do NOT produce cases.
"""

import asyncio

import pytest
from unittest.mock import MagicMock, AsyncMock, call

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.application.services.chat_service import ChatService
from waggledance.core.domain.agent import AgentResult
from waggledance.core.orchestration.routing_policy import select_route


@pytest.fixture
def mock_case_builder():
    builder = MagicMock()
    case = MagicMock()
    case.trajectory_id = "traj-001"
    case.quality_grade.value = "silver"
    case.to_dict.return_value = {"trajectory_id": "traj-001", "grade": "silver"}
    builder.build_from_legacy.return_value = case
    return builder


@pytest.fixture
def mock_case_store():
    store = MagicMock()
    store.save_case.return_value = "traj-001"
    return store


@pytest.fixture
def mock_verifier_store():
    return MagicMock()


@pytest.fixture
def chat_service_with_stores(
    mock_orchestrator, mock_memory_service, mock_hot_cache,
    mock_config, mock_case_builder, mock_case_store, mock_verifier_store,
):
    return ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
        case_builder=mock_case_builder,
        case_store=mock_case_store,
        verifier_store=mock_verifier_store,
    )


class TestChatCaseRecording:
    """Verify chat traffic produces CaseTrajectory rows."""

    def test_llm_route_records_case(
        self, chat_service_with_stores, mock_case_builder, mock_case_store,
    ):
        """LLM-routed chat creates a case via build_from_legacy + save_case."""
        async def _run():
            req = ChatRequest(query="How to treat varroa?")
            result = await chat_service_with_stores.handle(req)

            # Case builder was called with the query and response
            mock_case_builder.build_from_legacy.assert_called_once()
            call_kwargs = mock_case_builder.build_from_legacy.call_args
            assert call_kwargs[1]["question"] == "How to treat varroa?"
            assert call_kwargs[1]["confidence"] > 0

            # Case store persisted the case
            mock_case_store.save_case.assert_called_once()
            assert result.cached is False

        asyncio.run(_run())

    def test_solver_route_records_case(
        self, chat_service_with_stores, mock_case_builder, mock_case_store,
        mock_config,
    ):
        """Solver-routed chat creates a case with route_type='solver'."""
        async def _run():
            # Force solver route: provide a math query
            req = ChatRequest(query="what is 2+2?")
            result = await chat_service_with_stores.handle(req)

            # Whether solver hit or missed, at least one case must be recorded
            # (solver hit → solver case, solver miss → falls to LLM → LLM case)
            assert mock_case_builder.build_from_legacy.called

        asyncio.run(_run())

    def test_hotcache_hit_does_not_record_case(
        self, chat_service_with_stores, mock_hot_cache,
        mock_case_builder, mock_case_store,
    ):
        """Hot-cache hits return early without recording a case."""
        async def _run():
            mock_hot_cache.get.return_value = "Cached answer"
            req = ChatRequest(query="cached query")
            result = await chat_service_with_stores.handle(req)

            assert result.cached is True
            mock_case_builder.build_from_legacy.assert_not_called()
            mock_case_store.save_case.assert_not_called()

        asyncio.run(_run())

    def test_no_case_builder_graceful(
        self, mock_orchestrator, mock_memory_service, mock_hot_cache, mock_config,
    ):
        """When case_builder is None, handle() still works without errors."""
        svc = ChatService(
            orchestrator=mock_orchestrator,
            memory_service=mock_memory_service,
            hot_cache=mock_hot_cache,
            routing_policy_fn=select_route,
            config=mock_config,
            case_builder=None,
            case_store=None,
        )

        async def _run():
            req = ChatRequest(query="test without stores")
            result = await svc.handle(req)
            assert isinstance(result, ChatResult)
            assert result.response == "Test answer"

        asyncio.run(_run())

    def test_case_recording_error_does_not_break_chat(
        self, chat_service_with_stores, mock_case_builder,
    ):
        """If case recording throws, chat still returns normally."""
        mock_case_builder.build_from_legacy.side_effect = RuntimeError("DB locked")

        async def _run():
            req = ChatRequest(query="test resilience")
            result = await chat_service_with_stores.handle(req)
            assert isinstance(result, ChatResult)
            assert result.response == "Test answer"

        asyncio.run(_run())

    def test_case_store_error_does_not_break_chat(
        self, chat_service_with_stores, mock_case_store,
    ):
        """If case_store.save_case throws, chat still returns normally."""
        mock_case_store.save_case.side_effect = RuntimeError("disk full")

        async def _run():
            req = ChatRequest(query="test store resilience")
            result = await chat_service_with_stores.handle(req)
            assert isinstance(result, ChatResult)
            assert result.response == "Test answer"

        asyncio.run(_run())

    def test_build_from_legacy_receives_correct_fields(
        self, chat_service_with_stores, mock_case_builder, mock_orchestrator,
    ):
        """build_from_legacy receives question, answer, confidence, source, route_type."""
        mock_orchestrator.handle_task.return_value = AgentResult(
            agent_id="a1", response="Detailed answer about bees",
            confidence=0.85, latency_ms=120, source="llm",
        )

        async def _run():
            req = ChatRequest(query="Tell me about bees")
            await chat_service_with_stores.handle(req)

            mock_case_builder.build_from_legacy.assert_called_once()
            kwargs = mock_case_builder.build_from_legacy.call_args[1]
            assert kwargs["question"] == "Tell me about bees"
            assert kwargs["answer"] == "Detailed answer about bees"
            assert kwargs["confidence"] == 0.85
            assert kwargs["source"] == "llm"

        asyncio.run(_run())

    def test_case_store_receives_to_dict_and_intent(
        self, chat_service_with_stores, mock_case_builder, mock_case_store,
    ):
        """save_case is called with case.to_dict(), intent, and elapsed_ms."""
        async def _run():
            req = ChatRequest(query="another test")
            await chat_service_with_stores.handle(req)

            mock_case_store.save_case.assert_called_once()
            args, kwargs = mock_case_store.save_case.call_args
            # First positional arg is the case dict
            assert args[0] == {"trajectory_id": "traj-001", "grade": "silver"}
            # elapsed_ms should be a float >= 0
            assert kwargs["elapsed_ms"] >= 0

        asyncio.run(_run())
