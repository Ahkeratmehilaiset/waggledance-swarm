"""Ring messaging — Phase 9 §K.

Deterministic ordered routing of CellMessage objects across the ring.
Pure: no network calls; the runtime delivery layer is a future
addition (gated on Phase Z).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .cell_message_contract import CellMessage, validate
from .parent_child_relations import neighbors_of


@dataclass(frozen=True)
class RingDelivery:
    message_id_seq: int
    delivered: bool
    blocked_reason: str | None
    msg: CellMessage

    def to_dict(self) -> dict:
        return {
            "message_id_seq": self.message_id_seq,
            "delivered": self.delivered,
            "blocked_reason": self.blocked_reason,
            "msg": self.msg.to_dict(),
        }


def deliver_one(topology: dict, msg: CellMessage,
                  seq: int) -> RingDelivery:
    errors = validate(msg, topology)
    if errors:
        return RingDelivery(
            message_id_seq=seq, delivered=False,
            blocked_reason="; ".join(errors), msg=msg,
        )
    # Validate neighbor relation for ring messages
    if msg.kind in ("ring_request", "ring_response",
                      "neighbor_observation"):
        if msg.to_cell_id not in neighbors_of(topology, msg.from_cell_id):
            return RingDelivery(
                message_id_seq=seq, delivered=False,
                blocked_reason=(
                    f"to_cell_id {msg.to_cell_id!r} is not a neighbor "
                    f"of {msg.from_cell_id!r}"
                ),
                msg=msg,
            )
    if msg.kind == "child_to_parent":
        cell = (topology.get("cells") or {}).get(msg.from_cell_id) or {}
        if cell.get("parent_cell_id") != msg.to_cell_id:
            return RingDelivery(
                message_id_seq=seq, delivered=False,
                blocked_reason=(
                    f"child_to_parent: {msg.to_cell_id!r} is not parent "
                    f"of {msg.from_cell_id!r}"
                ),
                msg=msg,
            )
    if msg.kind == "parent_to_child":
        cell = (topology.get("cells") or {}).get(msg.from_cell_id) or {}
        children = cell.get("child_cell_ids") or []
        if msg.to_cell_id not in children:
            return RingDelivery(
                message_id_seq=seq, delivered=False,
                blocked_reason=(
                    f"parent_to_child: {msg.to_cell_id!r} is not a child "
                    f"of {msg.from_cell_id!r}"
                ),
                msg=msg,
            )
    return RingDelivery(
        message_id_seq=seq, delivered=True,
        blocked_reason=None, msg=msg,
    )


def deliver_batch(topology: dict, messages: list[CellMessage]
                     ) -> list[RingDelivery]:
    """Deterministic per-message delivery; sequence preserved."""
    return [deliver_one(topology, m, seq)
            for seq, m in enumerate(messages)]
