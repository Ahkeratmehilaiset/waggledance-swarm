"""Tests for FallbackChain — ordered execution with confidence threshold."""

import asyncio

import pytest

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.policies.fallback_policy import FallbackChain


def _make_task():
    return TaskRequest(
        id="t1", query="test", language="en", profile="COTTAGE",
        user_id=None, context=[], timestamp=0.0,
    )


def _make_handler(confidence: float, response: str = "ok"):
    async def handler(task):
        return AgentResult(
            agent_id="h1", response=response, confidence=confidence,
            latency_ms=10, source="llm",
        )
    return handler


def _make_failing_handler():
    async def handler(task):
        raise RuntimeError("handler error")
    return handler


class TestFallbackChain:
    def test_returns_first_above_threshold(self):
        async def _run():
            chain = FallbackChain(min_confidence=0.6)
            handlers = {
                "memory": _make_handler(0.3),
                "llm": _make_handler(0.8, "good"),
                "swarm": _make_handler(0.9),
            }
            result = await chain.execute(_make_task(), handlers)
            assert result.confidence == 0.8
            assert result.response == "good"
        asyncio.run(_run())

    def test_falls_through_all_below_threshold(self):
        async def _run():
            chain = FallbackChain(min_confidence=0.9)
            handlers = {
                "memory": _make_handler(0.3),
                "llm": _make_handler(0.5),
                "swarm": _make_handler(0.7),
            }
            result = await chain.execute(_make_task(), handlers)
            assert result.confidence == 0.7  # last result returned
        asyncio.run(_run())

    def test_handles_exception_gracefully(self):
        async def _run():
            chain = FallbackChain(min_confidence=0.5)
            handlers = {
                "memory": _make_failing_handler(),
                "llm": _make_handler(0.8),
            }
            result = await chain.execute(_make_task(), handlers)
            assert result.confidence == 0.8
        asyncio.run(_run())

    def test_custom_step_list(self):
        async def _run():
            chain = FallbackChain(steps=["swarm", "llm"], min_confidence=0.5)
            handlers = {
                "swarm": _make_handler(0.9, "swarm_answer"),
                "llm": _make_handler(0.8),
            }
            result = await chain.execute(_make_task(), handlers)
            assert result.response == "swarm_answer"
        asyncio.run(_run())

    def test_empty_handlers_returns_default(self):
        async def _run():
            chain = FallbackChain()
            result = await chain.execute(_make_task(), {})
            assert result.agent_id == "fallback_exhausted"
            assert result.confidence == 0.0
        asyncio.run(_run())

    def test_execution_order_matches_steps(self):
        async def _run():
            order = []

            def tracked_handler(name, confidence):
                async def handler(task):
                    order.append(name)
                    return AgentResult(
                        agent_id=name, response=name, confidence=confidence,
                        latency_ms=0, source="llm",
                    )
                return handler

            chain = FallbackChain(steps=["memory", "llm", "swarm"], min_confidence=0.95)
            handlers = {
                "memory": tracked_handler("memory", 0.3),
                "llm": tracked_handler("llm", 0.5),
                "swarm": tracked_handler("swarm", 0.7),
            }
            await chain.execute(_make_task(), handlers)
            assert order == ["memory", "llm", "swarm"]
        asyncio.run(_run())
