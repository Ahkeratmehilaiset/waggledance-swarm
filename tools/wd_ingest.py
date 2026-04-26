# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""wd_ingest — universal ingestion driver (Phase 9 §H).

Reads pinned source files / mentor packs / linked DBs and folds them
into a vector_provenance graph. Offline only; never makes network
calls.

CLI:
  python tools/wd_ingest.py --help
  python tools/wd_ingest.py --source path/to/file.md --apply
  python tools/wd_ingest.py --mentor-pack path/to/pack.json --apply
  python tools/wd_ingest.py --vectorize-from path/to.faiss --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.ingestion import universal_ingestor as ui  # noqa: E402
from waggledance.core.vector_identity import vector_provenance_graph as vpg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=None,
                    help="local file or folder")
    ap.add_argument("--mentor-pack", type=Path, default=None)
    ap.add_argument("--vectorize-from", type=Path, default=None,
                    help="FAISS DB to copy/import (placeholder)")
    ap.add_argument("--capsule-context", type=str, default="neutral_v1")
    ap.add_argument("--mode", type=str, default="copy_mode",
                    choices=("copy_mode", "link_mode", "stream_mode"))
    ap.add_argument("--graph-path", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9" / "vector_graph.json")
    ap.add_argument("--ts", type=str, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    graph = vpg.VectorProvenanceGraph()
    candidates: list = []

    if args.source and args.source.exists():
        if args.source.is_file():
            candidates.extend(ui.ingest_local_file(
                args.source, capsule_context=args.capsule_context,
                ingested_via=args.mode,
            ))
    if args.mentor_pack and args.mentor_pack.exists():
        try:
            pack = json.loads(args.mentor_pack.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pack = {"items": []}
        candidates.extend(ui.ingest_mentor_context_pack(
            pack, capsule_context=args.capsule_context,
            ingested_via=args.mode,
        ))

    result = ui.ingest_into_graph(graph, candidates)
    summary = {
        "candidates_offered": len(candidates),
        "nodes_added": result["nodes_added"],
        "nodes_deduped": result["nodes_deduped"],
        "graph_size": len(graph.nodes),
        "mode": args.mode,
        "capsule_context": args.capsule_context,
        "dry_run": not args.apply,
    }

    if args.apply:
        args.graph_path.parent.mkdir(parents=True, exist_ok=True)
        args.graph_path.write_text(
            json.dumps(graph.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        summary["graph_path"] = args.graph_path.as_posix()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== wd_ingest ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not args.apply:
            print("(use --apply to write graph)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
