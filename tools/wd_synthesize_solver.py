# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""wd_synthesize_solver — Phase 9 §U3 CLI driver.

Routes a gap_ref to U1 or U3 and (in U3 path) emits a raw solver
candidate. NEVER promotes to live runtime; quotas + cold/shadow
throttling enforced by solver_quarantine.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.solver_synthesis import (  # noqa: E402
    gap_to_solver_spec as gts,
    solver_candidate_store as scs,
    solver_quarantine as sq,
    validators as sv,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap-ref", type=str, required=True)
    ap.add_argument("--solver-name", type=str, default="u3_candidate_x")
    ap.add_argument("--cell-id", type=str, default="general")
    ap.add_argument("--table-hint", type=Path, default=None,
                    help="Optional JSON file with a structured table hint")
    ap.add_argument("--store-path", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9"
                          / "solver_candidates.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    table_hint = None
    if args.table_hint and args.table_hint.exists():
        try:
            table_hint = json.loads(args.table_hint.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            table_hint = None

    decision = gts.route_gap(gap_ref=args.gap_ref,
                                  table_hint=table_hint)

    summary = {
        "gap_ref": args.gap_ref,
        "chosen_path": decision.chosen_path,
        "best_match": (decision.best_match.to_dict()
                        if decision.best_match else None),
        "rationale": decision.rationale,
        "dry_run": not args.apply,
    }

    if decision.chosen_path == "U3_residual" and args.apply:
        cand = scs.make_candidate(
            solver_name=args.solver_name,
            cell_id=args.cell_id,
            spec_or_code={"placeholder": "u3_free_form_pending"},
            source_gap_ref=args.gap_ref,
            produced_by="U3_synth_cli",
        )
        store = scs.SolverCandidateStore()
        if args.store_path.exists():
            try:
                data = json.loads(args.store_path.read_text(encoding="utf-8"))
                for cid, c_d in (data.get("candidates") or {}).items():
                    pass   # full deserialization left to a future commit
            except json.JSONDecodeError:
                pass
        store.add(cand)
        scs.save_store(store, args.store_path)
        report = sv.validate_candidate(cand)
        summary["candidate_id"] = cand.candidate_id
        summary["initial_state"] = cand.state
        summary["validation_verdict"] = report.verdict
        summary["store_path"] = args.store_path.as_posix()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== wd_synthesize_solver ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to persist candidate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
