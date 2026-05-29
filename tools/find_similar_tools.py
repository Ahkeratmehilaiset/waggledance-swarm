#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Find modules structurally similar to a given file (review-coverage aid).

Queries the ChromaDB collection built by `build_tool_similarity_index.py` and
returns the top-N most structurally similar modules, with a similarity score,
the def/class count, and the imports shared with the query.

INTENDED USE — after a fail-open (or any bug) is found in one verification/
review tool, run this against that file to enumerate the *sibling consumers and
look-alikes* that most likely share the same shape, so each one can be audited
with the same discipline (forge every nested field, fuzz type-confusion, run
inputs empirically). This narrows where to look; it does not decide anything.

    python tools/find_similar_tools.py --file tools/<some_magma_tool>.py
    python tools/find_similar_tools.py --file tools/<x>.py --top-k 12 --audit
    python tools/find_similar_tools.py --text "consumes verification summary ok flag" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
import build_tool_similarity_index as bidx  # noqa: E402

QUERY_PREFIX = "search_query: "


def _imports_of(text: str) -> set[str]:
    _skeleton, _n, imports = bidx.extract_skeleton(text)
    return set(imports)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find structurally similar modules from the index.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Path to a module to find siblings for.")
    src.add_argument("--text", help="Free-text query instead of a file.")
    parser.add_argument("--root", default=str(bidx.ROOT))
    parser.add_argument("--persist-dir", default=None)
    parser.add_argument("--model", default=bidx.DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=bidx.DEFAULT_OLLAMA_URL)
    parser.add_argument("--timeout", type=float, default=bidx.DEFAULT_EMBED_TIMEOUT)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--audit", action="store_true", help="Phrase output as an audit punch-list.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    persist_dir = Path(args.persist_dir).resolve() if args.persist_dir else (root / bidx.DEFAULT_PERSIST_SUBDIR)
    if not persist_dir.exists():
        print(json.dumps({"error": "index_missing", "persist_dir": str(persist_dir),
                          "hint": "Run tools/build_tool_similarity_index.py first."}))
        return 1

    self_rel = None
    query_imports: set[str] = set()
    if args.file:
        fpath = Path(args.file)
        fpath = fpath if fpath.is_absolute() else (root / fpath)
        if not fpath.is_file():
            print(json.dumps({"error": "file_not_found", "file": str(fpath)}))
            return 1
        text = fpath.read_text(encoding="utf-8", errors="replace")
        skeleton, _n, imports = bidx.extract_skeleton(text)
        query_imports = set(imports)
        try:
            self_rel = fpath.resolve().relative_to(root).as_posix()
        except ValueError:
            self_rel = None
        query_text = skeleton
    else:
        query_text = args.text

    try:
        vector = bidx.embed(
            query_text, model=args.model, base_url=args.ollama_url,
            timeout=args.timeout, prefix=QUERY_PREFIX,
        )
    except RuntimeError as exc:
        print(json.dumps({"error": "embed_failed", "detail": str(exc)}))
        return 2

    import chromadb

    client = chromadb.PersistentClient(path=str(persist_dir))
    try:
        coll = client.get_collection(name=bidx.COLLECTION_NAME, embedding_function=None)
    except Exception:
        print(json.dumps({"error": "collection_missing", "collection": bidx.COLLECTION_NAME,
                          "hint": "Run tools/build_tool_similarity_index.py first."}))
        return 1
    if coll.count() == 0:
        print(json.dumps({"error": "index_empty", "hint": "Run tools/build_tool_similarity_index.py first."}))
        return 1

    n_results = min(args.top_k + 1, coll.count())
    res = coll.query(query_embeddings=[vector], n_results=n_results,
                     include=["metadatas", "distances"])
    ids = res.get("ids", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    results = []
    for _id, meta, dist in zip(ids, metas, dists):
        if self_rel is not None and _id == self_rel:
            continue
        meta = meta or {}
        their_imports = set((meta.get("imports") or "").split(", ")) if meta.get("imports") else set()
        shared = sorted(query_imports & their_imports) if query_imports else []
        results.append({
            "path": _id,
            "similarity": round(1.0 - (float(dist) / 2.0), 4),  # WD convention
            "n_defs": meta.get("n_defs"),
            "shared_imports": shared,
        })
        if len(results) >= args.top_k:
            break

    if args.json:
        print(json.dumps({
            "query": args.file or args.text,
            "model": args.model,
            "results": results,
        }, indent=2))
        return 0

    query_label = args.file if args.file else f'"{args.text}"'
    if args.audit:
        print(f"AUDIT PUNCH-LIST -- siblings of {query_label} to review with the same discipline:")
    else:
        print(f"Top {len(results)} modules similar to {query_label}:")
    for r in results:
        shared = f"  shared_imports={r['shared_imports']}" if r["shared_imports"] else ""
        print(f"  {r['similarity']:.4f}  {r['path']}  (defs={r['n_defs']}){shared}")
    if not results:
        print("  (no other modules in the index)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
