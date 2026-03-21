"""Tests for Finnish diacritics normalization in DomainCapsule.match_decision.

Updated for v3.0 cutover: cottage capsule was restructured — varroa_treatment,
honey_yield, hive_survival removed. Tests cover remaining 5 decisions.

7 tests covering:
- 'lammitys' (ASCII) matches keyword 'lämmitys' -> heating_cost
- 'jaatya' (ASCII) matches keyword 'jäätyä'     -> frost_protection
- 'tehtava' (ASCII) matches keyword 'tehtävä'   -> seasonal_task
- Removed decisions return None from capsule
- Regression: original diacritic queries still match (remaining decisions)
- Full routing: ASCII heating query routes correctly
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


def run():
    cap = _capsule()
    router = SmartRouterV2(cap)

    # 1. ASCII 'lammitys' matches diacritic keyword 'lämmitys' -> heating_cost
    m = cap.match_decision("paljonko lammitys maksaa")
    if m and m.decision_id == "heating_cost":
        OK("ASCII 'lammitys' matches keyword 'lämmitys' -> heating_cost")
    else:
        FAIL_MSG("ASCII 'lammitys' capsule match",
                 f"got {m.decision_id if m else None}")

    # 2. ASCII 'jaatya' matches diacritic keyword 'jäätyä' -> frost_protection
    m = cap.match_decision("onko putki vaarassa jaatya")
    if m and m.decision_id == "frost_protection":
        OK("ASCII 'jaatya' matches keyword 'jäätyä' -> frost_protection")
    else:
        FAIL_MSG("ASCII 'jaatya' capsule match",
                 f"got {m.decision_id if m else None}")

    # 3. Removed decisions return None (varroa_treatment removed in v3.0)
    m = cap.match_decision("varroa laakitys tarvitaan")
    if m is None:
        OK("Removed decision 'varroa_treatment' correctly returns None")
    else:
        FAIL_MSG("Removed decision should be None",
                 f"got {m.decision_id if m else None}")

    # 4. ASCII 'tehtava' matches diacritic keyword 'tehtävä' -> seasonal_task
    m = cap.match_decision("mita on tehtava talvessa")
    if m and m.decision_id == "seasonal_task":
        OK("ASCII 'tehtava' matches keyword 'tehtävä' -> seasonal_task")
    else:
        FAIL_MSG("ASCII 'tehtava' capsule match",
                 f"got {m.decision_id if m else None}")

    # 5. Removed decisions return None (hive_survival removed in v3.0)
    m = cap.match_decision("mehilaiset selviaa talvesta")
    if m is None:
        OK("Removed decision 'hive_survival' correctly returns None")
    else:
        FAIL_MSG("Removed decision should be None",
                 f"got {m.decision_id if m else None}")

    # 6. Regression: remaining diacritic queries still match correctly
    regressions = [
        ("paljonko lämmitys maksaa", "heating_cost"),
        ("pakkanen voi rikkoa putki", "frost_protection"),
    ]
    reg_ok = all(
        (cap.match_decision(q) or type("X", (), {"decision_id": None})()).decision_id == exp
        for q, exp in regressions
    )
    if reg_ok:
        OK("Regression: remaining diacritic queries still match correctly")
    else:
        bad = [(q, (cap.match_decision(q) or type("X", (), {"decision_id": None})()).decision_id, e)
               for q, e in regressions
               if (cap.match_decision(q) or type("X", (), {"decision_id": None})()).decision_id != e]
        FAIL_MSG("Regression in diacritic queries", str(bad))

    # 7. Full routing: 'paljonko lammitys maksaa' routes to model_based
    r = router.route("paljonko lammitys maksaa")
    if r.layer == "model_based" and r.model == "heating_cost":
        OK("Router: 'paljonko lammitys maksaa' -> model_based/heating_cost")
    else:
        FAIL_MSG("Full routing ASCII heating query",
                 f"layer={r.layer}, model={r.model}")


def main():
    print("\n=== test_capsule_fi_normalization ===")
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
