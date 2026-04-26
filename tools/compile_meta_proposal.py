# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""compile_meta_proposal — Phase 9 §O CLI driver."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.proposal_compiler import (  # noqa: E402
    pr_draft_compiler as prc,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta-proposal-path", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.meta_proposal_path.exists():
        print(f"meta-proposal not found: {args.meta_proposal_path}",
               file=sys.stderr)
        return 2
    proposal = json.loads(
        args.meta_proposal_path.read_text(encoding="utf-8")
    )
    bundle = prc.compile_bundle(proposal)
    summary = {
        "bundle_id": bundle.bundle_id,
        "source_meta_proposal_id": bundle.source_meta_proposal_id,
        "affected_files_count": len(bundle.affected_files),
        "no_main_branch_auto_merge": bundle.no_main_branch_auto_merge,
        "no_runtime_mutation": bundle.no_runtime_mutation,
        "dry_run": not args.apply,
    }
    if args.apply:
        out = args.output_dir or (
            ROOT / "docs" / "runs" / "phase9" / "compiled_proposals"
                / bundle.bundle_id
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / "bundle.json").write_text(
            json.dumps(bundle.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (out / "pr_draft.md").write_text(bundle.pr_draft_md,
                                             encoding="utf-8")
        (out / "patch_skeleton.diff").write_text(bundle.patch_skeleton,
                                                     encoding="utf-8")
        (out / "review_checklist.md").write_text(
            bundle.review_checklist, encoding="utf-8",
        )
        summary["bundle_dir"] = out.as_posix()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== compile_meta_proposal ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to write bundle files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
