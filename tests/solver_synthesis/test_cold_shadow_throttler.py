# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.cold_shadow_throttler.

The cold/shadow throttler is the deterministic token-bucket that
bounds how many candidate solvers enter cold and shadow evaluation
per unit time, plus a global concurrency cap. At RULE 15/16 scale
(10k–50k solvers) a regression here can flood shadow evaluation with
candidates and exhaust the inference / verification budget, or
under-admit and leave promotion-ready candidates idle.

Direct test coverage on this file was zero before this PR. The
module has an injectable clock so tests stay deterministic without
real time.

Pinned invariants:

- Constructor rejects non-positive capacities, negative refill rates,
  and non-positive max_in_flight.
- `admit` rejects unknown lanes; admits when tokens ≥ 1 and
  in_flight < max_in_flight; decrements tokens by exactly 1 and
  increments in_flight by exactly 1 on grant.
- Token refill uses elapsed seconds × refill_per_second, capped at
  capacity (no over-refill above capacity).
- `release` decrements in_flight only; never below zero.
- max_in_flight gate fires before lane-token check, regardless of
  which lane is requested.
- snapshot / recent_verdicts surface internal state for telemetry.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis.cold_shadow_throttler import (
    ColdShadowThrottler,
    ThrottleVerdict,
)


# --- helpers --------------------------------------------------------

class _FakeClock:
    """Deterministic monotonic clock for testing refill math."""

    def __init__(self, start: float = 0.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _throttler(**kwargs) -> tuple[ColdShadowThrottler, _FakeClock]:
    clock = _FakeClock()
    defaults = dict(
        cold_capacity=2.0,
        cold_refill_per_second=1.0,
        shadow_capacity=3.0,
        shadow_refill_per_second=2.0,
        max_in_flight=4,
        clock=clock,
    )
    defaults.update(kwargs)
    return ColdShadowThrottler(**defaults), clock


# --- constructor validation ----------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"cold_capacity": 0.0},
    {"cold_capacity": -1.0},
    {"shadow_capacity": 0.0},
    {"shadow_capacity": -5.0},
])
def test_throttler_rejects_non_positive_capacities(kwargs):
    with pytest.raises(ValueError, match="capacit"):
        ColdShadowThrottler(**kwargs)


def test_throttler_rejects_negative_refill_rates():
    with pytest.raises(ValueError, match="refill"):
        ColdShadowThrottler(cold_refill_per_second=-1.0)
    with pytest.raises(ValueError, match="refill"):
        ColdShadowThrottler(shadow_refill_per_second=-0.5)


def test_throttler_rejects_non_positive_max_in_flight():
    with pytest.raises(ValueError, match="max_in_flight"):
        ColdShadowThrottler(max_in_flight=0)
    with pytest.raises(ValueError, match="max_in_flight"):
        ColdShadowThrottler(max_in_flight=-3)


# --- admit: lane validation + token decrement ----------------------

def test_admit_rejects_unknown_lane():
    t, _ = _throttler()
    with pytest.raises(ValueError, match="unknown lane"):
        t.admit("magma")


def test_admit_grants_when_tokens_available_and_decrements_by_one():
    t, _ = _throttler()
    snap_before = t.snapshot()
    assert snap_before["cold"]["tokens"] == 2.0
    v = t.admit("cold")
    assert isinstance(v, ThrottleVerdict)
    assert v.admitted is True
    assert v.lane == "cold"
    assert v.reason == "ok"
    snap_after = t.snapshot()
    assert snap_after["cold"]["tokens"] == 1.0
    assert snap_after["cold"]["in_flight"] == 1


def test_admit_rejects_when_lane_tokens_exhausted():
    t, _ = _throttler(cold_capacity=1.0, cold_refill_per_second=0.0)
    v1 = t.admit("cold")
    assert v1.admitted is True
    # Release immediately so in_flight isn't blocking; only tokens are
    # the gating condition here.
    t.release("cold")
    v2 = t.admit("cold")
    assert v2.admitted is False
    assert v2.lane == "rejected"
    assert "exhausted" in v2.reason


def test_admit_rejects_when_max_in_flight_reached():
    t, _ = _throttler(max_in_flight=2)
    a = t.admit("cold")
    b = t.admit("shadow")
    assert a.admitted and b.admitted
    assert t.in_flight == 2
    c = t.admit("shadow")  # third should hit max_in_flight
    assert c.admitted is False
    assert "max_in_flight=2" in c.reason


def test_max_in_flight_gate_fires_before_token_check():
    """Even when lane has tokens, max_in_flight blocks first."""
    t, _ = _throttler(max_in_flight=1)
    a = t.admit("cold")
    assert a.admitted
    # cold has 1 token left, but max_in_flight=1 already reached.
    b = t.admit("cold")
    assert b.admitted is False
    assert "max_in_flight" in b.reason


# --- token refill ---------------------------------------------------

def test_tokens_refill_proportional_to_elapsed_time():
    t, clock = _throttler(
        cold_capacity=10.0, cold_refill_per_second=1.0,
    )
    # Drain to 5 tokens by admitting 5.
    for _ in range(5):
        v = t.admit("cold")
        t.release("cold")
        assert v.admitted
    snap = t.snapshot()
    assert snap["cold"]["tokens"] == pytest.approx(5.0)
    # Advance clock 3 seconds → +3 tokens (5 + 3 = 8).
    clock.advance(3.0)
    snap = t.snapshot()
    assert snap["cold"]["tokens"] == pytest.approx(8.0)


def test_tokens_refill_capped_at_capacity():
    t, clock = _throttler(
        cold_capacity=2.0, cold_refill_per_second=1.0,
    )
    # Already at capacity; advancing time should NOT exceed it.
    clock.advance(100.0)
    snap = t.snapshot()
    assert snap["cold"]["tokens"] == pytest.approx(2.0)


def test_tokens_refill_does_not_apply_when_clock_does_not_advance():
    t, _clock = _throttler(
        cold_capacity=5.0, cold_refill_per_second=1.0,
    )
    v = t.admit("cold")
    t.release("cold")
    snap = t.snapshot()
    assert snap["cold"]["tokens"] == pytest.approx(4.0)


# --- release --------------------------------------------------------

def test_release_decrements_in_flight():
    t, _ = _throttler()
    t.admit("cold")
    assert t.in_flight == 1
    t.release("cold")
    assert t.in_flight == 0


def test_release_unknown_lane_raises():
    t, _ = _throttler()
    with pytest.raises(ValueError, match="unknown lane"):
        t.release("magma")


def test_release_does_not_go_below_zero():
    """Calling release without a prior admit MUST NOT push in_flight
    below zero — extra releases are silently no-ops."""
    t, _ = _throttler()
    t.release("cold")  # nothing in flight
    t.release("cold")  # second extraneous release
    assert t.in_flight == 0


# --- snapshot + recent_verdicts ------------------------------------

def test_snapshot_reports_per_lane_state_and_global_in_flight():
    t, _ = _throttler()
    t.admit("cold")
    t.admit("shadow")
    snap = t.snapshot()
    assert snap["cold"]["in_flight"] == 1
    assert snap["shadow"]["in_flight"] == 1
    assert snap["in_flight_total"] == 2
    assert snap["max_in_flight"] == 4


def test_recent_verdicts_records_admit_decisions_in_order():
    t, _ = _throttler()
    t.admit("cold")
    t.admit("shadow")
    t.admit("cold")
    verdicts = t.recent_verdicts()
    assert len(verdicts) == 3
    assert verdicts[0].lane == "cold"
    assert verdicts[1].lane == "shadow"
    assert verdicts[2].lane == "cold"
    assert all(v.admitted for v in verdicts)


def test_recent_verdicts_records_rejection_with_reason():
    t, _ = _throttler(max_in_flight=1)
    t.admit("cold")
    t.admit("shadow")  # should be rejected
    verdicts = t.recent_verdicts()
    rejected = [v for v in verdicts if not v.admitted]
    assert len(rejected) == 1
    assert rejected[0].lane == "rejected"
    assert "max_in_flight" in rejected[0].reason
