"""End-to-end tests for Phase C: Model-based interface + Symbolic Solver.

Tests the full chain:
  Query -> SmartRouter v2 -> model_based(heating_cost) -> Solver -> ModelResult -> NL output

Also tests: ModelResult, ModelRegistry, input extraction, capsule integration.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.model_interface import ModelResult, BaseModel
from core.symbolic_solver import (
    SymbolicSolver, ModelRegistry, SolverResult,
    extract_inputs_from_query,
)
from core.domain_capsule import DomainCapsule
from core.smart_router_v2 import SmartRouterV2

OK = 0
FAIL = 0


def check(name, condition, detail=""):
    global OK, FAIL
    if condition:
        OK += 1
        print(f"  OK {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} -- {detail}")


# ── ModelResult ──────────────────────────────────────────────

print("\n=== ModelResult ===")

mr = ModelResult(
    success=True, value=6.40, unit="EUR",
    formula_used="daily_energy_kwh * spot_price_ckwh / 100",
    inputs_used={"T_outdoor": -15, "area_m2": 80},
    assumptions=["R_value=3.0 (oletus)"],
    derivation_steps=[
        {"name": "heat_loss_rate", "value": 960.0, "unit": "W", "formula": "(21-(-15))*80/3.0"},
        {"name": "daily_energy_kwh", "value": 23.04, "unit": "kWh", "formula": "960*24/1000"},
        {"name": "daily_cost", "value": 6.40, "unit": "EUR", "formula": "23.04*8/100"},
    ],
    validation=[{"check": "daily_cost < 100", "passed": True, "message": ""}],
    confidence=0.8, risk_level="normal",
)

d = mr.to_dict()
check("ModelResult.to_dict() has success", d["success"] is True)
check("ModelResult.to_dict() has value", d["value"] == 6.40)
check("ModelResult.to_dict() has unit", d["unit"] == "EUR")
check("ModelResult.to_dict() has derivation_steps", len(d["derivation_steps"]) == 3)
check("ModelResult.to_dict() has confidence", d["confidence"] == 0.8)

nl_fi = mr.to_natural_language("fi")
check("NL Finnish has Tulos", "Tulos:" in nl_fi)
check("NL Finnish has Kaava", "Kaava:" in nl_fi)
check("NL Finnish has Laskenta", "Laskenta:" in nl_fi)

nl_en = mr.to_natural_language("en")
check("NL English has Result", "Result:" in nl_en)

# Error result
mr_err = ModelResult(success=False, error="missing input X")
nl_err = mr_err.to_natural_language("fi")
check("Error NL has epaonnistui", "epaonnistui" in nl_err.lower())

nl_err_en = mr_err.to_natural_language("en")
check("Error NL EN has failed", "failed" in nl_err_en.lower())


# ── Input Extraction ─────────────────────────────────────────

print("\n=== Input Extraction ===")

inputs = extract_inputs_from_query("Paljonko lammitys maksaa kun on -15 astetta?")
check("Extract -15 astetta -> T_outdoor=-15",
      inputs.get("T_outdoor") == -15, f"got {inputs}")

inputs = extract_inputs_from_query("ulkona on -20 celsius ja talo on 120 m2")
check("Extract -20 celsius and 120 m2",
      inputs.get("T_outdoor") == -20 and inputs.get("area_m2") == 120,
      f"got {inputs}")

inputs = extract_inputs_from_query("hinta on 12 c/kwh")
check("Extract 12 c/kwh",
      inputs.get("spot_price_ckwh") == 12, f"got {inputs}")

inputs = extract_inputs_from_query("15 pakkasta")
check("Extract 15 pakkasta -> T_outdoor=-15",
      inputs.get("T_outdoor") == -15, f"got {inputs}")

inputs = extract_inputs_from_query("mita kuuluu?")
check("No inputs from unrelated query",
      len(inputs) == 0, f"got {inputs}")


# ── Solver E2E ────────────────────────────────────────────────

print("\n=== Solver E2E ===")

solver = SymbolicSolver()

# Heating cost with -15C outdoor
r = solver.solve("heating_cost", {"T_outdoor": -15})
check("Heating cost solves successfully", r.success, str(r.error))

# Expected: heat_loss = (21-(-15))*80/3.0 = 960 W
# daily_kwh = 960*24/1000 = 23.04
# daily_cost = 23.04*8/100 = 1.8432
# monthly_cost = 1.8432*30 = 55.296 (solver returns last formula)
expected_daily = round((21 - (-15)) * 80 / 3.0 * 24 / 1000 * 8 / 100, 4)
expected_monthly = round(expected_daily * 30, 4)
check(f"Monthly cost = {expected_monthly} EUR (last formula)",
      r.value is not None and abs(r.value - expected_monthly) < 0.01,
      f"got {r.value}")

# Daily cost in all_values
daily = r.all_values.get("daily_cost")
check(f"Daily cost in all_values = {expected_daily}",
      daily is not None and abs(daily - expected_daily) < 0.01,
      f"got {daily}")

check("Risk level is normal for moderate cost",
      r.risk_level == "normal", f"got {r.risk_level}")

# Test SolverResult -> ModelResult conversion
mr = r.to_model_result()
check("to_model_result() returns ModelResult",
      isinstance(mr, ModelResult))
check("ModelResult.success matches", mr.success == r.success)
check("ModelResult.value matches", mr.value == r.value)
check("ModelResult.derivation_steps populated",
      len(mr.derivation_steps) >= 3, f"got {len(mr.derivation_steps)}")


# ── solve_for_chat ────────────────────────────────────────────

print("\n=== solve_for_chat ===")

mr = solver.solve_for_chat("heating_cost",
                           "Paljonko lammitys maksaa kun on -15 astetta?")
check("solve_for_chat returns ModelResult", isinstance(mr, ModelResult))
check("solve_for_chat success", mr.success, str(mr.error))
check("solve_for_chat value = monthly_cost",
      mr.value is not None and abs(mr.value - expected_monthly) < 0.01,
      f"got {mr.value}")
check("solve_for_chat T_outdoor=-15 extracted",
      mr.inputs_used.get("T_outdoor") == -15, f"got {mr.inputs_used}")

nl = mr.to_natural_language("fi")
check("solve_for_chat NL has Tulos", "Tulos:" in nl,
      f"got {nl[:80]}")


# ── ModelRegistry from capsule ────────────────────────────────

print("\n=== ModelRegistry from capsule ===")

capsule = DomainCapsule.load("cottage")
reg = ModelRegistry.from_capsule(capsule._raw)
check("from_capsule loads heating_cost",
      reg.get("heating_cost") is not None)

all_models = reg.list_models()
check("from_capsule has 10+ models", len(all_models) >= 10,
      f"got {len(all_models)}")


# ── Full E2E Chain: Query -> Router -> Solver -> NL ───────────

print("\n=== Full E2E Chain ===")

capsule = DomainCapsule.load("cottage")
router = SmartRouterV2(capsule)
solver = SymbolicSolver()

# Use keywords that match capsule decision ("heating", "cost", "kwh")
query = "calculate heating cost kwh -15 astetta"
route = router.route(query)
check("Router routes to model_based",
      route.layer == "model_based",
      f"got {route.layer} ({route.reason})")
check("Router identifies heating_cost decision",
      route.decision_id == "heating_cost",
      f"got {route.decision_id}")

# Solve
model_id = route.model or route.decision_id
mr = solver.solve_for_chat(model_id, query)
check("E2E solve succeeds", mr.success, str(mr.error))
check(f"E2E monthly value = {expected_monthly} EUR",
      mr.value is not None and abs(mr.value - expected_monthly) < 0.01,
      f"got {mr.value}")

# Format response
response = mr.to_natural_language("fi")
check("E2E response has Tulos", "Tulos:" in response)
check("E2E response has Kaava", "Kaava:" in response)

# Verify the acceptance criterion from OPUS_TASKS.md:
# Query -> Router -> model_based (heating_cost)
# -> Solver -> monthly_cost = 55.296 EUR
# -> NL output with formula and result
check("E2E acceptance: router layer", route.layer == "model_based")
check("E2E acceptance: decision_id", route.decision_id == "heating_cost")
check("E2E acceptance: solver success", mr.success)
check("E2E acceptance: NL output complete",
      "Tulos:" in response and "Kaava:" in response and "Laskenta:" in response)

print(f"\n--- E2E output ---")
print(response)
print(f"--- end ---")


# ── Summary ──────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {OK} ok, {FAIL} fail")
if FAIL:
    sys.exit(1)
