"""Tests for bee-domain axiom FAISS indexing (post index_bee_axioms.py).

8 tests covering:
- Axioms FAISS collection exists on disk
- Collection has >= 135 vectors (92 base + 43 bee axioms)
- All 4 bee model main doc_ids are present
- Bee axiom variable doc_ids are present
- Bee axiom formula doc_ids are present
- Metadata fields correct (type, model_id, domain)
- No duplicate doc_ids in collection
- Bee models indexed under domain 'cottage'
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.faiss_store import FaissRegistry

PASS = []
FAIL = []

BEE_MODELS = {"honey_yield", "varroa_treatment", "swarm_risk", "colony_food_reserves"}
MIN_VECTORS = 130   # 92 base + 43 bee = 135, allow small margin


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def run():
    registry = FaissRegistry()
    col = registry.get_or_create("axioms")

    # 1. Collection loaded from disk with vectors
    if col.count > 0:
        OK(f"Axioms FAISS collection loaded ({col.count} vectors)")
    else:
        FAIL_MSG("Axioms FAISS collection empty or not found", f"count={col.count}")

    # 2. Count >= MIN_VECTORS (base + bee axioms indexed)
    if col.count >= MIN_VECTORS:
        OK(f"Axioms count {col.count} >= {MIN_VECTORS} (bee axioms indexed)")
    else:
        FAIL_MSG("Axioms count too low", f"got {col.count}, expected >= {MIN_VECTORS}")

    # 3. All 4 bee model main doc_ids present
    doc_ids_set = set(col._doc_ids)
    main_ids = {f"axiom:{m}:main" for m in BEE_MODELS}
    found_main = main_ids.intersection(doc_ids_set)
    if len(found_main) == 4:
        OK(f"All 4 bee model main doc_ids indexed: {sorted(BEE_MODELS)}")
    else:
        missing = main_ids - doc_ids_set
        FAIL_MSG("Bee model main doc_ids missing", str(missing))

    # 4. Variable doc_ids present (spot-check key variables)
    spot_vars = [
        "axiom:honey_yield:var:colony_strength",
        "axiom:varroa_treatment:var:varroa_before",
        "axiom:swarm_risk:var:queen_age_years",
        "axiom:colony_food_reserves:var:food_available_kg",
    ]
    missing_vars = [v for v in spot_vars if v not in doc_ids_set]
    if not missing_vars:
        OK(f"Bee model variable doc_ids indexed (spot-check {len(spot_vars)} vars)")
    else:
        FAIL_MSG("Bee variable doc_ids missing", str(missing_vars))

    # 5. Formula doc_ids present (spot-check key formulas)
    spot_formulas = [
        "axiom:honey_yield:formula:season_honey_kg",
        "axiom:varroa_treatment:formula:mite_load_pct",
        "axiom:swarm_risk:formula:swarm_probability_pct",
        "axiom:colony_food_reserves:formula:feeding_needed_kg",
    ]
    missing_formulas = [f for f in spot_formulas if f not in doc_ids_set]
    if not missing_formulas:
        OK(f"Bee model formula doc_ids indexed (spot-check {len(spot_formulas)} formulas)")
    else:
        FAIL_MSG("Bee formula doc_ids missing", str(missing_formulas))

    # 6. Metadata correct for honey_yield:main
    try:
        idx = col._doc_ids.index("axiom:honey_yield:main")
        meta = col._metadata[idx]
        if (meta.get("type") == "axiom_main"
                and meta.get("model_id") == "honey_yield"
                and meta.get("domain") == "cottage"):
            OK(f"honey_yield:main metadata correct (type={meta['type']}, domain={meta['domain']})")
        else:
            FAIL_MSG("honey_yield:main metadata wrong", str(meta))
    except ValueError:
        FAIL_MSG("honey_yield:main not in _doc_ids")

    # 7. No duplicate doc_ids
    if len(col._doc_ids) == len(doc_ids_set):
        OK(f"No duplicate doc_ids in axioms collection ({len(col._doc_ids)} unique)")
    else:
        n_dupes = len(col._doc_ids) - len(doc_ids_set)
        FAIL_MSG("Duplicate doc_ids found", f"{n_dupes} duplicates in {len(col._doc_ids)} total")

    # 8. Bee models all indexed under domain 'cottage'
    cottage_models = {
        m.get("model_id")
        for m in col._metadata
        if m.get("domain") == "cottage" and m.get("type") == "axiom_main"
    }
    missing_models = BEE_MODELS - cottage_models
    if not missing_models:
        OK(f"All 4 bee models indexed under domain 'cottage'")
    else:
        FAIL_MSG("Bee models not in cottage domain", str(missing_models))


def main():
    print("\n=== test_axiom_faiss ===")
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
