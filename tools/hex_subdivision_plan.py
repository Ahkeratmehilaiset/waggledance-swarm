#!/usr/bin/env python3
"""Hex subdivision planner — reads cell manifests and the composition
report, proposes cell-subdivision candidates, and writes
`docs/runs/hex_subdivision_plan.md`.

This tool **never mutates the runtime topology**. It only writes a
candidate plan document. Human review + a separate, reviewed commit
against `waggledance/core/hex_cell_topology.py` is what actually moves
a cell's sub-structure.

Triggers (any one is enough to emit a candidate):
- solver_count_by_cell above threshold
- cell_gap_score above threshold
- llm_fallback_rate_by_cell above threshold
- intra-cell semantic entropy (distinct output units) above threshold
- too many bridge candidates anchored on the same cell
- repeated quality-gate rejections because the cell scope is too broad
  (currently derived from manifest.recent_rejections — [] today)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml  # noqa: F401
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
CELLS_DIR = ROOT / "docs" / "cells"
COMPOSITION_REPORT_PATH = ROOT / "docs" / "runs" / "solver_composition_report.md"
PLAN_PATH = ROOT / "docs" / "runs" / "hex_subdivision_plan.md"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.composition_graph import (  # noqa: E402
    build_graph,
)

# Defaults for triggers (tunable via CLI flags)
DEFAULT_SOLVER_COUNT_THRESHOLD = 30
DEFAULT_GAP_SCORE_THRESHOLD = 0.7
DEFAULT_FALLBACK_RATE_THRESHOLD = 0.5
DEFAULT_ENTROPY_THRESHOLD = 6
DEFAULT_BRIDGE_THRESHOLD = 6
DEFAULT_REJECTION_THRESHOLD = 3


@dataclass
class SubdivisionTrigger:
    """One reason a cell might want to be subdivided."""
    name: str
    observed: float | int
    threshold: float | int
    note: str = ""


@dataclass
class SubdivisionCandidate:
    parent_cell: str
    triggers: list[SubdivisionTrigger]
    proposed_sub_cells: list[str]
    expected_benefit: str
    risk: str
    rollback_plan: str
    tests_needed: list[str]
    # Deterministic ordering helper
    severity: float = 0.0


def _severity_score(triggers: list[SubdivisionTrigger]) -> float:
    """Higher = stronger reason to subdivide. Deterministic."""
    s = 0.0
    for t in triggers:
        try:
            if isinstance(t.observed, (int, float)) and t.threshold:
                s += max(0.0, float(t.observed) / float(t.threshold))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return round(s, 3)


def _load_manifest(cell: str) -> dict | None:
    p = CELLS_DIR / cell / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _default_sub_cell_names(parent: str, triggers: list[SubdivisionTrigger]) -> list[str]:
    """Best-effort naming hint for the proposed sub-cells. No actual
    topology change — these are suggestions for the human."""
    # If entropy triggered, suggest splitting by the common unit families
    # listed in the manifest; otherwise name by broad domain adjectives
    generic_suffixes = ["core", "advanced", "seasonal", "adjacent"]
    return [f"{parent}.{suffix}" for suffix in generic_suffixes[:3]]


def _gather_triggers(cell: str, manifest: dict | None,
                     bridges_per_cell: dict[str, int],
                     entropy_cells: set[str],
                     thresholds: dict) -> list[SubdivisionTrigger]:
    triggers: list[SubdivisionTrigger] = []

    if manifest:
        sc = manifest.get("solver_count", 0)
        if sc >= thresholds["solver_count"]:
            triggers.append(SubdivisionTrigger(
                name="solver_count",
                observed=sc,
                threshold=thresholds["solver_count"],
                note=f"{sc} solvers registered in this cell",
            ))

        gs = manifest.get("gap_score", 0) or 0
        if isinstance(gs, (int, float)) and gs >= thresholds["gap_score"]:
            triggers.append(SubdivisionTrigger(
                name="cell_gap_score",
                observed=float(gs),
                threshold=thresholds["gap_score"],
                note="many open production gaps matched this cell's vocabulary",
            ))

        fr = manifest.get("llm_fallback_rate")
        if isinstance(fr, (int, float)) and fr >= thresholds["fallback_rate"]:
            triggers.append(SubdivisionTrigger(
                name="llm_fallback_rate",
                observed=float(fr),
                threshold=thresholds["fallback_rate"],
                note="high share of cell queries fall through to LLM",
            ))

        rej = manifest.get("recent_rejections") or []
        if len(rej) >= thresholds["rejections"]:
            triggers.append(SubdivisionTrigger(
                name="proposal_rejections",
                observed=len(rej),
                threshold=thresholds["rejections"],
                note="repeated proposals rejected — cell scope may be too broad",
            ))

    if cell in entropy_cells:
        triggers.append(SubdivisionTrigger(
            name="output_unit_entropy",
            observed=1, threshold=1,
            note="cell hosts many distinct output units — likely heterogeneous",
        ))

    anchored = bridges_per_cell.get(cell, 0)
    if anchored >= thresholds["bridges"]:
        triggers.append(SubdivisionTrigger(
            name="bridge_anchor_count",
            observed=anchored,
            threshold=thresholds["bridges"],
            note="many valid bridge candidates anchor on this cell",
        ))
    return triggers


def _gather_library(axioms_dir: Path = ROOT / "configs" / "axioms") -> list[dict]:
    """Load axiom YAMLs in the shape composition_graph expects."""
    import yaml
    solvers: list[dict] = []
    for path in sorted(axioms_dir.rglob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                axiom = yaml.safe_load(f) or {}
        except Exception:
            continue
        if not axiom.get("model_id"):
            continue
        inputs = [
            {"name": n, "unit": (spec or {}).get("unit", "") if isinstance(spec, dict) else ""}
            for n, spec in (axiom.get("variables") or {}).items()
        ]
        outputs: list[dict] = []
        primary = (axiom.get("solver_output_schema") or {}).get("primary_value") or {}
        if primary.get("name"):
            outputs.append({"name": primary["name"], "unit": primary.get("unit", "")})
        for f in axiom.get("formulas", []) or []:
            if f.get("output_unit") and f.get("name"):
                o = {"name": f["name"], "unit": f["output_unit"]}
                if o not in outputs:
                    outputs.append(o)
        solvers.append({
            "id": axiom["model_id"],
            "cell": axiom.get("cell_id") or "general",
            "inputs": inputs,
            "outputs": outputs,
        })
    return solvers


def build_plan(
    cells: list[str],
    thresholds: dict | None = None,
) -> dict:
    thresholds = thresholds or {}
    thresholds = {
        "solver_count": thresholds.get("solver_count", DEFAULT_SOLVER_COUNT_THRESHOLD),
        "gap_score": thresholds.get("gap_score", DEFAULT_GAP_SCORE_THRESHOLD),
        "fallback_rate": thresholds.get("fallback_rate", DEFAULT_FALLBACK_RATE_THRESHOLD),
        "entropy": thresholds.get("entropy", DEFAULT_ENTROPY_THRESHOLD),
        "bridges": thresholds.get("bridges", DEFAULT_BRIDGE_THRESHOLD),
        "rejections": thresholds.get("rejections", DEFAULT_REJECTION_THRESHOLD),
    }

    solvers = _gather_library()
    graph = build_graph(solvers, entropy_threshold=thresholds["entropy"])
    entropy_cells = set(graph["stats"].cells_with_high_entropy)
    bridges_per_cell = dict(graph["stats"].cells_with_bridges)

    candidates: list[SubdivisionCandidate] = []
    for cell in cells:
        manifest = _load_manifest(cell)
        triggers = _gather_triggers(cell, manifest, bridges_per_cell,
                                     entropy_cells, thresholds)
        if not triggers:
            continue
        cand = SubdivisionCandidate(
            parent_cell=cell,
            triggers=triggers,
            proposed_sub_cells=_default_sub_cell_names(cell, triggers),
            expected_benefit=(
                "routing precision improves once queries hit a sub-cell "
                "matching their topic, and the teacher's per-manifest "
                "context shrinks."
            ),
            risk=(
                "sub-cell adjacency must be wired correctly; a bad split "
                "degrades ring-1 reachability and may starve some solvers "
                "of neighbor assistance."
            ),
            rollback_plan=(
                "revert the hex_cell_topology.py commit. Solvers in "
                "sub-cells are re-tagged back to parent cell_id in their "
                "YAML and the registry reload picks it up on next server "
                "start."
            ),
            tests_needed=[
                "adjacency and ring-2 match expected sub-cells",
                "every existing solver in the parent maps cleanly to "
                "exactly one sub-cell",
                "routing accuracy on the cell's oracle set is >= "
                "pre-subdivision baseline",
                "LLM fallback rate on the cell does not regress",
            ],
            severity=_severity_score(triggers),
        )
        candidates.append(cand)

    candidates.sort(key=lambda c: (-c.severity, c.parent_cell))
    return {
        "thresholds": thresholds,
        "candidates": candidates,
        "entropy_cells": sorted(entropy_cells),
        "bridges_per_cell": bridges_per_cell,
    }


def render_plan(plan: dict) -> str:
    gen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Hex subdivision plan",
        "",
        f"- **Generated:** {gen_at}",
        "",
        "## Thresholds in effect",
        "",
        "| trigger | threshold |",
        "|---|---|",
    ]
    for k, v in plan["thresholds"].items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend([
        "",
        "## Candidates",
        "",
    ])

    if not plan["candidates"]:
        lines.append("*(no cell met any subdivision trigger — topology is stable at current scale)*")
    else:
        for c in plan["candidates"]:
            lines.append(f"### `{c.parent_cell}` — severity {c.severity}")
            lines.append("")
            lines.append("Triggers:")
            for t in c.triggers:
                lines.append(
                    f"- `{t.name}` observed={t.observed} threshold={t.threshold}"
                    + (f" — {t.note}" if t.note else "")
                )
            lines.append("")
            lines.append(f"**Proposed sub-cells:** {', '.join('`' + s + '`' for s in c.proposed_sub_cells)}")
            lines.append("")
            lines.append(f"**Expected benefit:** {c.expected_benefit}")
            lines.append(f"**Risk:** {c.risk}")
            lines.append(f"**Rollback plan:** {c.rollback_plan}")
            lines.append("")
            lines.append("**Tests needed:**")
            for t in c.tests_needed:
                lines.append(f"- {t}")
            lines.append("")

    lines.extend([
        "",
        "## Reference data",
        "",
        "### Cells anchoring bridge candidates",
        "",
    ])
    if plan["bridges_per_cell"]:
        for cell, n in sorted(plan["bridges_per_cell"].items(),
                              key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{cell}`: {n}")
    else:
        lines.append("*(none)*")

    lines.extend([
        "",
        "### Cells flagged for high output-unit entropy",
        "",
    ])
    if plan["entropy_cells"]:
        for cell in plan["entropy_cells"]:
            lines.append(f"- `{cell}`")
    else:
        lines.append("*(none)*")

    lines.append("")
    lines.append("")
    lines.append(
        "> **Important:** This tool writes a document, not a "
        "topology change. Moving from flat cells to sub-cells requires "
        "a separate reviewed commit against "
        "`waggledance/core/hex_cell_topology.py` plus the tests listed "
        "per candidate."
    )
    lines.append("")
    return "\n".join(lines)


def run(cells: list[str] | None = None,
        plan_path: Path = PLAN_PATH,
        thresholds: dict | None = None) -> dict:
    if cells is None:
        cells = [
            "general", "thermal", "energy", "safety",
            "seasonal", "math", "system", "learning",
        ]
    plan = build_plan(cells, thresholds=thresholds)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(render_plan(plan), encoding="utf-8")
    return {
        "plan_path": plan_path.as_posix(),
        "candidates": [asdict(c) for c in plan["candidates"]],
        "thresholds": plan["thresholds"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, default=PLAN_PATH)
    ap.add_argument("--solver-count-threshold", type=int,
                    default=DEFAULT_SOLVER_COUNT_THRESHOLD)
    ap.add_argument("--gap-score-threshold", type=float,
                    default=DEFAULT_GAP_SCORE_THRESHOLD)
    ap.add_argument("--fallback-rate-threshold", type=float,
                    default=DEFAULT_FALLBACK_RATE_THRESHOLD)
    ap.add_argument("--entropy-threshold", type=int,
                    default=DEFAULT_ENTROPY_THRESHOLD)
    ap.add_argument("--bridge-threshold", type=int,
                    default=DEFAULT_BRIDGE_THRESHOLD)
    ap.add_argument("--rejection-threshold", type=int,
                    default=DEFAULT_REJECTION_THRESHOLD)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    thresholds = {
        "solver_count": args.solver_count_threshold,
        "gap_score": args.gap_score_threshold,
        "fallback_rate": args.fallback_rate_threshold,
        "entropy": args.entropy_threshold,
        "bridges": args.bridge_threshold,
        "rejections": args.rejection_threshold,
    }

    result = run(plan_path=args.plan, thresholds=thresholds)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"plan: {result['plan_path']}")
        print(f"candidates: {len(result['candidates'])}")
        for c in result["candidates"]:
            print(f"  {c['parent_cell']} (severity={c['severity']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
