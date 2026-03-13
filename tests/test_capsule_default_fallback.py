"""Tests for capsule default_fallback routing (v1.13.0).

5 tests covering:
- capsule.default_fallback is 'llm_reasoning' when set in YAML
- Unknown query (no keywords, no capsule match) routes to llm_reasoning
- Known queries still route correctly (fallback not triggered)
- Capsule without default_fallback field defaults to 'llm_reasoning'
- Disabled default_fallback layer falls through to priority-based fallback
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


def _capsule(overrides: dict | None = None):
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if overrides:
        raw.update(overrides)
    return DomainCapsule(raw)


def run():
    # 1. capsule.default_fallback parsed correctly from YAML
    cap = _capsule()
    if cap.default_fallback == "llm_reasoning":
        OK("capsule.default_fallback == 'llm_reasoning' (set in cottage.yaml)")
    else:
        FAIL_MSG("default_fallback not parsed", f"got '{cap.default_fallback}'")

    # 2. Unknown query (no keywords, no capsule match) -> llm_reasoning
    router = SmartRouterV2(cap)
    r = router.route("viita rame sammalpinta turvesuo")  # bog/mire terms, no keywords
    if r.layer == "llm_reasoning":
        OK("Unknown query routes to llm_reasoning (not rule_constraints)")
    else:
        FAIL_MSG("Unknown query fallback", f"got {r.layer} (expected llm_reasoning)")

    # 3. Known query still routes correctly (fallback not triggered)
    r = router.route("pitaako suojata putket pakkaselta")
    if r.layer == "rule_constraints":
        OK("Known query ('pitaako') still routes to rule_constraints")
    else:
        FAIL_MSG("Known query regression", f"got {r.layer}")

    # 4. Capsule without default_fallback field defaults to 'llm_reasoning'
    cap_nofield = _capsule(overrides={"default_fallback": None})
    # When None, data.get("default_fallback", "llm_reasoning") returns None,
    # but SmartRouterV2 handles disabled layer by falling through to priority list.
    # More useful test: capsule with no field at all (pop from dict)
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw2 = yaml.safe_load(f)
    raw2.pop("default_fallback", None)
    cap_nofield2 = DomainCapsule(raw2)
    if cap_nofield2.default_fallback == "llm_reasoning":
        OK("Capsule without default_fallback field defaults to 'llm_reasoning'")
    else:
        FAIL_MSG("default_fallback missing field default",
                 f"got '{cap_nofield2.default_fallback}'")

    # 5. Disabled default_fallback falls through to priority-based fallback
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw3 = yaml.safe_load(f)
    raw3["default_fallback"] = "retrieval"
    raw3["layers"]["retrieval"]["enabled"] = False  # disable it
    cap_disabled = DomainCapsule(raw3)
    router2 = SmartRouterV2(cap_disabled)
    r2 = router2.route("viita rame sammalpinta turvesuo")
    # retrieval disabled -> falls through to priority[0] = rule_constraints
    if r2.layer != "retrieval":
        OK(f"Disabled default_fallback falls through to priority list (got {r2.layer})")
    else:
        FAIL_MSG("Disabled fallback should not route to disabled layer",
                 f"got {r2.layer}")


def main():
    print("\n=== test_capsule_default_fallback ===")
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
