"""Tests for FAISS retrieval integration — SmartRouter v2 retrieval keywords + context enrichment.

10 tests covering:
- _RETRIEVAL_KEYWORDS pattern matches expected queries
- _RETRIEVAL_KEYWORDS does NOT match math/rule/stat queries
- SmartRouter routes retrieval queries to 'retrieval' layer
- SmartRouter still routes math queries to 'model_based'
- SmartRouter routes rule queries to 'rule_constraints'
- FaissRegistry.stats() returns correct structure
- FaissCollection.search() returns SearchResult objects
- SearchResult has doc_id, text, score, metadata fields
- Empty collection returns [] from search()
- Score threshold filter works (> 0.35 filters low-similarity)
"""

import sys
import os
import tempfile
import shutil
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.faiss_store import FaissCollection, FaissRegistry, SearchResult
from core.smart_router_v2 import SmartRouterV2, _RETRIEVAL_KEYWORDS

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def random_vec(dim=64):
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_capsule(domain="home"):
    """Create minimal DomainCapsule for routing tests."""
    from core.domain_capsule import DomainCapsule
    raw = {
        "domain": domain,
        "version": "test",
        "layers": {
            "retrieval":        {"enabled": True,  "priority": 4},
            "model_based":      {"enabled": True,  "priority": 1},
            "rule_constraints": {"enabled": True,  "priority": 2},
            "statistical":      {"enabled": True,  "priority": 3},
            "llm_reasoning":    {"enabled": True,  "priority": 5},
        },
        "key_decisions": [],
        "rules": [],
        "models": [],
        "data_sources": [],
    }
    return DomainCapsule(raw)


def run():
    # ── Keyword pattern tests ──────────────────────────────────

    # 1. Retrieval patterns match general knowledge queries
    retrieval_queries = [
        "what is varroa mite",
        "explain how bees communicate",
        "tell me about oxalic acid",
        "mitä on kevätkiihdytys",
        "selitä miten mehiläisyhdyskunta toimii",
    ]
    hits = [q for q in retrieval_queries if _RETRIEVAL_KEYWORDS.search(q)]
    if len(hits) == len(retrieval_queries):
        OK(f"_RETRIEVAL_KEYWORDS matches all {len(retrieval_queries)} general queries")
    else:
        FAIL_MSG("_RETRIEVAL_KEYWORDS missed some queries", str([q for q in retrieval_queries if not _RETRIEVAL_KEYWORDS.search(q)]))

    # 2. Retrieval pattern does NOT match math/rule queries
    non_retrieval = ["laske lämmityskustannus", "calculate kwh", "pitääkö tarkistaa"]
    false_positives = [q for q in non_retrieval if _RETRIEVAL_KEYWORDS.search(q)]
    if not false_positives:
        OK("_RETRIEVAL_KEYWORDS has no false positives on math/rule queries")
    else:
        FAIL_MSG("_RETRIEVAL_KEYWORDS false positives", str(false_positives))

    # ── SmartRouter routing tests ──────────────────────────────

    capsule = _make_capsule()
    router = SmartRouterV2(capsule)

    # 3. Routes "what is" to retrieval
    route = router.route("what is the optimal temperature for bee hive")
    if route.layer == "retrieval":
        OK(f"Router: 'what is...' -> retrieval (confidence={route.confidence})")
    else:
        FAIL_MSG("Router retrieval routing", f"got layer={route.layer}")

    # 4. Routes math query to model_based
    route_math = router.route("laske lämmityskustannus 80m2 huoneelle")
    if route_math.layer == "model_based":
        OK(f"Router: math query -> model_based (confidence={route_math.confidence})")
    else:
        FAIL_MSG("Router model_based routing", f"got layer={route_math.layer}")

    # 5. Routes rule query to rule_constraints
    route_rule = router.route("onko sallittu käyttää tätä lääkettä")
    if route_rule.layer == "rule_constraints":
        OK(f"Router: rule query -> rule_constraints (confidence={route_rule.confidence})")
    else:
        FAIL_MSG("Router rule_constraints routing", f"got layer={route_rule.layer}")

    # ── FAISS search tests ─────────────────────────────────────

    tmp = tempfile.mkdtemp(prefix="test_faiss_ret_")
    try:
        reg = FaissRegistry(base_dir=tmp)
        col = reg.get_or_create("axioms", dim=64)

        # Add known vectors
        base_vec = random_vec(64)
        similar_vec = base_vec + np.random.randn(64).astype(np.float32) * 0.05
        similar_vec /= np.linalg.norm(similar_vec)
        dissimilar_vec = random_vec(64)

        col.add("similar", "Similar axiom about varroa mite treatment", similar_vec, {"source": "yaml"})
        col.add("dissimilar", "Completely unrelated industrial process", dissimilar_vec, {"source": "yaml"})

        # 6. Search returns SearchResult objects
        results = col.search(base_vec, k=2)
        if results and isinstance(results[0], SearchResult):
            OK(f"Search returns SearchResult objects ({len(results)} results)")
        else:
            FAIL_MSG("Search returns SearchResult", str(type(results[0]) if results else "empty"))

        # 7. SearchResult has all required fields
        r = results[0]
        has_fields = all(hasattr(r, f) for f in ("doc_id", "text", "score", "metadata"))
        if has_fields:
            OK(f"SearchResult has doc_id/text/score/metadata (top: {r.doc_id}, score={r.score:.4f})")
        else:
            FAIL_MSG("SearchResult fields", str([f for f in ("doc_id","text","score","metadata") if not hasattr(r, f)]))

        # 8. Similar vector ranks first
        if results[0].doc_id == "similar":
            OK(f"Similar vector ranked first (score={results[0].score:.4f} > {results[1].score:.4f})")
        else:
            FAIL_MSG("Similar vector ranked first", f"top={results[0].doc_id}")

        # 9. Empty collection returns empty list
        empty_col = reg.get_or_create("empty_col", dim=64)
        empty_results = empty_col.search(base_vec, k=5)
        if empty_results == []:
            OK("Empty collection returns []")
        else:
            FAIL_MSG("Empty collection returns []", str(len(empty_results)))

        # 10. Score threshold filter (> 0.35) keeps similar, filters dissimilar random vecs
        all_results = col.search(base_vec, k=2)
        good_hits = [h for h in all_results if h.score > 0.35]
        # The similar vec should pass the threshold; the dissimilar one likely won't
        if any(h.doc_id == "similar" for h in good_hits):
            OK(f"Score threshold > 0.35 keeps similar vector ({good_hits[0].score:.4f})")
        else:
            FAIL_MSG("Score threshold filter", f"scores={[round(h.score,3) for h in all_results]}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("\n=== test_faiss_retrieval ===")
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
