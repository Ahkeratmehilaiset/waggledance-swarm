"""Tests for SmartRouterV2 stats tracking and reason codes (v1.14.0).

7 tests covering:
- stats() returns total_routes, layer_distribution, capsule_domain
- total_routes increments by 1 per route() call (not 3-4)
- reason codes are now category-specific: keyword_classifier:seasonal etc.
- capsule_decision_match reason is preserved
- layer_distribution correctly tracks all layer types
- Route is deterministic (repeated calls return same result)
- stats() after mixed routing shows correct distribution
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

    # 1. stats() returns expected keys
    s = router.stats()
    if all(k in s for k in ("total_routes", "layer_distribution", "capsule_domain")):
        OK("stats() returns total_routes, layer_distribution, capsule_domain")
    else:
        FAIL_MSG("stats() missing keys", str(s.keys()))

    # 2. total_routes increments by 1 per call (not doubled)
    before = router.stats()["total_routes"]
    router.route("pitaako suojata putket pakkaselta")
    after = router.stats()["total_routes"]
    if after == before + 1:
        OK("total_routes increments by exactly 1 per route() call")
    else:
        FAIL_MSG("total_routes wrong increment", f"before={before}, after={after}")

    # 3. Seasonal reason code: keyword_classifier:seasonal
    r = router.route("mita tehdaan maaliskuussa")
    if r.reason == "keyword_classifier:seasonal":
        OK("Seasonal query reason = 'keyword_classifier:seasonal'")
    else:
        FAIL_MSG("Seasonal reason code", f"got '{r.reason}'")

    # 4. Rule reason code: keyword_classifier:rule
    r = router.route("pitaako suojata putket pakkaselta")
    if r.reason == "keyword_classifier:rule":
        OK("Rule query reason = 'keyword_classifier:rule'")
    else:
        FAIL_MSG("Rule reason code", f"got '{r.reason}'")

    # 5. Capsule match reason preserved: capsule_decision_match
    r = router.route("paljonko lämmitys maksaa")
    if r.reason == "capsule_decision_match":
        OK("Capsule match reason = 'capsule_decision_match'")
    else:
        FAIL_MSG("Capsule reason code", f"got '{r.reason}'")

    # 6. Routing is deterministic (repeated calls return identical result)
    q = "onko trendi normaali vai anomaali"
    r1 = router.route(q)
    r2 = router.route(q)
    if r1.layer == r2.layer and r1.reason == r2.reason:
        OK("Routing is deterministic (same query -> same result)")
    else:
        FAIL_MSG("Routing non-deterministic",
                 f"r1={r1.layer}/{r1.reason}, r2={r2.layer}/{r2.reason}")

    # 7. layer_distribution tracks multiple layers
    # Route one query per layer type
    router2 = _router()
    router2.route("mita tehdaan maaliskuussa")     # retrieval (seasonal)
    router2.route("pitaako putket suojata")         # rule_constraints
    router2.route("onko trendi normaali")           # statistical
    s2 = router2.stats()
    dist = s2["layer_distribution"]
    if "retrieval" in dist and "rule_constraints" in dist and "statistical" in dist:
        OK(f"layer_distribution tracks all layers: {dist}")
    else:
        FAIL_MSG("layer_distribution missing layers", str(dist))


def main():
    print("\n=== test_router_stats ===")
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
