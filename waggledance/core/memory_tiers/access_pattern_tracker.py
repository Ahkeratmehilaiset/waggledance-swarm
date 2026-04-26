"""Access-pattern tracker — Phase 9 §L.

Records read/write accesses per node. CRITICAL invariant: tracking
must NEVER rewrite meaning — it stores access counts, not derived
or compressed content.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AccessRecord:
    node_id: str
    access_count: int
    last_access_iso: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "access_count": self.access_count,
            "last_access_iso": self.last_access_iso,
        }


@dataclass
class AccessPatternTracker:
    records: dict[str, AccessRecord] = field(default_factory=dict)

    def record_access(self, node_id: str,
                          ts_iso: str) -> "AccessPatternTracker":
        prev = self.records.get(node_id)
        new_count = (prev.access_count if prev else 0) + 1
        self.records[node_id] = AccessRecord(
            node_id=node_id,
            access_count=new_count,
            last_access_iso=ts_iso,
        )
        return self

    def get(self, node_id: str) -> AccessRecord | None:
        return self.records.get(node_id)

    def to_dict(self) -> dict:
        return {nid: r.to_dict()
                for nid, r in sorted(self.records.items())}
