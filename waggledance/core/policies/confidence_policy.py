"""Confidence-based decision policies."""

from waggledance.core.domain.agent import AgentResult


def should_escalate_to_swarm(
    result: AgentResult,
    threshold: float = 0.7,
) -> bool:
    """Return True if the result confidence is too low for direct answer."""
    return result.confidence < threshold


def should_cache_result(
    result: AgentResult,
    query_frequency: int,
) -> bool:
    """Return True if the result should be cached in hot cache.

    Cache if confidence is high enough and query appears frequently.
    """
    return result.confidence >= 0.8 and query_frequency >= 2
