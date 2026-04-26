# SPDX-License-Identifier: BUSL-1.1
"""Cell message contract — Phase 9 §K.

Validates messages between cells over the ring. Pure validation; no
network calls.
"""
from __future__ import annotations

from dataclasses import dataclass


MESSAGE_KINDS = (
    "ring_request", "ring_response", "child_to_parent",
    "parent_to_child", "neighbor_observation",
)


@dataclass(frozen=True)
class CellMessage:
    schema_version: int
    from_cell_id: str
    to_cell_id: str
    kind: str
    payload: dict
    no_runtime_mutation: bool

    def __post_init__(self) -> None:
        if self.kind not in MESSAGE_KINDS:
            raise ValueError(
                f"unknown message kind: {self.kind!r}; "
                f"allowed: {MESSAGE_KINDS}"
            )
        if self.from_cell_id == self.to_cell_id:
            raise ValueError(
                f"from_cell_id and to_cell_id must differ: "
                f"{self.from_cell_id!r}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "from_cell_id": self.from_cell_id,
            "to_cell_id": self.to_cell_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "no_runtime_mutation": self.no_runtime_mutation,
        }


def make_message(*,
                      from_cell_id: str,
                      to_cell_id: str,
                      kind: str,
                      payload: dict | None = None,
                      ) -> CellMessage:
    return CellMessage(
        schema_version=1,
        from_cell_id=from_cell_id,
        to_cell_id=to_cell_id,
        kind=kind,
        payload=dict(payload or {}),
        no_runtime_mutation=True,
    )


def validate(msg: CellMessage,
                topology: dict) -> list[str]:
    """Return list of validation errors against the topology
    (e.g. that to_cell_id is a known neighbor/parent/child)."""
    errors: list[str] = []
    cells = topology.get("cells") or {}
    if msg.from_cell_id not in cells:
        errors.append(f"unknown from_cell_id: {msg.from_cell_id!r}")
    if msg.to_cell_id not in cells:
        errors.append(f"unknown to_cell_id: {msg.to_cell_id!r}")
    return errors
