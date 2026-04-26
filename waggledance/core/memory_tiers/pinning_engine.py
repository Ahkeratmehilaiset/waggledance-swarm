"""Pinning engine — Phase 9 §L.

Foundational identity anchors are PINNED and may NEVER demote to
cold/glacier. Pin status is sticky; only an explicit unpin (e.g.
human archival) may release it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PinRecord:
    node_id: str
    pinned: bool
    pin_reason: str
    anchor_status: str | None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "pinned": self.pinned,
            "pin_reason": self.pin_reason,
            "anchor_status": self.anchor_status,
        }


@dataclass
class PinningEngine:
    pins: dict[str, PinRecord] = field(default_factory=dict)

    def pin(self, *, node_id: str, reason: str,
              anchor_status: str | None = None) -> "PinningEngine":
        self.pins[node_id] = PinRecord(
            node_id=node_id, pinned=True, pin_reason=reason,
            anchor_status=anchor_status,
        )
        return self

    def unpin(self, node_id: str) -> bool:
        existing = self.pins.get(node_id)
        if existing is None or not existing.pinned:
            return False
        self.pins[node_id] = PinRecord(
            node_id=node_id, pinned=False,
            pin_reason="explicitly_unpinned",
            anchor_status=existing.anchor_status,
        )
        return True

    def is_pinned(self, node_id: str) -> bool:
        rec = self.pins.get(node_id)
        return rec is not None and rec.pinned

    def to_dict(self) -> dict:
        return {nid: r.to_dict()
                for nid, r in sorted(self.pins.items())}


def auto_pin_foundational(engine: PinningEngine,
                                node_id: str,
                                anchor_status: str) -> bool:
    """Foundational anchors are auto-pinned. Returns True iff pin
    was applied."""
    if anchor_status == "foundational":
        engine.pin(
            node_id=node_id,
            reason="foundational_anchor_auto_pin",
            anchor_status=anchor_status,
        )
        return True
    return False
