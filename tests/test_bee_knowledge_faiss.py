"""Tests for bee knowledge FAISS collection (seasonal tasks calendar).

8 tests covering:
- bee_knowledge collection exists and has >= 120 vectors
- All 12 months have overview doc_ids indexed
- FI task chunks exist for key months (March, August, September)
- EN task chunks exist for key months
- General fact doc_ids indexed
- Metadata fields correct (knowledge_id, domain, chunk_type)
- No duplicate doc_ids
- Keyword-rich months have keyword chunks (varroa, parveaminen, talvi)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.faiss_store import FaissRegistry

PASS = []
FAIL = []

MIN_VECTORS = 120   # seasonal_tasks.yaml → 129 chunks


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def run():
    registry = FaissRegistry()
    col = registry.get_or_create("bee_knowledge")

    # 1. Collection loaded with vectors
    if col.count >= MIN_VECTORS:
        OK(f"bee_knowledge collection loaded ({col.count} vectors >= {MIN_VECTORS})")
    else:
        FAIL_MSG("bee_knowledge collection empty or too small", f"count={col.count}")

    doc_ids_set = set(col._doc_ids)

    # 2. All 12 months have overview chunks
    missing_months = []
    for m in range(1, 13):
        did = f"know:seasonal_tasks:month:{m:02d}:overview"
        if did not in doc_ids_set:
            missing_months.append(m)
    if not missing_months:
        OK("All 12 month overview chunks indexed")
    else:
        FAIL_MSG("Missing month overview chunks", str(missing_months))

    # 3. FI task chunks exist for key months (3=March, 8=August, 9=September)
    spot_fi = [
        "know:seasonal_tasks:month:03:fi:0",   # March first FI task
        "know:seasonal_tasks:month:08:fi:0",   # August first FI task
        "know:seasonal_tasks:month:09:fi:0",   # September first FI task
    ]
    missing_fi = [d for d in spot_fi if d not in doc_ids_set]
    if not missing_fi:
        OK("FI task chunks indexed for March/August/September")
    else:
        FAIL_MSG("Missing FI task chunks", str(missing_fi))

    # 4. EN task chunks exist for key months
    spot_en = [
        "know:seasonal_tasks:month:05:en:0",   # May swarm season
        "know:seasonal_tasks:month:07:en:0",   # July harvest
        "know:seasonal_tasks:month:10:en:0",   # October winter prep
    ]
    missing_en = [d for d in spot_en if d not in doc_ids_set]
    if not missing_en:
        OK("EN task chunks indexed for May/July/October")
    else:
        FAIL_MSG("Missing EN task chunks", str(missing_en))

    # 5. General fact chunks indexed
    fact_ids = [
        "know:seasonal_tasks:fact:fi:0",
        "know:seasonal_tasks:fact:en:0",
    ]
    missing_facts = [d for d in fact_ids if d not in doc_ids_set]
    if not missing_facts:
        OK("General fact chunks indexed (FI + EN)")
    else:
        FAIL_MSG("Missing general fact chunks", str(missing_facts))

    # 6. Metadata correct for a sample chunk
    try:
        idx = col._doc_ids.index("know:seasonal_tasks:month:04:overview")
        meta = col._metadata[idx]
        if (meta.get("knowledge_id") == "seasonal_tasks"
                and meta.get("domain") == "cottage"
                and meta.get("chunk_type") == "month_overview"
                and meta.get("month") == 4):
            OK("April overview metadata correct (knowledge_id, domain, chunk_type, month)")
        else:
            FAIL_MSG("April overview metadata wrong", str(meta))
    except ValueError:
        FAIL_MSG("April overview chunk not found in _doc_ids")

    # 7. No duplicate doc_ids
    if len(col._doc_ids) == len(doc_ids_set):
        OK(f"No duplicate doc_ids ({len(col._doc_ids)} unique)")
    else:
        n_dupes = len(col._doc_ids) - len(doc_ids_set)
        FAIL_MSG("Duplicate doc_ids found", f"{n_dupes} duplicates")

    # 8. Keyword chunks exist for high-risk months (varroa months: August, October)
    keyword_chunks = [
        "know:seasonal_tasks:month:08:keywords",   # August: varroa treatment
        "know:seasonal_tasks:month:09:keywords",   # September: mouse guard
        "know:seasonal_tasks:month:10:keywords",   # October: winter prep
    ]
    missing_kw = [d for d in keyword_chunks if d not in doc_ids_set]
    if not missing_kw:
        OK("Keyword chunks indexed for August/September/October")
    else:
        FAIL_MSG("Missing keyword chunks", str(missing_kw))


def main():
    print("\n=== test_bee_knowledge_faiss ===")
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
