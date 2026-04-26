"""Cold tier — Phase 9 §L. Disk-resident; cheap to keep, slower to access."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColdTier:
    """Disk-resident in spirit; in-memory for the scaffold. The
    real-runtime cold tier is a future addition that swaps to disk."""
    nodes: dict[str, dict] = field(default_factory=dict)

    def get(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def put(self, node_id: str, payload: dict) -> "ColdTier":
        self.nodes[node_id] = payload
        return self

    def evict(self, node_id: str) -> dict | None:
        return self.nodes.pop(node_id, None)

    def size(self) -> int:
        return len(self.nodes)
