#!/usr/bin/env python3
"""render_hologram_reality — Phase 9 §P CLI driver.

Reads available pinned artifacts (self_model, world_model, dream
curriculum, hive review bundle, vector graph, missions) and renders
a RealitySnapshot. Missing inputs produce explicit unavailable panels;
the view NEVER fabricates values.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.ui.hologram import (  # noqa: E402
    hologram_adapter as ha,
    hologram_snapshot as hs,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-model-path", type=Path, default=None)
    ap.add_argument("--world-model-path", type=Path, default=None)
    ap.add_argument("--dream-curriculum-path", type=Path, default=None)
    ap.add_argument("--hive-review-bundle-path", type=Path, default=None)
    ap.add_argument("--vector-graph-path", type=Path, default=None)
    ap.add_argument("--missions-path", type=Path, default=None)
    ap.add_argument("--ts", type=str, default=None)
    ap.add_argument("--branch-name", type=str,
                    default="phase9/autonomy-fabric")
    ap.add_argument("--base-commit-hash", type=str, default="")
    ap.add_argument("--pinned-input-manifest-sha256", type=str,
                    default="sha256:unknown")
    ap.add_argument("--output-path", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    snapshot = ha.render_from_artifacts(
        produced_at_iso=args.ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        self_model_path=args.self_model_path,
        world_model_path=args.world_model_path,
        dream_curriculum_path=args.dream_curriculum_path,
        hive_review_bundle_path=args.hive_review_bundle_path,
        vector_graph_path=args.vector_graph_path,
        missions_path=args.missions_path,
        branch_name=args.branch_name,
        base_commit_hash=args.base_commit_hash,
        pinned_input_manifest_sha256=args.pinned_input_manifest_sha256,
    )
    summary = {
        "panels_total": len(snapshot.panels),
        "panels_available": sum(1 for p in snapshot.panels if p.available),
        "panels_unavailable":
            sum(1 for p in snapshot.panels if not p.available),
        "dry_run": not args.apply,
    }
    if args.apply:
        out = args.output_path or (
            ROOT / "docs" / "runs" / "phase9" / "reality_view.json"
        )
        hs.save_snapshot(snapshot, out)
        summary["output_path"] = out.as_posix()

    if args.json:
        out = dict(summary)
        out["snapshot"] = snapshot.to_dict()
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print("=== render_hologram_reality ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        for p in snapshot.panels:
            avail = "✓" if p.available else "—"
            print(f"  [{avail}] {p.panel_id}: {p.title}")
            if not p.available:
                print(f"      reason: {p.rationale_if_unavailable}")
        if not args.apply:
            print("(use --apply to write snapshot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
