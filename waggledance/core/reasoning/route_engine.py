"""Route engine — wraps SmartRouterV2 + RouteTelemetry for route analysis.

Provides route accuracy tracking, LLM fallback rate monitoring,
and route optimization recommendations based on telemetry data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SmartRouterV2 = None
_RouteTelemetry = None


def _get_legacy_classes():
    global _SmartRouterV2, _RouteTelemetry
    if _SmartRouterV2 is None:
        try:
            from core.smart_router_v2 import SmartRouterV2
            _SmartRouterV2 = SmartRouterV2
        except ImportError:
            pass
    if _RouteTelemetry is None:
        try:
            from core.route_telemetry import RouteTelemetry
            _RouteTelemetry = RouteTelemetry
        except ImportError:
            pass


@dataclass
class RouteMetrics:
    """Aggregated metrics for a route type."""
    route_type: str
    total_queries: int = 0
    successes: int = 0
    fallbacks: int = 0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    accuracy: float = 0.0
    llm_fallback_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_type": self.route_type,
            "total_queries": self.total_queries,
            "successes": self.successes,
            "accuracy": round(self.accuracy, 4),
            "llm_fallback_rate": round(self.llm_fallback_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
        }


@dataclass
class RouteDecision:
    """Recorded route decision for analysis."""
    route_type: str
    intent: str
    quality_path: str
    success: bool
    latency_ms: float
    was_fallback: bool = False
    timestamp: float = field(default_factory=time.time)


class RouteEngine:
    """Route analysis and optimization engine.

    Wraps SmartRouterV2 and RouteTelemetry to provide:
    - Route accuracy tracking per route type
    - LLM fallback rate monitoring
    - Route performance comparison
    - Routing optimization recommendations
    """

    def __init__(self, smart_router=None, telemetry=None):
        _get_legacy_classes()
        self._router = smart_router
        self._telemetry = telemetry
        self._decisions: List[RouteDecision] = []
        self._max_decisions = 2000
        self._quality_counts: Dict[str, int] = {}

    def record_decision(self, route_type: str, intent: str,
                        quality_path: str, success: bool,
                        latency_ms: float, was_fallback: bool = False) -> None:
        """Record a routing decision for analysis."""
        decision = RouteDecision(
            route_type=route_type,
            intent=intent,
            quality_path=quality_path,
            success=success,
            latency_ms=latency_ms,
            was_fallback=was_fallback,
        )
        self._decisions.append(decision)
        if len(self._decisions) > self._max_decisions:
            self._decisions = self._decisions[-self._max_decisions:]

        self._quality_counts[quality_path] = self._quality_counts.get(quality_path, 0) + 1

        if self._telemetry:
            try:
                self._telemetry.record(route_type, latency_ms, success, was_fallback)
            except Exception:
                pass

    def get_route_metrics(self, route_type: str = None) -> List[RouteMetrics]:
        """Get aggregated metrics per route type."""
        by_route: Dict[str, List[RouteDecision]] = {}
        for d in self._decisions:
            if route_type and d.route_type != route_type:
                continue
            if d.route_type not in by_route:
                by_route[d.route_type] = []
            by_route[d.route_type].append(d)

        results = []
        for rt, decisions in by_route.items():
            total = len(decisions)
            successes = sum(1 for d in decisions if d.success)
            fallbacks = sum(1 for d in decisions if d.was_fallback)
            latencies = sorted(d.latency_ms for d in decisions)

            results.append(RouteMetrics(
                route_type=rt,
                total_queries=total,
                successes=successes,
                fallbacks=fallbacks,
                accuracy=successes / total if total else 0,
                llm_fallback_rate=fallbacks / total if total else 0,
                avg_latency_ms=sum(latencies) / total if total else 0,
                p50_latency_ms=latencies[total // 2] if total else 0,
                p99_latency_ms=latencies[int(total * 0.99)] if total else 0,
            ))

        results.sort(key=lambda m: m.total_queries, reverse=True)
        return results

    def get_llm_fallback_rate(self) -> float:
        """Overall LLM fallback rate across all routes."""
        if not self._decisions:
            return 0.0
        fallbacks = sum(1 for d in self._decisions if d.was_fallback)
        return fallbacks / len(self._decisions)

    def get_route_accuracy(self) -> float:
        """Overall route accuracy (success rate) across all routes."""
        if not self._decisions:
            return 0.0
        successes = sum(1 for d in self._decisions if d.success)
        return successes / len(self._decisions)

    def get_quality_distribution(self) -> Dict[str, float]:
        """Distribution of quality paths (gold/silver/bronze)."""
        total = sum(self._quality_counts.values())
        if not total:
            return {}
        return {k: v / total for k, v in self._quality_counts.items()}

    def get_specialist_accuracy(self) -> Dict[str, float]:
        """Per-intent accuracy (proxy for specialist model accuracy)."""
        by_intent: Dict[str, List[bool]] = {}
        for d in self._decisions:
            if d.intent not in by_intent:
                by_intent[d.intent] = []
            by_intent[d.intent].append(d.success)

        return {
            intent: sum(results) / len(results)
            for intent, results in by_intent.items()
            if results
        }

    def recommend_improvements(self) -> List[Dict[str, str]]:
        """Generate routing improvement recommendations based on telemetry."""
        recommendations = []
        metrics = self.get_route_metrics()

        for m in metrics:
            if m.accuracy < 0.7 and m.total_queries > 10:
                recommendations.append({
                    "route": m.route_type,
                    "issue": "low_accuracy",
                    "detail": f"Accuracy {m.accuracy:.0%} below 70% threshold",
                    "suggestion": "Consider training specialist model for this route",
                })
            if m.llm_fallback_rate > 0.5 and m.total_queries > 10:
                recommendations.append({
                    "route": m.route_type,
                    "issue": "high_fallback",
                    "detail": f"LLM fallback rate {m.llm_fallback_rate:.0%}",
                    "suggestion": "Add solver or retrieval capability for this intent",
                })
            if m.avg_latency_ms > 3000 and m.total_queries > 5:
                recommendations.append({
                    "route": m.route_type,
                    "issue": "high_latency",
                    "detail": f"Avg latency {m.avg_latency_ms:.0f}ms",
                    "suggestion": "Consider caching or lighter solver",
                })

        return recommendations

    def stats(self) -> Dict[str, Any]:
        return {
            "total_decisions": len(self._decisions),
            "overall_accuracy": round(self.get_route_accuracy(), 4),
            "llm_fallback_rate": round(self.get_llm_fallback_rate(), 4),
            "quality_distribution": {k: round(v, 4)
                                     for k, v in self.get_quality_distribution().items()},
            "routes_tracked": len(set(d.route_type for d in self._decisions)),
            "legacy_router_available": self._router is not None,
            "legacy_telemetry_available": self._telemetry is not None,
        }
