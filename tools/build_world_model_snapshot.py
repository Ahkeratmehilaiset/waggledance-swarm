# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""build_world_model_snapshot — Phase 9 §I CLI driver.

Reads pinned upstream artifacts (curiosity log, dream replay reports,
mentor packs) and emits a deterministic WorldModelSnapshot.

Runtime safety: zero touch. No port 8002. No live LLM. No runtime
mutation.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.world_model import (  # noqa: E402
    causal_engine as ce,
    external_evidence_collector as eec,
    prediction_calibrator as pcal,
    prediction_engine as pe,
    world_model_snapshot as wms,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curiosity-log", type=Path, default=None)
    ap.add_argument("--replay-report", type=Path, default=None)
    ap.add_argument("--mentor-pack", type=Path, default=None)
    ap.add_argument("--ts", type=str, default=None)
    ap.add_argument("--branch-name", type=str,
                    default="phase9/autonomy-fabric")
    ap.add_argument("--base-commit-hash", type=str, default="")
    ap.add_argument("--pinned-input-manifest-sha256", type=str,
                    default="sha256:unknown")
    ap.add_argument("--output-dir", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9" / "world_model")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    facts: list = []
    if args.curiosity_log and args.curiosity_log.exists():
        rows = []
        for line in args.curiosity_log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        facts.extend(eec.from_curiosity_log(rows))
    if args.replay_report and args.replay_report.exists():
        try:
            r = json.loads(args.replay_report.read_text(encoding="utf-8"))
            facts.extend(eec.from_dream_replay_report(r))
        except json.JSONDecodeError:
            pass
    if args.mentor_pack and args.mentor_pack.exists():
        try:
            pack = json.loads(args.mentor_pack.read_text(encoding="utf-8"))
            facts.extend(eec.from_mentor_context_pack(pack))
        except json.JSONDecodeError:
            pass

    causes: list = []
    predictions: list = []
    calibration: dict = {}

    snapshot = wms.make_snapshot(
        produced_at_iso=args.ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        external_facts=facts,
        causal_relations=causes,
        predictions=predictions,
        calibration_per_dimension=calibration,
        branch_name=args.branch_name,
        base_commit_hash=args.base_commit_hash,
        pinned_input_manifest_sha256=args.pinned_input_manifest_sha256,
    )

    summary = {
        "snapshot_id": snapshot.snapshot_id,
        "facts": len(snapshot.external_facts),
        "causes": len(snapshot.causal_relations),
        "predictions": len(snapshot.predictions),
        "uncertainty_summary": snapshot.uncertainty_summary,
        "dry_run": not args.apply,
    }

    if args.apply:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out = args.output_dir / f"world_model_snapshot_{snapshot.snapshot_id}.json"
        out.write_text(wms.to_canonical_json(snapshot), encoding="utf-8")
        summary["snapshot_path"] = out.as_posix()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== build_world_model_snapshot ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to write snapshot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
