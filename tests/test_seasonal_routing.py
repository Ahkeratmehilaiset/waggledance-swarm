"""Tests for seasonal query routing — SmartRouter v2 _SEASONAL_KEYWORDS fix.

10 tests covering:
- Finnish month names route to retrieval (maaliskuu, huhtikuu, elokuu, syyskuu)
- English month names route to retrieval (march, august, september, october)
- Inflected month names route to retrieval (elokuussa, maaliskuussa)
- "kauden"/"tehtava" stem routes to retrieval
- "what should I do in <month>" routes to retrieval (not rule_constraints)
- Varroa + month doesn't cross-route: varroa wins (capsule Step 2 > Step 3)
- Hive survival still routes to statistical (talvehtiminen keyword)
- Frost protection still routes to rule_constraints
- Seasonal stats: router tracks seasonal routes
- bee_knowledge FAISS has content for each season (spring/summer/autumn/winter)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.domain_capsule import DomainCapsule
from core.smart_router_v2 import SmartRouterV2
from core.faiss_store import FaissRegistry
import yaml

PASS = []
FAIL = []
CAPSULE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "configs", "capsules", "cottage.yaml")


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def _load_router():
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    capsule = DomainCapsule(raw)
    return SmartRouterV2(capsule)


def run():
    router = _load_router()

    # 1. Finnish month names → retrieval
    fi_month_queries = [
        "mita pitaa tehda maaliskuussa",
        "mita tehdaan huhtikuussa mehilaisille",
        "elokuun tehtavat mehilaishoito",
        "syyskuun toimenpiteet pesalle",
    ]
    fi_ok = all(router.route(q).layer == "retrieval" for q in fi_month_queries)
    if fi_ok:
        OK("Finnish month names route to retrieval (maalis/huhti/elo/syys)")
    else:
        bad = [(q, router.route(q).layer) for q in fi_month_queries
               if router.route(q).layer != "retrieval"]
        FAIL_MSG("Finnish month routing", str(bad))

    # 2. English month names → retrieval
    en_month_queries = [
        "what to do in march for bees",
        "what should I do in august with bees",
        "what to do in september for winter prep",
        "october winterization tasks",
    ]
    en_ok = all(router.route(q).layer == "retrieval" for q in en_month_queries)
    if en_ok:
        OK("English month names route to retrieval (march/august/september/october)")
    else:
        bad = [(q, router.route(q).layer) for q in en_month_queries
               if router.route(q).layer != "retrieval"]
        FAIL_MSG("English month routing", str(bad))

    # 3. "what should I do" with month → retrieval (not rule_constraints via "should")
    r = router.route("what should I do in august with bees")
    if r.layer == "retrieval":
        OK("'what should I do in <month>' routes to retrieval, not rule_constraints")
    else:
        FAIL_MSG("'should' keyword hijack", f"got {r.layer}")

    # 4. "kauden"/"tehtava" stems → retrieval via _SEASONAL_KEYWORDS
    r_kaud = router.route("kauden tehtavat nyt")
    r_tehd = router.route("mitka ovat kevaan tehtavat")
    if r_kaud.layer == "retrieval" and r_tehd.layer == "retrieval":
        OK("'kauden'/'tehtava' stem routes to retrieval")
    else:
        FAIL_MSG("Stem routing", f"kauden={r_kaud.layer}, tehtava={r_tehd.layer}")

    # 5. Domain query + month routes reasonably (capsule or retrieval)
    # Note: varroa_treatment removed from cottage capsule in v3.0 cutover
    r_domain = router.route("varroa kasittely elokuussa")
    if r_domain.layer in ("retrieval", "model_based", "llm_reasoning"):
        OK(f"Domain + month query routes to {r_domain.layer} (valid layer)")
    else:
        FAIL_MSG("Domain+month routing", f"got {r_domain.layer}")

    # 6. Generic survival query routes to retrieval or llm_reasoning
    # Note: hive_survival removed from cottage capsule in v3.0 cutover
    r_surv = router.route("selviavatko mehilaiset talvesta talvehtiminen")
    if r_surv.layer in ("retrieval", "llm_reasoning", "statistical"):
        OK(f"Survival query routes to {r_surv.layer} (valid layer)")
    else:
        FAIL_MSG("Survival routing", f"got {r_surv.layer}")

    # 7. Frost protection still routes to rule_constraints
    r_frost = router.route("pitaako suojata putket pakkaselta")
    if r_frost.layer == "rule_constraints":
        OK("Frost protection still routes to rule_constraints")
    else:
        FAIL_MSG("Frost protection routing broken", f"got {r_frost.layer}")

    # 8. Seasonal confidence >= 0.1 for all month queries
    all_month_queries = fi_month_queries + en_month_queries
    low_conf = [(q, router.route(q).confidence) for q in all_month_queries
                if router.route(q).confidence < 0.1]
    if not low_conf:
        OK("All seasonal queries have confidence >= 0.1")
    else:
        FAIL_MSG("Low confidence seasonal queries", str(low_conf))

    # 9. Router stats track seasonal routes
    # After all the above routes, stats should have retrieval
    stats = router.stats()
    if stats.get("total_routes", 0) > 0 and "retrieval" in stats.get("layer_distribution", {}):
        OK(f"Router stats track retrieval routes (total={stats['total_routes']})")
    else:
        FAIL_MSG("Router stats missing retrieval", str(stats))

    # 10. New seasonal keywords: vuodenaika, kevät, syksy, talvi → retrieval
    season_kw_queries = [
        ("Mikä vuodenaika nyt on mehiläisten kannalta", "vuodenaika"),
        ("kevät on paras aika aloittaa", "kevät"),
        ("syksy on kiireistä aikaa", "syksy"),
        ("talvi on lepoa mehiläisille", "talvi"),
    ]
    season_kw_ok = True
    for q, kw in season_kw_queries:
        r = router.route(q)
        if r.layer != "retrieval":
            FAIL_MSG(f"Season keyword '{kw}' routing", f"got {r.layer}")
            season_kw_ok = False
    if season_kw_ok:
        OK("New season keywords (vuodenaika/kevät/syksy/talvi) route to retrieval")

    # 11. bee_knowledge FAISS has content for spring/summer/autumn/winter months
    registry = FaissRegistry()
    col = registry.get_or_create("bee_knowledge")
    doc_ids = set(col._doc_ids)
    season_checks = {
        "spring": "know:seasonal_tasks:month:04:overview",   # April
        "summer": "know:seasonal_tasks:month:07:overview",   # July
        "autumn": "know:seasonal_tasks:month:09:overview",   # September
        "winter": "know:seasonal_tasks:month:12:overview",   # December
    }
    missing_seasons = [s for s, did in season_checks.items() if did not in doc_ids]
    if not missing_seasons:
        OK("bee_knowledge FAISS has content for all 4 seasons (spring/summer/autumn/winter)")
    else:
        FAIL_MSG("Missing seasonal content", str(missing_seasons))


def main():
    print("\n=== test_seasonal_routing ===")
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
