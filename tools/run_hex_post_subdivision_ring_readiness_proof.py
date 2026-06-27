#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline proof that a shadow subdivision yields a ring-ready child set.

Lead-dispatched fable lane (48h hex-mesh autonomy roadmap) -- the capstone that
ties subdivision (parent-child) and ring routing together. It applies a
subdivision plan OFFLINE and proves the resulting candidate topology is
ring-ready for the new shadow children, without granting any runtime authority:

* the new children form a sibling ring -- each new child's neighbor set is
  exactly the other new children (mutual sibling adjacency);
* a ring message (ring_request / neighbor_observation) between two new siblings
  DELIVERS, and a child_to_parent message from a new child to the subdivided
  parent DELIVERS (the real edges are honoured);
* a ring message from a new child to a NON-sibling (the parent or root, which is
  not its neighbor) is BLOCKED on the topology boundary (``not_neighbor``);
* the candidate preserves the parent-child invariants (bidirectional + acyclic);
* RUNTIME MUTATION AUTHORITY FALSE: the SOURCE topology is byte-identical before
  and after, and the delivery summary reports ``transport_applied == False``.

Observability-only: it plans, builds a candidate topology, and reads what
``deliver_one`` decides; it performs no live topology mutation, routing
influence, or transport.
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


REPORT_VERSION = "wd.hex_post_subdivision_ring_readiness_proof.v0"

_PARENT = "thermal"
_CHILDREN = ("thermal.a", "thermal.b")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the proof. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-27T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_post_subdivision_ring_readiness_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex post-subdivision ring readiness proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex post-subdivision ring readiness proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex post-subdivision ring readiness proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_post_subdivision_ring_readiness_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    source = _topology()
    source_digest_before = sha256_digest(source)

    plan = plan_subdivision(
        parent_cell_id=_PARENT,
        new_child_cell_ids=_CHILDREN,
        rationale="proof: post-subdivision ring readiness",
    )
    candidate = apply_plan_to_topology(source, plan)
    child_a, child_b = sorted(plan.new_child_cell_ids)

    # ring messages over the candidate
    sibling_ring = deliver_one(
        candidate, _msg(child_a, child_b, "ring_request"), 0)
    sibling_obs = deliver_one(
        candidate, _msg(child_a, child_b, "neighbor_observation"), 1)
    child_to_parent = deliver_one(
        candidate, _msg(child_a, _PARENT, "child_to_parent"), 2)
    non_sibling_parent = deliver_one(
        candidate, _msg(child_a, _PARENT, "ring_request"), 3)
    non_sibling_root = deliver_one(
        candidate, _msg(child_a, "root", "ring_request"), 4)

    summary = summarize_ring_delivery_batch(deliver_batch(candidate, [
        _msg(child_a, child_b, "ring_request"),
        _msg(child_a, _PARENT, "child_to_parent"),
        _msg(child_a, _PARENT, "ring_request"),
    ]))
    source_digest_after = sha256_digest(source)

    proof_checks = {
        "new_children_form_sibling_ring": (
            neighbors_of(candidate, child_a) == [child_b]
            and neighbors_of(candidate, child_b) == [child_a]
        ),
        "sibling_ring_message_delivers": (
            sibling_ring.delivered is True
            and sibling_obs.delivered is True
        ),
        "child_to_parent_to_subdivided_parent_delivers": (
            child_to_parent.delivered is True
        ),
        "non_sibling_message_blocks_not_neighbor": (
            non_sibling_parent.delivered is False
            and non_sibling_parent.blocked_category == RING_BLOCK_NOT_NEIGHBOR
            and non_sibling_root.delivered is False
            and non_sibling_root.blocked_category == RING_BLOCK_NOT_NEIGHBOR
        ),
        "candidate_preserves_hierarchy_invariants": (
            _bidirectional(candidate) and _acyclic(candidate)
            and all(parent_of(candidate, c) == _PARENT
                    for c in plan.new_child_cell_ids)
            and all(c in children_of(candidate, _PARENT)
                    for c in plan.new_child_cell_ids)
        ),
        "source_topology_unchanged": (
            source_digest_before == source_digest_after
        ),
        "no_transport_in_delivery_summary": (
            summary["transport_applied"] is False
        ),
    }
    blockers = [
        name for name, passed in proof_checks.items() if passed is not True
    ]
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "proof_checks": proof_checks,
        "plan": plan.to_dict(),
        "new_children": [child_a, child_b],
        "candidate_cell_ids": sorted((candidate.get("cells") or {}).keys()),
        "source_topology_digest_before": source_digest_before,
        "source_topology_digest_after": source_digest_after,
        "ring_delivery_summary": summary,
    }
    proof_path = out_dir / "hex_post_subdivision_ring_readiness_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _topology() -> dict[str, Any]:
    return {
        "cells": {
            "root": {
                "cell_id": "root", "parent_cell_id": None,
                "child_cell_ids": ["thermal"], "neighbor_cell_ids": [],
            },
            "thermal": {
                "cell_id": "thermal", "parent_cell_id": "root",
                "child_cell_ids": [], "neighbor_cell_ids": [],
            },
        },
    }


def _bidirectional(topology: dict) -> bool:
    cells = sorted((topology.get("cells") or {}).keys())
    return all(
        parent_of(topology, child) == cid
        for cid in cells
        for child in children_of(topology, cid)
    ) and all(
        cid in children_of(topology, parent_of(topology, cid))
        for cid in cells
        if parent_of(topology, cid) is not None
    )


def _acyclic(topology: dict) -> bool:
    cells = (topology.get("cells") or {}).keys()
    return all(cid not in ancestors_of(topology, cid) for cid in cells)


def _msg(from_cell_id: str, to_cell_id: str, kind: str) -> CellMessage:
    return make_message(
        from_cell_id=from_cell_id,
        to_cell_id=to_cell_id,
        kind=kind,
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
