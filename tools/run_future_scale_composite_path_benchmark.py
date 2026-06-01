#!/usr/bin/env python3
"""Emit a versioned local benchmark for useful composite solver paths.

The benchmark is intentionally offline-only. It reads the configured axiom
library, builds the existing typed composition graph, and emits a deterministic
JSON artifact that can later be wired into the future-scale manifest aggregator.
It does not mutate runtime routing, authority, manifests, or solver configs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is present in CI
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
AXIOMS_DIR = ROOT / "configs" / "axioms"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.composition_graph import build_graph  # noqa: E402


SCHEMA_VERSION = "future_scale.composite_path_benchmark.v1"
AXIS_ID = "useful_composite_paths"


def _safe_path_label(path: Path, root: Path = ROOT) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return "<external_axioms_dir>"


def _load_axiom_solvers(
    axioms_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    solvers: list[dict[str, Any]] = []
    scan = {
        "axioms_dir_exists": axioms_dir.exists(),
        "files_scanned": 0,
        "files_loaded": 0,
        "files_skipped": 0,
        "skip_reasons": {
            "yaml_parse_error": 0,
            "missing_model_id": 0,
        },
    }
    if not axioms_dir.exists():
        return solvers, scan
    for path in sorted(axioms_dir.rglob("*.yaml")):
        scan["files_scanned"] += 1
        try:
            with path.open(encoding="utf-8") as f:
                axiom = yaml.safe_load(f) or {}
        except Exception:
            scan["files_skipped"] += 1
            scan["skip_reasons"]["yaml_parse_error"] += 1
            continue
        if not axiom.get("model_id"):
            scan["files_skipped"] += 1
            scan["skip_reasons"]["missing_model_id"] += 1
            continue

        inputs = [
            {
                "name": name,
                "unit": (spec or {}).get("unit", "")
                if isinstance(spec, dict)
                else "",
            }
            for name, spec in (axiom.get("variables") or {}).items()
        ]
        outputs: list[dict[str, Any]] = []
        primary = (axiom.get("solver_output_schema") or {}).get("primary_value") or {}
        if primary.get("name"):
            outputs.append(
                {
                    "name": primary["name"],
                    "unit": primary.get("unit", ""),
                    "primary": True,
                }
            )
        for formula in axiom.get("formulas", []) or []:
            if formula.get("output_unit") and formula.get("name"):
                output = {"name": formula["name"], "unit": formula["output_unit"]}
                if output not in outputs:
                    outputs.append(output)

        solvers.append(
            {
                "id": axiom["model_id"],
                "cell": axiom.get("cell_id") or "general",
                "inputs": inputs,
                "outputs": outputs,
            }
        )
    scan["files_loaded"] = len(solvers)
    return solvers, scan


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite_number(field: str, value: int | float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _depth_counts(paths: list[Any]) -> dict[str, int]:
    counts = {"2": 0, "3": 0, "4": 0}
    for path in paths:
        key = str(path.depth)
        if key in counts:
            counts[key] += 1
    return counts


def _top_path_records(bridges: list[Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for bridge in bridges[: max(0, limit)]:
        records.append(
            {
                "score": _finite_number("bridge.score", bridge.score),
                "depth": int(bridge.path.depth),
                "from_cell": str(bridge.from_cell),
                "to_cell": str(bridge.to_cell),
                "shared_unit": str(bridge.shared_unit),
                "nodes": [str(node_id) for node_id in bridge.path.nodes],
            }
        )
    return records


def build_composite_path_benchmark(
    axioms_dir: Path = AXIOMS_DIR,
    *,
    max_depth: int = 4,
    min_bridge_score: float = 1.0,
    top_limit: int = 20,
) -> dict[str, Any]:
    if max_depth < 2:
        raise ValueError("max_depth must be >= 2")
    min_score = _finite_number("min_bridge_score", min_bridge_score)

    solvers, scan_summary = _load_axiom_solvers(axioms_dir)
    graph = build_graph(solvers, max_depth=max_depth)
    stats = graph["stats"]
    bridges = [
        bridge
        for bridge in graph["bridges"]
        if _finite_number("bridge.score", bridge.score) >= min_score
    ]

    useful_by_depth = _depth_counts([bridge.path for bridge in bridges])
    useful_total = sum(useful_by_depth.values())
    paths_by_depth = {
        "2": int(stats.paths_depth2),
        "3": int(stats.paths_depth3),
        "4": int(stats.paths_depth4),
    }
    solver_projection = {
        "solvers": solvers,
        "max_depth": max_depth,
        "min_bridge_score": min_score,
    }

    evidence_status = (
        "measured_local"
        if useful_total > 0
        else "blocked_no_useful_composite_paths"
    )

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "axis_id": AXIS_ID,
        "ok": True,
        "evidence_status": evidence_status,
        "measurement_scope": "local_offline_axiom_library_composition_graph",
        "benchmark_artifact_present": True,
        "measured_value_present": useful_total > 0,
        "required_runtime_evidence_present": False,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "unbounded_claims_rejected": True,
        "controls_present": False,
        "runtime_authority_changed": False,
        "runtime_authority_granted": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "source": {
            "axioms_dir": _safe_path_label(axioms_dir),
            "solver_projection_digest": _canonical_digest(solver_projection),
            "solver_projection_count": len(solvers),
            "axiom_scan_summary": scan_summary,
            "composition_graph_api": "waggledance.core.learning.composition_graph.build_graph",
        },
        "parameters": {
            "max_depth": max_depth,
            "min_bridge_score": min_score,
            "top_limit": int(top_limit),
        },
        "summary": {
            "solver_nodes": int(stats.node_count),
            "valid_edges": int(stats.valid_edges),
            "rejected_edges": int(stats.rejected_edges),
            "paths_by_depth": paths_by_depth,
            "bridge_candidates_total": int(stats.bridges),
            "useful_composite_paths_total": int(useful_total),
            "useful_composite_paths_by_depth": useful_by_depth,
            "cells_with_bridges": {
                str(cell): int(count)
                for cell, count in sorted(stats.cells_with_bridges.items())
            },
        },
        "top_useful_paths": _top_path_records(bridges, top_limit),
        "blockers": [
            "needs repeated versioned benchmark windows before trend claims",
            "needs production corpus binding before runtime scalability claims",
            "needs serialized manifest aggregation after sibling axes land",
        ],
        "safe_conclusion": (
            "This artifact measures locally useful composite solver paths from "
            "the offline axiom library. It is evidence for one future-scale "
            "axis, not proof of unlimited scalability or production authority."
        ),
    }
    json.dumps(result, sort_keys=True, allow_nan=False)
    return result


def write_benchmark(output: Path, benchmark: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(benchmark, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--axioms-dir", type=Path, default=AXIOMS_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--min-bridge-score", type=float, default=1.0)
    parser.add_argument("--top-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    benchmark = build_composite_path_benchmark(
        args.axioms_dir,
        max_depth=args.max_depth,
        min_bridge_score=args.min_bridge_score,
        top_limit=args.top_limit,
    )
    if args.output is not None:
        write_benchmark(args.output, benchmark)
    if args.json or args.output is None:
        print(json.dumps(benchmark, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
