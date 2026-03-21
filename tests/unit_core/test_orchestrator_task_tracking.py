"""Tests for Orchestrator._track_task — BUG 2 regression tests."""

import asyncio
import logging

import pytest

from waggledance.core.orchestration.orchestrator import Orchestrator


class TestTaskTracking:
    def _make_orchestrator(self, mock_config, mock_llm, mock_event_bus,
                           mock_memory_repo, mock_vector_store, mock_trust_store):
        from waggledance.core.orchestration.round_table import RoundTableEngine
        from waggledance.core.orchestration.scheduler import Scheduler

        scheduler = Scheduler(mock_config)
        round_table = RoundTableEngine(mock_llm, mock_event_bus)

        return Orchestrator(
            scheduler=scheduler,
            round_table=round_table,
            memory=mock_memory_repo,
            vector_store=mock_vector_store,
            llm=mock_llm,
            trust_store=mock_trust_store,
            event_bus=mock_event_bus,
            config=mock_config,
            agents=[],
        )

    def test_track_task_adds_to_set(self, mock_config, mock_llm, mock_event_bus,
                                     mock_memory_repo, mock_vector_store, mock_trust_store):
        async def _run():
            orch = self._make_orchestrator(
                mock_config, mock_llm, mock_event_bus,
                mock_memory_repo, mock_vector_store, mock_trust_store,
            )

            async def noop():
                pass

            task = orch._track_task(noop())
            assert task in orch._background_tasks
            await task

        asyncio.run(_run())

    def test_completed_task_removed_from_set(self, mock_config, mock_llm, mock_event_bus,
                                              mock_memory_repo, mock_vector_store, mock_trust_store):
        async def _run():
            orch = self._make_orchestrator(
                mock_config, mock_llm, mock_event_bus,
                mock_memory_repo, mock_vector_store, mock_trust_store,
            )

            async def noop():
                pass

            task = orch._track_task(noop())
            await task
            await asyncio.sleep(0.01)  # allow callback to run
            assert task not in orch._background_tasks

        asyncio.run(_run())

    def test_failed_task_logs_error(self, mock_config, mock_llm, mock_event_bus,
                                    mock_memory_repo, mock_vector_store, mock_trust_store,
                                    caplog):
        async def _run():
            orch = self._make_orchestrator(
                mock_config, mock_llm, mock_event_bus,
                mock_memory_repo, mock_vector_store, mock_trust_store,
            )

            async def fail():
                raise ValueError("test error")

            with caplog.at_level(logging.ERROR):
                task = orch._track_task(fail())
                try:
                    await task
                except ValueError:
                    pass
                await asyncio.sleep(0.01)
            assert "Background task failed" in caplog.text

        asyncio.run(_run())

    def test_cancelled_task_does_not_log_error(self, mock_config, mock_llm, mock_event_bus,
                                                mock_memory_repo, mock_vector_store, mock_trust_store,
                                                caplog):
        async def _run():
            orch = self._make_orchestrator(
                mock_config, mock_llm, mock_event_bus,
                mock_memory_repo, mock_vector_store, mock_trust_store,
            )

            async def slow():
                await asyncio.sleep(100)

            with caplog.at_level(logging.ERROR):
                task = orch._track_task(slow())
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await asyncio.sleep(0.01)
            assert "Background task failed" not in caplog.text

        asyncio.run(_run())
