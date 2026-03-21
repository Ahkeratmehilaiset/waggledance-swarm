"""Tests for micromodel step in FallbackChain (v1.17.0)."""

import asyncio
import unittest

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.policies.fallback_policy import FallbackChain


def _make_task():
    return TaskRequest(
        id="t1", query="test query", language="en", profile="COTTAGE",
        user_id=None, context=[], timestamp=0.0,
    )


def _make_result(source: str, confidence: float = 0.8):
    return AgentResult(
        agent_id=f"{source}_agent", response=f"answer from {source}",
        confidence=confidence, latency_ms=10.0, source=source,
    )


class TestFallbackChainMicromodel(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_default_steps_include_micromodel(self):
        chain = FallbackChain()
        self.assertIn("micromodel", chain.steps)

    def test_micromodel_order_after_memory(self):
        chain = FallbackChain()
        mem_idx = chain.steps.index("memory")
        micro_idx = chain.steps.index("micromodel")
        llm_idx = chain.steps.index("llm")
        self.assertLess(mem_idx, micro_idx)
        self.assertLess(micro_idx, llm_idx)

    def test_micromodel_handler_called(self):
        chain = FallbackChain()
        task = _make_task()
        calls = []

        async def mem_handler(t):
            return _make_result("memory", confidence=0.3)  # low → fallback

        async def micro_handler(t):
            calls.append("micromodel")
            return _make_result("micromodel", confidence=0.9)

        handlers = {"memory": mem_handler, "micromodel": micro_handler}
        result = self._run(chain.execute(task, handlers))
        self.assertEqual(result.source, "micromodel")
        self.assertIn("micromodel", calls)

    def test_micromodel_skipped_if_no_handler(self):
        chain = FallbackChain()
        task = _make_task()

        async def llm_handler(t):
            return _make_result("llm", confidence=0.7)

        handlers = {"llm": llm_handler}
        result = self._run(chain.execute(task, handlers))
        self.assertEqual(result.source, "llm")

    def test_custom_steps_without_micromodel(self):
        chain = FallbackChain(steps=["memory", "llm"])
        self.assertNotIn("micromodel", chain.steps)


if __name__ == "__main__":
    unittest.main()
