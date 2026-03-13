"""Tests for /api/faiss/stats and /api/faiss/search logic (without live server).

Tests 8 assertions covering:
- FaissRegistry stats returns correct structure
- Empty dir returns zero collections
- Stats have required keys (name, count, dim)
- Collection vector counts are non-negative
- Search returns results list structure
- Search result has required keys (doc_id, text, score)
- Score is in valid cosine range [0, 1]
- Empty collection search returns empty list
"""

import sys
import os
import tempfile
import shutil
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.faiss_store import FaissCollection, FaissRegistry, SearchResult

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def random_vec(dim=768):
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _simulate_stats(base_dir):
    """Simulate the /api/faiss/stats logic without FastAPI."""
    from pathlib import Path
    p = Path(base_dir)
    if not p.exists():
        return {"collections": [], "total_vectors": 0}
    reg = FaissRegistry(base_dir=base_dir)
    collections = []
    for col_dir in sorted(p.iterdir()):
        if col_dir.is_dir():
            col = reg.get_or_create(col_dir.name)
            collections.append({"name": col_dir.name, "count": col.count, "dim": col.dim})
    return {"collections": collections, "total_vectors": sum(c["count"] for c in collections)}


def _simulate_search(base_dir, query_vec, collection, k=5):
    """Simulate the /api/faiss/search logic (without embedding)."""
    from pathlib import Path
    reg = FaissRegistry(base_dir=base_dir)
    col = reg.get_or_create(collection)
    results = col.search(query_vec, k=k)
    return {
        "results": [
            {"doc_id": r.doc_id, "text": r.text, "score": round(r.score, 4), "metadata": r.metadata}
            for r in results
        ]
    }


def run():
    tmp = tempfile.mkdtemp(prefix="test_faiss_api_")
    try:
        # Populate test collections
        reg = FaissRegistry(base_dir=tmp)
        col_ax = reg.get_or_create("axioms", dim=64)
        col_ag = reg.get_or_create("agents", dim=64)

        ax_vecs = [random_vec(64) for _ in range(5)]
        for i, v in enumerate(ax_vecs):
            col_ax.add(f"ax{i}", f"axiom text {i}", v, {"source": "yaml"})

        ag_vecs = [random_vec(64) for _ in range(3)]
        for i, v in enumerate(ag_vecs):
            col_ag.add(f"ag{i}", f"agent text {i}", v, {"agent_id": f"agent_{i}"})

        # Persist so _simulate_stats (which creates a new registry) can load them
        col_ax.save()
        col_ag.save()

        # 1. Stats returns correct structure
        stats = _simulate_stats(tmp)
        if "collections" in stats and "total_vectors" in stats:
            OK("Stats returns collections + total_vectors keys")
        else:
            FAIL_MSG("Stats structure", str(stats.keys()))

        # 2. Total vector count is correct
        if stats["total_vectors"] == 8:
            OK(f"total_vectors == 8 (got {stats['total_vectors']})")
        else:
            FAIL_MSG("total_vectors", str(stats["total_vectors"]))

        # 3. Each collection has required keys
        keys_ok = all("name" in c and "count" in c and "dim" in c for c in stats["collections"])
        if keys_ok:
            OK("Each collection has name/count/dim")
        else:
            FAIL_MSG("Collection keys", str(stats["collections"]))

        # 4. Vector counts non-negative
        all_positive = all(c["count"] >= 0 for c in stats["collections"])
        if all_positive:
            OK("All collection counts >= 0")
        else:
            FAIL_MSG("Non-negative counts", str(stats["collections"]))

        # 5. Empty dir returns zero collections
        empty_tmp = tempfile.mkdtemp(prefix="test_faiss_empty_")
        try:
            empty_stats = _simulate_stats(empty_tmp)
            if empty_stats["total_vectors"] == 0 and empty_stats["collections"] == []:
                OK("Empty dir returns 0 collections, 0 vectors")
            else:
                FAIL_MSG("Empty dir stats", str(empty_stats))
        finally:
            shutil.rmtree(empty_tmp, ignore_errors=True)

        # 6. Search returns results with required keys
        query_v = ax_vecs[0]
        search_result = _simulate_search(tmp, query_v, "axioms", k=3)
        results = search_result.get("results", [])
        if results and all("doc_id" in r and "text" in r and "score" in r for r in results):
            OK(f"Search results have doc_id/text/score ({len(results)} results)")
        else:
            FAIL_MSG("Search result keys", str(results[:1] if results else "empty"))

        # 7. Scores are in [-1, 1] (cosine similarity via L2-normalized IndexFlatIP)
        scores_ok = all(-1.01 <= r["score"] <= 1.01 for r in results)
        if scores_ok:
            OK(f"All scores in [-1,1] range (top={results[0]['score']:.4f})")
        else:
            FAIL_MSG("Score range", str([r["score"] for r in results]))

        # 8. Empty collection search returns empty list
        col_empty = reg.get_or_create("empty_test", dim=64)
        empty_search = _simulate_search(tmp, random_vec(64), "empty_test", k=5)
        if empty_search["results"] == []:
            OK("Empty collection search returns empty results list")
        else:
            FAIL_MSG("Empty collection search", str(empty_search["results"]))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("\n=== test_faiss_api ===")
    run()
    total = len(PASS) + len(FAIL)
    print(f"\nResult: {len(PASS)}/{total} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(f"  - {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
