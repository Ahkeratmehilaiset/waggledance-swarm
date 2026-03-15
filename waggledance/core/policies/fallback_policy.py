"""Fallback chain — ordered multi-step execution with confidence threshold.

Fallback order: memory -> micromodel -> llm -> swarm.
"""

import logging
from typing import Callable

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.task import TaskRequest

log = logging.getLogger(__name__)


class FallbackChain:
    """Ordered fallback: memory -> llm -> swarm. Returns first result above min_confidence."""

    def __init__(
        self,
        steps: list[str] | None = None,
        min_confidence: float = 0.6,
    ) -> None:
        self.steps = steps or ["memory", "micromodel", "llm", "swarm"]
        self.min_confidence = min_confidence

    async def execute(
        self,
        task: TaskRequest,
        handlers: dict[str, Callable],
    ) -> AgentResult:
        """Execute fallback chain, returning first result above min_confidence.

        Each handler is an async callable(TaskRequest) -> AgentResult.
        If all handlers fail or return low confidence, returns the last result.
        If no handlers are provided, returns an empty result.
        """
        last_result: AgentResult | None = None

        for step in self.steps:
            handler = handlers.get(step)
            if handler is None:
                continue

            try:
                result = await handler(task)
                last_result = result
                if result.confidence >= self.min_confidence:
                    return result
            except Exception as e:
                log.warning("Fallback step '%s' failed: %s", step, e)
                continue

        if last_result is not None:
            return last_result

        return AgentResult(
            agent_id="fallback_exhausted",
            response="",
            confidence=0.0,
            latency_ms=0.0,
            source="llm",
        )
