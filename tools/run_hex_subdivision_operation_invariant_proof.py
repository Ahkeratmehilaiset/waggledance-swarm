#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline proof for the hex subdivision-operation invariants.

Lead-dispatched fable lane (48h hex-mesh autonomy roadmap). Exercises the
subdivision operator -- ``plan_subdivision`` (a deterministic shadow plan) and
``apply_plan_to_topology`` (an OFFLINE candidate-topology builder) -- and asserts
the structural invariants of a subdivision step:

* the plan id is deterministic (``compute_plan_id`` re-derives it; identical
  inputs yield the identical plan) and the plan carries ``no_runtime_mutation``;
* malformed plans are rejected fail-closed (fewer than two children, the parent
  appearing among its own children, or duplicate children all raise);
* applying the plan registers the new children UNDER the parent, each as a
  ``shadow_only`` leaf whose parent is the subdivided cell;
* the resulting candidate topology preserves the parent-child invariants
  (bidirectional consistency + acyclic) over every cell;
* RUNTIME MUTATION AUTHORITY FALSE: the SOURCE topology is byte-identical before
  and after the apply (the candidate is a new dict; the live topology is never
  mutated), and applying the same plan twice yields an identical candidate.

Observability-only: it plans and builds candidate topologies; it performs no
live topology mutation, routing influence, or transport.
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

from waggledance.core.hex_topology.parent_child_relations import (  # noqa: E402
    ancestors_of,
    children_of,
    parent_of,
)
from waggledance.core.hex_topology.subdivision_operator import (  # noqa: E402
    apply_plan_to_topology,
    compute_plan_id,
    plan_subdivision,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.hex_subdivision_operation_invariant_proof.v0"

_PARENT = "thermal"
_NEW_CHILDREN = ("thermal.cooling", "thermal.heating")


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
        report = build_hex_subdivision_operation_invariant_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex subdivision operation invariant proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex subdivision operation invariant proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex subdivision operation invariant proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_subdivision_operation_invariant_proof(
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
        new_child_cell_ids=_NEW_CHILDREN,
        rationale="proof: offline subdivision-operation invariants",
    )
    plan_again = plan_subdivision(
        parent_cell_id=_PARENT,
        new_child_cell_ids=_NEW_CHILDREN,
        rationale="different rationale, same structural inputs",
    )

    candidate = apply_plan_to_topology(source, plan)
    candidate_again = apply_plan_to_topology(source, plan)
    source_digest_after = sha256_digest(source)

    candidate_cells = candidate.get("cells") or {}
    child_ids = sorted(plan.new_child_cell_ids)

    plan_validates_fail_closed = (
        _raises(lambda: plan_subdivision(
            parent_cell_id=_PARENT, new_child_cell_ids=("only_one",)))
        and _raises(lambda: plan_subdivision(
            parent_cell_id="a", new_child_cell_ids=("a", "b")))
        and _raises(lambda: plan_subdivision(
            parent_cell_id=_PARENT, new_child_cell_ids=("dup", "dup")))
    )

    proof_checks = {
        "plan_id_deterministic": (
            plan.plan_id == compute_plan_id(
                parent_cell_id=_PARENT, new_child_cell_ids=_NEW_CHILDREN)
            and plan.plan_id == plan_again.plan_id
            and plan.new_child_cell_ids == tuple(child_ids)
        ),
        "plan_validates_fail_closed": plan_validates_fail_closed,
        "plan_no_runtime_mutation_flag": plan.no_runtime_mutation is True,
        "apply_registers_children_under_parent": (
            all(c in children_of(candidate, _PARENT) for c in child_ids)
            and all(parent_of(candidate, c) == _PARENT for c in child_ids)
        ),
        "apply_children_are_shadow_leaves": all(
            candidate_cells.get(c, {}).get("live_state") == "shadow_only"
            and candidate_cells.get(c, {}).get("subdivision_state") == "leaf"
            for c in child_ids
        ),
        "candidate_preserves_hierarchy_invariants": (
            _bidirectional(candidate) and _acyclic(candidate)
        ),
        "source_topology_unchanged": (
            source_digest_before == source_digest_after
        ),
        "apply_deterministic": (
            sha256_digest(candidate) == sha256_digest(candidate_again)
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
        "source_topology_digest_before": source_digest_before,
        "source_topology_digest_after": source_digest_after,
        "candidate_cell_ids": sorted(candidate_cells.keys()),
        "candidate_digest": sha256_digest(candidate),
    }
    proof_path = out_dir / "hex_subdivision_operation_invariant_proof.json"
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


def _raises(thunk) -> bool:
    try:
        thunk()
        return False
    except ValueError:
        return True


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
