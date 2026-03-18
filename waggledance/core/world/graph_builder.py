"""
GraphBuilder — updates CognitiveGraph from runtime query lifecycle.

After each handle_query():
  - Ensures capability node exists (with usage stats)
  - Ensures intent node exists
  - Adds causal edge: intent → capability (with success/grade info)
  - Updates query count and success rate on capability node
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("waggledance.world.graph_builder")


class GraphBuilder:
    """Records query→capability→result relationships in CognitiveGraph."""

    def __init__(self, cognitive_graph):
        self._graph = cognitive_graph

    def record(
        self,
        query: str,
        intent: str,
        capability_id: str,
        executed: bool,
        quality_grade: str = "",
        quality_path: str = "",
    ) -> None:
        """Record a query lifecycle in the graph.

        Creates/updates:
          - intent node (e.g. "intent:math")
          - capability node (e.g. "capability:solve.math")
          - causal edge: intent → capability
        """
        if self._graph is None:
            return

        now = time.time()

        # 1. Ensure intent node
        intent_id = f"intent:{intent}"
        intent_node = self._graph.get_node(intent_id)
        if intent_node:
            self._graph.add_node(
                intent_id,
                node_type="intent",
                query_count=intent_node.get("query_count", 0) + 1,
                last_query=now,
            )
        else:
            self._graph.add_node(
                intent_id,
                node_type="intent",
                query_count=1,
                last_query=now,
            )

        # 2. Ensure capability node
        cap_id = f"capability:{capability_id}"
        cap_node = self._graph.get_node(cap_id)
        if cap_node:
            total = cap_node.get("invocation_count", 0) + 1
            successes = cap_node.get("success_count", 0) + (1 if executed else 0)
            self._graph.add_node(
                cap_id,
                node_type="capability",
                capability_id=capability_id,
                invocation_count=total,
                success_count=successes,
                success_rate=round(successes / total, 3) if total else 0,
                last_invoked=now,
            )
        else:
            self._graph.add_node(
                cap_id,
                node_type="capability",
                capability_id=capability_id,
                invocation_count=1,
                success_count=1 if executed else 0,
                success_rate=1.0 if executed else 0.0,
                last_invoked=now,
            )

        # 3. Add/update causal edge: intent → capability
        self._graph.add_edge(
            intent_id, cap_id,
            link_type="causal",
            quality_path=quality_path,
            quality_grade=quality_grade,
            last_used=now,
        )

    def ensure_agent_nodes(self, alias_registry) -> int:
        """Populate agent entity nodes from alias registry.

        Returns number of nodes added.
        """
        if self._graph is None:
            return 0

        added = 0
        for agent in alias_registry.all_agents():
            node_id = f"agent:{agent.canonical}"
            if not self._graph.has_node(node_id):
                self._graph.add_node(
                    node_id,
                    node_type="agent",
                    canonical=agent.canonical,
                    legacy_id=agent.legacy_id,
                    profiles=list(agent.profiles),
                )
                added += 1
        return added

    def ensure_capability_nodes(self, capability_registry) -> int:
        """Populate capability nodes from registry.

        Returns number of nodes added.
        """
        if self._graph is None:
            return 0

        added = 0
        for cap in capability_registry.list_all():
            node_id = f"capability:{cap.capability_id}"
            if not self._graph.has_node(node_id):
                self._graph.add_node(
                    node_id,
                    node_type="capability",
                    capability_id=cap.capability_id,
                    category=cap.category.value,
                    description=cap.description,
                    invocation_count=0,
                    success_count=0,
                    success_rate=0.0,
                )
                added += 1
        return added

    def stats(self) -> dict:
        if self._graph is None:
            return {"graph": None}
        g_stats = self._graph.stats()
        return {
            "graph_nodes": g_stats.get("nodes", 0),
            "graph_edges": g_stats.get("edges", 0),
            "edge_types": g_stats.get("edge_types", {}),
        }
