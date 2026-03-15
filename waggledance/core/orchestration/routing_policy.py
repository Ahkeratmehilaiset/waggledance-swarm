"""Routing policy — pure function, no I/O.

Ported from core/smart_router_v2.py and backend/routes/chat.py.
Routing order: hotcache -> micromodel -> memory -> llm -> swarm.
Only returns route types from ALLOWED_ROUTE_TYPES.
"""

from dataclasses import dataclass, field

from waggledance.core.domain.task import TaskRoute
from waggledance.core.ports.config_port import ConfigPort


ALLOWED_ROUTE_TYPES = frozenset({"hotcache", "memory", "micromodel", "llm", "swarm"})

SYSTEM_KEYWORDS = frozenset({
    "status", "tila", "health", "terveys", "uptime", "agents", "agentit",
    "memory", "muisti", "version", "versio",
})

TIME_KEYWORDS = frozenset({
    "time", "aika", "kellonaika", "päivämäärä", "date", "today", "tänään",
    "now", "nyt", "kello",
})


@dataclass
class RoutingFeatures:
    """Extracted features used for routing decisions."""

    query_length: int = 0
    language: str = "auto"
    has_hot_cache_hit: bool = False
    memory_score: float = 0.0
    is_time_query: bool = False
    is_system_query: bool = False
    matched_keywords: list[str] = field(default_factory=list)
    profile: str = "COTTAGE"
    has_micromodel_hit: bool = False
    micromodel_confidence: float = 0.0
    micromodel_enabled: bool = False


def select_route(features: RoutingFeatures, config: ConfigPort) -> TaskRoute:
    """Pure routing decision. Order: hotcache -> memory -> llm -> swarm.

    Only returns route_type from ALLOWED_ROUTE_TYPES.

    Decision logic ported from core/smart_router_v2.py:
    - Hot cache hit (confidence=1.0) -> hotcache
    - High memory score (>0.7) -> memory
    - Time/system queries or short queries -> llm
    - Complex queries (long, multi-keyword) with swarm enabled -> swarm
    - Default -> llm
    """
    import time

    start = time.monotonic()

    if features.has_hot_cache_hit:
        return TaskRoute(
            route_type="hotcache",
            confidence=1.0,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    if (
        features.micromodel_enabled
        and features.has_micromodel_hit
        and features.micromodel_confidence > 0.85
    ):
        return TaskRoute(
            route_type="micromodel",
            confidence=features.micromodel_confidence,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    if features.memory_score > 0.7:
        return TaskRoute(
            route_type="memory",
            confidence=features.memory_score,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    if features.is_time_query or features.is_system_query:
        return TaskRoute(
            route_type="llm",
            confidence=0.8,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    swarm_enabled = bool(config.get("swarm.enabled", False))
    if (
        swarm_enabled
        and features.query_length > 50
        and len(features.matched_keywords) >= 2
    ):
        return TaskRoute(
            route_type="swarm",
            confidence=0.7,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    return TaskRoute(
        route_type="llm",
        confidence=0.6,
        routing_latency_ms=(time.monotonic() - start) * 1000,
    )


def extract_features(
    query: str,
    hot_cache_hit: bool,
    memory_score: float,
    matched_keywords: list[str],
    profile: str,
    language: str = "auto",
    micromodel_enabled: bool = False,
    micromodel_hit: bool = False,
    micromodel_confidence: float = 0.0,
) -> RoutingFeatures:
    """Extract routing features from a query."""
    query_lower = query.lower()
    words = set(query_lower.split())

    return RoutingFeatures(
        query_length=len(query),
        language=language,
        has_hot_cache_hit=hot_cache_hit,
        memory_score=memory_score,
        is_time_query=bool(words & TIME_KEYWORDS),
        is_system_query=bool(words & SYSTEM_KEYWORDS),
        matched_keywords=matched_keywords,
        profile=profile,
        has_micromodel_hit=micromodel_hit,
        micromodel_confidence=micromodel_confidence,
        micromodel_enabled=micromodel_enabled,
    )
