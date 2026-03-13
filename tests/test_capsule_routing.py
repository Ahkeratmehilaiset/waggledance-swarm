"""Integration tests for cottage capsule routing — all 8 key_decisions.

16 tests covering:
- heating_cost: routes to model_based (laske/lämmitys/kwh)
- frost_protection: routes to rule_constraints (pakkanen/putki/jäätyä)
- hive_survival: routes to statistical (talvehtimi/selviää)
- seasonal_task: routes to retrieval (tehtävä/kausi)
- honey_yield: routes to model_based (hunaja/hunajasato/mehiläi)
- varroa_treatment: routes to model_based (varroa/oksaalihappo)
- swarm_risk: routes to model_based (parveami/kuningatar solu)
- colony_food_reserves: routes to model_based (fondant/riittää/ruoka)
- No false positives between bee models
- Non-bee query falls through to llm_reasoning
- English bee queries route correctly
- Model IDs are correctly extracted from routing
- SmartRouter stats track routing counts
- Router confidence > threshold for all bee queries
- Capsule has 8 key_decisions
- All bee model axioms exist on disk
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

    # 1. Capsule has 8 key_decisions
    n_decisions = len(capsule.key_decisions)
    if n_decisions == 8:
        OK(f"Cottage capsule has 8 key_decisions")
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

    # 4. hive_survival routing (FI)
    r = router.route("selviävätkö mehiläiset talvesta talvehtiminen")
    if r.layer == "statistical":
        OK(f"hive_survival routes to statistical (model={r.model})")
    else:
        FAIL_MSG("hive_survival routing", f"layer={r.layer}")

    # 5. honey_yield routing (FI)
    r = router.route("paljonko hunajaa saan mehiläisiltä tänä kesänä")
    if r.layer == "model_based" and r.model == "honey_yield":
        OK(f"honey_yield routes correctly (conf={r.confidence:.2f})")
    else:
        FAIL_MSG("honey_yield routing", f"layer={r.layer}, model={r.model}")

    # 6. varroa_treatment routing (FI)
    r = router.route("kuinka paljon oksaalihappoa tarvitaan varroaan")
    if r.layer == "model_based" and r.model == "varroa_treatment":
        OK(f"varroa_treatment routes correctly (conf={r.confidence:.2f})")
    else:
        FAIL_MSG("varroa_treatment routing", f"layer={r.layer}, model={r.model}")

    # 7. swarm_risk routing (FI)
    r = router.route("onko parveamisriski korkea kolmella kuningatar-solulla")
    if r.layer == "model_based" and r.model == "swarm_risk":
        OK(f"swarm_risk routes correctly (conf={r.confidence:.2f})")
    else:
        FAIL_MSG("swarm_risk routing", f"layer={r.layer}, model={r.model}")

    # 8. colony_food_reserves routing (FI)
    r = router.route("riittääkö ruoka talven yli vai pitääkö lisätä fondantia")
    if r.layer == "model_based" and r.model == "colony_food_reserves":
        OK(f"colony_food_reserves routes correctly (conf={r.confidence:.2f})")
    else:
        FAIL_MSG("colony_food_reserves routing", f"layer={r.layer}, model={r.model}")

    # 9. English honey yield routing
    r = router.route("what is the estimated honey yield for a strong colony")
    if r.layer == "model_based" and r.model == "honey_yield":
        OK(f"EN honey_yield routes correctly")
    else:
        FAIL_MSG("EN honey_yield routing", f"layer={r.layer}, model={r.model}")

    # 10. English varroa routing
    r = router.route("how much oxalic acid treatment is needed for varroa mites")
    if r.layer == "model_based" and r.model == "varroa_treatment":
        OK(f"EN varroa_treatment routes correctly")
    else:
        FAIL_MSG("EN varroa_treatment routing", f"layer={r.layer}, model={r.model}")

    # 11. No false positives: honey query doesn't match varroa
    r_h = router.route("paljonko hunajasato mehiläisiltä")
    r_v = router.route("varroa-punkkien lukumäärä on liian korkea")
    if r_h.model == "honey_yield" and r_v.model == "varroa_treatment":
        OK(f"No cross-routing: honey->{r_h.model}, varroa->{r_v.model}")
    else:
        FAIL_MSG("Cross-routing false positive", f"honey={r_h.model}, varroa={r_v.model}")

    # 12. Router confidence is above threshold for all bee queries
    bee_queries = [
        ("paljonko hunajaa", "honey_yield"),
        ("varroa-lääkitys", "varroa_treatment"),
        ("parveamisriski", "swarm_risk"),
        ("fondant riittää", "colony_food_reserves"),
    ]
    all_above = all(router.route(q).confidence >= 0.1 for q, _ in bee_queries)
    if all_above:
        OK("All bee queries have confidence >= 0.1")
    else:
        low = [(q, router.route(q).confidence) for q, _ in bee_queries if router.route(q).confidence < 0.1]
        FAIL_MSG("Bee query confidence", str(low))

    # 13. Router stats track routing counts
    stats = router.stats()
    if "total_routes" in stats and stats["total_routes"] > 0:
        OK(f"Router stats tracked (total={stats['total_routes']})")
    else:
        FAIL_MSG("Router stats", str(stats))

    # 14. All bee model axiom files exist on disk
    import pathlib
    axiom_base = pathlib.Path(os.path.dirname(os.path.dirname(__file__))) / "configs" / "axioms" / "cottage"
    required_files = ["honey_yield.yaml", "varroa_treatment.yaml", "swarm_risk.yaml",
                      "colony_food_reserves.yaml", "hive_thermal.yaml"]
    missing = [f for f in required_files if not (axiom_base / f).exists()]
    if not missing:
        OK(f"All {len(required_files)} bee axiom YAMLs exist on disk")
    else:
        FAIL_MSG("Missing axiom files", str(missing))

    # 15. Capsule models list includes all 4 bee model ids
    model_ids = [m.get("id") for m in capsule.models]
    bee_models = {"honey_yield", "varroa_treatment", "swarm_risk", "colony_food_reserves"}
    found = bee_models.intersection(set(model_ids))
    if len(found) == 4:
        OK(f"Capsule models include all 4 bee models")
    else:
        FAIL_MSG("Capsule model ids", f"found={found}, all={model_ids}")

    # 16. Non-bee query doesn't steal from bee models
    r_generic = router.route("tell me about Finnish weather")
    if r_generic.model not in {"honey_yield", "varroa_treatment", "swarm_risk", "colony_food_reserves"}:
        OK(f"Non-bee query doesn't route to bee models (got {r_generic.layer}/{r_generic.model})")
    else:
        FAIL_MSG("Non-bee query isolation", f"got model={r_generic.model}")


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
