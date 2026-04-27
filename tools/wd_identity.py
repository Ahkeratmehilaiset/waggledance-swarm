# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""wd_identity — anchor validation driver (Phase 9 §H).

Reads a vector graph + runs anchor validation on each candidate.
Per the constitution, foundational promotion ALWAYS requires
human_approval_id; this tool prepares evaluations only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.vector_identity import (  # noqa: E402
    identity_anchor as ia,
    vector_provenance_graph as vpg,
)


def _load_graph(path: Path) -> vpg.VectorProvenanceGraph:
    g = vpg.VectorProvenanceGraph()
    if not path.exists():
        return g
    data = json.loads(path.read_text(encoding="utf-8"))
    for nid, nd in (data.get("nodes") or {}).items():
        prov = nd.get("provenance") or {}
        node = vpg.VectorNode(
            schema_version=int(nd.get("schema_version") or 1),
            node_id=nid,
            content_sha256=str(nd["content_sha256"]),
            kind=str(nd["kind"]),
            anchor_status=str(nd["anchor_status"]),
            capsule_context=str(nd.get("capsule_context") or "neutral_v1"),
            source=str(prov.get("source") or ""),
            source_kind=str(prov.get("source_kind") or "local_file"),
            ingested_via=str(prov.get("ingested_via") or "copy_mode"),
            external_path=prov.get("external_path"),
            fixture_fallback_used=bool(prov.get("fixture_fallback_used") or False),
            ingested_at_tick=int(nd.get("ingested_at_tick") or 0),
            lineage=tuple(
                vpg.LineageEdge(
                    target_node_id=str(e["target_node_id"]),
                    relation=str(e["relation"]),
                    confidence=float(e.get("confidence") or 1.0),
                )
                for e in (nd.get("lineage") or [])
            ),
            tags=tuple(nd.get("tags") or ()),
        )
        g.nodes[nid] = node
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph-path", type=Path,
                    default=ROOT / "docs" / "runs" / "phase9" / "vector_graph.json")
    ap.add_argument("--validate-candidates", action="store_true",
                    help="run anchor validation on every candidate node")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    g = _load_graph(args.graph_path)
    summary: dict = {
        "graph_size": len(g.nodes),
        "graph_path": args.graph_path.as_posix(),
    }
    if args.validate_candidates:
        validations = []
        candidates = [n for n in g.nodes.values()
                       if n.anchor_status == "candidate"]
        for cand in candidates:
            siblings = [n for n in g.nodes.values()
                         if n.node_id != cand.node_id
                         and n.capsule_context == cand.capsule_context]
            v = ia.evaluate_candidate(
                candidate=cand, siblings_in_graph=siblings,
            )
            validations.append(v.to_dict())
        summary["candidate_count"] = len(candidates)
        summary["validations"] = validations

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("=== wd_identity ===")
        for k, v in summary.items():
            if k == "validations":
                print(f"{k}: {len(v)} validations")
                continue
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
