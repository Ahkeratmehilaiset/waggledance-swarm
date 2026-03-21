"""Tests for core/faiss_store.py — 10 tests."""

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


def random_vec(dim=768) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def run():
    tmp = tempfile.mkdtemp(prefix="test_faiss_")
    try:
        # 1. Add and search — identical vector gets score ~1.0
        col = FaissCollection("test1", dim=64, persist_dir=tmp + "/test1")
        vec = random_vec(64)
        col.add("doc1", "hello world", vec, {"tag": "test"})
        results = col.search(vec, k=1)
        if results and results[0].score > 0.99:
            OK(f"Identical vector retrieves itself (score={results[0].score:.4f})")
        else:
            score = results[0].score if results else "no results"
            FAIL_MSG("Identical vector retrieves itself", str(score))

        # 2. Count reflects adds
        if col.count == 1:
            OK("count == 1 after single add")
        else:
            FAIL_MSG("count == 1 after add", str(col.count))

        # 3. add_batch adds multiple documents
        ids = [f"id{i}" for i in range(5)]
        texts = [f"text {i}" for i in range(5)]
        vecs = np.stack([random_vec(64) for _ in range(5)])
        col.add_batch(ids, texts, vecs)
        if col.count == 6:
            OK("add_batch adds 5 docs (total=6)")
        else:
            FAIL_MSG("add_batch count", str(col.count))

        # 4. k is capped at collection size
        results = col.search(random_vec(64), k=100)
        if len(results) == 6:
            OK(f"k capped at collection size ({len(results)} results)")
        else:
            FAIL_MSG("k capped at collection size", str(len(results)))

        # 5. Similar vector ranks higher than random
        base = random_vec(64)
        noise = base + np.random.randn(64).astype(np.float32) * 0.05
        noise /= np.linalg.norm(noise)
        other = random_vec(64)

        col2 = FaissCollection("test2", dim=64, persist_dir=tmp + "/test2")
        col2.add("similar", "similar text", noise)
        col2.add("random", "random text", other)
        hits = col2.search(base, k=2)
        if hits and hits[0].doc_id == "similar":
            OK(f"Similar vector ranked first (score={hits[0].score:.3f})")
        else:
            ranking = [h.doc_id for h in hits]
            FAIL_MSG("Similar vector ranked first", str(ranking))

        # 6. Metadata returned in search results
        col3 = FaissCollection("test3", dim=64, persist_dir=tmp + "/test3")
        col3.add("doc_meta", "metadata test", random_vec(64), {"key": "value", "num": 42})
        hits = col3.search(random_vec(64), k=1)
        if hits and hits[0].metadata.get("key") == "value":
            OK("Metadata preserved in search results")
        else:
            FAIL_MSG("Metadata preserved", str(hits[0].metadata if hits else "no hits"))

        # 7. Persistence: save → load → search still works
        col4 = FaissCollection("persist_test", dim=64, persist_dir=tmp + "/persist_test")
        ref_vec = random_vec(64)
        col4.add("p1", "persistent doc", ref_vec, {"persisted": True})
        col4.save()

        col4b = FaissCollection("persist_test", dim=64, persist_dir=tmp + "/persist_test")
        hits = col4b.search(ref_vec, k=1)
        if hits and hits[0].doc_id == "p1" and hits[0].score > 0.99:
            OK("Save/load: doc found after reload (score>0.99)")
        else:
            score = hits[0].score if hits else "no hits"
            FAIL_MSG("Save/load persistence", f"score={score}")

        # 8. FaissRegistry get_or_create
        reg = FaissRegistry(base_dir=tmp + "/registry")
        c1 = reg.get_or_create("col_a", dim=64)
        c2 = reg.get_or_create("col_a", dim=64)
        if c1 is c2:
            OK("FaissRegistry returns same object for same name")
        else:
            FAIL_MSG("FaissRegistry identity", "different objects")

        # 9. FaissRegistry stats
        c1.add("x", "text", random_vec(64))
        cb = reg.get_or_create("col_b", dim=64)
        cb.add("y", "text", random_vec(64))
        cb.add("z", "text", random_vec(64))
        stats = reg.stats()
        if stats.get("col_a") == 1 and stats.get("col_b") == 2:
            OK(f"Registry stats correct: {stats}")
        else:
            FAIL_MSG("Registry stats", str(stats))

        # 10. Empty collection returns empty list
        empty = FaissCollection("empty", dim=64, persist_dir=tmp + "/empty")
        results = empty.search(random_vec(64), k=5)
        if results == []:
            OK("Empty collection search returns []")
        else:
            FAIL_MSG("Empty collection returns []", str(results))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("\n=== test_faiss_store ===")
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
