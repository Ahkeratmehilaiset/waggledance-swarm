# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
MAGMA: Cognitive Graph — NetworkX-based knowledge graph layer.
Tracks causal, derived_from, input_to, and semantic edges between facts.
Persisted as JSON for portability.
"""

import json
import logging
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx

log = logging.getLogger("waggledance.cognitive_graph")

# Valid edge types
EDGE_TYPES = ("causal", "derived_from", "input_to", "semantic")


class CognitiveGraph:
    """Directed knowledge graph with typed edges and JSON persistence."""

    def __init__(self, persist_path: str = "data/cognitive_graph.json"):
        self.persist_path = persist_path
        self.graph = nx.DiGraph()
        self._load()

    # ── Persistence ──────────────────────────────────────────

    def _load(self):
        p = Path(self.persist_path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self.graph = nx.node_link_graph(data, edges="links")
                log.info(f"CognitiveGraph loaded: {self.graph.number_of_nodes()} nodes, "
                         f"{self.graph.number_of_edges()} edges")
            except Exception as e:
                log.warning(f"CognitiveGraph load failed, starting fresh: {e}")
                self.graph = nx.DiGraph()
        else:
            log.info("CognitiveGraph: no persistence file, starting fresh")

    def save(self):
        p = Path(self.persist_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            from core.disk_guard import check_disk_space
            check_disk_space(str(p.parent), label="CognitiveGraph")
        except (ImportError, OSError):
            pass
        data = nx.node_link_data(self.graph, edges="links")
        content = json.dumps(data, ensure_ascii=False)
        # Atomic write: temp file + os.replace to prevent corruption
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(p))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── Node operations ──────────────────────────────────────

    def add_node(self, node_id: str, **attrs):
        """Add or update a node with metadata."""
        if "timestamp" not in attrs:
            attrs["timestamp"] = time.time()
        self.graph.add_node(node_id, **attrs)

    def remove_node(self, node_id: str) -> bool:
        if node_id in self.graph:
            self.graph.remove_node(node_id)
            return True
        return False

    def get_node(self, node_id: str) -> Optional[dict]:
        if node_id not in self.graph:
            return None
        return {"id": node_id, **dict(self.graph.nodes[node_id])}

    def has_node(self, node_id: str) -> bool:
        return node_id in self.graph

    # ── Edge operations ──────────────────────────────────────

    def add_edge(self, source: str, target: str, link_type: str = "semantic",
                 **attrs):
        """Add a typed directed edge. Auto-creates nodes if missing."""
        if link_type not in EDGE_TYPES:
            raise ValueError(f"Invalid link_type '{link_type}', must be one of {EDGE_TYPES}")
        if not self.has_node(source):
            self.add_node(source)
        if not self.has_node(target):
            self.add_node(target)
        attrs["link_type"] = link_type
        attrs["timestamp"] = time.time()
        self.graph.add_edge(source, target, **attrs)

    def remove_edge(self, source: str, target: str) -> bool:
        if self.graph.has_edge(source, target):
            self.graph.remove_edge(source, target)
            return True
        return False

    def get_edges(self, node_id: str) -> List[dict]:
        """Get all edges (in + out) for a node."""
        edges = []
        if node_id not in self.graph:
            return edges
        for _, target, data in self.graph.out_edges(node_id, data=True):
            edges.append({"source": node_id, "target": target, **data})
        for source, _, data in self.graph.in_edges(node_id, data=True):
            edges.append({"source": source, "target": node_id, **data})
        return edges

    # ── Query operations ─────────────────────────────────────

    def neighbors(self, node_id: str, link_type: Optional[str] = None,
                  direction: str = "both") -> List[str]:
        """Get neighbor node IDs, optionally filtered by edge type."""
        if node_id not in self.graph:
            return []
        result = set()
        if direction in ("out", "both"):
            for _, target, data in self.graph.out_edges(node_id, data=True):
                if link_type is None or data.get("link_type") == link_type:
                    result.add(target)
        if direction in ("in", "both"):
            for source, _, data in self.graph.in_edges(node_id, data=True):
                if link_type is None or data.get("link_type") == link_type:
                    result.add(source)
        return list(result)

    def find_dependents(self, node_id: str, max_depth: int = 5) -> List[Tuple[str, int]]:
        """
        BFS traversal following causal/derived_from edges downstream.
        Returns [(node_id, depth), ...] sorted by depth.
        """
        if node_id not in self.graph:
            return []
        dependents = []
        visited = {node_id}
        queue = deque([(node_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth > max_depth:
                continue
            for _, target, data in self.graph.out_edges(current, data=True):
                if target in visited:
                    continue
                if data.get("link_type") in ("causal", "derived_from"):
                    visited.add(target)
                    dependents.append((target, depth + 1))
                    queue.append((target, depth + 1))

        return sorted(dependents, key=lambda x: x[1])

    def find_ancestors(self, node_id: str, max_depth: int = 5) -> List[Tuple[str, int]]:
        """BFS traversal following causal/derived_from edges upstream (predecessors)."""
        if node_id not in self.graph:
            return []
        ancestors = []
        visited = {node_id}
        queue = deque([(node_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth > max_depth:
                continue
            for source, _, data in self.graph.in_edges(current, data=True):
                if source in visited:
                    continue
                if data.get("link_type") in ("causal", "derived_from", "input_to"):
                    visited.add(source)
                    ancestors.append((source, depth + 1))
                    queue.append((source, depth + 1))

        return sorted(ancestors, key=lambda x: x[1])

    def shortest_path(self, source: str, target: str) -> Optional[List[str]]:
        """Shortest path between two nodes, or None if unreachable."""
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    # ── Self-entity (v3.2) ──────────────────────────────────

    SELF_ENTITY_ID = "self"

    def ensure_self_entity(self, **overrides) -> dict:
        """Ensure the self-entity node exists with v3.2 attributes.

        The self-entity is a CognitiveGraph node, not a separate store.
        Same versioning, snapshots, diffs, provenance, and replay apply.
        """
        defaults = {
            "entity_type": "system",
            "active_goals": [],
            "persistent_motives": [],
            "confidence_self_assessment": 0.5,
            "felt_load": 0.0,
            "uncertainty_areas": [],
            "epistemic_uncertainty_score": 0.0,
            "last_major_correction": "",
            "capability_limits": [],
            "identity_version": "v3.2-autonomy",
            "last_reflection_at": "",
            "hardware": {},
        }
        if self.has_node(self.SELF_ENTITY_ID):
            existing = dict(self.graph.nodes[self.SELF_ENTITY_ID])
            defaults.update(existing)
        defaults.update(overrides)
        self.add_node(self.SELF_ENTITY_ID, **defaults)
        return self.get_node(self.SELF_ENTITY_ID)

    def get_self_entity(self) -> Optional[dict]:
        """Return self-entity attributes or None if not initialized."""
        return self.get_node(self.SELF_ENTITY_ID)

    def update_self_entity(self, **attrs) -> dict:
        """Update self-entity attributes. Creates if missing."""
        if not self.has_node(self.SELF_ENTITY_ID):
            return self.ensure_self_entity(**attrs)
        for k, v in attrs.items():
            self.graph.nodes[self.SELF_ENTITY_ID][k] = v
        self.graph.nodes[self.SELF_ENTITY_ID]["timestamp"] = time.time()
        return self.get_node(self.SELF_ENTITY_ID)

    def stats(self) -> dict:
        """Summary statistics."""
        edge_types: Dict[str, int] = {}
        for _, _, data in self.graph.edges(data=True):
            lt = data.get("link_type", "unknown")
            edge_types[lt] = edge_types.get(lt, 0) + 1
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "edge_types": edge_types,
            "has_self_entity": self.has_node(self.SELF_ENTITY_ID),
        }
