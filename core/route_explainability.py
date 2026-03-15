"""Route explainability — structured breakdown of routing decisions."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class RouteExplanation:
    query: str
    route_type: str = ""
    confidence: float = 0.0
    fallback_chain: list[str] = field(default_factory=list)
    cache_hit: bool = False
    matched_keywords: list[str] = field(default_factory=list)
    skip_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "route_type": self.route_type,
            "confidence": self.confidence,
            "fallback_chain": self.fallback_chain,
            "cache_hit": self.cache_hit,
            "matched_keywords": self.matched_keywords,
            "skip_reasons": self.skip_reasons,
        }

def explain_route(query: str,
                  hot_cache_hit: bool = False,
                  memory_score: float = 0.0,
                  micromodel_enabled: bool = False,
                  micromodel_hit: bool = False,
                  micromodel_confidence: float = 0.0,
                  matched_keywords: list[str] | None = None,
                  is_time_query: bool = False,
                  is_system_query: bool = False,
                  swarm_enabled: bool = False) -> RouteExplanation:
    """Generate a structured route explanation for a query."""
    exp = RouteExplanation(query=query, matched_keywords=matched_keywords or [])

    # Check hotcache
    if hot_cache_hit:
        exp.route_type = "hotcache"
        exp.confidence = 1.0
        exp.cache_hit = True
        return exp
    exp.skip_reasons["hotcache"] = "no cache hit"

    # Check micromodel
    if micromodel_enabled and micromodel_hit and micromodel_confidence > 0.85:
        exp.route_type = "micromodel"
        exp.confidence = micromodel_confidence
        return exp
    if not micromodel_enabled:
        exp.skip_reasons["micromodel"] = "disabled"
    elif not micromodel_hit:
        exp.skip_reasons["micromodel"] = "no hit"
    elif micromodel_confidence <= 0.85:
        exp.skip_reasons["micromodel"] = f"confidence {micromodel_confidence:.2f} <= 0.85"

    # Check memory
    if memory_score > 0.7:
        exp.route_type = "memory"
        exp.confidence = memory_score
        return exp
    exp.skip_reasons["memory"] = f"score {memory_score:.2f} <= 0.7"

    # Check LLM shortcuts
    if is_time_query or is_system_query:
        exp.route_type = "llm"
        exp.confidence = 0.8
        return exp

    # Check swarm
    if swarm_enabled and len(query) > 50 and len(matched_keywords or []) >= 2:
        exp.route_type = "swarm"
        exp.confidence = 0.7
        return exp
    if not swarm_enabled:
        exp.skip_reasons["swarm"] = "disabled"

    # Default
    exp.route_type = "llm"
    exp.confidence = 0.6
    exp.fallback_chain = ["memory", "micromodel", "llm", "swarm"]
    return exp
