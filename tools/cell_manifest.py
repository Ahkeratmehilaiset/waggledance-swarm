#!/usr/bin/env python3
"""Cell manifest generator — per-cell state cards for the Claude-teacher pipeline.

Context budget: each manifest is ONE cell only — hard limit so Claude sees
~20-50 solvers max, never the whole library. This is the structural defense
against teacher confusion when scaling to 1000s of solvers.

Reads:
  - configs/axioms/<domain>/*.yaml      (registered symbolic solvers)
  - configs/capsules/<domain>.yaml      (key_decisions → routing keywords)
  - waggledance/core/hex_cell_topology.py (cell IDs, adjacency, intent map)
  - docs/runs/ui_gauntlet_400h_*/hot_results.jsonl (production failures → gaps)
  - docs/runs/ui_gauntlet_400h_*/incident_log.jsonl (classified incidents)

Emits:
  - docs/cells/<cell_id>/MANIFEST.md     (human-readable)
  - docs/cells/<cell_id>/manifest.json   (Claude-feed structured input)

Usage:
    python tools/cell_manifest.py                # generate all cells
    python tools/cell_manifest.py --cell thermal # single cell

Next step (not this tool): tools/propose_solver.py feeds these manifests
one at a time to Claude and runs the 6-stage quality gate on responses.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
AXIOMS_DIR = ROOT / "configs" / "axioms"
CAPSULES_DIR = ROOT / "configs" / "capsules"
CELLS_OUT_DIR = ROOT / "docs" / "cells"


# Map cells from hex_cell_topology.py (kept in sync manually; re-import
# would work but this file is small and readable).
CELLS = [
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
]

_ADJACENCY = {
    "general":  {"safety", "seasonal", "math", "learning"},
    "thermal":  {"energy", "seasonal", "safety"},
    "energy":   {"thermal", "safety", "math"},
    "safety":   {"thermal", "energy", "system", "general"},
    "seasonal": {"thermal", "general", "learning"},
    "math":     {"energy", "general", "system"},
    "system":   {"safety", "math", "learning"},
    "learning": {"seasonal", "general", "system"},
}

# Rough cell classification — same shape as _DOMAIN_KEYWORDS in hex_cell_topology.
# Used only for heuristic "which cell does this decision/axiom belong to"
# labelling. When hex_topology assigns cells from embeddings instead of
# keywords, this falls back to the existing map.
_CELL_KEYWORDS = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump", "frost",
                 "temperature", "lämpö", "pakkanen", "freezing"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "turvallisuus", "varroa", "mite"],
    "seasonal": ["season", "month", "winter", "summer", "spring", "autumn",
                 "vuodenaika", "kevät", "kesä", "talvi", "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "swarm", "colony",
                 "optimize"],
    "system":   ["system", "status", "health", "uptime", "process", "mtbf",
                 "oee", "diagnose"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],  # fallback
}


def _load_axiom(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_capsule(domain: str) -> dict:
    p = CAPSULES_DIR / f"{domain}.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _classify_axiom_to_cell(axiom: dict, domain_capsule: dict) -> str:
    """Assign an axiom to a hex cell using keyword overlap with cell vocab."""
    mid = axiom.get("model_id", "")
    name = axiom.get("model_name", "")
    desc = axiom.get("description", "")

    # Combine axiom identifiers + any capsule key_decision keywords that
    # reference this model
    text = " ".join([mid, name, desc]).lower()
    for kd in domain_capsule.get("key_decisions", []):
        if kd.get("model") == mid:
            text += " " + " ".join(kd.get("keywords", [])).lower()

    scores = {}
    for cell, keywords in _CELL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score:
            scores[cell] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def _collect_all_solvers() -> list[dict]:
    """Return list of {cell, domain, model_id, ...} for every axiom."""
    rows = []
    for domain_dir in sorted(AXIOMS_DIR.iterdir()):
        if not domain_dir.is_dir():
            continue
        domain = domain_dir.name
        capsule = _load_capsule(domain)
        for ax_path in sorted(domain_dir.glob("*.yaml")):
            axiom = _load_axiom(ax_path)
            if not axiom.get("model_id"):
                continue
            cell = _classify_axiom_to_cell(axiom, capsule)
            formulas = axiom.get("formulas", [])
            variables = axiom.get("variables", {})
            # Extract I/O signature
            inputs = list(variables.keys())
            outputs = [f.get("name") for f in formulas if f.get("output_unit")]
            rows.append({
                "model_id": axiom["model_id"],
                "model_name": axiom.get("model_name", ""),
                "description": axiom.get("description", "")[:120],
                "cell": cell,
                "domain": domain,
                "axiom_file": str(ax_path.relative_to(ROOT)),
                "inputs": inputs,
                "outputs": outputs,
                "n_formulas": len(formulas),
            })
    return rows


def _find_campaign_dir() -> Path | None:
    candidates = sorted(
        p for p in (ROOT / "docs" / "runs").glob("ui_gauntlet_400h_*")
        if p.is_dir()
    )
    return candidates[-1] if candidates else None


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _gap_signals_from_production(cell: str, campaign_dir: Path) -> dict:
    """Scan campaign production data for queries that likely belong to this
    cell but failed to get a deterministic answer (responded=False or
    high-latency fallback). These are the 'open_gaps' for the teacher.
    """
    hot = _load_jsonl(campaign_dir / "hot_results.jsonl")
    keywords = set(_CELL_KEYWORDS.get(cell, []))
    if not keywords:
        return {"unresolved_examples": [], "unresolved_count": 0}

    unresolved = []
    for r in hot:
        # Non-responded queries are gaps
        if r.get("responded"):
            continue
        q = (r.get("query", "") or "").lower()
        # Heuristic match
        if any(kw in q for kw in keywords):
            unresolved.append({
                "query_id": r.get("query_id"),
                "ts": r.get("ts", "")[:19],
                "bucket": r.get("bucket"),
                "error": r.get("error", ""),
                "latency_ms": r.get("latency_ms", 0),
            })

    return {
        "unresolved_examples": unresolved[:10],
        "unresolved_count": len(unresolved),
    }


def _compute_gap_score(
    solvers_in_cell: list[dict],
    unresolved_count: int,
    total_queries_in_campaign: int,
) -> float:
    """Gap score 0..1. Higher = more teaching needed.

    Composed of:
      - low solver count relative to total library (underpopulated)
      - high unresolved-query rate against this cell's vocabulary
    """
    library_size = max(1, len(_collect_all_solvers()))
    cell_share = len(solvers_in_cell) / library_size
    cell_underpopulation = max(0, 0.125 - cell_share)  # expected 1/8 in each
    unresolved_rate = (unresolved_count / max(1, total_queries_in_campaign)) if total_queries_in_campaign else 0
    return round(min(1.0, cell_underpopulation * 5 + unresolved_rate * 3), 3)


def generate_cell_manifest(cell: str, campaign_dir: Path | None = None) -> tuple[Path, Path]:
    all_solvers = _collect_all_solvers()
    cell_solvers = [s for s in all_solvers if s["cell"] == cell]
    siblings = _ADJACENCY.get(cell, set())

    gap_info = {"unresolved_examples": [], "unresolved_count": 0}
    total_q = 0
    if campaign_dir:
        gap_info = _gap_signals_from_production(cell, campaign_dir)
        hot = _load_jsonl(campaign_dir / "hot_results.jsonl")
        total_q = len(hot)

    gap_score = _compute_gap_score(cell_solvers, gap_info["unresolved_count"], total_q)

    manifest = {
        "cell": cell,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "solver_count": len(cell_solvers),
        "gap_score": gap_score,
        "siblings": sorted(siblings),
        "existing_solvers": cell_solvers,
        "open_gaps": gap_info,
        "training_pairs_from_production": total_q,
        "teacher_protocol_reminder": (
            "This is the ONLY context you see. Propose 1-3 new solvers OR "
            "1 improvement. Return YAML matching the schema of axiom files "
            "in configs/axioms/<domain>/*.yaml. Tests REQUIRED. The quality "
            "gate (tools/propose_solver.py) will verify determinism, "
            "contradictions with existing solvers, and insight score before "
            "merging. Duplicates rejected by hash."
        ),
    }

    # Output paths
    out_dir = CELLS_OUT_DIR / cell
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "manifest.json"
    json_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    # Markdown view (human-readable)
    lines = [
        f"# Cell manifest — `{cell}`",
        "",
        f"**Generated:** {manifest['generated_at']}",
        f"**Solver count:** {manifest['solver_count']}",
        f"**Gap score:** {gap_score:.3f}  *(0 = cell saturated, 1 = cell mostly empty or failing)*",
        f"**Siblings (ring-1):** {', '.join(sorted(siblings))}",
        "",
        "## Existing solvers",
        "",
    ]
    if cell_solvers:
        lines.extend([
            "| model_id | domain | inputs | outputs | formulas |",
            "|---|---|---|---|---|",
        ])
        for s in cell_solvers:
            inputs_s = ", ".join(s["inputs"][:4]) + ("…" if len(s["inputs"]) > 4 else "")
            outputs_s = ", ".join(s["outputs"][:3]) + ("…" if len(s["outputs"]) > 3 else "")
            lines.append(
                f"| `{s['model_id']}` | {s['domain']} | {inputs_s} | {outputs_s} | {s['n_formulas']} |"
            )
    else:
        lines.append("*(none — cell is empty; high-priority for teaching)*")
    lines.append("")

    lines.extend([
        "## Open gaps from production",
        "",
        f"- Unresolved queries matching this cell's vocabulary: **{gap_info['unresolved_count']}**",
        f"- Total HOT queries in campaign: {total_q}",
        "",
    ])
    if gap_info["unresolved_examples"]:
        lines.append("### Examples (top 10)")
        lines.append("")
        for ex in gap_info["unresolved_examples"]:
            lines.append(f"- `{ex['query_id']}` ({ex['bucket']}, {ex['latency_ms']}ms) at {ex['ts']}")
        lines.append("")

    lines.extend([
        "## Teaching protocol reminder",
        "",
        manifest["teacher_protocol_reminder"],
        "",
        "## Schema reference",
        "",
        "New solvers must be YAML matching this shape (see",
        "`configs/axioms/cottage/honey_yield.yaml` for a complete example):",
        "",
        "```yaml",
        "model_id: <unique_snake_case>",
        "model_name: \"<Human Readable Name>\"",
        "description: \"<one sentence>\"",
        "formulas:",
        "  - name: <formula_name>",
        "    formula: \"<python-expression>\"",
        "    description: \"<what it computes>\"",
        "    output_unit: \"<unit>\"",
        "variables:",
        "  <var_name>:",
        "    description: \"<description>\"",
        "    unit: \"<unit>\"",
        "    range: [<min>, <max>]",
        "    default: <default>",
        "```",
        "",
    ])

    md_path = out_dir / "MANIFEST.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path, md_path


def generate_index(campaign_dir: Path | None) -> Path:
    """Top-level index across all cells. This is what leadership/pm sees.
    Claude NEVER sees this — only one cell at a time. This is for humans.
    """
    all_solvers = _collect_all_solvers()
    cell_stats = defaultdict(lambda: {"solvers": 0, "unresolved": 0})
    for s in all_solvers:
        cell_stats[s["cell"]]["solvers"] += 1

    total_q = 0
    if campaign_dir:
        hot = _load_jsonl(campaign_dir / "hot_results.jsonl")
        total_q = len(hot)
        for cell in CELLS:
            g = _gap_signals_from_production(cell, campaign_dir)
            cell_stats[cell]["unresolved"] = g["unresolved_count"]

    lines = [
        "# Hex-cell library index",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"**Total solvers:** {len(all_solvers)}",
        f"**Campaign queries analysed:** {total_q}",
        "",
        "## Per-cell status",
        "",
        "| cell | solvers | gap score | unresolved prod queries | siblings |",
        "|---|---|---|---|---|",
    ]
    for cell in CELLS:
        gs = _compute_gap_score(
            [s for s in all_solvers if s["cell"] == cell],
            cell_stats[cell]["unresolved"],
            total_q,
        )
        sibs = ", ".join(sorted(_ADJACENCY.get(cell, set())))
        lines.append(
            f"| `{cell}` | {cell_stats[cell]['solvers']} | {gs:.3f} | "
            f"{cell_stats[cell]['unresolved']} | {sibs} |"
        )

    lines.extend([
        "",
        "## How to use",
        "",
        "1. **Leadership/PM:** look at gap scores — high-score cells are where "
        "Claude-teacher should spend time next.",
        "2. **Claude-teacher session (future `tools/propose_solver.py`):** feeds "
        "ONE cell's `manifest.json` at a time. Claude sees 20-50 solvers max.",
        "3. **Regenerate this index:** `python tools/cell_manifest.py` rewrites "
        "`docs/cells/<cell>/{MANIFEST.md,manifest.json}` for all cells plus "
        "this index.",
        "",
        "## Scaling math",
        "",
        "- Level 0 (now): 8 cells. Target 50 solvers/cell = 400 solvers.",
        "- Level 1 (3 mo): each cell → 6 sub-cells. 48 × 50 = 2400 solvers.",
        "- Level 2 (12 mo): 288 cells × 30 = 8640 solvers.",
        "- Level 3 (24 mo): 1728 cells × 20 = ~34k solvers.",
        "",
        "Routing path length = log₆(n) ≈ 6 hops to 36k, per-hop ~3-5 ms embedding "
        "match → ~20-30 ms end-to-end for 34k-solver library.",
        "",
    ])

    idx_path = CELLS_OUT_DIR / "INDEX.md"
    CELLS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx_path.write_text("\n".join(lines), encoding="utf-8")
    return idx_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", help="Generate one cell only (default: all)")
    ap.add_argument("--no-production-scan", action="store_true",
                    help="Skip gap-signal scan of campaign jsonl")
    args = ap.parse_args()

    campaign_dir = None if args.no_production_scan else _find_campaign_dir()
    if campaign_dir:
        print(f"scanning production data from {campaign_dir.name}")

    if args.cell:
        if args.cell not in CELLS:
            print(f"unknown cell '{args.cell}'. Valid: {CELLS}", file=sys.stderr)
            return 1
        j, m = generate_cell_manifest(args.cell, campaign_dir)
        print(f"generated {m}")
    else:
        for cell in CELLS:
            j, m = generate_cell_manifest(cell, campaign_dir)
            print(f"  {cell:9} -> {m.relative_to(ROOT)}")
        idx = generate_index(campaign_dir)
        print(f"index: {idx.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
