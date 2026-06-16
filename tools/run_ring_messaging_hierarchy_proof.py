# SPDX-License-Identifier: BUSL-1.1
"""Deterministic offline proof of ring messaging + hierarchy boundaries.

Proves the WD Image #1 *ring messaging / hierarchy* pillar: over a fixed
deterministic cell topology, the pure hierarchy relations are self-consistent
and the ring delivery layer enforces its boundaries — a ring message only
delivers to a real neighbor, a child_to_parent message only to the real parent,
a parent_to_child message only to a real child, and a structurally invalid
message is blocked. No message crosses an invalid boundary.

Pure and offline: `ring_messaging.deliver_one/deliver_batch` perform no network
or runtime mutation (`CellMessage.no_runtime_mutation` is True). Every expected
neighbor/parent/non-neighbor is DERIVED from the topology functions, so the
proof asserts the boundary property rather than a hardcoded answer.

Exact validation commands::

    python tools/run_ring_messaging_hierarchy_proof.py --json
    python -m pytest tests/test_ring_messaging_hierarchy_proof.py -q

Engineering record; offline; forbidden-vocabulary guarded. No claim of
superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.cell_message_contract import make_message  # noqa: E402
from waggledance.core.hex_topology.parent_child_relations import (  # noqa: E402
    ancestors_of,
    children_of,
    descendants_of,
    neighbors_of,
    parent_of,
)
from waggledance.core.hex_topology.ring_messaging import (  # noqa: E402
    RING_BLOCK_NOT_CHILD,
    RING_BLOCK_NOT_NEIGHBOR,
    RING_BLOCK_NOT_PARENT,
    RING_BLOCK_SCHEMA_INVALID,
    deliver_batch,
    deliver_one,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.ring_messaging_hierarchy_proof.v1"
CAPABILITY_ID = "ring_messaging_hierarchy"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(parent: str | None, children: list[str], neighbors: list[str]) -> dict[str, Any]:
    return {
        "parent_cell_id": parent,
        "child_cell_ids": list(children),
        "neighbor_cell_ids": list(neighbors),
    }


def build_topology() -> dict[str, Any]:
    """A fixed, deterministic two-level cell tree with explicit neighbor rings."""
    return {
        "cells": {
            "root": _cell(None, ["a", "b"], []),
            "a": _cell("root", ["a1", "a2"], ["b"]),
            "b": _cell("root", [], ["a"]),
            "a1": _cell("a", [], ["a2"]),
            "a2": _cell("a", [], ["a1"]),
        }
    }


def _hierarchy_consistency(topology: dict[str, Any]) -> dict[str, Any]:
    cells = list(topology["cells"].keys())
    checks: dict[str, bool] = {}
    # Children point back to their parent.
    checks["children_point_to_parent"] = all(
        parent_of(topology, child) == cell
        for cell in cells
        for child in children_of(topology, cell)
    )
    # Neighbor links are mutual.
    checks["neighbors_mutual"] = all(
        cell in neighbors_of(topology, n)
        for cell in cells
        for n in neighbors_of(topology, cell)
    )
    # ancestors_of terminates at the root and matches the parent walk.
    checks["ancestors_of_a1_is_a_root"] = ancestors_of(topology, "a1") == ["a", "root"]
    # descendants_of(root) covers every non-root cell.
    checks["descendants_of_root_complete"] = descendants_of(topology, "root") == sorted(
        c for c in cells if c != "root"
    )
    # No cell is its own ancestor (acyclic).
    checks["acyclic"] = all(cell not in ancestors_of(topology, cell) for cell in cells)
    return {"ok": all(checks.values()), "checks": checks}


def _ring_boundary(topology: dict[str, Any]) -> dict[str, Any]:
    cells = list(topology["cells"].keys())
    src = "a1"
    real_neighbor = neighbors_of(topology, src)[0]
    non_neighbor = next(
        c for c in cells
        if c != src and c not in neighbors_of(topology, src)
    )
    real_parent = parent_of(topology, src)
    non_parent = next(c for c in cells if c != src and c != real_parent)
    parent_cell = "a"
    real_child = children_of(topology, parent_cell)[0]
    non_child = next(
        c for c in cells
        if c != parent_cell and c not in children_of(topology, parent_cell)
    )

    cases = [
        ("ring_to_neighbor_delivers",
         make_message(from_cell_id=src, to_cell_id=real_neighbor, kind="ring_request"),
         True, None),
        ("ring_to_non_neighbor_blocked",
         make_message(from_cell_id=src, to_cell_id=non_neighbor, kind="ring_request"),
         False, RING_BLOCK_NOT_NEIGHBOR),
        ("child_to_real_parent_delivers",
         make_message(from_cell_id=src, to_cell_id=real_parent, kind="child_to_parent"),
         True, None),
        ("child_to_non_parent_blocked",
         make_message(from_cell_id=src, to_cell_id=non_parent, kind="child_to_parent"),
         False, RING_BLOCK_NOT_PARENT),
        ("parent_to_real_child_delivers",
         make_message(from_cell_id=parent_cell, to_cell_id=real_child, kind="parent_to_child"),
         True, None),
        ("parent_to_non_child_blocked",
         make_message(from_cell_id=parent_cell, to_cell_id=non_child, kind="parent_to_child"),
         False, RING_BLOCK_NOT_CHILD),
        ("unknown_cell_schema_invalid",
         make_message(from_cell_id="ghost", to_cell_id=src, kind="ring_request"),
         False, RING_BLOCK_SCHEMA_INVALID),
    ]

    results: list[dict[str, Any]] = []
    all_ok = True
    no_runtime_mutation = True
    for name, msg, want_delivered, want_category in cases:
        rd = deliver_one(topology, msg, seq=len(results) + 1)
        ok = rd.delivered is want_delivered and (
            want_category is None or rd.blocked_category == want_category
        )
        all_ok = all_ok and ok
        no_runtime_mutation = no_runtime_mutation and msg.no_runtime_mutation is True
        results.append({
            "case": name,
            "delivered": rd.delivered,
            "blocked_category": rd.blocked_category,
            "expected_delivered": want_delivered,
            "expected_category": want_category,
            "ok": ok,
        })
    return {"ok": all_ok, "no_runtime_mutation": no_runtime_mutation, "cases": results}


def _determinism(topology: dict[str, Any]) -> dict[str, Any]:
    msgs = [
        make_message(from_cell_id="a1", to_cell_id="a2", kind="ring_request"),
        make_message(from_cell_id="a1", to_cell_id="a", kind="child_to_parent"),
        make_message(from_cell_id="a", to_cell_id="a1", kind="parent_to_child"),
        make_message(from_cell_id="a1", to_cell_id="b", kind="ring_request"),
    ]
    run1 = [d.to_dict() for d in deliver_batch(topology, msgs)]
    run2 = [d.to_dict() for d in deliver_batch(topology, msgs)]
    identical = json.dumps(run1, sort_keys=True) == json.dumps(run2, sort_keys=True)
    return {"runs": 2, "batch_identical": identical, "delivered_count": sum(
        1 for d in run1 if d.get("delivered")
    )}


def build_ring_messaging_hierarchy_proof() -> dict[str, Any]:
    topology = build_topology()
    hierarchy = _hierarchy_consistency(topology)
    ring = _ring_boundary(topology)
    determinism = _determinism(topology)

    blockers: list[str] = []
    if not hierarchy["ok"]:
        blockers.append("hierarchy_inconsistent")
    if not ring["ok"]:
        blockers.append("ring_boundary_violation")
    if not ring["no_runtime_mutation"]:
        blockers.append("runtime_mutation_flag_open")
    if not determinism["batch_identical"]:
        blockers.append("non_deterministic_delivery")

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": not blockers,
        "blockers": blockers,
        "capability_id": CAPABILITY_ID,
        "hierarchy": hierarchy,
        "ring_boundary": ring,
        "deterministic_replay": determinism,
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": determinism["batch_identical"],
            "no_runtime_mutation": ring["no_runtime_mutation"],
            "no_invalid_boundary_delivery": ring["ok"],
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    h = report["hierarchy"]
    r = report["ring_boundary"]
    d = report["deterministic_replay"]
    return "\n".join([
        "Ring messaging + hierarchy proof",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  hierarchy_ok={h['ok']} ({sum(1 for v in h['checks'].values() if v)}/{len(h['checks'])} checks)",
        f"  ring_boundary_ok={r['ok']} ({sum(1 for c in r['cases'] if c['ok'])}/{len(r['cases'])} cases) "
        f"no_runtime_mutation={r['no_runtime_mutation']}",
        f"  deterministic_replay: runs={d['runs']} batch_identical={d['batch_identical']} delivered={d['delivered_count']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    # Word-boundary match so acronyms like "AGI" do not false-positive inside
    # legitimate words (e.g. "messaging").
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Optional new directory for the JSON proof artifact; must not already exist.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_ring_messaging_hierarchy_proof()

    summary = render_summary(report)
    assert_vocabulary_clean(summary)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary)

    if args.out_dir is not None:
        out_dir = args.out_dir.resolve()
        if out_dir.exists():
            print(f"out_dir must not exist: {out_dir}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True)
        (out_dir / "ring_messaging_hierarchy_proof.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
