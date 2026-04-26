#!/usr/bin/env python3
"""wd_link — manage external linked sources (Phase 9 §H link_mode)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.ingestion import (  # noqa: E402
    link_manager as lm,
    link_watcher as lw,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", type=str, default=None,
                    help="external source path to link (e.g. local FAISS DB)")
    ap.add_argument("--source-kind", type=str, default="local_file",
                    choices=("local_file", "folder", "faiss_db", "stream"))
    ap.add_argument("--capsule-context", type=str, default="neutral_v1")
    ap.add_argument("--observe", action="store_true",
                    help="observe all linked sources for changes")
    ap.add_argument("--links-path", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9" / "links.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    links = lm.load_links(args.links_path)
    summary: dict = {"links_count": len(links)}

    if args.add:
        if not Path(args.add).exists():
            print(f"source not found: {args.add}", file=sys.stderr)
            return 2
        record = lm.make_link(
            external_path=str(Path(args.add).resolve()),
            source_kind=args.source_kind,
            capsule_context=args.capsule_context,
        )
        links = [r for r in links if r.link_id != record.link_id] + [record]
        summary["added_link_id"] = record.link_id
        if args.apply:
            lm.save_links(links, args.links_path)

    if args.observe:
        observations = lw.observe_all(links)
        summary["observations"] = [o.to_dict() for o in observations]
        summary["critical_count"] = sum(
            1 for o in observations if o.state == "critical_change"
        )

    summary["links_total"] = len(links)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== wd_link ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
