"""Warm tier — Phase 9 §L. Local fast storage (in-memory + disk-spillable)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WarmTier:
    nodes: dict[str, dict] = field(default_factory=dict)

    def get(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def put(self, node_id: str, payload: dict) -> "WarmTier":
        self.nodes[node_id] = payload
        return self

    def evict(self, node_id: str) -> dict | None:
        return self.nodes.pop(node_id, None)

    def size(self) -> int:
        return len(self.nodes)
