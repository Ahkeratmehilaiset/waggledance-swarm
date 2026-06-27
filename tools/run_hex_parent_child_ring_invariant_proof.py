#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline conformance proof for the hex parent-child + ring-routing invariants.

Lead-dispatched lane (48h hex-mesh autonomy roadmap). Exercises the two stable hex
primitives -- ``parent_child_relations`` (offline hierarchy queries) and
``ring_messaging`` (deterministic observability-only delivery) -- on a sample
topology and asserts their structural invariants:

PARENT-CHILD (over every cell):
  * bidirectional consistency: child in children_of(parent) <=> parent_of(child)==parent
  * acyclic hierarchy: no cell is its own ancestor
  * ancestor/descendant duality: a in ancestors_of(b) <=> b in descendants_of(a)
  * sibling consistency: every sibling shares the parent and excludes self
  * the root has no parent

RING ROUTING (per message kind):
  * a valid neighbor message delivers; a non-neighbor one blocks (topology_boundary)
  * child_to_parent / parent_to_child enforce the real hierarchy edge (not_parent / not_child)
  * a message referencing an unknown cell blocks on schema (schema_invalid)
  * the batch observability summary is internally consistent

RUNTIME MUTATION AUTHORITY FALSE (the dormant / observability-only invariant):
  * the topology digest is byte-identical before and after every query + delivery
  * the delivery summary reports transport_applied == False

It performs NO topology mutation, NO routing influence, NO transport; it only reads.
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
    descendants_of,
    parent_of,
    siblings_of,
)
from waggledance.core.hex_topology.ring_messaging import (  # noqa: E402
    RING_BLOCK_CLASS_SCHEMA,
    RING_BLOCK_CLASS_TOPOLOGY,
    RING_BLOCK_NOT_CHILD,
    RING_BLOCK_NOT_NEIGHBOR,
    RING_BLOCK_NOT_PARENT,
    RING_BLOCK_SCHEMA_INVALID,
    deliver_batch,
    deliver_one,
    summarize_ring_delivery_batch,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.hex_parent_child_ring_invariant_proof.v0"


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
        report = build_hex_parent_child_ring_invariant_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex parent-child + ring invariant proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex parent-child + ring invariant proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex parent-child + ring invariant proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_parent_child_ring_invariant_proof(
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

    topology = _topology()
    cell_ids = sorted((topology.get("cells") or {}).keys())
    digest_before = sha256_digest(topology)

    # --- parent-child invariants over EVERY cell ---
    bidirectional = all(
        parent_of(topology, child) == cid
        for cid in cell_ids
        for child in children_of(topology, cid)
    ) and all(
        cid in children_of(topology, parent_of(topology, cid))
        for cid in cell_ids
        if parent_of(topology, cid) is not None
    )
    acyclic = all(
        cid not in ancestors_of(topology, cid) for cid in cell_ids
    )
    duality = all(
        (a in ancestors_of(topology, b))
        == (b in descendants_of(topology, a))
        for a in cell_ids
        for b in cell_ids
    )
    sibling_consistent = all(
        all(
            sib != cid
            and parent_of(topology, sib) == parent_of(topology, cid)
            for sib in siblings_of(topology, cid)
        )
        for cid in cell_ids
    )
    root_no_parent = parent_of(topology, "root") is None

    # --- ring routing invariants per kind ---
    valid_neighbor = deliver_one(
        topology, _msg("a", "b", "ring_request"), 0
    )
    non_neighbor = deliver_one(
        topology, _msg("a", "a1", "ring_request"), 1
    )
    child_to_parent_ok = deliver_one(
        topology, _msg("a1", "a", "child_to_parent"), 2
    )
    child_to_parent_wrong = deliver_one(
        topology, _msg("a1", "root", "child_to_parent"), 3
    )
    parent_to_child_ok = deliver_one(
        topology, _msg("a", "a1", "parent_to_child"), 4
    )
    parent_to_child_wrong = deliver_one(
        topology, _msg("a", "b", "parent_to_child"), 5
    )
    schema_invalid = deliver_one(
        topology, _msg("a", "ghost", "ring_request"), 6
    )

    batch = deliver_batch(topology, [
        _msg("a", "b", "ring_request"),
        _msg("a", "a1", "ring_request"),
        _msg("a1", "a", "child_to_parent"),
        _msg("a", "ghost", "parent_to_child"),
    ])
    summary = summarize_ring_delivery_batch(batch)

    digest_after = sha256_digest(topology)

    proof_checks = {
        "parent_child_bidirectional_consistent": bidirectional,
        "hierarchy_acyclic": acyclic,
        "ancestor_descendant_duality": duality,
        "sibling_consistency": sibling_consistent,
        "root_has_no_parent": root_no_parent,
        "ring_valid_neighbor_delivered": (
            valid_neighbor.delivered is True
            and valid_neighbor.blocked_category is None
        ),
        "ring_non_neighbor_blocked_topology_class": (
            non_neighbor.delivered is False
            and non_neighbor.blocked_category == RING_BLOCK_NOT_NEIGHBOR
        ),
        "ring_child_to_parent_edge_enforced": (
            child_to_parent_ok.delivered is True
            and child_to_parent_wrong.delivered is False
            and child_to_parent_wrong.blocked_category == RING_BLOCK_NOT_PARENT
        ),
        "ring_parent_to_child_edge_enforced": (
            parent_to_child_ok.delivered is True
            and parent_to_child_wrong.delivered is False
            and parent_to_child_wrong.blocked_category == RING_BLOCK_NOT_CHILD
        ),
        "ring_unknown_cell_blocked_schema_class": (
            schema_invalid.delivered is False
            and schema_invalid.blocked_category == RING_BLOCK_SCHEMA_INVALID
        ),
        "ring_summary_internally_consistent": (
            summary["total"]
            == summary["delivered_count"] + summary["blocked_count"]
            == len(batch)
            and sum(summary["blocked_by_category"].values())
            == summary["blocked_count"]
            and summary["blocked_by_class"].get(RING_BLOCK_CLASS_TOPOLOGY, 0)
            + summary["blocked_by_class"].get(RING_BLOCK_CLASS_SCHEMA, 0)
            == summary["blocked_count"]
        ),
        "runtime_mutation_authority_false": (
            digest_before == digest_after
            and summary["transport_applied"] is False
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
        "topology_cell_ids": cell_ids,
        "topology_digest_before": digest_before,
        "topology_digest_after": digest_after,
        "ring_delivery_summary": summary,
        "block_category_classes": {
            RING_BLOCK_NOT_NEIGHBOR: RING_BLOCK_CLASS_TOPOLOGY,
            RING_BLOCK_NOT_PARENT: RING_BLOCK_CLASS_TOPOLOGY,
            RING_BLOCK_NOT_CHILD: RING_BLOCK_CLASS_TOPOLOGY,
            RING_BLOCK_SCHEMA_INVALID: RING_BLOCK_CLASS_SCHEMA,
        },
    }
    proof_path = out_dir / "hex_parent_child_ring_invariant_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _topology() -> dict[str, Any]:
    # root -> {a, b}; a -> {a1, a2}; a<->b neighbors; a1<->a2 neighbors.
    return {
        "cells": {
            "root": {
                "cell_id": "root", "parent_cell_id": None,
                "child_cell_ids": ["a", "b"], "neighbor_cell_ids": [],
            },
            "a": {
                "cell_id": "a", "parent_cell_id": "root",
                "child_cell_ids": ["a1", "a2"], "neighbor_cell_ids": ["b"],
            },
            "b": {
                "cell_id": "b", "parent_cell_id": "root",
                "child_cell_ids": [], "neighbor_cell_ids": ["a"],
            },
            "a1": {
                "cell_id": "a1", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a2"],
            },
            "a2": {
                "cell_id": "a2", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a1"],
            },
        },
    }


def _msg(from_cell_id: str, to_cell_id: str, kind: str) -> CellMessage:
    # make_message stamps schema_version=1 and no_runtime_mutation=True (the
    # message contract itself refuses a runtime-mutating message).
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
