#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline proof that a MULTI-LEVEL hex mesh self-organizes with sound invariants.

Fable lane (48h hex-mesh autonomy roadmap, "Self-organizing swarm mesh" storyboard
area). Extends the single-subdivision proofs to a TWO-LEVEL mesh: a root subdivides
into branch cells, and each branch subdivides into leaf cells -- entirely OFFLINE,
granting no runtime authority. It proves the self-organized mesh holds:

* multi-level hierarchy: every level's children register under the correct parent;
  the whole tree stays bidirectional + acyclic across BOTH levels;
* per-level sibling rings: siblings at each level are mutual ring neighbours, and
  a ring message between same-level siblings DELIVERS;
* cross-level child_to_parent DELIVERS (a leaf reaches its branch parent);
* mesh isolation: a ring message to a NON-sibling in a DIFFERENT subtree (e.g. a
  leaf of branch A to a leaf of branch B) is BLOCKED on the topology boundary
  (not_neighbor) -- the mesh does not silently bridge subtrees;
* ancestor/descendant duality across levels (a leaf's ancestors are its branch and
  the root);
* deterministic self-organization: applying the plan set is reproducible;
* RUNTIME MUTATION AUTHORITY FALSE: the SOURCE topology is byte-identical before and
  after, and the ring-delivery summary reports transport_applied == False.

Observability-only: it plans, builds a candidate mesh, and reads what deliver_one
decides; no live topology mutation, routing influence, or transport.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.cell_message_contract import (  # noqa: E402
    CellMessage,
    make_message,
)
from waggledance.core.hex_topology.parent_child_relations import (  # noqa: E402
    ancestors_of,
    children_of,
    neighbors_of,
    parent_of,
)
from waggledance.core.hex_topology.ring_messaging import (  # noqa: E402
    RING_BLOCK_NOT_NEIGHBOR,
    deliver_batch,
    deliver_one,
    summarize_ring_delivery_batch,
)
from waggledance.core.hex_topology.subdivision_operator import (  # noqa: E402
    apply_plan_to_topology,
    plan_subdivision,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.hex_swarm_mesh_self_organization_proof.v0"

_ROOT = "root"
_BRANCHES = ("root.alpha", "root.beta")
_ALPHA_LEAVES = ("root.alpha.a", "root.alpha.b")
_BETA_LEAVES = ("root.beta.a", "root.beta.b")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="New output directory for the proof; must not exist.")
    parser.add_argument("--now", default=None,
                        help="Optional UTC override such as 2026-06-29T00:00:00Z.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_swarm_mesh_self_organization_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"hex swarm-mesh self-organization proof FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"hex swarm-mesh self-organization proof OK: {report['proof_path']}")
    else:
        print("hex swarm-mesh self-organization proof FAILED: "
              f"{', '.join(report['blockers'])}", file=sys.stderr)
    return 0 if report["ok"] else 1


def _subdivide(source: dict, parent: str, children: tuple[str, ...]):
    plan = plan_subdivision(
        parent_cell_id=parent,
        new_child_cell_ids=children,
        rationale="proof: multi-level swarm-mesh self-organization",
    )
    return apply_plan_to_topology(source, plan), plan


def build_hex_swarm_mesh_self_organization_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    source = _topology()
    source_digest_before = sha256_digest(source)

    # self-organize the two-level mesh from a single source (no mutation of source)
    lvl1, _p1 = _subdivide(source, _ROOT, _BRANCHES)
    lvl2a, _p2 = _subdivide(lvl1, "root.alpha", _ALPHA_LEAVES)
    candidate = _subdivide(lvl2a, "root.beta", _BETA_LEAVES)[0]

    # determinism: re-derive the whole mesh independently and compare digests
    r1, _ = _subdivide(_topology(), _ROOT, _BRANCHES)
    r2, _ = _subdivide(r1, "root.alpha", _ALPHA_LEAVES)
    candidate_repeat = _subdivide(r2, "root.beta", _BETA_LEAVES)[0]

    a_a, a_b = sorted(_ALPHA_LEAVES)
    b_a, _b_b = sorted(_BETA_LEAVES)

    sib_ring = deliver_one(candidate, _msg(a_a, a_b, "ring_request"), 0)
    child_parent = deliver_one(candidate, _msg(a_a, "root.alpha", "child_to_parent"), 1)
    cross_subtree = deliver_one(candidate, _msg(a_a, b_a, "ring_request"), 2)
    branch_ring = deliver_one(
        candidate, _msg("root.alpha", "root.beta", "ring_request"), 3)

    summary = summarize_ring_delivery_batch(deliver_batch(candidate, [
        _msg(a_a, a_b, "ring_request"),
        _msg(a_a, "root.alpha", "child_to_parent"),
        _msg(a_a, b_a, "ring_request"),
    ]))
    source_digest_after = sha256_digest(source)

    all_new = list(_BRANCHES) + list(_ALPHA_LEAVES) + list(_BETA_LEAVES)
    proof_checks = {
        "multi_level_hierarchy_registered": (
            all(parent_of(candidate, b) == _ROOT for b in _BRANCHES)
            and all(parent_of(candidate, c) == "root.alpha" for c in _ALPHA_LEAVES)
            and all(parent_of(candidate, c) == "root.beta" for c in _BETA_LEAVES)
            and all(b in children_of(candidate, _ROOT) for b in _BRANCHES)
        ),
        "whole_tree_bidirectional_and_acyclic": (
            _bidirectional(candidate) and _acyclic(candidate)
        ),
        "per_level_sibling_rings": (
            neighbors_of(candidate, "root.alpha") == ["root.beta"]
            and neighbors_of(candidate, "root.beta") == ["root.alpha"]
            and neighbors_of(candidate, a_a) == [a_b]
            and neighbors_of(candidate, b_a) == [_b_b]
        ),
        "same_level_ring_delivers": (
            sib_ring.delivered is True and branch_ring.delivered is True
        ),
        "cross_level_child_to_parent_delivers": (
            child_parent.delivered is True
        ),
        "cross_subtree_non_neighbor_blocks": (
            cross_subtree.delivered is False
            and cross_subtree.blocked_category == RING_BLOCK_NOT_NEIGHBOR
        ),
        "ancestor_descendant_duality_across_levels": (
            ancestors_of(candidate, a_a) == ["root.alpha", _ROOT]
            and ancestors_of(candidate, b_a) == ["root.beta", _ROOT]
        ),
        "self_organization_deterministic": (
            sha256_digest(candidate) == sha256_digest(candidate_repeat)
        ),
        "source_topology_unchanged": (
            source_digest_before == source_digest_after
        ),
        "no_transport_in_delivery_summary": (
            summary["transport_applied"] is False
        ),
    }
    blockers = [n for n, ok in proof_checks.items() if ok is not True]
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "proof_checks": proof_checks,
        "mesh_cell_ids": sorted((candidate.get("cells") or {}).keys()),
        "new_cells": all_new,
        "source_topology_digest_before": source_digest_before,
        "source_topology_digest_after": source_digest_after,
        "ring_delivery_summary": summary,
    }
    proof_path = out_dir / "hex_swarm_mesh_self_organization_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    return report


def _topology() -> dict[str, Any]:
    return {
        "cells": {
            "root": {"cell_id": "root", "parent_cell_id": None,
                     "child_cell_ids": [], "neighbor_cell_ids": []},
        },
    }


def _bidirectional(topology: dict) -> bool:
    cells = sorted((topology.get("cells") or {}).keys())
    return all(
        parent_of(topology, child) == cid
        for cid in cells for child in children_of(topology, cid)
    ) and all(
        cid in children_of(topology, parent_of(topology, cid))
        for cid in cells if parent_of(topology, cid) is not None
    )


def _acyclic(topology: dict) -> bool:
    cells = (topology.get("cells") or {}).keys()
    return all(cid not in ancestors_of(topology, cid) for cid in cells)


def _msg(from_cell_id: str, to_cell_id: str, kind: str) -> CellMessage:
    return make_message(from_cell_id=from_cell_id, to_cell_id=to_cell_id, kind=kind)


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
