"""Tests for EscalationPolicy — when to trigger Round Table."""

import pytest

from waggledance.core.domain.agent import AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.policies.escalation_policy import EscalationPolicy


def _task(query="test query"):
    return TaskRequest(
        id="t1", query=query, language="en", profile="COTTAGE",
        user_id=None, context=[], timestamp=0.0,
    )


def _result(confidence=0.8, response="answer"):
    return AgentResult(
        agent_id="a1", response=response, confidence=confidence,
        latency_ms=100, source="llm",
    )


class TestEscalationPolicy:
    def test_low_confidence_triggers(self):
        policy = EscalationPolicy(confidence_threshold=0.5)
        assert policy.needs_round_table(_result(confidence=0.3), _task()) is True

    def test_high_confidence_does_not_trigger(self):
        policy = EscalationPolicy(confidence_threshold=0.5)
        assert policy.needs_round_table(_result(confidence=0.9), _task()) is False

    def test_hallucination_signal_triggers(self):
        policy = EscalationPolicy()
        result = _result(confidence=0.6, response="")
        assert policy.needs_round_table(result, _task()) is True

    def test_complex_query_with_medium_confidence_triggers(self):
        policy = EscalationPolicy(min_query_length=10)
        long_query = "How do I treat varroa mites in spring with oxalic acid?"
        result = _result(confidence=0.6)
        assert policy.needs_round_table(result, _task(long_query)) is True
