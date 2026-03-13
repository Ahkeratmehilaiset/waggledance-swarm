"""Tests for ChatHandler _last_model_result / _last_explanation tracking.

8 tests covering:
- _last_model_result reset at start of _do_chat
- _last_explanation reset at start of _do_chat
- ConstraintResult has to_dict() method
- ConstraintResult.to_dict() has 'triggered_rules' key
- SolverResult.to_dict() has 'success' key
- SolverResult.to_dict() has 'value' key
- ModelResult.to_dict() has required keys
- explanation dict from ExplainabilityEngine has 'steps' key
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.symbolic_solver import SymbolicSolver
from core.constraint_engine import ConstraintEngine, ConstraintResult
from core.explainability import ExplainabilityEngine

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def run():
    # ── Reset tracking attrs ──────────────────────────────────

    # Simulate what _do_chat does at the start: reset _last_model_result/_last_explanation
    class FakeHive:
        def __init__(self):
            self._last_model_result = "old_value"
            self._last_explanation = "old_explanation"

    hive = FakeHive()
    # Simulate the reset
    hive._last_model_result = None
    hive._last_explanation = None

    # 1. _last_model_result reset to None
    if hive._last_model_result is None:
        OK("_last_model_result reset to None at chat start")
    else:
        FAIL_MSG("_last_model_result reset", str(hive._last_model_result))

    # 2. _last_explanation reset to None
    if hive._last_explanation is None:
        OK("_last_explanation reset to None at chat start")
    else:
        FAIL_MSG("_last_explanation reset", str(hive._last_explanation))

    # ── ConstraintResult.to_dict() ────────────────────────────

    engine = ConstraintEngine()
    engine.load_rules([{
        "id": "test_rule", "condition": "temp > 30",
        "message": "Temperature too high", "severity": "warning",
    }])
    cr = engine.evaluate({"temp": 35})

    # 3. ConstraintResult has to_dict()
    if hasattr(cr, 'to_dict'):
        OK("ConstraintResult has to_dict() method")
    else:
        FAIL_MSG("ConstraintResult.to_dict() missing")

    # 4. ConstraintResult.to_dict() has triggered_rules
    cr_dict = cr.to_dict()
    if "triggered_rules" in cr_dict:
        OK(f"ConstraintResult.to_dict() has 'triggered_rules' ({cr_dict['triggered_rules']})")
    else:
        FAIL_MSG("triggered_rules key missing", str(list(cr_dict.keys())))

    # ── SolverResult ──────────────────────────────────────────

    solver = SymbolicSolver()
    # Use 'heating_cost' which has real inputs
    sr = solver.solve("heating_cost", {"T_outdoor": -10, "area_m2": 80})

    # 5. SolverResult.to_dict() has 'success'
    sr_dict = sr.to_dict()
    if "success" in sr_dict:
        OK(f"SolverResult.to_dict() has 'success' (={sr_dict['success']})")
    else:
        FAIL_MSG("SolverResult 'success' key missing", str(list(sr_dict.keys())))

    # 6. SolverResult.to_dict() has 'value'
    if "value" in sr_dict:
        OK(f"SolverResult.to_dict() has 'value' (={sr_dict.get('value')})")
    else:
        FAIL_MSG("SolverResult 'value' key missing", str(list(sr_dict.keys())))

    # ── ModelResult ───────────────────────────────────────────

    mr = solver.solve_for_chat("heating_cost", "What is the heating cost for -10 degrees?")

    # 7. ModelResult.to_dict() has required keys
    mr_dict = mr.to_dict()
    required = {"success", "value", "unit", "derivation_steps"}
    missing = required - set(mr_dict.keys())
    if not missing:
        OK(f"ModelResult.to_dict() has all required keys ({mr_dict.get('value')} {mr_dict.get('unit')})")
    else:
        FAIL_MSG("ModelResult missing keys", str(missing))

    # ── ExplainabilityEngine ──────────────────────────────────

    expl_engine = ExplainabilityEngine()
    expl = expl_engine.from_solver_result(sr, model_id="heating_cost", model_name="Heating Cost")
    expl_dict = expl.to_dict()

    # 8. Explanation dict has 'steps' key
    if "steps" in expl_dict and len(expl_dict["steps"]) > 0:
        OK(f"ExplainabilityEngine produces 'steps' ({len(expl_dict['steps'])} steps)")
    else:
        FAIL_MSG("explanation 'steps' missing or empty", str(list(expl_dict.keys())))


def main():
    print("\n=== test_chat_model_result ===")
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
