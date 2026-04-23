#!/usr/bin/env python3
"""B.2 — Three-way shadow runner (keyword / flat / hex).

Per v3 §1.5 + v3.1: Phase C compares three architectures on same query set.

Architectures:
  A. keyword  — current production router (solver_router_v2)
  B. flat     — single global FAISS IndexFlatIP over ALL docs, no cells
  C. hex      — cell-based routing with ring1/ring2 expansion

Input: sample of queries (from hot_results.jsonl or handcrafted oracle set).
Output: docs/runs/hybrid_shadow_three_way_<date>.md + json.

Does NOT mutate runtime config. Phase C-only.

Usage:
    python tools/shadow_route_three_way.py \\
        --queries 1000 \\
        --source hot_results
    python tools/shadow_route_three_way.py \\
        --queries 500 \\
        --source oracle \\
        --oracle-dir tests/oracle
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import faiss  # noqa: E402
from waggledance.core.learning.embedding_cache import (  # noqa: E402
    EmbeddingCache, canonicalize_for_embedding,
)

STAGING_DIR = ROOT / "data" / "faiss_staging"
CAMPAIGN_DIR = ROOT / "docs" / "runs" / "ui_gauntlet_400h_20260413_092800"
OUTPUT_DIR = ROOT / "docs" / "runs"
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBEDDING_MODEL = "nomic-embed-text"


def _load_cell_indices() -> dict:
    """Load all staging cell FAISS indices + their doc metadata."""
    cells = {}
    for cell_dir in STAGING_DIR.iterdir():
        if not cell_dir.is_dir():
            continue
        idx_path = cell_dir / "index.faiss"
        meta_path = cell_dir / "meta.json"
        if not idx_path.exists() or not meta_path.exists():
            continue
        index = faiss.read_index(str(idx_path))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cells[cell_dir.name] = {"index": index, "meta": meta}
    return cells


def _build_flat_index(cells: dict) -> dict:
    """Combine all cells into one flat index for comparison."""
    all_vectors = []
    all_meta = []
    for cell_id, cell_data in cells.items():
        # Extract raw vectors from each cell
        n = cell_data["index"].ntotal
        dim = cell_data["index"].d
        vecs = np.zeros((n, dim), dtype=np.float32)
        for i in range(n):
            vecs[i] = cell_data["index"].reconstruct(i)
        for doc_meta in cell_data["meta"]:
            doc_meta = dict(doc_meta)
            doc_meta["_cell"] = cell_id
            all_meta.append(doc_meta)
        all_vectors.append(vecs)

    if not all_vectors:
        return None
    combined = np.vstack(all_vectors)
    flat = faiss.IndexFlatIP(combined.shape[1])
    flat.add(combined)
    return {"index": flat, "meta": all_meta}


def _embed_query(query: str, cache: EmbeddingCache) -> np.ndarray:
    cached = cache.get(EMBEDDING_MODEL, query)
    if cached is not None:
        return np.array(cached, dtype=np.float32)
    r = httpx.post(
        OLLAMA_URL,
        json={"model": EMBEDDING_MODEL, "input": [canonicalize_for_embedding(query)],
              "keep_alive": "30m"},
        timeout=60.0,
    )
    r.raise_for_status()
    vec = r.json()["embeddings"][0]
    cache.put(EMBEDDING_MODEL, query, vec)
    return np.array(vec, dtype=np.float32)


def route_keyword(query: str) -> dict:
    """Architecture A — current production router (stub for now)."""
    # In real integration this would call solver_router_v2. For shadow
    # runner baseline, approximate with keyword overlap.
    from tools.cell_manifest import _CELL_KEYWORDS
    q_lower = query.lower()
    best_cell, best_score = None, 0
    for cell, kws in _CELL_KEYWORDS.items():
        score = sum(1 for k in kws if k in q_lower)
        if score > best_score:
            best_cell, best_score = cell, score
    return {
        "route_type": "keyword",
        "cell": best_cell,
        "score": best_score,
        "doc_id": None,   # keyword doesn't select a doc
    }


def route_flat(query_vec: np.ndarray, flat_index: dict, k: int = 5) -> dict:
    vec = query_vec.copy().reshape(1, -1)
    faiss.normalize_L2(vec)
    D, I = flat_index["index"].search(vec, k)
    top = []
    for i, d in zip(I[0], D[0]):
        if i >= 0:
            m = flat_index["meta"][i]
            top.append({"doc_id": m["doc_id"], "score": float(d),
                        "canonical_solver_id": m["canonical_solver_id"],
                        "cell": m.get("_cell")})
    return {
        "route_type": "flat",
        "top_k": top,
        "chosen_solver": top[0]["canonical_solver_id"] if top else None,
        "chosen_cell": top[0].get("cell") if top else None,
        "score": top[0]["score"] if top else 0.0,
    }


def route_hex(query_vec: np.ndarray, query: str, cells: dict, centroids: dict, k: int = 5) -> dict:
    """Architecture C — hex with ring1 expansion."""
    from tools.cell_manifest import _CELL_KEYWORDS
    from tools.backfill_axioms_to_hex import classify_keyword_cell, compute_centroid_cell_top1

    q_lower = query.lower()
    keyword_cell = max(_CELL_KEYWORDS, key=lambda c: sum(1 for k in _CELL_KEYWORDS[c] if k in q_lower))
    # Top-3 centroid cells
    centroid_scores = {}
    for cell, data in centroids.items():
        c = np.array(data["centroid"], dtype=np.float32)
        q = query_vec / (np.linalg.norm(query_vec) + 1e-12)
        sim = float(np.dot(q, c / (np.linalg.norm(c) + 1e-12)))
        centroid_scores[cell] = sim
    top3_centroid = sorted(centroid_scores.items(), key=lambda x: -x[1])[:3]

    # Candidate cells
    from waggledance.core.hex_cell_topology import _ADJACENCY
    candidate = {keyword_cell}
    candidate.update(c for c, _ in top3_centroid)
    candidate.update(_ADJACENCY.get(keyword_cell, set()))
    if top3_centroid:
        candidate.update(_ADJACENCY.get(top3_centroid[0][0], set()))

    # Search each candidate cell (only if non-empty)
    all_hits = []
    for cell_id in candidate:
        if cell_id not in cells:
            continue  # empty cell — skip local search
        cell_data = cells[cell_id]
        vec = query_vec.copy().reshape(1, -1)
        faiss.normalize_L2(vec)
        D, I = cell_data["index"].search(vec, k)
        for i, d in zip(I[0], D[0]):
            if i >= 0:
                m = cell_data["meta"][i]
                all_hits.append({"doc_id": m["doc_id"], "score": float(d),
                                 "canonical_solver_id": m["canonical_solver_id"],
                                 "cell": cell_id})

    # Dedup by canonical_solver_id, take top by score
    best_per_solver = {}
    for hit in all_hits:
        sid = hit["canonical_solver_id"]
        if sid not in best_per_solver or best_per_solver[sid]["score"] < hit["score"]:
            best_per_solver[sid] = hit
    top = sorted(best_per_solver.values(), key=lambda h: -h["score"])[:k]

    return {
        "route_type": "hex",
        "keyword_cell": keyword_cell,
        "top3_centroid": [{"cell": c, "score": s} for c, s in top3_centroid],
        "candidate_cells": sorted(candidate),
        "keyword_centroid_disagreement": keyword_cell not in {c for c, _ in top3_centroid[:1]},
        "top_k": top,
        "chosen_solver": top[0]["canonical_solver_id"] if top else None,
        "chosen_cell": top[0]["cell"] if top else None,
        "score": top[0]["score"] if top else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--source", choices=["hot_results", "oracle"], default="hot_results")
    ap.add_argument("--oracle-dir", default="tests/oracle")
    args = ap.parse_args()

    # Load queries
    queries = []
    if args.source == "hot_results":
        hot_path = CAMPAIGN_DIR / "hot_results.jsonl"
        if not hot_path.exists():
            print(f"ERROR: {hot_path} not found")
            return 1
        with open(hot_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    queries.append(row.get("query") or row.get("query_id"))
                except json.JSONDecodeError:
                    continue
                if len(queries) >= args.queries:
                    break
        queries = [q for q in queries if q and len(q) > 2]
    else:
        oracle_dir = ROOT / args.oracle_dir
        if not oracle_dir.exists():
            print(f"Oracle dir {oracle_dir} not found")
            return 1
        import yaml
        for yml in oracle_dir.rglob("*.yaml"):
            data = yaml.safe_load(open(yml, encoding="utf-8"))
            for pos_q in data.get("positive", []):
                queries.append({"text": pos_q, "expected_solver": data.get("solver"),
                                "kind": "positive"})
            for neg_q in data.get("negative", []):
                queries.append({"text": neg_q, "expected_solver": None, "kind": "negative"})
            if len(queries) >= args.queries:
                break
        queries = queries[:args.queries]

    print(f"Loaded {len(queries)} queries from {args.source}")

    # Load staging indices
    print("Loading staging FAISS indices...")
    cells = _load_cell_indices()
    if not cells:
        print("ERROR: no staging indices. Run `tools/hex_manifest.py build` first.")
        return 1
    flat = _build_flat_index(cells)
    centroids_file = ROOT / "data" / "faiss_staging" / "cell_centroids.json"
    centroids = json.loads(centroids_file.read_text(encoding="utf-8"))["cells"]

    # Cache
    cache = EmbeddingCache(path=ROOT / "data" / "embedding_cache.sqlite")

    # Run three architectures per query
    results = []
    t_start = time.perf_counter()
    for i, q in enumerate(queries):
        query_text = q if isinstance(q, str) else q.get("text", "")
        if not query_text:
            continue

        t0 = time.perf_counter()
        a_kw = route_keyword(query_text)
        t_kw = (time.perf_counter() - t0) * 1000

        # Embed once
        qvec = _embed_query(query_text, cache)

        t0 = time.perf_counter()
        a_flat = route_flat(qvec, flat)
        t_flat = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        a_hex = route_hex(qvec, query_text, cells, centroids)
        t_hex = (time.perf_counter() - t0) * 1000

        row = {
            "query": query_text,
            "expected_solver": q.get("expected_solver") if isinstance(q, dict) else None,
            "expected_kind": q.get("kind") if isinstance(q, dict) else None,
            "keyword": {**a_kw, "latency_ms": round(t_kw, 3)},
            "flat": {**a_flat, "latency_ms": round(t_flat, 3)},
            "hex": {**a_hex, "latency_ms": round(t_hex, 3)},
        }
        results.append(row)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(queries)} processed...")

    total_s = time.perf_counter() - t_start
    print(f"  Total: {total_s:.1f}s ({total_s/len(queries)*1000:.1f}ms/query)")

    # Aggregate metrics
    lat_kw = [r["keyword"]["latency_ms"] for r in results]
    lat_flat = [r["flat"]["latency_ms"] for r in results]
    lat_hex = [r["hex"]["latency_ms"] for r in results]

    agg = {
        "n_queries": len(results),
        "latency_ms": {
            "keyword": {"p50": round(median(lat_kw), 2), "p95": round(sorted(lat_kw)[int(len(lat_kw)*0.95)-1], 2) if lat_kw else 0},
            "flat": {"p50": round(median(lat_flat), 2), "p95": round(sorted(lat_flat)[int(len(lat_flat)*0.95)-1], 2) if lat_flat else 0},
            "hex": {"p50": round(median(lat_hex), 2), "p95": round(sorted(lat_hex)[int(len(lat_hex)*0.95)-1], 2) if lat_hex else 0},
        },
        "agreement": {
            "flat_vs_hex_solver": sum(1 for r in results
                                       if r["flat"].get("chosen_solver") == r["hex"].get("chosen_solver"))
                                  / max(1, len(results)),
            "keyword_vs_hex_cell": sum(1 for r in results
                                        if r["keyword"].get("cell") == r["hex"].get("chosen_cell"))
                                   / max(1, len(results)),
            "keyword_centroid_disagreement_rate": sum(1 for r in results
                                                       if r["hex"].get("keyword_centroid_disagreement"))
                                                  / max(1, len(results)),
        },
    }

    if results and results[0].get("expected_solver") is not None:
        # Oracle precision
        def is_correct(route, expected, expected_kind):
            if expected_kind == "negative":
                return route.get("chosen_solver") is None or route.get("score", 0) < 0.35
            return route.get("chosen_solver") == expected

        for arch_key in ("keyword", "flat", "hex"):
            correct = sum(
                1 for r in results
                if is_correct(r[arch_key], r.get("expected_solver"), r.get("expected_kind"))
            )
            agg.setdefault("oracle_precision_at_1", {})[arch_key] = round(correct / max(1, len(results)), 4)

    # Write results
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_json = OUTPUT_DIR / f"hybrid_shadow_three_way_{ts}.json"
    out_json.write_text(json.dumps({"aggregate": agg, "per_query": results}, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    md = [
        f"# Phase C three-way shadow — {ts}",
        "",
        f"**Queries:** {agg['n_queries']}",
        f"**Source:** {args.source}",
        "",
        "## Latency (ms)",
        "",
        "| Architecture | p50 | p95 |",
        "|---|---:|---:|",
        f"| keyword | {agg['latency_ms']['keyword']['p50']} | {agg['latency_ms']['keyword']['p95']} |",
        f"| flat | {agg['latency_ms']['flat']['p50']} | {agg['latency_ms']['flat']['p95']} |",
        f"| hex | {agg['latency_ms']['hex']['p50']} | {agg['latency_ms']['hex']['p95']} |",
        "",
        "## Agreement",
        "",
        f"- Flat vs hex solver agreement: {agg['agreement']['flat_vs_hex_solver']:.1%}",
        f"- Keyword vs hex cell agreement: {agg['agreement']['keyword_vs_hex_cell']:.1%}",
        f"- Keyword/centroid disagreement rate: {agg['agreement']['keyword_centroid_disagreement_rate']:.1%}",
        "",
    ]
    if "oracle_precision_at_1" in agg:
        md += [
            "## Oracle precision@1",
            "",
            "| Architecture | Precision |",
            "|---|---:|",
        ]
        for arch, p in agg["oracle_precision_at_1"].items():
            md.append(f"| {arch} | {p:.1%} |")
        md.append("")
    md += [
        "## Decision gates (v3 §1.5)",
        "",
        "- If `hex` precision@1 ≥ `keyword` - 2pp AND `hex` recall@5 ≥ `keyword` + 5pp → proceed to Phase D-1",
        "- If `flat` ties or beats `hex` → abandon hex topology, refactor to flat-only",
        "- If both lose to `keyword` → do not enable",
        "",
        f"**Full per-query data:** `{out_json.relative_to(ROOT)}`",
    ]
    out_md = OUTPUT_DIR / f"hybrid_shadow_three_way_{ts}.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\nResults: {out_md.relative_to(ROOT)}")
    print(f"         {out_json.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
