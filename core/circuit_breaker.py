"""Circuit Breaker — degraded mode when services are slow/down.

Extracted from memory_engine.py (v1.17.0).
"""

import logging
import time

log = logging.getLogger("consciousness")


class CircuitBreaker:
    """B3: Generic circuit breaker for external service calls.

    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)

    When a service fails `failure_threshold` times within `window_s` seconds,
    the breaker opens and all calls return the fallback for `recovery_s` seconds.
    After recovery time, one test call is allowed (half-open).
    If it succeeds → close. If it fails → re-open.
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, failure_threshold: int = 3,
                 window_s: float = 60.0, recovery_s: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.recovery_s = recovery_s
        self.state = self.CLOSED
        self._failures: list = []  # timestamps of recent failures
        self._opened_at: float = 0.0
        self._total_trips = 0
        self._total_blocked = 0

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_s:
                self.state = self.HALF_OPEN
                log.info(f"CircuitBreaker[{self.name}]: HALF_OPEN (testing recovery)")
                return True
            self._total_blocked += 1
            return False
        # HALF_OPEN: allow one test request
        return True

    def record_success(self):
        """Call after a successful service call."""
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
            self._failures.clear()
            log.info(f"CircuitBreaker[{self.name}]: CLOSED (recovered)")

    def record_failure(self):
        """Call after a failed service call."""
        now = time.monotonic()
        self._failures.append(now)
        # Prune old failures outside window
        cutoff = now - self.window_s
        self._failures = [t for t in self._failures if t > cutoff]

        if self.state == self.HALF_OPEN:
            self._trip()
            return

        if len(self._failures) >= self.failure_threshold:
            self._trip()

    def _trip(self):
        """Open the circuit breaker."""
        self.state = self.OPEN
        self._opened_at = time.monotonic()
        self._total_trips += 1
        log.warning(f"CircuitBreaker[{self.name}]: OPEN "
                    f"({self._total_trips} trips, recovering in {self.recovery_s}s)")

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "recent_failures": len(self._failures),
            "total_trips": self._total_trips,
            "total_blocked": self._total_blocked,
        }
