"""
SmartRouter v2 — capsule-aware query routing.

Routes each query to the best reasoning layer using:
1. HotCache check (~0.5ms)
2. Capsule key_decision match (keyword → layer)
3. Keyword classifier (math/rule/stat keywords)
4. Capsule priority fallback (highest-priority enabled layer)

Returns a RouteResult with layer, decision_id, confidence, reason, and timing.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core.domain_capsule import DomainCapsule

log = logging.getLogger(__name__)

# Keyword patterns for layer classification (language-agnostic)
_MATH_KEYWORDS = re.compile(
    r"\b(paljonko|kuinka paljon|laske|calculate|how much|cost|hinta|"
    r"price|formula|kaava|kwh|watt|celsius|fahrenheit|euro|€|\d+\s*[+\-*/])\b",
    re.IGNORECASE,
)
_RULE_KEYWORDS = re.compile(
    r"\b(pitääkö|must|should|onko sallittu|allowed|kielletty|forbidden|"
    r"raja|limit|threshold|sääntö|rule|hälytys|alert|varoitus|warning|"
    r"turvallisuus|safety|check|tarkista)\b",
    re.IGNORECASE,
)
_STAT_KEYWORDS = re.compile(
    r"\b(normaali|normal|anomal|poikkeama|deviation|trendi|trend|"
    r"keskiarvo|average|mediaani|median|hajonta|variance|tilasto|"
    r"statistic|cpk|oee|spc|baseline)\b",
    re.IGNORECASE,
)


@dataclass
class RouteResult:
    """Result of routing a query to a reasoning layer."""
    layer: str
    decision_id: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    routing_time_ms: float = 0.0
    model: Optional[str] = None
    rules: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    fallback: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "decision_id": self.decision_id,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "routing_time_ms": round(self.routing_time_ms, 2),
            "model": self.model,
            "rules": self.rules,
            "inputs": self.inputs,
            "fallback": self.fallback,
        }


class SmartRouterV2:
    """Capsule-aware router that selects the best reasoning layer."""

    def __init__(self, capsule: DomainCapsule,
                 hot_cache: Any = None):
        self.capsule = capsule
        self._hot_cache = hot_cache
        self._route_count = 0
        self._layer_counts: dict[str, int] = {}

    def route(self, query: str) -> RouteResult:
        """Route a query to the best reasoning layer.

        Priority order:
        1. HotCache hit → return cached layer
        2. Capsule key_decision match → decision's primary layer
        3. Keyword classifier → detected layer
        4. Capsule priority fallback → highest-priority enabled layer
        """
        t0 = time.perf_counter()
        self._route_count += 1

        # Step 1: HotCache check
        if self._hot_cache:
            try:
                hit = self._hot_cache.get(query)
                if hit is not None:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return RouteResult(
                        layer="retrieval",
                        confidence=1.0,
                        reason="hot_cache_hit",
                        routing_time_ms=elapsed,
                    )
            except Exception:
                pass

        # Step 2: Capsule key_decision match
        match = self.capsule.match_decision(query)
        if match and match.confidence >= 0.2:
            if self.capsule.is_layer_enabled(match.layer):
                elapsed = (time.perf_counter() - t0) * 1000
                result = RouteResult(
                    layer=match.layer,
                    decision_id=match.decision_id,
                    confidence=match.confidence,
                    reason="capsule_decision_match",
                    routing_time_ms=elapsed,
                    model=match.model,
                    rules=match.rules,
                    inputs=match.inputs,
                    fallback=match.fallback,
                )
                self._record(result.layer)
                return result
            # Fallback if primary layer disabled
            if match.fallback and self.capsule.is_layer_enabled(match.fallback):
                elapsed = (time.perf_counter() - t0) * 1000
                result = RouteResult(
                    layer=match.fallback,
                    decision_id=match.decision_id,
                    confidence=match.confidence * 0.8,
                    reason="capsule_decision_fallback",
                    routing_time_ms=elapsed,
                    model=match.model,
                    rules=match.rules,
                    inputs=match.inputs,
                )
                self._record(result.layer)
                return result

        # Step 3: Keyword classifier
        layer = self._classify_keywords(query)
        if layer and self.capsule.is_layer_enabled(layer):
            elapsed = (time.perf_counter() - t0) * 1000
            result = RouteResult(
                layer=layer,
                confidence=0.5,
                reason="keyword_classifier",
                routing_time_ms=elapsed,
            )
            self._record(result.layer)
            return result

        # Step 4: Capsule priority fallback
        ordered = self.capsule.get_layers_by_priority()
        fallback_layer = ordered[0].name if ordered else "llm_reasoning"
        elapsed = (time.perf_counter() - t0) * 1000
        result = RouteResult(
            layer=fallback_layer,
            confidence=0.1,
            reason="capsule_priority_fallback",
            routing_time_ms=elapsed,
        )
        self._record(result.layer)
        return result

    def stats(self) -> dict[str, Any]:
        """Return routing statistics."""
        return {
            "total_routes": self._route_count,
            "layer_distribution": dict(self._layer_counts),
            "capsule_domain": self.capsule.domain,
        }

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    def _classify_keywords(query: str) -> Optional[str]:
        """Classify query by keyword patterns."""
        if _MATH_KEYWORDS.search(query):
            return "model_based"
        if _RULE_KEYWORDS.search(query):
            return "rule_constraints"
        if _STAT_KEYWORDS.search(query):
            return "statistical"
        return None

    def _record(self, layer: str) -> None:
        self._layer_counts[layer] = self._layer_counts.get(layer, 0) + 1
