"""Tests for core/constraint_engine.py — 8 tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.constraint_engine import ConstraintEngine

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" — {detail}" if detail else ""))


RULES = [
    {
        "id": "low_battery",
        "conditions": [{"field": "battery_pct", "op": "<", "value": 20}],
        "logic": "AND",
        "severity": "warning",
        "message": "Battery low",
    },
    {
        "id": "high_temp_critical",
        "conditions": [{"field": "temperature", "op": ">", "value": 60}],
        "logic": "AND",
        "severity": "critical",
        "message": "Temperature critical",
    },
    {
        "id": "combined_stress",
        "conditions": [
            {"field": "battery_pct", "op": "<", "value": 30},
            {"field": "signal_dbm", "op": "<", "value": -90},
        ],
        "logic": "AND",
        "severity": "warning",
        "message": "Low battery AND poor signal",
    },
    {
        "id": "any_sensor_offline",
        "conditions": [
            {"field": "sensor_a_ok", "op": "==", "value": False},
            {"field": "sensor_b_ok", "op": "==", "value": False},
        ],
        "logic": "OR",
        "severity": "info",
        "message": "At least one sensor offline",
    },
]


def run():
    engine = ConstraintEngine()
    engine.load_rules(RULES)

    # 1. Triggered when condition met
    result = engine.evaluate({"battery_pct": 10, "temperature": 25})
    triggered_ids = [r.rule_id for r in result.triggered_rules]
    if "low_battery" in triggered_ids:
        OK("Rule triggers when condition met (battery < 20)")
    else:
        FAIL_MSG("Rule triggers when condition met", str(triggered_ids))

    # 2. Not triggered when condition not met
    result = engine.evaluate({"battery_pct": 80, "temperature": 25})
    triggered_ids = [r.rule_id for r in result.triggered_rules]
    if "low_battery" not in triggered_ids:
        OK("Rule does not trigger when condition not met")
    else:
        FAIL_MSG("Rule does not trigger when condition not met", str(triggered_ids))

    # 3. AND logic: both conditions required
    result = engine.evaluate({"battery_pct": 20, "signal_dbm": -80})  # only battery < 30, signal ok
    triggered_ids = [r.rule_id for r in result.triggered_rules]
    if "combined_stress" not in triggered_ids:
        OK("AND logic: not triggered when only one condition met")
    else:
        FAIL_MSG("AND logic: not triggered when only one condition met", str(triggered_ids))

    result = engine.evaluate({"battery_pct": 20, "signal_dbm": -95})  # both met
    triggered_ids = [r.rule_id for r in result.triggered_rules]
    if "combined_stress" in triggered_ids:
        OK("AND logic: triggered when both conditions met")
    else:
        FAIL_MSG("AND logic: triggered when both conditions met", str(triggered_ids))

    # 4. OR logic: one condition sufficient
    result = engine.evaluate({"sensor_a_ok": False, "sensor_b_ok": True})
    triggered_ids = [r.rule_id for r in result.triggered_rules]
    if "any_sensor_offline" in triggered_ids:
        OK("OR logic: triggered when one of two conditions met")
    else:
        FAIL_MSG("OR logic: triggered when one of two conditions met", str(triggered_ids))

    # 5. Highest severity is critical when critical rule triggered
    result = engine.evaluate({"battery_pct": 5, "temperature": 70})
    if result.highest_severity == "critical":
        OK(f"Highest severity is critical (triggered: {[r.rule_id for r in result.triggered_rules]})")
    else:
        FAIL_MSG("Highest severity is critical", f"got {result.highest_severity}")

    # 6. to_natural_language("fi") for triggered rule
    result = engine.evaluate({"battery_pct": 5, "temperature": 25})
    triggered = result.triggered_rules[0]
    fi_text = triggered.to_natural_language("fi")
    if "LAUKAISI" in fi_text and "low_battery" in fi_text:
        OK(f"to_natural_language('fi') contains 'LAUKAISI': {fi_text[:60]}")
    else:
        FAIL_MSG("to_natural_language('fi') contains 'LAUKAISI'", fi_text)

    # 7. NOT condition (using nested dict)
    not_rule = [{
        "id": "not_ok",
        "conditions": [{"NOT": {"field": "status", "op": "==", "value": "ok"}}],
        "logic": "AND",
        "severity": "warning",
        "message": "Status is not ok",
    }]
    engine2 = ConstraintEngine()
    engine2.load_rules(not_rule)
    result_not_ok = engine2.evaluate({"status": "error"})
    result_ok = engine2.evaluate({"status": "ok"})
    if result_not_ok.triggered_rules and not result_ok.triggered_rules:
        OK("NOT condition: triggers when negated condition false, not when true")
    else:
        FAIL_MSG(
            "NOT condition",
            f"not_ok={bool(result_not_ok.triggered_rules)}, ok={bool(result_ok.triggered_rules)}",
        )

    # 8. ConstraintResult.to_natural_language with no triggers
    result_clean = engine.evaluate({"battery_pct": 90, "temperature": 30,
                                    "sensor_a_ok": True, "sensor_b_ok": True})
    fi_clean = result_clean.to_natural_language("fi")
    if "OK" in fi_clean and not result_clean.triggered_rules:
        OK("ConstraintResult.to_natural_language('fi') all-clear message")
    else:
        FAIL_MSG("ConstraintResult all-clear message", fi_clean)


def main():
    print("\n=== test_constraint_engine ===")
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
