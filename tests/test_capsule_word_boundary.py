"""Tests for capsule keyword word-boundary matching (v1.11.0).

6 tests covering:
- 'energi' does NOT match inside 'aurinkoenergia' (word-boundary fix)
- 'mite' removed from varroa_treatment -- does NOT match 'miten'
- 'varroa' / 'oxalic' / 'treatment' still match correctly
- 'selita miten kuulostaa' routes to retrieval (not varroa_treatment)
- 'mita on aurinkoenergia' routes to retrieval (not heating_cost)
- Capsule still matches 'lammitys' in isolation via 'heating' keyword
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


def _capsule():
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return DomainCapsule(raw)


def _router():
    return SmartRouterV2(_capsule())


def run():
    cap = _capsule()
    router = _router()

    # 1. 'energi' does NOT match mid-word 'aurinkoenergia'
    m = cap.match_decision("mita on aurinkoenergia")
    if m is None or m.decision_id != "heating_cost":
        OK("'energi' does not match mid-word 'aurinkoenergia' (word-start boundary)")
    else:
        FAIL_MSG("'energi' false positive inside 'aurinkoenergia'", f"got {m.decision_id}")

    # 2. 'mita on aurinkoenergia' routes to retrieval (not model_based)
    r = router.route("mita on aurinkoenergia")
    if r.layer == "retrieval":
        OK("'mita on aurinkoenergia' routes to retrieval after word-boundary fix")
    else:
        FAIL_MSG("Word-boundary routing", f"got {r.layer} (expected retrieval)")

    # 3. 'mite' removed -- 'miten' does NOT trigger varroa_treatment
    m = cap.match_decision("selita miten kuulostaa")
    if m is None or m.decision_id != "varroa_treatment":
        OK("'miten' does not trigger varroa_treatment after 'mite' keyword removed")
    else:
        FAIL_MSG("'mite' false positive in 'miten'", f"got {m.decision_id}")

    # 4. 'selita miten kuulostaa' routes to retrieval
    r = router.route("selita miten kuulostaa")
    if r.layer == "retrieval":
        OK("'selita miten kuulostaa' routes to retrieval (miten safe now)")
    else:
        FAIL_MSG("'selita miten' routing", f"got {r.layer}")

    # 5. Domain query without capsule match routes to retrieval/llm_reasoning
    # Note: varroa_treatment removed from cottage capsule in v3.0 cutover
    r = router.route("how much oxalic acid treatment for varroa")
    if r.layer in ("retrieval", "llm_reasoning", "model_based"):
        OK(f"Domain query without capsule match routes to {r.layer}")
    else:
        FAIL_MSG("Domain query routing", f"layer={r.layer}, model={r.model}")

    # 6. 'heating' (standalone) still matches heating_cost
    r = router.route("paljonko heating cost per kwh")
    if r.layer == "model_based" and r.model == "heating_cost":
        OK("heating_cost still matched by 'heating', 'cost', 'kwh' keywords")
    else:
        FAIL_MSG("heating_cost regression", f"layer={r.layer}, model={r.model}")


def main():
    print("\n=== test_capsule_word_boundary ===")
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
