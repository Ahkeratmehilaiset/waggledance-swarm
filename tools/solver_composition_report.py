#!/usr/bin/env python3
"""Build the typed solver composition graph and emit a deterministic
report to `docs/runs/solver_composition_report.md`.

Reads the cottage and other axiom YAMLs as the source of truth for
solver I/O surfaces. Pure offline tool — does not hit port 8002 or the
running campaign.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
AXIOMS_DIR = ROOT / "configs" / "axioms"
REPORT_PATH = ROOT / "docs" / "runs" / "solver_composition_report.md"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.composition_graph import (  # noqa: E402
    build_graph,
)


def _load_axioms(axioms_dir: Path) -> list[dict]:
    solvers: list[dict] = []
    for path in sorted(axioms_dir.rglob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                axiom = yaml.safe_load(f) or {}
        except Exception:
            continue
        if not axiom.get("model_id"):
            continue
        cell = axiom.get("cell_id") or "general"

        # Inputs: axiom YAMLs use variables map. Flatten to list of dicts.
        inputs = [
            {"name": name, "unit": (spec or {}).get("unit", "") if isinstance(spec, dict) else ""}
            for name, spec in (axiom.get("variables") or {}).items()
        ]
        # Outputs: primary from solver_output_schema + per-formula outputs with output_unit
        outputs: list[dict] = []
        schema = axiom.get("solver_output_schema") or {}
        primary = schema.get("primary_value") or {}
        if primary.get("name"):
            outputs.append({"name": primary["name"], "unit": primary.get("unit", "")})
        for f in axiom.get("formulas", []) or []:
            if f.get("output_unit") and f.get("name"):
                o = {"name": f["name"], "unit": f["output_unit"]}
                if o not in outputs:
                    outputs.append(o)

        solvers.append({
            "id": axiom["model_id"],
            "cell": cell,
            "inputs": inputs,
            "outputs": outputs,
        })
    return solvers


def _rendered_report(graph: dict, scan_info: dict) -> str:
    stats = graph["stats"]
    bridges = graph["bridges"]
    gen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Solver composition report",
        "",
        f"- **Generated:** {gen_at}",
        f"- **Axiom dir:** `{scan_info['axioms_dir']}`",
        f"- **Axioms scanned:** {scan_info['scanned']}",
        f"- **Solver nodes:** {stats.node_count}",
        f"- **Valid typed edges:** {stats.valid_edges}",
        f"- **Rejected edges:** {stats.rejected_edges}",
        f"- **Paths depth 2 / 3 / 4:** {stats.paths_depth2} / {stats.paths_depth3} / {stats.paths_depth4}",
        f"- **Bridge candidates:** {stats.bridges}",
        "",
        "## Rejected-edge reasons",
        "",
    ]
    if stats.rejected_reasons:
        for reason, count in sorted(stats.rejected_reasons.items()):
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("*(none)*")

    lines.extend([
        "",
        "## Top bridge candidates",
        "",
    ])
    if bridges:
        lines.extend([
            "| score | from | to | unit | path |",
            "|---|---|---|---|---|",
        ])
        for b in bridges[:20]:
            path_str = " → ".join(b.path.nodes)
            lines.append(
                f"| {b.score} | `{b.from_cell}` | `{b.to_cell}` | "
                f"`{b.shared_unit or '—'}` | {path_str} |"
            )
    else:
        lines.append("*(no valid bridge candidates at current library size)*")

    lines.extend([
        "",
        "## Cells with bridge potential",
        "",
    ])
    if stats.cells_with_bridges:
        for cell, n in sorted(stats.cells_with_bridges.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{cell}`: {n} bridge endpoints")
    else:
        lines.append("*(none)*")

    lines.extend([
        "",
        "## Cells flagged for high entropy",
        "",
        "A cell with many distinct output units may be heterogeneous",
        "enough to warrant subdivision (Phase 7 input).",
        "",
    ])
    if stats.cells_with_high_entropy:
        for cell in stats.cells_with_high_entropy:
            lines.append(f"- `{cell}`")
    else:
        lines.append("*(none)*")

    # Advisory rescale edges — NOT runtime-valid. Surfaced only as
    # suggestions to the human reviewer.
    rescales = graph.get("rescale_edges") or []
    lines.extend([
        "",
        "## Advisory rescale-possible edges (NOT runtime-valid)",
        "",
        "These edges share a unit family (power, energy, length, …)",
        "but differ in unit (e.g. W ↔ kW, factor 0.001). They would",
        "chain if an explicit rescale step were inserted. The runtime",
        "router never follows these. They are suggestions only.",
        "",
        f"- **Total:** {stats.advisory_rescale_edges}",
    ])
    if stats.advisory_rescale_by_family:
        lines.append("- By family:")
        for fam, n in stats.advisory_rescale_by_family.items():
            lines.append(f"  - `{fam}`: {n}")
    lines.append("")
    if rescales:
        lines.extend([
            "| family | src | src_unit | dst | dst_unit | factor |",
            "|---|---|---|---|---|---|",
        ])
        for r in rescales[:20]:
            lines.append(
                f"| `{r.family}` | `{r.src}` | `{r.src_unit}` | "
                f"`{r.dst}` | `{r.dst_unit}` | {r.factor:g} |"
            )
        if len(rescales) > 20:
            lines.append(f"| *… {len(rescales) - 20} more* | | | | | |")

    lines.append("")
    return "\n".join(lines)


def run(axioms_dir: Path = AXIOMS_DIR,
        report_path: Path = REPORT_PATH,
        max_depth: int = 4) -> dict:
    solvers = _load_axioms(axioms_dir)
    graph = build_graph(solvers, max_depth=max_depth)
    report = _rendered_report(graph, {
        "axioms_dir": axioms_dir.relative_to(ROOT).as_posix() if axioms_dir.is_absolute() and str(axioms_dir).startswith(str(ROOT)) else axioms_dir.as_posix(),
        "scanned": len(solvers),
    })
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    stats = graph["stats"]
    return {
        "report_path": report_path.as_posix(),
        "summary": {
            "nodes": stats.node_count,
            "valid_edges": stats.valid_edges,
            "rejected_edges": stats.rejected_edges,
            "bridges": stats.bridges,
            "paths_depth2": stats.paths_depth2,
            "paths_depth3": stats.paths_depth3,
            "paths_depth4": stats.paths_depth4,
            "advisory_rescale_edges": stats.advisory_rescale_edges,
            "advisory_rescale_by_family": stats.advisory_rescale_by_family,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--axioms-dir", type=Path, default=AXIOMS_DIR)
    ap.add_argument("--report", type=Path, default=REPORT_PATH)
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = run(args.axioms_dir, args.report, args.max_depth)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"report written: {out['report_path']}")
        for k, v in out["summary"].items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
