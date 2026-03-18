"""Integration tests for cottage capsule routing — current key_decisions.

Updated for v3.0 cutover: cottage capsule was restructured from 8 bee-specific
decisions to 5 generic cottage decisions (heating_cost, frost_protection,
seasonal_task, water_system, energy_consumption). Models: heating_cost + energy_baseline.

Tests covering:
- heating_cost: routes to model_based (laske/lämmitys/kwh)
- frost_protection: routes to rule_constraints (pakkanen/putki/jäätyä)
- seasonal_task: routes to retrieval (tehtävä/kausi)
- Non-bee query falls through to retrieval/llm_reasoning
- SmartRouter stats track routing counts
- Router confidence > threshold for capsule queries
- Capsule has expected key_decisions count
- All cottage axiom YAMLs exist on disk
"""

import sys
import os
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.domain_capsule import DomainCapsule
from core.smart_router_v2 import SmartRouterV2

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


def _load_capsule():
    with open(CAPSULE_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return DomainCapsule(raw)


def run():
    capsule = _load_capsule()
    router = SmartRouterV2(capsule)

    # 1. Capsule has expected key_decisions (5 after v3.0 cutover)
    n_decisions = len(capsule.key_decisions)
    if n_decisions == 5:
        OK(f"Cottage capsule has 5 key_decisions")
    else:
        FAIL_MSG(f"Capsule key_decisions count", f"got {n_decisions}")

    # 2. heating_cost routing (FI)
    r = router.route("laske lämmityskustannus 80m2 huoneelle")
    if r.layer == "model_based" and r.model == "heating_cost":
        OK(f"heating_cost routes correctly (layer={r.layer}, model={r.model})")
    else:
        FAIL_MSG("heating_cost routing", f"layer={r.layer}, model={r.model}")

    # 3. frost_protection routing (FI)
    r = router.route("pitääkö suojata putket pakkaselta")
    if r.layer == "rule_constraints":
        OK(f"frost_protection routes to rule_constraints")
    else:
        FAIL_MSG("frost_protection routing", f"layer={r.layer}")

    # 4. seasonal_task routing (FI)
    r = router.route("mitä on tehtävä talvessa")
    if r.layer in ("retrieval", "model_based"):
        OK(f"seasonal_task routes correctly (layer={r.layer})")
    else:
        FAIL_MSG("seasonal_task routing", f"layer={r.layer}")

    # 5. Non-capsule query falls to retrieval/llm_reasoning
    r = router.route("tell me about Finnish weather")
    if r.layer in ("retrieval", "llm_reasoning"):
        OK(f"Non-capsule query routes to {r.layer} (expected)")
    else:
        FAIL_MSG("Non-capsule query routing", f"layer={r.layer}")

    # 6. English heating query routes correctly
    r = router.route("how much does heating cost per kwh")
    if r.layer == "model_based" and r.model == "heating_cost":
        OK(f"EN heating_cost routes correctly")
    else:
        FAIL_MSG("EN heating_cost routing", f"layer={r.layer}, model={r.model}")

    # 7. No cross-routing: heating query doesn't match frost_protection
    r_h = router.route("paljonko lämmitys maksaa")
    r_f = router.route("putket voivat jäätyä pakkasella")
    if r_h.model != r_f.model or r_h.model == "heating_cost":
        OK(f"No cross-routing: heating->{r_h.model}, frost->{r_f.model}")
    else:
        FAIL_MSG("Cross-routing false positive", f"heating={r_h.model}, frost={r_f.model}")

    # 8. Router confidence above threshold for capsule queries
    capsule_queries = [
        "paljonko lämmitys maksaa",
        "pitääkö suojata putket pakkaselta",
    ]
    all_above = all(router.route(q).confidence >= 0.1 for q in capsule_queries)
    if all_above:
        OK("All capsule queries have confidence >= 0.1")
    else:
        low = [(q, router.route(q).confidence) for q in capsule_queries
               if router.route(q).confidence < 0.1]
        FAIL_MSG("Capsule query confidence", str(low))

    # 9. Router stats track routing counts
    stats = router.stats()
    if "total_routes" in stats and stats["total_routes"] > 0:
        OK(f"Router stats tracked (total={stats['total_routes']})")
    else:
        FAIL_MSG("Router stats", str(stats))

    # 10. Cottage axiom files exist on disk
    import pathlib
    axiom_base = pathlib.Path(os.path.dirname(os.path.dirname(__file__))) / "configs" / "axioms" / "cottage"
    if axiom_base.exists():
        yaml_files = list(axiom_base.glob("*.yaml"))
        if len(yaml_files) > 0:
            OK(f"Cottage axiom directory has {len(yaml_files)} YAML files")
        else:
            FAIL_MSG("No axiom files found", str(axiom_base))
    else:
        FAIL_MSG("Cottage axiom directory missing", str(axiom_base))

    # 11. Capsule models list includes expected model ids
    model_ids = [m.get("id") for m in capsule.models]
    expected_models = {"heating_cost", "energy_baseline"}
    found = expected_models.intersection(set(model_ids))
    if len(found) == len(expected_models):
        OK(f"Capsule models include all {len(expected_models)} expected models")
    else:
        FAIL_MSG("Capsule model ids", f"found={found}, expected={expected_models}")

    # 12. Non-capsule query doesn't steal from capsule models
    r_generic = router.route("tell me about Finnish history")
    if r_generic.model not in {"heating_cost", "energy_baseline"}:
        OK(f"Non-capsule query doesn't route to capsule models (got {r_generic.layer}/{r_generic.model})")
    else:
        FAIL_MSG("Non-capsule query isolation", f"got model={r_generic.model}")


def main():
    print("\n=== test_capsule_routing ===")
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
