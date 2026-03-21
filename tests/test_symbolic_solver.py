"""Tests for core/symbolic_solver.py — 12 tests."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.symbolic_solver import ModelRegistry, SymbolicSolver

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

    # 1. Registry loads all 10 axiom models
    models = solver.registry.list_models()
    expected_ids = {
        "battery_discharge", "signal_propagation",
        "hive_thermal_balance", "heating_cost", "solar_yield",
        "pipe_freezing", "heat_pump_cop", "comfort_energy_tradeoff",
        "oee_decomposition", "mtbf_prediction",
    }
    missing = expected_ids - set(models)
    if not missing:
        OK("ModelRegistry loads all 10 axiom models")
    else:
        FAIL_MSG("ModelRegistry loads all 10 axiom models", f"missing: {missing}")

    # 2. Unknown model returns error result
    r = solver.solve("nonexistent_model_xyz")
    if not r.success and r.error and "not found" in r.error.lower():
        OK("Unknown model returns error with message")
    else:
        FAIL_MSG("Unknown model returns error with message", str(r))

    # 3. Battery: ESP32 example -> ~3743 h
    r = solver.solve("battery_discharge", {
        "capacity_mah": 3000,
        "I_active": 80,
        "I_sleep": 0.01,
        "duty_cycle": 0.01,
    })
    if r.success and r.value is not None:
        diff = abs(r.value - 3704)
        if diff < 5:
            OK(f"Battery ESP32 example ~3704 h (got {r.value:.1f})")
        else:
            FAIL_MSG("Battery ESP32 example ~3704 h", f"got {r.value:.1f}")
    else:
        FAIL_MSG("Battery ESP32 example", str(r.error))

    # 4. Battery defaults-only run succeeds
    r = solver.solve("battery_discharge")
    if r.success and r.value is not None and r.value > 0:
        OK(f"Battery defaults-only run succeeds (value={r.value:.1f})")
    else:
        FAIL_MSG("Battery defaults-only run succeeds", str(r.error))

    # 5. Assumptions list populated when using defaults
    r = solver.solve("battery_discharge")
    if r.success and len(r.assumptions) >= 4:
        OK(f"Assumptions list has {len(r.assumptions)} entries for all-default run")
    else:
        FAIL_MSG("Assumptions list populated", str(r.assumptions))

    # 6. OEE with good values -> normal risk
    r = solver.solve("oee_decomposition", {
        "planned_time": 480,
        "downtime": 20,
        "actual_output": 850,
        "ideal_cycle_rate": 2.0,
        "good_units": 840,
        "total_units": 850,
    })
    if r.success and r.risk_level == "normal":
        OK(f"OEE good inputs -> normal risk (oee~{r.all_values.get('oee', '?'):.3f})")
    else:
        FAIL_MSG("OEE good inputs -> normal risk", f"risk={r.risk_level}, err={r.error}")

    # 7. Hive thermal — weak colony, harsh winter -> critical
    r = solver.solve("hive_thermal_balance", {
        "N_bees": 5000,
        "T_outside": -25,
        "R_insulation": 0.8,
    })
    if r.success and r.risk_level == "critical":
        OK(f"Hive thermal weak colony critical (steady_state~{r.all_values.get('steady_state_temp', '?'):.1f}C)")
    else:
        FAIL_MSG("Hive thermal weak colony -> critical", f"risk={r.risk_level}, err={r.error}")

    # 8. MTBF calculation
    r = solver.solve("mtbf_prediction", {
        "total_operating_hours": 10000,
        "number_of_failures": 5,
    })
    if r.success and r.all_values.get("mtbf") == 2000.0:
        OK("MTBF 10000h / 5 failures = 2000 h")
    else:
        FAIL_MSG("MTBF calculation", f"got {r.all_values.get('mtbf')}, err={r.error}")

    # 9. Range warning when value out of bounds
    r = solver.solve("battery_discharge", {
        "capacity_mah": 3000,
        "I_active": 80,
        "I_sleep": 0.01,
        "duty_cycle": 2.0,   # exceeds max 1.0
    })
    has_warning = any("duty_cycle" in w for w in r.warnings)
    if has_warning:
        OK("Out-of-range input generates warning")
    else:
        FAIL_MSG("Out-of-range input generates warning", str(r.warnings))

    # 10. Validation checks in result
    r = solver.solve("battery_discharge", {
        "capacity_mah": 3000,
        "I_active": 80,
        "I_sleep": 0.01,
        "duty_cycle": 0.01,
    })
    if r.validation and all("passed" in v for v in r.validation):
        passed_count = sum(1 for v in r.validation if v["passed"])
        OK(f"Validation checks present, {passed_count}/{len(r.validation)} passed")
    else:
        FAIL_MSG("Validation checks in result", str(r.validation))

    # 11. Compute time under 10 ms
    r = solver.solve("heat_pump_cop")
    if r.compute_time_ms < 10.0:
        OK(f"Compute time < 10 ms (got {r.compute_time_ms:.2f} ms)")
    else:
        FAIL_MSG("Compute time < 10 ms", f"{r.compute_time_ms:.2f} ms")

    # 12. to_dict() returns all required keys
    r = solver.solve("battery_discharge")
    d = r.to_dict()
    required = {"success", "value", "unit", "all_values", "formulas_used",
                "inputs_used", "assumptions", "derivation_steps",
                "validation", "risk_level", "compute_time_ms", "warnings"}
    missing_keys = required - set(d.keys())
    if not missing_keys:
        OK("to_dict() returns all required keys")
    else:
        FAIL_MSG("to_dict() returns all required keys", str(missing_keys))


def main():
    print("\n=== test_symbolic_solver ===")
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
