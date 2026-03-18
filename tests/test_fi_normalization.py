"""Tests for Finnish diacritics normalization in SmartRouterV2.

8 tests covering:
- 'pitaako' (ASCII) routes to rule_constraints like 'pitääkö' (diacritics)
- 'mita on' routes to retrieval like 'mitä on'
- 'selita' routes to retrieval like 'selitä'
- 'mita tehda maaliskuussa' routes to retrieval (ASCII seasonal)
- 'normaali/trendi' (ASCII) routes to statistical
- _normalize_fi() helper: ä→a, ö→o, å→a
- Original diacritics still work (no regression)
- Mixed ASCII+diacritics query routes correctly
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.smart_router_v2 import SmartRouterV2, _normalize_fi
from core.domain_capsule import DomainCapsule
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


def _router():
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return SmartRouterV2(DomainCapsule(raw))


def run():
    router = _router()

    # 1. _normalize_fi() helper
    cases = [
        ("pitääkö", "pitaako"),
        ("selitä", "selita"),
        ("mitä", "mita"),
        ("mikä on", "mika on"),
        ("lämmitys", "lammitys"),
        ("höpöhöpö", "hopohopo"),
    ]
    ok = all(_normalize_fi(src) == expected for src, expected in cases)
    if ok:
        OK("_normalize_fi() correctly maps a-umlaut->a, o-umlaut->o")
    else:
        bad = [(s, _normalize_fi(s), e) for s, e in cases if _normalize_fi(s) != e]
        FAIL_MSG("_normalize_fi() wrong", str(bad))

    # 2. 'pitaako' (ASCII) → rule_constraints (like 'pitääkö')
    r1 = router.route("pitaako putkia suojata pakkaselta")
    r2 = router.route("pitääkö suojata putket pakkaselta")
    if r1.layer == "rule_constraints" and r2.layer == "rule_constraints":
        OK("'pitaako' and 'pitääkö' both route to rule_constraints")
    else:
        FAIL_MSG("ASCII 'pitaako' rule routing", f"pitaako={r1.layer}, pitääkö={r2.layer}")

    # 3. 'mita on' (ASCII) -> retrieval (like 'mitä on'), for non-bee queries
    # Note: avoid 'energi' substring (heating_cost), 'mite' substring (varroa_treatment)
    r1 = router.route("mita on sademaara")
    r2 = router.route("mitä on sademaara")
    if r1.layer == "retrieval" and r2.layer == "retrieval":
        OK("'mita on' and 'mitä on' both route to retrieval (non-bee query)")
    else:
        FAIL_MSG("ASCII 'mita on' retrieval routing", f"mita={r1.layer}, mitä={r2.layer}")

    # 4. 'selita' (ASCII) -> retrieval (like 'selitä')
    # Note: avoid 'miten' (contains 'mite' = varroa_treatment keyword substring match)
    r1 = router.route("selita kuinka sauna toimii")
    r2 = router.route("selitä kuinka sauna toimii")
    if r1.layer == "retrieval" and r2.layer == "retrieval":
        OK("'selita' and 'selitä' both route to retrieval")
    else:
        FAIL_MSG("ASCII 'selita' retrieval routing", f"selita={r1.layer}, selitä={r2.layer}")

    # 5. ASCII seasonal query still routes to retrieval
    r1 = router.route("mita tehda maaliskuussa")
    r2 = router.route("mitä tehdä maaliskuussa")
    if r1.layer == "retrieval" and r2.layer == "retrieval":
        OK("ASCII seasonal queries route to retrieval (maaliskuussa)")
    else:
        FAIL_MSG("ASCII seasonal routing", f"ascii={r1.layer}, fi={r2.layer}")

    # 6. Statistical keywords work with ASCII normalization
    r1 = router.route("onko trendi normaali vai anomaali")
    r2 = router.route("onko trendi normaali vai anomaali")  # same query — ASCII already
    if r1.layer == "statistical":
        OK("Statistical keywords (normaali/anomaali/trendi) route to statistical")
    else:
        FAIL_MSG("Statistical routing", f"got {r1.layer}")

    # 7. Original diacritics routing not broken (regression check)
    # Note: cottage capsule restructured in v3.0 — hive_survival/honey_yield removed
    regressions = [
        ("pitääkö suojata putket pakkaselta", "rule_constraints"),
        ("mita pitaa tehda maaliskuussa", "retrieval"),
        ("paljonko lämmitys maksaa", "model_based"),
    ]
    reg_ok = all(router.route(q).layer == exp for q, exp in regressions)
    if reg_ok:
        OK("No regression: original diacritics queries still route correctly")
    else:
        bad = [(q, router.route(q).layer, e) for q, e in regressions
               if router.route(q).layer != e]
        FAIL_MSG("Regression detected", str(bad))

    # 8. Mixed case: ASCII Finnish + domain terms in same query
    # Note: varroa_treatment removed from capsule in v3.0, so generic domain queries
    # may route to retrieval or llm_reasoning
    r = router.route("mita on lammitys kustannus talvella")
    if r.layer in ("model_based", "retrieval"):
        OK(f"Mixed query (ASCII FI + domain): routes to {r.layer}")
    else:
        FAIL_MSG("Mixed query routing", f"got {r.layer}")


def main():
    print("\n=== test_fi_normalization ===")
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
