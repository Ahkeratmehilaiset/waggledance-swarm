"""Tests for bee-domain axiom models: honey_yield, varroa_treatment, swarm_risk, colony_food_reserves.

12 tests covering:
- honey_yield: solve() returns success + value in kg
- honey_yield: strong colony > weak colony yield
- varroa_treatment: mite_load_pct calculation correct
- varroa_treatment: critical risk level when load > 3%
- swarm_risk: probability = 100 for crowded/old-queen/queen-cells case
- swarm_risk: probability low for spacious/new-queen case
- solve_for_chat returns ModelResult with to_natural_language
- All 4 models appear in SymbolicSolver registry
- ModelResult.to_dict() has required keys for all 4
- swarm_risk warning level triggers correctly
- colony_food_reserves: feeding_needed=0 when adequate stores
- colony_food_reserves: warning when deficit > 0
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.symbolic_solver import SymbolicSolver

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def run():
    solver = SymbolicSolver()

    # 1. honey_yield: basic solve
    sr = solver.solve("honey_yield", {"colony_strength": 60000, "flow_days": 60})
    if sr.success and sr.value is not None and sr.unit == "kg":
        OK(f"honey_yield: success, value={sr.value:.1f} {sr.unit}")
    else:
        FAIL_MSG("honey_yield solve", f"success={sr.success}, value={sr.value}, unit={sr.unit}, err={sr.error}")

    # 2. honey_yield: strong > weak colony
    sr_strong = solver.solve("honey_yield", {"colony_strength": 60000, "flow_days": 60})
    sr_weak = solver.solve("honey_yield", {"colony_strength": 10000, "flow_days": 20})
    if sr_strong.success and sr_weak.success and sr_strong.value > sr_weak.value:
        OK(f"honey_yield: strong ({sr_strong.value:.1f} kg) > weak ({sr_weak.value:.1f} kg)")
    else:
        FAIL_MSG("honey_yield strong > weak", f"{sr_strong.value} vs {sr_weak.value}")

    # 3. varroa_treatment: mite load % correct
    # mite_load_pct = (1200/25000)*100 = 4.8
    sr_vt = solver.solve("varroa_treatment", {"varroa_before": 1200, "colony_strength": 25000})
    if sr_vt.success and abs(sr_vt.value - 4.8) < 0.01:
        OK(f"varroa_treatment: mite_load_pct={sr_vt.value:.2f}%")
    else:
        FAIL_MSG("varroa_treatment mite_load_pct", f"got {sr_vt.value}, expected 4.8")

    # 4. varroa_treatment: critical risk at high mite load
    sr_crit = solver.solve("varroa_treatment", {"varroa_before": 5000, "colony_strength": 20000})
    if sr_crit.success and sr_crit.risk_level == "critical":
        OK(f"varroa_treatment: critical risk at {sr_crit.value:.1f}% mite load")
    else:
        FAIL_MSG("varroa_treatment critical risk", f"risk_level={sr_crit.risk_level}, value={sr_crit.value}")

    # 5. swarm_risk: crowded/old-queen case → probability=100
    sr_high = solver.solve("swarm_risk", {
        "empty_combs": 1, "total_combs": 12, "queen_age_years": 2.5,
        "queen_cells": 3, "season_factor": 1.0
    })
    if sr_high.success and sr_high.value == 100:
        OK(f"swarm_risk: crowded+old queen+cells = {sr_high.value}%")
    else:
        FAIL_MSG("swarm_risk crowded case", f"got {sr_high.value}")

    # 6. swarm_risk: spacious/new-queen case → probability low
    sr_low = solver.solve("swarm_risk", {
        "empty_combs": 8, "total_combs": 12, "queen_age_years": 0.5,
        "queen_cells": 0, "season_factor": 0.1
    })
    if sr_low.success and sr_low.value < 30:
        OK(f"swarm_risk: spacious+new queen = {sr_low.value:.1f}%")
    else:
        FAIL_MSG("swarm_risk low case", f"got {sr_low.value}")

    # 7. solve_for_chat returns natural language
    mr = solver.solve_for_chat("honey_yield", "kuinka paljon hunajaa saan 50000 mehiläiseltä")
    nl = mr.to_natural_language("fi")
    if mr.success and "kg" in nl and len(nl) > 20:
        OK(f"honey_yield.solve_for_chat: '{nl[:60]}'")
    else:
        FAIL_MSG("honey_yield solve_for_chat", nl[:80] if nl else "empty")

    # 8. All 4 models in solver registry
    needed = {"honey_yield", "varroa_treatment", "swarm_risk", "colony_food_reserves"}
    # Check by trying to solve each
    found = set()
    for model_id in needed:
        sr_test = solver.solve(model_id, {})
        if sr_test.success or sr_test.error is None or "not found" not in str(sr_test.error or ""):
            found.add(model_id)
    if len(found) == 4:
        OK(f"All 4 bee models in registry: {found}")
    else:
        FAIL_MSG("Bee models in registry", f"found={found}")

    # 9. ModelResult.to_dict() has required keys
    mr2 = solver.solve_for_chat("varroa_treatment", "how many mites after treatment")
    d = mr2.to_dict()
    required = {"success", "value", "unit", "derivation_steps"}
    missing = required - set(d.keys())
    if not missing:
        OK(f"varroa_treatment ModelResult.to_dict() has required keys (value={d.get('value')} {d.get('unit')})")
    else:
        FAIL_MSG("ModelResult.to_dict() missing keys", str(missing))

    # 10. swarm_risk warning level
    sr_warn = solver.solve("swarm_risk", {
        "empty_combs": 3, "total_combs": 12, "queen_age_years": 1.5,
        "queen_cells": 1, "season_factor": 0.7
    })
    # base = (1-3/12)*40 + (1.5/3)*30 + 1*20 + 0.7*10 = 30 + 15 + 20 + 7 = 72 → warning
    if sr_warn.success and sr_warn.risk_level in ("warning", "critical"):
        OK(f"swarm_risk: warning/critical at {sr_warn.value:.1f}% (risk={sr_warn.risk_level})")
    else:
        FAIL_MSG("swarm_risk warning level", f"risk={sr_warn.risk_level}, value={sr_warn.value}")

    # 11. colony_food_reserves: adequate stores → feeding_needed=0
    sr_food_ok = solver.solve("colony_food_reserves",
                              {"bee_cluster_kg": 2.5, "food_available_kg": 15, "winter_months": 5})
    if sr_food_ok.success and sr_food_ok.value == 0.0 and sr_food_ok.risk_level == "normal":
        OK(f"colony_food_reserves: adequate stores → feeding_needed=0, risk=normal")
    else:
        FAIL_MSG("colony_food_reserves adequate", f"value={sr_food_ok.value}, risk={sr_food_ok.risk_level}")

    # 12. colony_food_reserves: insufficient stores → warning
    sr_food_low = solver.solve("colony_food_reserves",
                               {"bee_cluster_kg": 1.5, "food_available_kg": 5, "winter_months": 5})
    if sr_food_low.success and sr_food_low.value > 0 and sr_food_low.risk_level in ("warning", "critical"):
        OK(f"colony_food_reserves: insufficient stores → feeding_needed={sr_food_low.value:.2f} kg, risk={sr_food_low.risk_level}")
    else:
        FAIL_MSG("colony_food_reserves insufficient", f"value={sr_food_low.value}, risk={sr_food_low.risk_level}")


def main():
    print("\n=== test_bee_axioms ===")
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
