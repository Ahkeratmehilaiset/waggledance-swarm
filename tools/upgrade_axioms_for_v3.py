#!/usr/bin/env python3
"""Upgrade existing axioms with v3-required fields.

Per `docs/plans/hybrid_retrieval_activation_v3_2026-04-23.md` §1.2 + §1.8:
Every axiom must declare:
  - cell_id: assigned hex cell (for source-side cell honesty)
  - solver_output_schema: typed output fields for postprocessor

This tool is idempotent — running it twice produces the same output.
Existing cell_id / solver_output_schema are preserved.

Heuristic cell_id assignment uses the same keyword-overlap logic as
`tools/cell_manifest.py`. Each axiom also gets an implicit
placement_review.status = "auto_heuristic" tag so backfill knows this
is not yet human-verified.

Run:
    python tools/upgrade_axioms_for_v3.py --dry-run      # see what would change
    python tools/upgrade_axioms_for_v3.py                 # apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AXIOMS_DIR = ROOT / "configs" / "axioms"

# Same classification as cell_manifest.py
_CELL_KEYWORDS = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump", "frost",
                 "temperature", "lämpö", "pakkanen", "freezing", "pipe"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "turvallisuus", "varroa", "mite", "swarm"],
    "seasonal": ["season", "month", "winter", "summer", "spring", "autumn",
                 "vuodenaika", "kevät", "kesä", "talvi", "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "colony",
                 "optimize"],
    "system":   ["system", "status", "health", "uptime", "process", "mtbf",
                 "oee", "diagnose", "signal", "propagation"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],
}


def classify_to_cell(axiom: dict) -> str:
    """Same heuristic as cell_manifest.py for consistency."""
    text = " ".join([
        axiom.get("model_id", ""),
        axiom.get("model_name", ""),
        axiom.get("description", ""),
    ]).lower()

    scores = {}
    for cell, kws in _CELL_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text)
        if score:
            scores[cell] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def infer_solver_output_schema(axiom: dict) -> dict:
    """Infer solver_output_schema from formulas list.

    Primary value = last formula (usually the aggregate/final answer).
    Comparable fields = all formula outputs with numeric units.
    """
    formulas = axiom.get("formulas", [])
    if not formulas:
        return {
            "primary_value": None,
            "comparable_fields": [],
            "output_mode": "trace_only",
        }

    # Primary is typically the final formula (may be overridden)
    primary = formulas[-1]
    primary_unit = primary.get("output_unit", "")

    comparable = []
    for f in formulas:
        unit = f.get("output_unit", "")
        if unit and unit not in ("", "count", "boolean"):
            comparable.append({"name": f.get("name"), "unit": unit})

    return {
        "primary_value": {
            "name": primary.get("name"),
            "type": "number",
            "unit": primary_unit,
        },
        "comparable_fields": comparable,
        "output_mode": "numeric",
    }


def upgrade_axiom_file(path: Path, dry_run: bool) -> dict:
    """Upgrade a single axiom file in place (or dry-run). Returns changes dict."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
        axiom = yaml.safe_load(raw)

    if not axiom or not axiom.get("model_id"):
        return {"path": str(path), "status": "skipped_no_model_id"}

    changes = []

    if "cell_id" not in axiom:
        cell = classify_to_cell(axiom)
        axiom["cell_id"] = cell
        changes.append(f"added cell_id={cell}")

    if "placement_review" not in axiom:
        axiom["placement_review"] = {
            "status": "auto_heuristic",
            "reviewed_by": "tools/upgrade_axioms_for_v3.py",
            "reason": "initial heuristic classification by keyword overlap",
            "reviewed_at": "2026-04-23",
        }
        changes.append("added placement_review (auto_heuristic)")

    if "solver_output_schema" not in axiom:
        schema = infer_solver_output_schema(axiom)
        axiom["solver_output_schema"] = schema
        changes.append(f"added solver_output_schema (primary={schema.get('primary_value', {}).get('name')})")

    if not changes:
        return {"path": str(path), "status": "unchanged"}

    # Write with stable key ordering
    if not dry_run:
        # Preserve original key order by writing in canonical order
        ordered = {}
        for key in ["model_id", "model_name", "description", "cell_id",
                    "affinity_cells", "placement_review",
                    "solver_output_schema", "formulas", "variables"]:
            if key in axiom:
                ordered[key] = axiom[key]
        # Append any remaining keys not listed above
        for key, val in axiom.items():
            if key not in ordered:
                ordered[key] = val

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(ordered, f, sort_keys=False, allow_unicode=True,
                      default_flow_style=False, width=120)

    return {
        "path": str(path.relative_to(ROOT)),
        "status": "upgraded" if not dry_run else "would_upgrade",
        "model_id": axiom["model_id"],
        "cell_id": axiom["cell_id"],
        "changes": changes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results = []
    for yaml_path in sorted(AXIOMS_DIR.rglob("*.yaml")):
        results.append(upgrade_axiom_file(yaml_path, args.dry_run))

    status_summary = {}
    for r in results:
        status_summary[r["status"]] = status_summary.get(r["status"], 0) + 1

    print(f"{'Dry-run:' if args.dry_run else 'Applied:'}")
    print(f"  Files processed: {len(results)}")
    for status, count in sorted(status_summary.items()):
        print(f"    {status}: {count}")

    for r in results:
        if r.get("changes"):
            print(f"\n  {r['path']}  (cell={r['cell_id']})")
            for ch in r["changes"]:
                print(f"    - {ch}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
