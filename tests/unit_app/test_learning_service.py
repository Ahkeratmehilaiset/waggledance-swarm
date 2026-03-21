"""Tests for LearningService — BUG 3 regression: convergence stall detection."""

import asyncio

import pytest
from unittest.mock import AsyncMock

from waggledance.application.services.learning_service import LearningService
from waggledance.core.domain.events import EventType


@pytest.fixture
def learning_service(mock_orchestrator, mock_memory_service, mock_llm, mock_event_bus):
    return LearningService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        llm=mock_llm,
        event_bus=mock_event_bus,
        stall_threshold=3,
    )


class TestLearningService:
    def test_returns_new_facts_count(self, learning_service, mock_llm):
        async def _run():
            mock_llm.generate.side_effect = ["A specific fact about bees.", "YES valid"]
            result = await learning_service.run_night_tick()
            assert result["new_facts"] == 1
        asyncio.run(_run())

    def test_empty_cycle_increments_counter(self, learning_service, mock_llm):
        async def _run():
            mock_llm.generate.side_effect = ["A fact", "NO invalid"]
            result = await learning_service.run_night_tick()
            assert result["new_facts"] == 0
            assert result["consecutive_empty"] == 1
        asyncio.run(_run())

    def test_stall_detected_after_n_cycles(self, learning_service, mock_llm):
        async def _run():
            mock_llm.generate.side_effect = ["fact", "NO"] * 3
            for _ in range(2):
                result = await learning_service.run_night_tick()
                assert result["stall_detected"] is False
            result = await learning_service.run_night_tick()
            assert result["stall_detected"] is True
        asyncio.run(_run())

    def test_stall_resets_counter(self, learning_service, mock_llm):
        async def _run():
            mock_llm.generate.side_effect = ["fact", "NO"] * 4
            for _ in range(3):
                await learning_service.run_night_tick()
            result = await learning_service.run_night_tick()
            assert result["consecutive_empty"] <= 1
        asyncio.run(_run())

    def test_stall_publishes_event(self, learning_service, mock_llm, mock_event_bus):
        async def _run():
            mock_llm.generate.side_effect = ["fact", "NO"] * 3
            for _ in range(3):
                await learning_service.run_night_tick()
            calls = mock_event_bus.publish.call_args_list
            stall_events = [c for c in calls if c[0][0].type == EventType.NIGHT_STALL_DETECTED]
            assert len(stall_events) == 1
        asyncio.run(_run())

    def test_nonempty_cycle_resets_counter(self, learning_service, mock_llm):
        async def _run():
            mock_llm.generate.side_effect = [
                "fact", "NO",
                "fact", "NO",
                "A valid fact", "YES",
            ]
            await learning_service.run_night_tick()
            await learning_service.run_night_tick()
            result = await learning_service.run_night_tick()
            assert result["new_facts"] == 1
            assert result["consecutive_empty"] == 0
        asyncio.run(_run())
