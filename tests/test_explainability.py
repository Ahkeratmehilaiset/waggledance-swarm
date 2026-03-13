"""Tests for core/explainability.py — 6 tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.symbolic_solver import SymbolicSolver
from core.explainability import ExplainabilityEngine, Explanation, ExplanationStep

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" — {detail}" if detail else ""))


def run():
    solver = SymbolicSolver()
    engine = ExplainabilityEngine()

    # Solve battery model to get a SolverResult
    r = solver.solve("battery_discharge", {
        "capacity_mah": 3000,
        "I_active": 80,
        "I_sleep": 0.01,
        "duty_cycle": 0.01,
    })

    expl = engine.from_solver_result(r, model_name="Battery Life Calculator")

    # 1. from_solver_result creates steps
    if expl.steps:
        n = len(expl.steps)
        actions = [s.action for s in expl.steps]
        if "formula" in actions and "risk_assessment" in actions:
            OK(f"from_solver_result creates {n} steps including formula + risk_assessment")
        else:
            FAIL_MSG("Steps include formula and risk_assessment", str(actions))
    else:
        FAIL_MSG("from_solver_result creates steps", "no steps created")

    # 2. to_natural_language("fi") contains Finnish keywords
    fi_text = expl.to_natural_language("fi")
    if "Laskettu" in fi_text and "Riskiluokka" in fi_text:
        OK("to_natural_language('fi') contains 'Laskettu' and 'Riskiluokka'")
    else:
        FAIL_MSG("to_natural_language('fi') Finnish keywords", fi_text[:200])

    # 3. to_natural_language("en") contains English keywords
    en_text = expl.to_natural_language("en")
    if "Computed" in en_text and "Risk level" in en_text:
        OK("to_natural_language('en') contains 'Computed' and 'Risk level'")
    else:
        FAIL_MSG("to_natural_language('en') English keywords", en_text[:200])

    # 4. to_dict() has required keys
    d = expl.to_dict()
    required = {"model_id", "model_name", "steps", "conclusion", "risk_level"}
    missing = required - set(d.keys())
    if not missing:
        OK("to_dict() returns all required keys")
    else:
        FAIL_MSG("to_dict() required keys", str(missing))

    # 5. risk_level propagated into Explanation
    r_critical = solver.solve("hive_thermal_balance", {
        "N_bees": 5000,
        "T_outside": -25,
        "R_insulation": 0.8,
    })
    expl_crit = engine.from_solver_result(r_critical, model_name="Hive Thermal")
    if expl_crit.risk_level == "critical":
        OK("risk_level 'critical' propagated into Explanation")
    else:
        FAIL_MSG("risk_level propagated", f"got {expl_crit.risk_level}")

    # 6. Conclusion string present when value computed
    r2 = solver.solve("battery_discharge")
    expl2 = engine.from_solver_result(r2, model_name="Battery")
    if expl2.conclusion:
        OK(f"conclusion populated: '{expl2.conclusion}'")
    else:
        FAIL_MSG("conclusion populated", "empty string")


def main():
    print("\n=== test_explainability ===")
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
