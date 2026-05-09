# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.policies.confidence_policy.

confidence_policy is the swarm-escalation gate AND the hot-cache
admission filter. Both are autonomy-loop critical:

- `should_escalate_to_swarm` decides whether an agent's answer is
  trusted directly or sent through the more-expensive swarm
  consultation path. A drifted threshold silently routes either
  too few or too many queries through the swarm.
- `should_cache_result` decides whether a result enters the hot
  cache. Caching a low-confidence answer poisons subsequent reads;
  failing to cache a high-confidence frequent answer wastes the
  hot path.

Both functions live in 22 LoC of code with NO direct unit tests
on main as of 2026-05-09. Pinning their boundary behavior here.

Pinned invariants:

- `should_escalate_to_swarm` returns True iff
  `result.confidence < threshold`. Default threshold is 0.7. The
  comparison is strictly < (equality at the threshold does NOT
  escalate).
- `should_cache_result` returns True iff
  `result.confidence >= 0.8 AND query_frequency >= 2`. Both
  thresholds are inclusive (== passes). Either condition failing
  drops to False.
"""
from __future__ import annotations

import pytest

from waggledance.core.domain.agent import AgentResult
from waggledance.core.policies.confidence_policy import (
    should_cache_result,
    should_escalate_to_swarm,
)


def _result(confidence: float, source: str = "solver") -> AgentResult:
    return AgentResult(
        agent_id="agent-1",
        response="resp",
        confidence=confidence,
        latency_ms=1.0,
        source=source,
    )


# --- should_escalate_to_swarm -------------------------------------

@pytest.mark.parametrize("confidence,expected", [
    (0.0,  True),    # zero confidence escalates
    (0.5,  True),    # below default threshold
    (0.69, True),    # just below default threshold
    (0.7,  False),   # exact threshold does NOT escalate (strict <)
    (0.71, False),   # above threshold
    (1.0,  False),   # full confidence
])
def test_should_escalate_to_swarm_default_threshold(confidence, expected):
    assert should_escalate_to_swarm(_result(confidence)) is expected


def test_should_escalate_to_swarm_threshold_at_exactly_threshold_does_not_escalate():
    """Strict-less-than semantics pinned: confidence == threshold
    must NOT escalate. A future refactor flipping this to <= would
    silently push every threshold-borderline answer into the more
    expensive swarm path."""
    assert should_escalate_to_swarm(_result(0.7), threshold=0.7) is False


def test_should_escalate_to_swarm_with_custom_threshold_below():
    """Custom threshold parameter is honored: 0.9 threshold means
    even 0.85 confidence escalates."""
    assert should_escalate_to_swarm(_result(0.85), threshold=0.9) is True


def test_should_escalate_to_swarm_with_custom_threshold_above():
    """Confidence well above the custom threshold: no escalation."""
    assert should_escalate_to_swarm(_result(0.95), threshold=0.9) is False


def test_should_escalate_to_swarm_zero_threshold_never_escalates():
    """A 0.0 threshold means nothing meets the strict-less-than
    criterion (since confidence must be a float >= 0)."""
    assert should_escalate_to_swarm(_result(0.0), threshold=0.0) is False


def test_should_escalate_to_swarm_one_threshold_always_escalates_below_one():
    """A 1.0 threshold means every confidence below 1.0 escalates."""
    assert should_escalate_to_swarm(_result(0.99), threshold=1.0) is True
    assert should_escalate_to_swarm(_result(1.0),  threshold=1.0) is False


# --- should_cache_result ------------------------------------------

@pytest.mark.parametrize("confidence,frequency,expected", [
    (0.8,  2,  True),    # exactly both thresholds
    (1.0,  10, True),    # well above
    (0.9,  2,  True),    # high confidence, minimum frequency
    (0.79, 5,  False),   # confidence just below 0.8
    (0.8,  1,  False),   # frequency just below 2
    (0.5,  1,  False),   # both below
    (0.0,  100, False),  # zero confidence — never cache
    (1.0,  0,  False),   # never queried — never cache
])
def test_should_cache_result_threshold_combinations(
    confidence, frequency, expected,
):
    assert should_cache_result(
        _result(confidence), frequency,
    ) is expected


def test_should_cache_result_confidence_threshold_inclusive():
    """confidence == 0.8 AT minimum frequency must cache.
    A future tightening to > 0.8 would silently drop borderline
    well-confident frequent results."""
    assert should_cache_result(_result(0.8), 2) is True


def test_should_cache_result_frequency_threshold_inclusive():
    """frequency == 2 (exact) must cache when confidence is high
    enough."""
    assert should_cache_result(_result(0.85), 2) is True


def test_should_cache_result_low_confidence_never_caches():
    """Even at very high frequency, a low-confidence result must
    NEVER enter the hot cache — caching a 0.2 confidence answer
    would poison subsequent reads."""
    assert should_cache_result(_result(0.2), 1000) is False


def test_should_cache_result_high_confidence_one_off_query_skipped():
    """A one-off query (frequency 1) is not cached even at full
    confidence — the cache is for repeated traffic, not single
    hits."""
    assert should_cache_result(_result(1.0), 1) is False


def test_should_cache_result_zero_frequency_never_caches():
    """Frequency 0 (never queried) must skip the cache regardless
    of confidence — caching unqueried answers wastes the hot tier."""
    assert should_cache_result(_result(1.0), 0) is False


def test_should_cache_result_negative_frequency_never_caches():
    """Negative frequency is nonsense input but must NOT silently
    accept (would be a sign of upstream bug); skip safely."""
    assert should_cache_result(_result(0.95), -1) is False
