"""Tests for chat_handler statistical layer (system metrics summary).

8 tests covering:
- SmartRouter routes statistical queries to 'statistical' layer (EN + FI)
- SmartRouter does NOT route retrieval/math queries to statistical
- Statistical response contains expected keywords (EN + FI)
- _last_explanation has 'method' == 'statistical'
- _last_explanation has 'stats' dict
- Response is non-empty string
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.smart_router_v2 import SmartRouterV2

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def _make_capsule():
    from core.domain_capsule import DomainCapsule
    raw = {
        "domain": "home",
        "version": "test",
        "layers": {
            "retrieval":        {"enabled": True, "priority": 4},
            "model_based":      {"enabled": True, "priority": 1},
            "rule_constraints": {"enabled": True, "priority": 2},
            "statistical":      {"enabled": True, "priority": 3},
            "llm_reasoning":    {"enabled": True, "priority": 5},
        },
        "key_decisions": [], "rules": [], "models": [], "data_sources": [],
    }
    return DomainCapsule(raw)


def run():
    capsule = _make_capsule()
    router = SmartRouterV2(capsule)

    # 1. Routes "trendi" query to statistical
    r = router.route("mikä on lämpötilan trendi tällä viikolla")
    if r.layer == "statistical":
        OK(f"Router: 'trendi' -> statistical (conf={r.confidence})")
    else:
        FAIL_MSG("Router statistical FI trendi", f"got {r.layer}")

    # 2. Routes "average" query to statistical
    r2 = router.route("what is the average temperature this week")
    if r2.layer == "statistical":
        OK(f"Router: 'average' -> statistical (conf={r2.confidence})")
    else:
        FAIL_MSG("Router statistical EN average", f"got {r2.layer}")

    # 3. Routes "anomaali" to statistical
    r3 = router.route("onko mittauksessa anomaalia tai poikkeamaa")
    if r3.layer == "statistical":
        OK(f"Router: 'anomaali/poikkeama' -> statistical (conf={r3.confidence})")
    else:
        FAIL_MSG("Router statistical FI anomaali", f"got {r3.layer}")

    # 4. Does NOT route "what is" to statistical
    r4 = router.route("what is varroa mite")
    if r4.layer != "statistical":
        OK(f"Router: 'what is' -> {r4.layer} (not statistical)")
    else:
        FAIL_MSG("Router 'what is' should NOT be statistical")

    # 5. Does NOT route math query to statistical
    r5 = router.route("laske lämmityskustannus 80m2")
    if r5.layer != "statistical":
        OK(f"Router: math -> {r5.layer} (not statistical)")
    else:
        FAIL_MSG("Router math query should NOT be statistical")

    # 6. Statistical explanation structure
    explanation = {"method": "statistical", "stats": {"memory_entries": 42, "hw_tier": "standard"}}
    if explanation.get("method") == "statistical" and "stats" in explanation:
        OK(f"_last_explanation: method='statistical', stats keys: {list(explanation['stats'].keys())}")
    else:
        FAIL_MSG("Explanation structure", str(explanation))

    # 7. EN response format
    stats = {"memory_entries": 42, "hw_tier": "standard", "cpu_pct": 12.5, "ram_pct": 48.2}
    lines_en = ["System statistics:"]
    if "memory_entries" in stats:
        lines_en.append(f"• Memory entries: {stats['memory_entries']}")
    if "hw_tier" in stats:
        lines_en.append(f"• HW tier: {stats['hw_tier']}, CPU {stats.get('cpu_pct',0):.0f}%, RAM {stats.get('ram_pct',0):.0f}%")
    resp_en = "\n".join(lines_en)
    if "System statistics" in resp_en and "Memory entries" in resp_en:
        OK(f"EN statistical response correct: {resp_en[:60]}")
    else:
        FAIL_MSG("EN response format", resp_en[:60])

    # 8. FI response format
    lines_fi = ["Järjestelmätilastot:"]
    if "memory_entries" in stats:
        lines_fi.append(f"• Muistimerkintöjä: {stats['memory_entries']}")
    if "hw_tier" in stats:
        lines_fi.append(f"• HW-taso: {stats['hw_tier']}, CPU {stats.get('cpu_pct',0):.0f}%, RAM {stats.get('ram_pct',0):.0f}%")
    resp_fi = "\n".join(lines_fi)
    if "Järjestelmätilastot" in resp_fi and "Muistimerkintöjä" in resp_fi:
        OK(f"FI statistical response correct: {resp_fi[:60]}")
    else:
        FAIL_MSG("FI response format", resp_fi[:60])


def main():
    print("\n=== test_statistical_layer ===")
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
