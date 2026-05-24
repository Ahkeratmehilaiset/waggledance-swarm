# SPDX-License-Identifier: Apache-2.0
"""Regression tests for legacy Agent task/status consistency."""

from __future__ import annotations

import asyncio

from agents.base_agent import Agent
from core.llm_provider import LLMResponse
from memory.shared_memory import SharedMemory


class _FailingLLM:
    async def generate(self, *args, **kwargs):
        raise RuntimeError("llm boom")


class _ErrorResponseLLM:
    async def generate(self, *args, **kwargs):
        return LLMResponse(content="", model="stub-model", error=True)


def test_execute_task_failure_restores_shared_agent_status(tmp_path) -> None:
    async def run() -> None:
        mem = SharedMemory(str(tmp_path / "agent_status.sqlite"))
        await mem.initialize()
        try:
            agent = Agent(
                name="Status Agent",
                agent_type="status_agent",
                system_prompt="system",
                llm=_FailingLLM(),
                memory=mem,
            )
            await agent.initialize()
            task_id = await mem.add_task(
                "failing task",
                assigned_agent=agent.id,
            )

            try:
                await agent.execute_task({
                    "id": task_id,
                    "title": "failing task",
                    "description": "trigger failure",
                })
            except RuntimeError:
                pass
            else:  # pragma: no cover - defensive assertion clarity
                raise AssertionError("expected failing LLM to abort task")

            cursor = await mem._db.execute(  # type: ignore[union-attr]
                "SELECT status FROM agents WHERE id = ?",
                (agent.id,),
            )
            row = await cursor.fetchone()
            tasks = await mem.get_tasks(status="failed")

            assert agent.status == "idle"
            assert row["status"] == "idle"
            assert [task["id"] for task in tasks] == [task_id]
        finally:
            await mem.close()

    asyncio.run(run())


def test_think_fails_closed_on_llm_error_response(tmp_path) -> None:
    async def run() -> None:
        mem = SharedMemory(str(tmp_path / "agent_llm_error.sqlite"))
        await mem.initialize()
        try:
            agent = Agent(
                name="Status Agent",
                agent_type="status_agent",
                system_prompt="system",
                llm=_ErrorResponseLLM(),
                memory=mem,
            )
            await agent.initialize()

            try:
                await agent.think("query")
            except RuntimeError as exc:
                assert "LLM generation failed" in str(exc)
            else:  # pragma: no cover - defensive assertion clarity
                raise AssertionError("expected LLM error response to fail")

            cursor = await mem._db.execute(  # type: ignore[union-attr]
                "SELECT status FROM agents WHERE id = ?",
                (agent.id,),
            )
            row = await cursor.fetchone()
            memories = await mem.get_recent_memories(agent_id=agent.id)

            assert agent.status == "error"
            assert row["status"] == "error"
            assert memories == []
        finally:
            await mem.close()

    asyncio.run(run())
