# SPDX-License-Identifier: BUSL-1.1
"""Ring messaging — Phase 9 §K.

Deterministic ordered routing of CellMessage objects across the ring.
Pure: no network calls; the runtime delivery layer is a future
addition (gated on Phase Z).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .cell_message_contract import CellMessage, validate
from .parent_child_relations import neighbors_of

RING_DELIVERY_OBSERVABILITY_SCHEMA = "hex.ring_delivery_observability.v0"

# Stable, structural categories for a blocked delivery. Set at the block site
# (where the cause is known), NOT by parsing the free-text blocked_reason —
# free-text classification is fragile and fail-open. ``None`` == delivered.
RING_BLOCK_SCHEMA_INVALID = "schema_invalid"
RING_BLOCK_NOT_NEIGHBOR = "not_neighbor"
RING_BLOCK_NOT_PARENT = "not_parent"
RING_BLOCK_NOT_CHILD = "not_child"


@dataclass(frozen=True)
class RingDelivery:
    message_id_seq: int
    delivered: bool
    blocked_reason: str | None
    msg: CellMessage
    blocked_category: str | None = None

    def to_dict(self) -> dict:
        return {
            "message_id_seq": self.message_id_seq,
            "delivered": self.delivered,
            "blocked_reason": self.blocked_reason,
            "blocked_category": self.blocked_category,
            "msg": self.msg.to_dict(),
        }


def deliver_one(topology: dict, msg: CellMessage,
                  seq: int) -> RingDelivery:
    errors = validate(msg, topology)
    if errors:
        return RingDelivery(
            message_id_seq=seq, delivered=False,
            blocked_reason="; ".join(errors), msg=msg,
            blocked_category=RING_BLOCK_SCHEMA_INVALID,
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
                blocked_category=RING_BLOCK_NOT_NEIGHBOR,
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
                blocked_category=RING_BLOCK_NOT_PARENT,
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
                blocked_category=RING_BLOCK_NOT_CHILD,
            )
    return RingDelivery(
        message_id_seq=seq, delivered=True,
        blocked_reason=None, msg=msg,
        blocked_category=None,
    )


def deliver_batch(topology: dict, messages: list[CellMessage]
                     ) -> list[RingDelivery]:
    """Deterministic per-message delivery; sequence preserved."""
    return [deliver_one(topology, m, seq)
            for seq, m in enumerate(messages)]


def summarize_ring_delivery_batch(
    deliveries: list[RingDelivery],
) -> dict:
    """Aggregate ring-health observability over a batch of deliveries.

    Pure read-only view over already-computed ``RingDelivery`` results: the
    overall delivery success ratio, delivered/blocked counts per message kind,
    and a histogram of blocked deliveries by their stable ``blocked_category``
    (so a fragmenting ring — many ``not_neighbor`` / ``not_parent`` blocks —
    is visible at a glance). It changes NO delivery decision and performs NO
    transport; it only reads what ``deliver_one`` already decided, and is
    re-derivable from the delivery list. This is the multi-instance
    coordination readiness signal, not a network layer.
    """
    total = 0
    delivered_count = 0
    blocked_count = 0
    by_message_kind: dict[str, dict[str, int]] = {}
    blocked_by_category: dict[str, int] = {}
    for delivery in deliveries:
        total += 1
        kind = delivery.msg.kind
        slot = by_message_kind.setdefault(
            kind, {"delivered": 0, "blocked": 0}
        )
        if delivery.delivered:
            delivered_count += 1
            slot["delivered"] += 1
        else:
            blocked_count += 1
            slot["blocked"] += 1
            category = delivery.blocked_category or "unspecified"
            blocked_by_category[category] = (
                blocked_by_category.get(category, 0) + 1
            )
    success_ratio = (delivered_count / total) if total else 0.0
    return {
        "schema_version": RING_DELIVERY_OBSERVABILITY_SCHEMA,
        "total": total,
        "delivered_count": delivered_count,
        "blocked_count": blocked_count,
        "delivery_success_ratio": success_ratio,
        "by_message_kind": {
            kind: by_message_kind[kind] for kind in sorted(by_message_kind)
        },
        "blocked_by_category": dict(sorted(blocked_by_category.items())),
        "transport_applied": False,
    }
