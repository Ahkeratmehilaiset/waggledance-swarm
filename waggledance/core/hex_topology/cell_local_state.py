# SPDX-License-Identifier: BUSL-1.1
"""Cell local state — Phase 9 §K.

Per-cell shard placeholders. Each shard is a deterministic dict; no
live runtime calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import OPTIONAL_SHARDS, REQUIRED_SHARDS


@dataclass
class CellLocalState:
    cell_id: str
    curiosity: dict = field(default_factory=dict)
    self_model: dict = field(default_factory=dict)
    vector: dict = field(default_factory=dict)
    event_log: list = field(default_factory=list)
    proposal_queue: list = field(default_factory=list)
    dream_queue: list = field(default_factory=list)
    local_budget: dict = field(default_factory=dict)
    builder_lane_queue: list | None = None

    def shards_present(self) -> dict[str, bool]:
        return {
            "curiosity": True,
            "self_model": True,
            "vector": True,
            "event_log": True,
            "proposal_queue": True,
            "dream_queue": True,
            "local_budget": True,
            "builder_lane_queue": self.builder_lane_queue is not None,
        }

    def to_dict(self) -> dict:
        d = {
            "cell_id": self.cell_id,
            "curiosity": dict(self.curiosity),
            "self_model": dict(self.self_model),
            "vector": dict(self.vector),
            "event_log": list(self.event_log),
            "proposal_queue": list(self.proposal_queue),
            "dream_queue": list(self.dream_queue),
            "local_budget": dict(self.local_budget),
        }
        if self.builder_lane_queue is not None:
            d["builder_lane_queue"] = list(self.builder_lane_queue)
        return d
