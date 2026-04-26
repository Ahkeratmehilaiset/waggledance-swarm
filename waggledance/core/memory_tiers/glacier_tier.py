"""Glacier tier — Phase 9 §L. Long-term archive; slowest access; never lossy."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GlacierTier:
    nodes: dict[str, dict] = field(default_factory=dict)

    def get(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def put(self, node_id: str, payload: dict) -> "GlacierTier":
        self.nodes[node_id] = payload
        return self

    def evict(self, node_id: str) -> dict | None:
        return self.nodes.pop(node_id, None)

    def size(self) -> int:
        return len(self.nodes)
