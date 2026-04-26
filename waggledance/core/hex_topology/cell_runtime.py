# SPDX-License-Identifier: BUSL-1.1
"""Cell runtime — Phase 9 §K.

CellRuntime ties one cell_id to its local state + topology metadata.
NEVER mutates real runtime; all live state changes flow through Phase Z.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import (
    HEX_RUNTIME_SCHEMA_VERSION,
    LIVE_STATES,
    REQUIRED_SHARDS,
    SUBDIVISION_STATES,
)
from .cell_local_state import CellLocalState


@dataclass(frozen=True)
class CellRuntime:
    schema_version: int
    cell_id: str
    parent_cell_id: str | None
    child_cell_ids: tuple[str, ...]
    neighbor_cell_ids: tuple[str, ...]
    shards_present: dict[str, bool]
    live_state: str
    subdivision_state: str
    capsule_context: str = "neutral_v1"

    def __post_init__(self) -> None:
        if self.live_state not in LIVE_STATES:
            raise ValueError(f"unknown live_state: {self.live_state!r}")
        if self.subdivision_state not in SUBDIVISION_STATES:
            raise ValueError(
                f"unknown subdivision_state: {self.subdivision_state!r}"
            )
        for s in REQUIRED_SHARDS:
            if not self.shards_present.get(s, False):
                raise ValueError(
                    f"required shard missing: {s!r}"
                )
        if self.cell_id in self.child_cell_ids:
            raise ValueError(
                "cell_id cannot appear in own child_cell_ids"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "cell_id": self.cell_id,
            "parent_cell_id": self.parent_cell_id,
            "child_cell_ids": list(self.child_cell_ids),
            "neighbor_cell_ids": list(self.neighbor_cell_ids),
            "shards": dict(sorted(self.shards_present.items())),
            "live_state": self.live_state,
            "subdivision_state": self.subdivision_state,
            "capsule_context": self.capsule_context,
        }


def make_runtime(*,
                      cell_id: str,
                      local_state: CellLocalState | None = None,
                      parent_cell_id: str | None = None,
                      child_cell_ids: tuple[str, ...] = (),
                      neighbor_cell_ids: tuple[str, ...] = (),
                      live_state: str = "shadow_only",
                      subdivision_state: str = "leaf",
                      capsule_context: str = "neutral_v1",
                      ) -> CellRuntime:
    if local_state is None:
        local_state = CellLocalState(cell_id=cell_id)
    elif local_state.cell_id != cell_id:
        raise ValueError(
            f"local_state.cell_id {local_state.cell_id!r} != "
            f"cell_id {cell_id!r}"
        )
    return CellRuntime(
        schema_version=HEX_RUNTIME_SCHEMA_VERSION,
        cell_id=cell_id,
        parent_cell_id=parent_cell_id,
        child_cell_ids=tuple(sorted(child_cell_ids)),
        neighbor_cell_ids=tuple(sorted(neighbor_cell_ids)),
        shards_present=local_state.shards_present(),
        live_state=live_state,
        subdivision_state=subdivision_state,
        capsule_context=capsule_context,
    )


def make_topology(runtimes: list[CellRuntime]) -> dict:
    """Compose a topology dict from CellRuntime objects (deterministic
    sorted order)."""
    cells = {r.cell_id: r.to_dict()
              for r in sorted(runtimes, key=lambda x: x.cell_id)}
    return {"schema_version": HEX_RUNTIME_SCHEMA_VERSION, "cells": cells}
