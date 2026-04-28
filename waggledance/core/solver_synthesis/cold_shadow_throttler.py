# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Cold / shadow throttler for solver synthesis.

The synthesis pipeline (P4) can produce many candidate solvers per
hour: declarative compiles are cheap, but each candidate that enters
shadow evaluation runs against live request samples and consumes
inference / verification budget. At scale (10k–50k solvers per RULE
15/16) we cannot let every candidate flood shadow simultaneously.

This throttler is a deterministic token-bucket that bounds:

* the number of candidates allowed into **cold** evaluation per
  unit time (cold = first observations against live samples, no
  promotion potential);
* the number of candidates allowed into **shadow** evaluation per
  unit time (shadow = continued evaluation against live samples,
  candidate is eligible for promotion);
* a global concurrency cap (no more than ``max_in_flight``
  evaluations active at once).

The throttler is pure-Python, has no I/O, and is safe to stub. The
real synthesis orchestrator persists throttler decisions to the
control plane (``provider_jobs``-style row optional). Tests stay
deterministic by injecting a clock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional


@dataclass(frozen=True)
class ThrottleVerdict:
    admitted: bool
    lane: str  # "cold" | "shadow" | "rejected"
    reason: str
    available_tokens: float


@dataclass
class _LaneState:
    name: str
    capacity: float
    refill_per_second: float
    tokens: float
    last_refill_ts: float
    in_flight: int = 0


class ColdShadowThrottler:
    """Two-lane token-bucket with concurrency cap."""

    def __init__(
        self,
        *,
        cold_capacity: float = 10.0,
        cold_refill_per_second: float = 1.0,
        shadow_capacity: float = 50.0,
        shadow_refill_per_second: float = 5.0,
        max_in_flight: int = 32,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if cold_capacity <= 0 or shadow_capacity <= 0:
            raise ValueError("capacities must be positive")
        if cold_refill_per_second < 0 or shadow_refill_per_second < 0:
            raise ValueError("refill rates must be non-negative")
        if max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        self._clock = clock or time.monotonic
        now = self._clock()
        self._cold = _LaneState(
            name="cold",
            capacity=cold_capacity,
            refill_per_second=cold_refill_per_second,
            tokens=cold_capacity,
            last_refill_ts=now,
        )
        self._shadow = _LaneState(
            name="shadow",
            capacity=shadow_capacity,
            refill_per_second=shadow_refill_per_second,
            tokens=shadow_capacity,
            last_refill_ts=now,
        )
        self._max_in_flight = max_in_flight
        self._lock = threading.RLock()
        self._recent_verdicts: Deque[ThrottleVerdict] = deque(maxlen=256)

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._cold.in_flight + self._shadow.in_flight

    def admit(self, lane: str) -> ThrottleVerdict:
        if lane not in {"cold", "shadow"}:
            raise ValueError(f"unknown lane {lane!r}; expected 'cold' or 'shadow'")
        with self._lock:
            self._refill_now()
            if self.in_flight >= self._max_in_flight:
                v = ThrottleVerdict(
                    admitted=False,
                    lane="rejected",
                    reason=f"max_in_flight={self._max_in_flight} reached",
                    available_tokens=0.0,
                )
                self._recent_verdicts.append(v)
                return v
            state = self._cold if lane == "cold" else self._shadow
            if state.tokens < 1.0:
                v = ThrottleVerdict(
                    admitted=False,
                    lane="rejected",
                    reason=f"{state.name}_lane_tokens_exhausted",
                    available_tokens=state.tokens,
                )
                self._recent_verdicts.append(v)
                return v
            state.tokens -= 1.0
            state.in_flight += 1
            v = ThrottleVerdict(
                admitted=True,
                lane=state.name,
                reason="ok",
                available_tokens=state.tokens,
            )
            self._recent_verdicts.append(v)
            return v

    def release(self, lane: str) -> None:
        if lane not in {"cold", "shadow"}:
            raise ValueError(f"unknown lane {lane!r}")
        with self._lock:
            state = self._cold if lane == "cold" else self._shadow
            if state.in_flight > 0:
                state.in_flight -= 1

    def snapshot(self) -> dict:
        with self._lock:
            self._refill_now()
            return {
                "cold": {
                    "tokens": self._cold.tokens,
                    "capacity": self._cold.capacity,
                    "refill_per_second": self._cold.refill_per_second,
                    "in_flight": self._cold.in_flight,
                },
                "shadow": {
                    "tokens": self._shadow.tokens,
                    "capacity": self._shadow.capacity,
                    "refill_per_second": self._shadow.refill_per_second,
                    "in_flight": self._shadow.in_flight,
                },
                "max_in_flight": self._max_in_flight,
                "in_flight_total": self.in_flight,
            }

    def recent_verdicts(self) -> tuple[ThrottleVerdict, ...]:
        with self._lock:
            return tuple(self._recent_verdicts)

    # -- internal -------------------------------------------------------

    def _refill_now(self) -> None:
        now = self._clock()
        for state in (self._cold, self._shadow):
            elapsed = max(0.0, now - state.last_refill_ts)
            if elapsed > 0:
                state.tokens = min(
                    state.capacity, state.tokens + elapsed * state.refill_per_second
                )
                state.last_refill_ts = now
