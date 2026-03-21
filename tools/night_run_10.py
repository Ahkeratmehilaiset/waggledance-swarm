"""Run 10 night learning cycles and report results."""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, ".")

from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline


def _make_case(goal_type, cap_id, grade, profile, verifier_passed, residuals):
    case = CaseTrajectory(
        goal=Goal(type=goal_type, description=f"night {goal_type.value}"),
        selected_capabilities=[
            CapabilityContract(capability_id=cap_id, category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": verifier_passed, "confidence": 0.85},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def generate_cases(cycle_num: int, base_count: int = 25) -> list:
    combos = [
        (GoalType.OBSERVE, "solve.math", QualityGrade.GOLD, "APIARY", True, {"temp": 0.3, "humidity": 0.1}),
        (GoalType.DIAGNOSE, "solve.symbolic", QualityGrade.SILVER, "HOME", False, {"weight": 0.7, "load": 0.2}),
        (GoalType.PROTECT, "solve.constraints", QualityGrade.BRONZE, "FACTORY", True, {"pressure": 0.4}),
        (GoalType.OPTIMIZE, "optimize.schedule", QualityGrade.GOLD, "GADGET", False, {"latency": 0.9, "cpu": 0.5}),
        (GoalType.PLAN, "retrieve.hot_cache", QualityGrade.SILVER, "APIARY", True, {"mem": 0.6}),
        (GoalType.ACT, "detect.anomaly", QualityGrade.GOLD, "HOME", True, {"temp": 0.2, "voltage": 0.1}),
        (GoalType.VERIFY, "verify.checksum", QualityGrade.QUARANTINE, "FACTORY", False, {"drift": 1.2}),
    ]
    cases = []
    n = base_count + cycle_num * 2  # slightly more each cycle
    for i in range(n):
        gt, cap, grade, profile, vp, res = combos[i % len(combos)]
        cases.append(_make_case(gt, cap, grade, profile, vp, res))
    return cases


def main():
    print("=" * 60)
    print("  Night Learning — 10 Cycles")
    print("=" * 60)

    pipeline = NightLearningPipeline(profile="APIARY")
    all_results = []
    errors_found = []

    for cycle in range(1, 11):
        print(f"\n--- Cycle {cycle}/10 ---")
        cases = generate_cases(cycle)
        t0 = time.time()

        result = pipeline.run_cycle(day_cases=cases)
        elapsed = time.time() - t0

        status = "OK" if result.success else "ERRORS"
        print(f"  Status:    {status}")
        print(f"  Cases:     {result.cases_built} built, {result.cases_graded} graded")
        print(f"  Quality:   gold={result.gold_count} silver={result.silver_count} "
              f"bronze={result.bronze_count} quarantine={result.quarantine_count}")
        print(f"  Training:  {result.models_trained} models trained")
        print(f"  Canaries:  {result.canaries_evaluated} evaluated {result.canary_results}")
        print(f"  Procedural: {result.procedures_learned} learned, "
              f"{result.anti_patterns_learned} anti-patterns")
        print(f"  Duration:  {elapsed:.2f}s")

        if result.errors:
            for e in result.errors:
                print(f"  ERROR: {e}")
                errors_found.append((cycle, e))

        all_results.append(result.to_dict())

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    total_cases = sum(r["cases_built"] for r in all_results)
    total_trained = sum(r["models_trained"] for r in all_results)
    total_gold = sum(r["quality"]["gold"] for r in all_results)
    total_errors = len(errors_found)
    ok_cycles = sum(1 for r in all_results if r["success"])

    print(f"  Cycles:     {ok_cycles}/10 OK")
    print(f"  Total cases: {total_cases}")
    print(f"  Total gold:  {total_gold}")
    print(f"  Models trained: {total_trained}")
    print(f"  Errors:     {total_errors}")

    if errors_found:
        print("\n  Error details:")
        for cycle, err in errors_found:
            print(f"    Cycle {cycle}: {err}")

    # Save results
    with open("data/night_run_10_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to data/night_run_10_results.json")

    stats = pipeline.stats()
    print(f"\n  Pipeline stats: {json.dumps(stats, indent=2, default=str)}")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
