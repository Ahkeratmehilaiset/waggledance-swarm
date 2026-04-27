# SPDX-License-Identifier: BUSL-1.1
"""Hot tier — Phase 9 §L. In-memory, fastest access."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HotTier:
    """In-memory cache. Caller is responsible for capacity bounds."""
    nodes: dict[str, dict] = field(default_factory=dict)

    def get(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def put(self, node_id: str, payload: dict) -> "HotTier":
        self.nodes[node_id] = payload
        return self

    def evict(self, node_id: str) -> dict | None:
        return self.nodes.pop(node_id, None)

    def size(self) -> int:
        return len(self.nodes)
