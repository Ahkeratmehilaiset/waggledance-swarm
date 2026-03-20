"""
Production run -- long-duration night learning with monitoring.

Runs repeated learning cycles at intervals, tracking:
- Accuracy trends per model
- Memory usage
- Quality grade distribution shifts
- Error rates and recovery
- Procedural memory growth

Usage:
    python tools/production_run.py --hours 5
    python tools/production_run.py --hours 5 --interval 180
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone

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
from waggledance.core.specialist_models.specialist_trainer import SPECIALIST_MODELS

# -- Case generation -----------------------------------------

GOAL_TYPES = list(GoalType)
CAPABILITIES = [
    "solve.math", "solve.symbolic", "solve.constraints",
    "optimize.schedule", "retrieve.hot_cache", "detect.anomaly",
    "verify.checksum", "sense.temperature", "normalize.units",
    "explain.reasoning", "predict.trend", "plan.multi_step",
]
PROFILES = ["APIARY", "HOME", "FACTORY", "GADGET", "COTTAGE"]
GRADES = [QualityGrade.GOLD, QualityGrade.SILVER, QualityGrade.BRONZE, QualityGrade.QUARANTINE]
GRADE_WEIGHTS = [0.40, 0.30, 0.20, 0.10]  # realistic distribution


def _make_case(cycle: int) -> CaseTrajectory:
    """Generate a realistic case with some randomness."""
    gt = random.choice(GOAL_TYPES)
    cap_id = random.choice(CAPABILITIES)
    grade = random.choices(GRADES, weights=GRADE_WEIGHTS, k=1)[0]
    profile = random.choice(PROFILES)
    vp = grade in (QualityGrade.GOLD, QualityGrade.SILVER)

    # Varied residuals
    n_residuals = random.randint(1, 5)
    metrics = ["temp", "humidity", "weight", "load", "pressure", "latency", "cpu", "mem", "voltage", "drift"]
    residuals = {m: round(random.uniform(-1.0, 2.0), 3) for m in random.sample(metrics, n_residuals)}

    # Occasionally add multiple capabilities
    caps = [CapabilityContract(capability_id=cap_id, category=CapabilityCategory.SOLVE)]
    if random.random() > 0.7:
        extra = random.choice(CAPABILITIES)
        caps.append(CapabilityContract(capability_id=extra, category=CapabilityCategory.DETECT))

    case = CaseTrajectory(
        goal=Goal(type=gt, description=f"cycle-{cycle} {gt.value} task"),
        selected_capabilities=caps,
        verifier_result={"passed": vp, "confidence": round(random.uniform(0.5, 1.0), 2)},
        world_snapshot_before=WorldSnapshot(residuals=residuals),
        profile=profile,
    )
    case.quality_grade = grade
    return case


def generate_batch(cycle: int) -> list[CaseTrajectory]:
    """Generate a batch of 20-50 cases."""
    n = random.randint(20, 50)
    return [_make_case(cycle) for _ in range(n)]


# -- Monitoring ----------------------------------------------

def get_memory_mb() -> float:
    """Get current process memory in MB."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


# -- Main loop ----------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Production run")
    parser.add_argument("--hours", type=float, default=5.0)
    parser.add_argument("--interval", type=int, default=180, help="Seconds between cycles")
    args = parser.parse_args()

    duration_s = args.hours * 3600
    interval_s = args.interval
    max_cycles = int(duration_s / interval_s) + 1

    log_path = f"data/production_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    os.makedirs("data", exist_ok=True)

    print("=" * 70)
    print(f"  PRODUCTION RUN -- {args.hours}h, ~{max_cycles} cycles, {interval_s}s interval")
    print(f"  Started: {now_str()}")
    print(f"  Log: {log_path}")
    print("=" * 70)
    sys.stdout.flush()

    pipeline = NightLearningPipeline(profile="APIARY")

    # Tracking
    total_cases = 0
    total_errors = 0
    total_trained = 0
    accuracy_history: dict[str, list[float]] = {m: [] for m in SPECIALIST_MODELS}
    grade_totals = {"gold": 0, "silver": 0, "bronze": 0, "quarantine": 0}
    cycle_times: list[float] = []
    peak_mem = 0.0

    start_time = time.time()
    cycle = 0

    try:
        while time.time() - start_time < duration_s:
            cycle += 1
            cycle_start = time.time()
            mem_mb = get_memory_mb()
            peak_mem = max(peak_mem, mem_mb)

            # Generate and run
            cases = generate_batch(cycle)
            result = pipeline.run_cycle(day_cases=cases)

            elapsed = time.time() - cycle_start
            cycle_times.append(elapsed)
            total_cases += result.cases_built
            total_trained += result.models_trained

            grade_totals["gold"] += result.gold_count
            grade_totals["silver"] += result.silver_count
            grade_totals["bronze"] += result.bronze_count
            grade_totals["quarantine"] += result.quarantine_count

            if result.errors:
                total_errors += len(result.errors)

            # Extract accuracy from trainer history
            trainer_history = pipeline._trainer._training_history
            if trainer_history:
                recent = trainer_history[-len(SPECIALIST_MODELS):]
                for tr in recent:
                    if tr.model_id in accuracy_history:
                        accuracy_history[tr.model_id].append(tr.accuracy)

            # Status line
            run_elapsed = time.time() - start_time
            remaining = duration_s - run_elapsed
            status = "OK" if result.success else "ERR"
            print(
                f"[{now_str()}] Cycle {cycle:>4d} | {status} | "
                f"{result.cases_built:>2d} cases | "
                f"G{result.gold_count:>2d} S{result.silver_count:>2d} "
                f"B{result.bronze_count:>2d} Q{result.quarantine_count:>2d} | "
                f"{result.models_trained} models | "
                f"{elapsed:.2f}s | "
                f"mem={mem_mb:.0f}MB | "
                f"remaining={remaining/60:.0f}min"
            )
            if result.errors:
                for e in result.errors:
                    print(f"  ERROR: {e}")
            sys.stdout.flush()

            # Log to JSONL
            entry = {
                "cycle": cycle,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(run_elapsed, 1),
                "cycle_duration_s": round(elapsed, 3),
                "mem_mb": round(mem_mb, 1),
                "result": result.to_dict(),
            }
            with open(log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

            # Periodic summary every 10 cycles
            if cycle % 10 == 0:
                avg_time = sum(cycle_times[-10:]) / min(10, len(cycle_times))
                avg_acc = {}
                for m in SPECIALIST_MODELS:
                    vals = accuracy_history[m]
                    if vals:
                        avg_acc[m] = round(sum(vals[-10:]) / min(10, len(vals)), 3)

                print(f"\n  -- 10-cycle summary --")
                print(f"  Avg cycle time: {avg_time:.2f}s")
                print(f"  Total cases: {total_cases}, errors: {total_errors}")
                print(f"  Grade totals: {grade_totals}")
                print(f"  Avg accuracy (last 10):")
                for m, a in sorted(avg_acc.items()):
                    print(f"    {m:30s} {a:.3f}")
                print(f"  Peak memory: {peak_mem:.0f}MB\n")
                sys.stdout.flush()

            # Wait for next cycle
            wait = interval_s - (time.time() - cycle_start)
            if wait > 0 and (time.time() - start_time + wait) < duration_s:
                time.sleep(wait)

    except KeyboardInterrupt:
        print(f"\n  Interrupted at cycle {cycle}")
    except Exception as e:
        print(f"\n  FATAL: {e}")
        traceback.print_exc()

    # Final report
    run_duration = time.time() - start_time
    print("\n" + "=" * 70)
    print("  FINAL REPORT")
    print("=" * 70)
    print(f"  Duration:      {run_duration/3600:.2f}h ({cycle} cycles)")
    print(f"  Total cases:   {total_cases}")
    print(f"  Total errors:  {total_errors}")
    print(f"  Models trained: {total_trained}")
    print(f"  Grade totals:  {grade_totals}")
    print(f"  Peak memory:   {peak_mem:.0f}MB")

    if cycle_times:
        print(f"  Avg cycle:     {sum(cycle_times)/len(cycle_times):.2f}s")
        print(f"  Max cycle:     {max(cycle_times):.2f}s")

    print(f"\n  Accuracy trends (first -> last):")
    for m in SPECIALIST_MODELS:
        vals = accuracy_history[m]
        if vals:
            first5 = sum(vals[:5]) / min(5, len(vals))
            last5 = sum(vals[-5:]) / min(5, len(vals))
            trend = "UP" if last5 > first5 + 0.01 else "DN" if last5 < first5 - 0.01 else "->"
            print(f"    {m:30s} {first5:.3f} -> {last5:.3f} {trend}")

    print(f"\n  Log: {log_path}")
    print("=" * 70)
    sys.stdout.flush()

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
