# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""wd_bootstrap_solvers — Phase 9 §U1 CLI driver.

Drives bulk solver synthesis via the U1 declarative path. Reads a
collection of structured tables → produces SolverSpec records →
compiles into deterministic artifacts. NEVER writes to runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.solver_synthesis import (  # noqa: E402
    bulk_rule_extractor as bre,
    declarative_solver_spec as ds,
    deterministic_solver_compiler as dsc,
    solver_family_registry as sfr,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, default=None,
                    help="JSON file containing list of structured tables")
    ap.add_argument("--cell-id", type=str, default="general")
    ap.add_argument("--output-dir", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9" / "bootstrap")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    registry = sfr.SolverFamilyRegistry().register_defaults()
    tables: list[dict] = []
    if args.tables and args.tables.exists():
        try:
            data = json.loads(args.tables.read_text(encoding="utf-8"))
            if isinstance(data, list):
                tables = data
            elif isinstance(data, dict) and "tables" in data:
                tables = list(data["tables"])
        except json.JSONDecodeError:
            tables = []

    matches: list = []
    specs: list = []
    artifacts: list = []
    routings: dict[str, int] = {}

    for idx, t in enumerate(tables):
        candidates = bre.extract_from_table(t)
        for c in candidates:
            route = bre.match_route(c)
            routings[route] = routings.get(route, 0) + 1
            matches.append({"table_idx": idx,
                            "match": c.to_dict(),
                            "route": route})
            if route == "U3_residual":
                continue
            try:
                spec = ds.make_spec(
                    family_kind=c.family_kind,
                    solver_name=f"u1_{c.family_kind}_{idx:03d}",
                    cell_id=args.cell_id,
                    spec=c.extracted_spec,
                    source=f"table_{idx}",
                    source_kind="table",
                    registry=registry,
                )
            except ds.SpecValidationError:
                continue
            specs.append(spec.to_dict())
            try:
                artifact = dsc.compile_spec(spec)
                artifacts.append(artifact.to_dict())
            except (ValueError, KeyError):
                continue

    summary = {
        "tables_seen": len(tables),
        "matches_total": len(matches),
        "specs_compiled": len(specs),
        "artifacts_emitted": len(artifacts),
        "routing_counts": routings,
        "registered_families": registry.list_kinds(),
        "dry_run": not args.apply,
    }

    if args.apply:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "matches.json").write_text(
            json.dumps(matches, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (args.output_dir / "specs.json").write_text(
            json.dumps(specs, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (args.output_dir / "artifacts.json").write_text(
            json.dumps(artifacts, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        summary["output_dir"] = args.output_dir.as_posix()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== wd_bootstrap_solvers ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to write specs + artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
