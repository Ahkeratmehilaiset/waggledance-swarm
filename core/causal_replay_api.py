"""Causal replay API — helper for graph traversal endpoints."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class ReplayResult:
    node_id: str
    ancestors: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    shortest_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "ancestors": self.ancestors,
            "dependents": self.dependents,
            "shortest_path": self.shortest_path,
        }

class CausalReplayService:
    """Wraps CognitiveGraph operations for API use."""

    def __init__(self, graph=None):
        self._graph = graph

    def replay(self, node_id: str) -> ReplayResult:
        """Get ancestors, dependents, and shortest path for a node."""
        result = ReplayResult(node_id=node_id)

        if self._graph is None:
            return result

        if hasattr(self._graph, 'find_ancestors'):
            try:
                result.ancestors = list(self._graph.find_ancestors(node_id))
            except Exception as e:
                log.warning(f"find_ancestors failed: {e}")

        if hasattr(self._graph, 'find_dependents'):
            try:
                result.dependents = list(self._graph.find_dependents(node_id))
            except Exception as e:
                log.warning(f"find_dependents failed: {e}")

        if hasattr(self._graph, 'shortest_path') and result.ancestors:
            try:
                result.shortest_path = list(
                    self._graph.shortest_path(result.ancestors[0], node_id))
            except Exception:
                pass

        return result
