"""Escalation policy — when to trigger Round Table consensus."""

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.task import TaskRequest


class EscalationPolicy:
    """Decides when a single-agent answer should be escalated to Round Table."""

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        min_query_length: int = 20,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        self._min_query_length = min_query_length

    def needs_round_table(
        self,
        result: AgentResult,
        task: TaskRequest,
    ) -> bool:
        """Return True if the result should be escalated to Round Table.

        Criteria:
        - Low confidence (below threshold)
        - Hallucination signal (empty response with non-zero confidence)
        - Query is complex enough (long enough to warrant multi-agent review)
        """
        if result.confidence < self._confidence_threshold:
            return True

        if not result.response and result.confidence > 0:
            return True

        if len(task.query) >= self._min_query_length and result.confidence < 0.7:
            return True

        return False
