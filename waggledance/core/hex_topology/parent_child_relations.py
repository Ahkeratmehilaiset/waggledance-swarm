# SPDX-License-Identifier: BUSL-1.1
"""Parent / child / sibling / neighbor relations — Phase 9 §K.

Pure deterministic queries over the topology dict.
"""
from __future__ import annotations


def parent_of(topology: dict, cell_id: str) -> str | None:
    cell = (topology.get("cells") or {}).get(cell_id)
    if cell is None:
        return None
    return cell.get("parent_cell_id")


def children_of(topology: dict, cell_id: str) -> list[str]:
    cell = (topology.get("cells") or {}).get(cell_id)
    if cell is None:
        return []
    return sorted(cell.get("child_cell_ids") or [])


def siblings_of(topology: dict, cell_id: str) -> list[str]:
    parent = parent_of(topology, cell_id)
    if parent is None:
        return []
    sibs = [c for c in children_of(topology, parent) if c != cell_id]
    return sorted(sibs)


def neighbors_of(topology: dict, cell_id: str) -> list[str]:
    cell = (topology.get("cells") or {}).get(cell_id)
    if cell is None:
        return []
    return sorted(cell.get("neighbor_cell_ids") or [])


def ancestors_of(topology: dict, cell_id: str) -> list[str]:
    out: list[str] = []
    cur = cell_id
    seen: set[str] = set()
    while True:
        p = parent_of(topology, cur)
        if p is None or p in seen:
            break
        out.append(p)
        seen.add(p)
        cur = p
    return out


def descendants_of(topology: dict, cell_id: str) -> list[str]:
    out: list[str] = []
    stack = list(children_of(topology, cell_id))
    while stack:
        c = stack.pop()
        if c in out:
            continue
        out.append(c)
        stack.extend(children_of(topology, c))
    return sorted(out)
