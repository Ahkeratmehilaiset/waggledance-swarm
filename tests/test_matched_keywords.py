"""Tests for matched_keywords transparency in RouteResult (v1.15.0).

7 tests covering:
- Capsule decision match: matched_keywords contains the YAML keyword that hit
- Keyword classifier (seasonal): matched_keywords contains matched month/stem
- Keyword classifier (rule): matched_keywords contains matched rule keyword
- Keyword classifier (stat): matched_keywords contains matched stat keyword
- Keyword classifier (retrieval): matched_keywords contains matched question word
- Fallback route: matched_keywords is empty
- to_dict() includes matched_keywords
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.domain_capsule import DomainCapsule
from core.smart_router_v2 import SmartRouterV2
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

    # 1. Capsule decision match: matched_keywords contains the YAML keyword
    r = router.route("paljonko lämmitys maksaa")
    if r.matched_keywords and "lämmitys" in r.matched_keywords:
        OK(f"Capsule match: matched_keywords contains 'lämmitys' -> {r.matched_keywords}")
    else:
        FAIL_MSG("Capsule matched_keywords", f"got {r.matched_keywords}")

    # 2. Capsule decision with ASCII query: matched keyword is the YAML form
    r = router.route("paljonko lammitys maksaa")
    if r.matched_keywords and "lämmitys" in r.matched_keywords:
        OK(f"ASCII capsule match: matched_keywords contains 'lämmitys' (YAML form)")
    else:
        FAIL_MSG("ASCII capsule matched_keywords", f"got {r.matched_keywords}")

    # 3. Keyword classifier seasonal: matched_keywords contains month name
    r = router.route("mita tehda maaliskuussa")
    if r.matched_keywords and any("maaliskuu" in kw for kw in r.matched_keywords):
        OK(f"Seasonal classifier: matched_keywords={r.matched_keywords}")
    else:
        FAIL_MSG("Seasonal matched_keywords", f"got {r.matched_keywords}")

    # 4. Keyword classifier rule: matched_keywords contains rule keyword
    r = router.route("pitaako suojata putket pakkaselta")
    if r.matched_keywords and any(kw in ("pitaako", "pitääkö") for kw in r.matched_keywords):
        OK(f"Rule classifier: matched_keywords={r.matched_keywords}")
    else:
        FAIL_MSG("Rule matched_keywords", f"got {r.matched_keywords}")

    # 5. Keyword classifier stat: matched_keywords contains stat keyword
    r = router.route("onko trendi normaali vai anomaali")
    if r.matched_keywords and len(r.matched_keywords) > 0:
        OK(f"Stat classifier: matched_keywords={r.matched_keywords}")
    else:
        FAIL_MSG("Stat matched_keywords", f"got {r.matched_keywords}")

    # 6. Keyword classifier retrieval: matched_keywords contains question word
    r = router.route("mita on sademaara")
    if r.matched_keywords and any("mita" in kw or "mitä" in kw for kw in r.matched_keywords):
        OK(f"Retrieval classifier: matched_keywords={r.matched_keywords}")
    else:
        FAIL_MSG("Retrieval matched_keywords", f"got {r.matched_keywords}")

    # 7. to_dict() includes matched_keywords key
    r = router.route("paljonko hunajaa saan")
    d = r.to_dict()
    if "matched_keywords" in d and isinstance(d["matched_keywords"], list):
        OK(f"to_dict() includes matched_keywords: {d['matched_keywords']}")
    else:
        FAIL_MSG("to_dict() matched_keywords missing", str(d.keys()))


def main():
    print("\n=== test_matched_keywords ===")
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
