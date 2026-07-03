"""Route telemetry — per-route stats with bounded threshold tuning."""

import time
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class RouteStats:
    route_type: str
    count: int = 0
    success_count: int = 0
    total_latency_ms: float = 0.0
    latencies: list[float] = field(default_factory=list)
    fallback_count: int = 0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.count if self.count else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.count if self.count else 0.0

    def percentile(self, p: float) -> float:
        if not self.latencies:
            return 0.0
        sorted_l = sorted(self.latencies)
        idx = int(len(sorted_l) * p / 100)
        return sorted_l[min(idx, len(sorted_l) - 1)]

class RouteTelemetry:
    MAX_ADJUSTMENT = 0.05  # max threshold change per cycle

    def __init__(self, max_latency_samples: int = 1000, tuning_enabled: bool = True):
        self._stats: dict[str, RouteStats] = {}
        self._max_samples = max_latency_samples
        self._tuning_enabled = tuning_enabled
        self._thresholds: dict[str, float] = {}

    def record(self, route_type: str, latency_ms: float, success: bool, was_fallback: bool = False) -> None:
        if route_type not in self._stats:
            self._stats[route_type] = RouteStats(route_type=route_type)
        s = self._stats[route_type]
        s.count += 1
        s.total_latency_ms += latency_ms
        if success:
            s.success_count += 1
        if was_fallback:
            s.fallback_count += 1
        s.latencies.append(latency_ms)
        if len(s.latencies) > self._max_samples:
            s.latencies = s.latencies[-self._max_samples:]

    def get_stats(self, route_type: str) -> RouteStats | None:
        return self._stats.get(route_type)

    def all_stats(self) -> dict[str, RouteStats]:
        return dict(self._stats)

    def get_threshold(self, route_type: str, default: float = 0.6) -> float:
        return self._thresholds.get(route_type, default)

    def tune_threshold(self, route_type: str, current: float, target_success_rate: float = 0.8) -> float:
        """Bounded self-tuning: adjust threshold by max +/-0.05 per cycle."""
        if not self._tuning_enabled:
            return current
        stats = self._stats.get(route_type)
        if not stats or stats.count < 10:
            return current

        delta = 0.0
        if stats.success_rate < target_success_rate:
            delta = -0.01  # lower threshold to accept more
        elif stats.success_rate > target_success_rate + 0.1:
            delta = 0.01  # raise threshold

        delta = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, delta))
        new_threshold = max(0.1, min(0.95, current + delta))
        self._thresholds[route_type] = new_threshold
        return new_threshold

    def summary(self) -> dict:
        return {
            rt: {
                "count": s.count,
                "success_rate": round(s.success_rate, 3),
                "avg_latency_ms": round(s.avg_latency_ms, 1),
                "p50_ms": round(s.percentile(50), 1),
                "p99_ms": round(s.percentile(99), 1),
                "fallback_rate": round(s.fallback_count / s.count, 3) if s.count else 0,
            }
            for rt, s in self._stats.items()
        }

    def solver_first_served_stats(
        self,
        solver_route_types: frozenset[str] = frozenset({"solver"}),
        served_route_types: frozenset[str] | None = None,
    ) -> dict:
        """P2 claim-safe truth source: solver-first-served vs total-served.

        The ``deterministic_solver_first`` claim-safe milestone (Image #1
        Panel 2) is gated on ``solver_first_served_total / served_total`` over a
        coverage window. This exposes the raw counts as the *truth source*; the
        gated ``claim_safe`` flip applies the coverage-threshold policy
        downstream (never asserted here).

        ``solver_first_served_total`` counts served responses whose
        ``route_type`` is in ``solver_route_types`` (default: the deterministic
        ``"solver"`` route). ``served_total`` counts served responses whose
        ``route_type`` is in ``served_route_types`` (default: every recorded
        route_type -- one telemetry record == one served response). Callers may
        narrow the denominator (e.g. exclude cache hits) via
        ``served_route_types``.
        """
        # The served denominator (default: every recorded route_type).
        if served_route_types is None:
            served_items = list(self._stats.items())
        else:
            served_items = [
                (rt, s) for rt, s in self._stats.items()
                if rt in served_route_types
            ]
        served_total = sum(s.count for _, s in served_items)
        # Claim-safety invariant: solver-first-served is a SUBSET of served, so
        # the numerator is counted ONLY within the served denominator. This
        # guarantees the ratio can never exceed 1.0 even when the caller narrows
        # ``served_route_types`` to exclude a solver route (else an inflated
        # ratio could spuriously satisfy the S4 claim_safe threshold).
        solver_total = sum(
            s.count for rt, s in served_items if rt in solver_route_types
        )
        ratio = (solver_total / served_total) if served_total else 0.0
        return {
            "solver_first_served_total": solver_total,
            "served_total": served_total,
            "solver_first_served_ratio": ratio,
            "per_route_served": {rt: s.count for rt, s in self._stats.items()},
        }

    # ── v2.0: Specialist trainer feed ─────────────────────────

    def feed_specialist_trainer(self, specialist_trainer=None) -> int:
        """Export route telemetry data for specialist model training.

        Returns the number of route stats entries exported.
        The specialist trainer uses route-level success/failure data
        to train the route_classifier specialist model.
        """
        if specialist_trainer is None or not self._stats:
            return 0
        try:
            exported = 0
            for route_type, stats in self._stats.items():
                if stats.count >= 10:  # Only export meaningful samples
                    specialist_trainer.record_route_stats(
                        route_type=route_type,
                        success_rate=stats.success_rate,
                        avg_latency_ms=stats.avg_latency_ms,
                        sample_count=stats.count,
                    )
                    exported += 1
            return exported
        except Exception:
            return 0
