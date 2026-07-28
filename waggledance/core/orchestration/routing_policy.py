"""Routing policy — pure function, no I/O.

Ported from core/smart_router_v2.py and backend/routes/chat.py.
Routing order keeps hot-cache first, then by default prefers eligible
deterministic solvers over specialist micromodel hits before falling back to
memory, swarm, or LLM paths. The ordinary solver/micromodel precedence is
config-reversible. Only returns route types from ALLOWED_ROUTE_TYPES.
"""

import math
from dataclasses import dataclass, field

from waggledance.core.domain.task import TaskRoute
from waggledance.core.ports.config_port import ConfigPort


ALLOWED_ROUTE_TYPES = frozenset({
    "hotcache", "memory", "micromodel", "solver", "llm", "swarm",
})

# Solver-eligible intents (deterministic, no LLM needed)
SOLVER_INTENTS = frozenset({
    "math",
    "thermal",
    "stats",
    "symbolic",
    "constraint",
    "v3_13_0_solver",
})

SYSTEM_KEYWORDS = frozenset({
    "status", "tila", "health", "terveys", "uptime", "agents", "agentit",
    "memory", "muisti", "version", "versio",
})

TIME_KEYWORDS = frozenset({
    "time", "aika", "kellonaika", "päivämäärä", "date", "today", "tänään",
    "now", "nyt", "kello",
})


def _strict_config_bool(config: ConfigPort, key: str, default: bool) -> bool:
    """Read one boolean without truthiness coercion.

    A missing or malformed solver-first value keeps the safety-oriented
    canonical default.  Only an exact YAML/Python boolean can change it; values
    such as ``"false"``, ``0``, or hostile truthiness objects cannot silently
    alter routing authority.
    """
    value = config.get(key, default)
    return value if type(value) is bool else default


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
    profile: str = "HOME"
    has_micromodel_hit: bool = False
    micromodel_confidence: float = 0.0
    micromodel_enabled: bool = False
    solver_intent: str = ""


def select_route(features: RoutingFeatures, config: ConfigPort) -> TaskRoute:
    """Return one bounded route with deterministic solvers first by default.

    Only returns route_type from ALLOWED_ROUTE_TYPES.

    Decision logic ported from core/smart_router_v2.py:
    - Hot cache hit (confidence=1.0) -> hotcache
    - Explicit registry solver commands -> solver
    - Solver-eligible non-time/system intent -> solver when precedence is enabled
    - High-confidence micromodel hit -> micromodel
    - Remaining solver-eligible intent -> solver
    - Remaining time/system query -> llm
    - High memory score (>0.7) -> memory
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

    solver_first_enabled = _strict_config_bool(
        config,
        "routing.deterministic_solver_first_enabled",
        True,
    )

    # Explicit registry solver commands carry JSON payloads where words like
    # "date" or "status" are data, not chat-level time/system intents.
    if features.solver_intent == "v3_13_0_solver":
        return TaskRoute(
            route_type="solver",
            confidence=0.95,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    # Solver-first: eligible deterministic work must not be pre-empted by a
    # probabilistic specialist hit. Time/system flags exclude ordinary solver
    # selection and continue through the existing specialist/fallback order.
    if (
        solver_first_enabled
        and not features.is_time_query
        and not features.is_system_query
        and features.solver_intent in SOLVER_INTENTS
    ):
        return TaskRoute(
            route_type="solver",
            confidence=0.95,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    micromodel_confidence = features.micromodel_confidence
    confidence_is_bounded = (
        type(micromodel_confidence) in {int, float}
        and 0.0 <= micromodel_confidence <= 1.0
        and math.isfinite(float(micromodel_confidence))
    )
    if (
        features.micromodel_enabled
        and features.has_micromodel_hit
        and confidence_is_bounded
        and micromodel_confidence > 0.85
    ):
        return TaskRoute(
            route_type="micromodel",
            confidence=float(micromodel_confidence),
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    # Time/system queries override solver ("paljonko kello" is time, not math)
    if features.is_time_query or features.is_system_query:
        return TaskRoute(
            route_type="llm",
            confidence=0.8,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    # A disabled precedence flag restores the legacy micromodel priority; it
    # never disables deterministic solvers when no valid specialist hit exists.
    if features.solver_intent in SOLVER_INTENTS:
        return TaskRoute(
            route_type="solver",
            confidence=0.95,
            routing_latency_ms=(time.monotonic() - start) * 1000,
        )

    if features.memory_score > 0.7:
        return TaskRoute(
            route_type="memory",
            confidence=features.memory_score,
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


def _normalize_tokens(text: str) -> set[str]:
    """Tokenize and strip punctuation so 'status?' matches 'status'."""
    import re
    return {re.sub(r'[^\w]', '', w) for w in text.lower().split()} - {""}


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
    from waggledance.core.reasoning.solver_router import SolverRouter

    words = _normalize_tokens(query)

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
        solver_intent=SolverRouter.classify_intent(query),
    )
