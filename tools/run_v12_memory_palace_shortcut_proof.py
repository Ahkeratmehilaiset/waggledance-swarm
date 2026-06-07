# SPDX-License-Identifier: BUSL-1.1
"""Build a local V12 Memory Palace shortcut proof row.

The proof is intentionally read-only. It uses the existing Memory Palace
projection helpers to show how a memory placed in one palace room can rank a
related distant room as a read-side shortcut candidate. It does not mutate
memory, navigate runtime routing, call solvers, enqueue scheduler work, append
bridge events, or grant promotion authority.
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

from waggledance.core.memory_palace import (  # noqa: E402
    MemoryPlacement,
    PalaceNode,
    build_memory_palace_projection,
    derive_shortcut_hints,
    rank_shortcut_candidates_for_memory,
)


REPORT_VERSION = "wd.v12.memory_palace_shortcut_proof.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PROJECTION"
MEMORY_ID = "memory.learning.cell_imaging.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a read-only V12 Memory Palace shortcut proof from a "
            "deterministic local projection fixture."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_memory_palace_shortcut_proof(
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"Memory Palace shortcut proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_memory_palace_shortcut_proof(
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )
    nodes = _fixture_nodes()
    shortcuts = derive_shortcut_hints(
        nodes,
        min_shared_selector_keys=1,
        min_hierarchy_hops=3,
        max_hints_per_source=3,
    )
    projection = build_memory_palace_projection(
        nodes,
        placements=[
            MemoryPlacement(
                memory_id=MEMORY_ID,
                palace_node_id="room.learning.cell_imaging",
                confidence=0.8,
                placement_source="manual",
                vector_node_id="vector.memory.learning.cell_imaging.1",
                dedup_anchor="sha256:memory_learning_cell_imaging_1",
            )
        ],
        shortcuts=shortcuts,
    )
    ranked_candidates = rank_shortcut_candidates_for_memory(
        projection,
        MEMORY_ID,
        max_candidates=3,
    )
    authority_boundary = _authority_boundary(projection, ranked_candidates)
    top_candidate = ranked_candidates[0] if ranked_candidates else {}
    shortcut_proven = bool(ranked_candidates) and _authority_boundary_ok(
        authority_boundary,
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": shortcut_proven,
        "claim_label": CLAIM_LABEL,
        "substrate": "memory_palace_shortcut_projection",
        "source_of_truth": projection["source_of_truth"],
        "memory_id": MEMORY_ID,
        "projection": {
            "schema_version": projection["schema_version"],
            "node_count": len(projection["nodes"]),
            "placement_count": len(projection["placements"]),
            "shortcut_hint_count": len(projection["shortcuts"]),
        },
        "ranked_shortcuts": {
            "candidate_count": len(ranked_candidates),
            "top_candidate": top_candidate,
            "candidates": list(ranked_candidates),
        },
        "shortcut_proven": shortcut_proven,
        "authority_boundary": authority_boundary,
        "no_overclaim_guardrails": {
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_promotion_authority": True,
            "not_gate_skip": True,
            "not_networked_retrieval": True,
            "not_production_memory_migration": True,
            "deterministic_local_fixture": True,
        },
        "evidence_sources": [
            "waggledance/core/memory_palace/projection.py",
            "tests/core/test_memory_palace_projection.py",
        ],
        "operator_interpretation": (
            "The result is a read-side shortcut candidate for distant related "
            "expertise. It is not a runtime route, solver dispatch, storage "
            "mutation, promotion, or gate decision."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    projection = report["projection"]
    ranked = report["ranked_shortcuts"]
    top = ranked["top_candidate"]
    lines = [
        "# V12 Memory Palace Shortcut Proof",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_of_truth: `{report['source_of_truth']}`",
        f"- memory_id: `{report['memory_id']}`",
        "",
        "## Projection",
        "",
        f"| nodes | placements | shortcut hints | ranked candidates |",
        f"| ---: | ---: | ---: | ---: |",
        (
            f"| {projection['node_count']} | {projection['placement_count']} | "
            f"{projection['shortcut_hint_count']} | {ranked['candidate_count']} |"
        ),
        "",
        "## Top Shortcut Candidate",
        "",
        f"- target_node_id: `{top.get('target_node_id', 'none')}`",
        f"- rank_score: `{top.get('rank_score', 0.0)}`",
        f"- hierarchy_hops: `{top.get('hierarchy_hops', 0)}`",
        "- matched_selector_keys: `"
        + ", ".join(top.get("matched_selector_keys", []))
        + "`",
        "",
        "## Boundary",
        "",
    ]
    for key, value in sorted(report["authority_boundary"].items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend([
        "",
        "## What This Proves",
        "",
        (
            "A deterministic Memory Palace projection can rank a read-side "
            "shortcut from a cell-imaging memory toward related distant "
            "pathology/statistics expertise without traversing every "
            "hierarchy hop at runtime."
        ),
        "",
        "## What This Does Not Prove",
        "",
        (
            "This is not router dispatch, solver execution, storage mutation, "
            "autonomous promotion, bridge append, network retrieval, or a "
            "production memory migration."
        ),
        "",
    ])
    return "\n".join(lines)


def _fixture_nodes() -> list[PalaceNode]:
    return [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.cell_imaging",
            kind="room",
            label="Cell imaging cases",
            parent_id="wing.learning",
            selectors={
                "tags": ["segmentation", "cell_imaging"],
                "vector_kind": ["claim"],
                "capsule_context": ["research"],
            },
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={
                "tags": ["segmentation", "pathology"],
                "vector_kind": ["claim"],
            },
        ),
        PalaceNode(node_id="wing.system", kind="wing", label="System"),
        PalaceNode(
            node_id="room.system.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.system",
            selectors={
                "tags": ["segmentation"],
                "vector_kind": ["claim"],
            },
        ),
    ]


def _authority_boundary(
    projection: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
) -> dict[str, bool]:
    projection_false_fields = (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
    )
    candidate_false_fields = (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
        "solver_call_authority",
    )
    return {
        "read_side_projection_only": projection.get("source_of_truth")
        == "projection_only",
        "projection_authority_flags_false": all(
            projection.get(field) is False for field in projection_false_fields
        ),
        "candidate_no_runtime_mutation": all(
            candidate.get("no_runtime_mutation") is True
            for candidate in candidates
        ),
        "candidate_authority_flags_false": all(
            all(candidate.get(field) is False for field in candidate_false_fields)
            for candidate in candidates
        ),
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
    }


def _authority_boundary_ok(boundary: dict[str, bool]) -> bool:
    required_true = (
        "read_side_projection_only",
        "projection_authority_flags_false",
        "candidate_no_runtime_mutation",
        "candidate_authority_flags_false",
    )
    required_false = (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "gate_skip_performed",
        "network_access_performed",
    )
    return (
        all(boundary.get(field) is True for field in required_true)
        and all(boundary.get(field) is False for field in required_false)
    )


def _parse_utc(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("--now must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("--now must include timezone information")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
