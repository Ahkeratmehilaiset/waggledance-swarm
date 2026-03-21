"""Causal engine — wraps CognitiveGraph for causal reasoning and impact analysis.

Provides causal chain discovery, impact propagation estimation,
root cause analysis, and counterfactual reasoning over the knowledge graph.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CognitiveGraph = None


def _get_graph_class():
    global _CognitiveGraph
    if _CognitiveGraph is None:
        try:
            from core.cognitive_graph import CognitiveGraph
            _CognitiveGraph = CognitiveGraph
        except ImportError:
            _CognitiveGraph = None
    return _CognitiveGraph


@dataclass
class CausalChain:
    """A discovered causal chain between two entities."""
    source: str
    target: str
    path: List[str]
    link_types: List[str]
    strength: float = 0.0
    depth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "path": self.path,
            "link_types": self.link_types,
            "strength": round(self.strength, 4),
            "depth": self.depth,
        }


@dataclass
class ImpactEstimate:
    """Estimated impact of a change to an entity."""
    entity: str
    affected_entities: List[str]
    impact_scores: Dict[str, float]
    total_impact: float = 0.0
    max_depth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity,
            "affected_count": len(self.affected_entities),
            "affected_entities": self.affected_entities,
            "impact_scores": {k: round(v, 4) for k, v in self.impact_scores.items()},
            "total_impact": round(self.total_impact, 4),
            "max_depth": self.max_depth,
        }


class CausalEngine:
    """Causal reasoning over the CognitiveGraph.

    Supports:
    - Causal chain discovery between entities
    - Impact propagation estimation
    - Root cause analysis (backward causal traversal)
    - What-if analysis (counterfactual propagation)
    """

    def __init__(self, cognitive_graph=None, persist_path: str = "data/cognitive_graph.json"):
        if cognitive_graph is not None:
            self._graph = cognitive_graph
        else:
            cls = _get_graph_class()
            self._graph = cls(persist_path=persist_path) if cls else None
        self._history: List[Dict] = []
        self._max_history = 100

    @property
    def available(self) -> bool:
        return self._graph is not None

    def find_causal_chain(self, source: str, target: str,
                          max_depth: int = 5) -> Optional[CausalChain]:
        """Find the shortest causal path between two entities."""
        if not self._graph:
            return None
        try:
            path = self._graph.shortest_path(source, target)
        except Exception:
            path = None
        if not path:
            return None

        link_types = []
        for i in range(len(path) - 1):
            edges = self._graph.get_edges(path[i])
            for edge in edges:
                if (edge.get("target") == path[i + 1] or
                        edge.get("source") == path[i + 1]):
                    link_types.append(edge.get("link_type", "unknown"))
                    break
            else:
                link_types.append("unknown")

        strength = 0.9 ** (len(path) - 1)  # decay per hop

        chain = CausalChain(
            source=source,
            target=target,
            path=path,
            link_types=link_types,
            strength=strength,
            depth=len(path) - 1,
        )
        self._history.append({"type": "chain", "result": chain.to_dict()})
        return chain

    def estimate_impact(self, entity: str, change_magnitude: float = 1.0,
                        max_depth: int = 3) -> ImpactEstimate:
        """Estimate the downstream impact of changing an entity's value.

        Impact decays exponentially with graph distance.
        """
        if not self._graph:
            return ImpactEstimate(entity=entity, affected_entities=[],
                                  impact_scores={})

        try:
            dependents = self._graph.find_dependents(entity, max_depth=max_depth)
        except Exception:
            dependents = []

        impact_scores = {}
        affected = []
        for dep_id, depth in dependents:
            impact = change_magnitude * (0.7 ** depth)
            impact_scores[dep_id] = impact
            affected.append(dep_id)

        total = sum(impact_scores.values())
        result = ImpactEstimate(
            entity=entity,
            affected_entities=affected,
            impact_scores=impact_scores,
            total_impact=total,
            max_depth=max(d for _, d in dependents) if dependents else 0,
        )
        self._history.append({"type": "impact", "result": result.to_dict()})
        return result

    def find_root_causes(self, entity: str,
                         max_depth: int = 5) -> List[Tuple[str, int]]:
        """Find upstream causes of an entity (backward causal traversal)."""
        if not self._graph:
            return []
        try:
            ancestors = self._graph.find_ancestors(entity, max_depth=max_depth)
            return ancestors
        except Exception:
            return []

    def what_if(self, entity: str, new_value: float,
                baselines: Dict[str, float] = None,
                max_depth: int = 3) -> Dict[str, float]:
        """Estimate new downstream values if entity changes.

        Uses linear propagation: new_downstream = baseline + impact × delta.
        """
        if not baselines:
            baselines = {}
        current = baselines.get(entity, 0.0)
        delta = new_value - current

        impact = self.estimate_impact(entity, abs(delta), max_depth=max_depth)
        projected = {}
        for dep_id, score in impact.impact_scores.items():
            base = baselines.get(dep_id, 0.0)
            direction = 1.0 if delta >= 0 else -1.0
            projected[dep_id] = base + direction * score

        self._history.append({
            "type": "what_if",
            "entity": entity,
            "old_value": current,
            "new_value": new_value,
            "projected_changes": len(projected),
        })
        return projected

    def get_entity_context(self, entity: str) -> Dict[str, Any]:
        """Get full causal context for an entity: ancestors, dependents, edges."""
        if not self._graph:
            return {"entity": entity, "available": False}
        node = self._graph.get_node(entity)
        edges = self._graph.get_edges(entity)
        try:
            ancestors = self._graph.find_ancestors(entity, max_depth=3)
        except Exception:
            ancestors = []
        try:
            dependents = self._graph.find_dependents(entity, max_depth=3)
        except Exception:
            dependents = []
        return {
            "entity": entity,
            "available": True,
            "attributes": node or {},
            "edges": edges,
            "ancestors": [(a, d) for a, d in ancestors],
            "dependents": [(a, d) for a, d in dependents],
        }

    def stats(self) -> Dict[str, Any]:
        graph_stats = self._graph.stats() if self._graph else {}
        return {
            "graph_available": self._graph is not None,
            "graph_stats": graph_stats,
            "queries_executed": len(self._history),
        }
