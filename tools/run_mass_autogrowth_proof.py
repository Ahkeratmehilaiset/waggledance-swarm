#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mass-safe self-starting autogrowth proof (Phase 12).

Closes the full autonomy loop end-to-end at scale:

    runtime gap signals
        -> RuntimeGapDetector.record (control plane + growth_events)
        -> digest_signals_into_intents (growth_intents + autogrowth_queue)
        -> AutogrowthScheduler.run_until_idle (auto_promotion_engine)
        -> LowRiskSolverDispatcher hits

Generates 30 distinct canonical low-risk seeds across all six
allowlisted families (5 each), feeds them as gap signals, lets the
scheduler drain the queue, and probes each promoted solver via the
runtime dispatcher.

Default output:

    docs/runs/phase12_self_starting_autogrowth_2026_04_30/
        mass_autogrowth_proof.json
        mass_autogrowth_proof.db        (gitignored via *.db)

Reproduce:

    python tools/run_mass_autogrowth_proof.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    DispatchQuery,
    GapSignal,
    LowRiskGrower,
    LowRiskSolverDispatcher,
    RuntimeGapDetector,
    digest_signals_into_intents,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


# -- canonical seed bank ------------------------------------------------


def _scalar_seeds() -> list[tuple[str, str, dict]]:
    """5 unit-conversion seeds, each with a probe input + expected output."""

    seeds = [
        ("celsius_to_kelvin", "thermal", {"factor": 1.0, "offset": 273.15}),
        ("celsius_to_fahrenheit", "thermal", {"factor": 9.0 / 5.0, "offset": 32.0}),
        ("meters_to_kilometers", "math", {"factor": 0.001}),
        ("kilograms_to_pounds", "math", {"factor": 2.20462}),
        ("kilowatts_to_watts", "energy", {"factor": 1000.0}),
    ]
    out = []
    for name, cell, params in seeds:
        spec = {"from_unit": "_", "to_unit": "_", **params}
        cases = [
            {"inputs": {"x": float(v)},
              "expected": float(v) * spec["factor"] + spec.get("offset", 0.0)}
            for v in (-10.0, 0.0, 25.0, 100.0)
        ]
        samples = [{"x": float(i) * 1.7 - 50.0} for i in range(40)]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
            "oracle_kind": "formula_recompute",
        }))
    return out


def _lookup_seeds() -> list[tuple[str, str, dict]]:
    seeds = [
        ("color_to_action", "general",
          {"red": "stop", "green": "go", "yellow": "slow"}, "wait"),
        ("status_to_severity", "system",
          {"ok": 0, "warn": 1, "error": 2, "fatal": 3}, -1),
        ("day_to_workday", "general",
          {"mon": True, "tue": True, "wed": True, "thu": True,
           "fri": True, "sat": False, "sun": False}, False),
        ("hue_to_temp", "thermal",
          {"red": "warm", "blue": "cool", "white": "neutral"}, "neutral"),
        ("verdict_to_label", "safety",
          {"pass": "OK", "fail": "ALERT", "skip": "SKIP"}, "UNKNOWN"),
    ]
    out = []
    for name, cell, table, default in seeds:
        spec = {"table": dict(table), "default": default}
        keys = list(table.keys()) + ["__missing_key__"]
        cases = [
            {"inputs": {"key": k},
              "expected": table.get(k, default)}
            for k in keys
        ]
        samples = [{"key": k}
                    for k in (list(table.keys()) * 3 + ["x"] * 2)]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
        }))
    return out


def _threshold_seeds() -> list[tuple[str, str, dict]]:
    seeds = [
        ("hot_above_30c", "thermal", 30.0, ">", "hot", "cool"),
        ("cold_below_5c", "thermal", 5.0, "<", "cold", "warm"),
        ("low_battery_below_20", "energy", 20.0, "<", "low", "ok"),
        ("overload_above_80pct", "system", 80.0, ">=", "overload", "ok"),
        ("frost_below_0c", "seasonal", 0.0, "<", "frost", "above_zero"),
    ]
    out = []
    for name, cell, threshold, op, t_label, f_label in seeds:
        spec = {"threshold": threshold, "operator": op,
                 "true_label": t_label, "false_label": f_label}
        ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
        f = ops[op]
        cases = [
            {"inputs": {"x": v},
              "expected": t_label if f(v, threshold) else f_label}
            for v in (threshold - 10, threshold, threshold + 10, threshold + 100)
        ]
        samples = [{"x": float(i)}
                    for i in range(int(threshold) - 30, int(threshold) + 30)]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
        }))
    return out


def _interval_seeds() -> list[tuple[str, str, dict]]:
    seeds = [
        ("temp_band", "thermal", [
            {"min": -50.0, "max": 0.0, "label": "freezing"},
            {"min": 0.0, "max": 15.0, "label": "cold"},
            {"min": 15.0, "max": 25.0, "label": "comfort"},
            {"min": 25.0, "max": 40.0, "label": "warm"},
            {"min": 40.0, "max": 100.0, "label": "hot"},
        ], "out_of_range"),
        ("age_band", "general", [
            {"min": 0, "max": 13, "label": "child"},
            {"min": 13, "max": 20, "label": "teen"},
            {"min": 20, "max": 65, "label": "adult"},
            {"min": 65, "max": 130, "label": "senior"},
        ], None),
        ("cpu_band", "system", [
            {"min": 0.0, "max": 25.0, "label": "idle"},
            {"min": 25.0, "max": 75.0, "label": "active"},
            {"min": 75.0, "max": 100.0, "label": "loaded"},
        ], "n/a"),
        ("hour_of_day_band", "general", [
            {"min": 0, "max": 6, "label": "night"},
            {"min": 6, "max": 12, "label": "morning"},
            {"min": 12, "max": 18, "label": "afternoon"},
            {"min": 18, "max": 24, "label": "evening"},
        ], "unknown"),
        ("score_band", "learning", [
            {"min": 0.0, "max": 0.5, "label": "low"},
            {"min": 0.5, "max": 0.8, "label": "mid"},
            {"min": 0.8, "max": 1.01, "label": "high"},
        ], "out"),
    ]
    out = []
    for name, cell, intervals, oor in seeds:
        spec = {"intervals": intervals, "out_of_range_label": oor}

        def label_for(x: float, intervals=intervals, oor=oor):
            for iv in intervals:
                if iv["min"] <= x < iv["max"]:
                    return iv["label"]
            return oor

        # Validation: pick one input inside each interval + one OOR
        cases: list[dict] = []
        for iv in intervals:
            mid = (iv["min"] + iv["max"]) / 2.0
            cases.append({"inputs": {"x": mid}, "expected": iv["label"]})
        cases.append({"inputs": {"x": -1e9}, "expected": oor})
        samples = [{"x": float(i) * 0.5 - 5.0}
                    for i in range(60)]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
        }))
    return out


def _linear_seeds() -> list[tuple[str, str, dict]]:
    seeds = [
        ("comfort_score", "general", [0.6, -0.3, 0.1], 0.0,
          ["temp_dev", "humidity_dev", "noise_dev"]),
        ("energy_estimate_kwh", "energy", [0.001, 0.5], 0.05,
          ["watts", "hours"]),
        ("safety_index", "safety", [-0.5, -0.3, 1.0], 0.5,
          ["risk_a", "risk_b", "compliance"]),
        ("seasonal_weight", "seasonal", [0.2, 0.7, 0.1], 0.0,
          ["temp_z", "daylight_z", "humidity_z"]),
        ("performance_score", "system", [0.4, 0.4, 0.2], 0.1,
          ["throughput_z", "latency_z_inv", "error_rate_inv"]),
    ]
    out = []
    for name, cell, coefs, intercept, cols in seeds:
        spec = {"coefficients": list(coefs), "intercept": intercept,
                 "input_columns": list(cols)}

        def y_for(inputs: Mapping[str, Any], coefs=coefs, b=intercept,
                   cols=cols):
            return sum(c * float(inputs[col]) for c, col in zip(coefs, cols)) + b

        # Validation: a few hand-picked tuples
        sample_inputs = [
            {col: float(i) * 0.5 + idx for idx, col in enumerate(cols)}
            for i in range(5)
        ]
        cases = [
            {"inputs": inp, "expected": y_for(inp)}
            for inp in sample_inputs
        ]
        # Shadow samples: more variety
        shadow = [
            {col: float(j * 0.3) + (-1.0 if k % 2 else 1.0)
              for k, col in enumerate(cols)}
            for j in range(20)
        ]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": shadow,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
        }))
    return out


def _interp_seeds() -> list[tuple[str, str, dict]]:
    seeds = [
        ("battery_charge_curve", "energy",
          [{"x": 0.0, "y": 0.0}, {"x": 50.0, "y": 60.0},
            {"x": 100.0, "y": 100.0}], 0.0, 100.0, "clip"),
        ("comfort_curve_temp", "thermal",
          [{"x": 0.0, "y": 0.1}, {"x": 21.0, "y": 1.0},
            {"x": 35.0, "y": 0.0}], 0.0, 35.0, "clip"),
        ("power_efficiency_curve", "energy",
          [{"x": 0.0, "y": 0.0}, {"x": 0.3, "y": 0.55},
            {"x": 0.7, "y": 0.85}, {"x": 1.0, "y": 0.95}], 0.0, 1.0, "clip"),
        ("daylight_factor_hour", "seasonal",
          [{"x": 0, "y": 0.0}, {"x": 6, "y": 0.2},
            {"x": 12, "y": 1.0}, {"x": 18, "y": 0.3},
            {"x": 24, "y": 0.0}], 0.0, 24.0, "clip"),
        ("learning_rate_decay", "learning",
          [{"x": 0.0, "y": 0.001}, {"x": 100.0, "y": 0.0005},
            {"x": 1000.0, "y": 0.0001}], 0.0, 1000.0, "clip"),
    ]
    out = []
    for name, cell, knots, min_x, max_x, policy in seeds:
        spec = {"knots": list(knots), "method": "linear",
                 "min_x": min_x, "max_x": max_x,
                 "out_of_range_policy": policy}

        def interp(x: float, knots=knots, min_x=min_x, max_x=max_x):
            x = max(min_x, min(max_x, float(x)))
            for i in range(len(knots) - 1):
                x0 = float(knots[i]["x"])
                x1 = float(knots[i + 1]["x"])
                if x0 <= x <= x1:
                    if x1 == x0:
                        return float(knots[i]["y"])
                    y0 = float(knots[i]["y"])
                    y1 = float(knots[i + 1]["y"])
                    t = (x - x0) / (x1 - x0)
                    return y0 + (y1 - y0) * t
            return float(knots[-1]["y"])

        # Validation cases: at every knot + one mid-segment + one OOR
        cases: list[dict] = []
        for k in knots:
            cases.append(
                {"inputs": {"x": float(k["x"])}, "expected": float(k["y"])}
            )
        mid_x = (float(knots[0]["x"]) + float(knots[1]["x"])) / 2.0
        cases.append({"inputs": {"x": mid_x}, "expected": interp(mid_x)})
        cases.append(
            {"inputs": {"x": float(min_x) - 99.0}, "expected": interp(min_x)}
        )
        samples = [{"x": float(min_x) + i * (max_x - min_x) / 40.0}
                    for i in range(41)]
        out.append((name, cell, {
            "spec": spec,
            "validation_cases": cases,
            "shadow_samples": samples,
            "solver_name_seed": name,
            "cell_id": cell,
            "source": "phase12_mass_proof",
            "source_kind": "canonical_seed",
        }))
    return out


def all_seeds() -> Iterable[tuple[str, str, str, dict]]:
    """Yield (family_kind, solver_name_seed, cell, seed_dict)."""

    sets: list[tuple[str, list[tuple[str, str, dict]]]] = [
        ("scalar_unit_conversion", _scalar_seeds()),
        ("lookup_table", _lookup_seeds()),
        ("threshold_rule", _threshold_seeds()),
        ("interval_bucket_classifier", _interval_seeds()),
        ("linear_arithmetic", _linear_seeds()),
        ("bounded_interpolation", _interp_seeds()),
    ]
    for family_kind, items in sets:
        for name, cell, seed in items:
            yield family_kind, name, cell, seed


# -- runner -------------------------------------------------------------


def run(out_dir: Path, db_path: Path) -> dict:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    grower = LowRiskGrower(cp)
    grower.ensure_low_risk_policies()

    # Phase 12 step 1: detector records signals (self-starting evidence)
    detector = RuntimeGapDetector(cp)
    in_memory_signals: list[GapSignal] = []
    for family_kind, seed_name, cell, seed in all_seeds():
        sig = GapSignal(
            kind="miss",
            family_kind=family_kind,
            cell_coord=cell,
            intent_seed=seed_name,
            weight=1.0,
            payload={"miss_count": 1, "seed_name": seed_name},
            spec_seed=seed,
        )
        detector.record(sig)
        in_memory_signals.append(sig)

    # Phase 12 step 2: digest into queued intents
    digest = digest_signals_into_intents(
        cp,
        candidate_signals=in_memory_signals,
        min_signals_per_intent=1,
        autoenqueue=True,
    )

    # Phase 12 step 3: scheduler drains the queue end-to-end
    scheduler = AutogrowthScheduler(cp, scheduler_id="mass_proof_scheduler")
    drained = scheduler.run_until_idle(max_ticks=200)

    # Phase 12 step 4: dispatcher serves the auto-promoted solvers
    dispatcher = LowRiskSolverDispatcher(cp)
    dispatch_results: list[dict] = []
    probe_inputs = {
        "scalar_unit_conversion": {"x": 25.0},
        "lookup_table": {"key": "red"},
        "threshold_rule": {"x": 35.0},
        "interval_bucket_classifier": {"x": 17.5},
        "linear_arithmetic": {
            "temp_dev": 1.0, "humidity_dev": 0.5, "noise_dev": 0.2,
            "watts": 100.0, "hours": 2.0,
            "risk_a": 0.5, "risk_b": 0.2, "compliance": 1.0,
            "temp_z": 0.0, "daylight_z": 0.0, "humidity_z": 0.0,
            "throughput_z": 1.0, "latency_z_inv": 1.0, "error_rate_inv": 1.0,
        },
        "bounded_interpolation": {"x": 25.0},
    }
    for family_kind in (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    ):
        res = dispatcher.dispatch(DispatchQuery(
            family_kind=family_kind, inputs=probe_inputs[family_kind],
        ))
        dispatch_results.append({
            "family_kind": family_kind,
            "matched": res.matched,
            "solver_name": res.solver_name,
            "output": res.output,
            "reason": res.reason,
        })
    dispatcher.flush_kpi_snapshot()

    proof: dict[str, Any] = {
        "proof_version": 1,
        "schema_version": cp.schema_version(),
        "primary_teacher_lane_id": grower.primary_teacher_lane_id,
        "self_starting": True,
        "signals_recorded": detector.stats.signals_recorded,
        "signals_per_family": dict(detector.stats.by_family),
        "intents_created": digest.intents_created,
        "intents_enqueued": digest.intents_enqueued,
        "scheduler": {
            "id": scheduler.scheduler_id,
            "ticks_total": scheduler.stats.ticks_total,
            "ticks_idle": scheduler.stats.ticks_idle,
            "auto_promoted": scheduler.stats.auto_promoted,
            "rejected": scheduler.stats.rejected,
            "errored": scheduler.stats.errored,
            "by_family_promoted": dict(scheduler.stats.by_family_promoted),
            "drained_count": drained,
        },
        "dispatch_results": dispatch_results,
        "kpis": {
            "candidates_total": sum(1 for _ in cp.iter_solvers()),
            "auto_promotions_total": cp.count_solvers(status="auto_promoted"),
            "rejections_total": len(
                cp.list_promotion_decisions(decision="rejected", limit=10000)
            ),
            "rollbacks_total": len(
                cp.list_promotion_decisions(decision="rollback", limit=10000)
            ),
            "validation_runs_total": cp.stats().table_counts["validation_runs"],
            "shadow_evaluations_total": cp.stats().table_counts["shadow_evaluations"],
            "growth_intents_total": cp.count_growth_intents(),
            "growth_intents_fulfilled": cp.count_growth_intents(status="fulfilled"),
            "autogrowth_runs_total": cp.stats().table_counts["autogrowth_runs"],
            "growth_events_total": cp.stats().table_counts["growth_events"],
        },
        "control_plane_table_counts": dict(cp.stats().table_counts),
    }

    # Per-cell breakdown of fulfilled intents (cell-aware reporting)
    cell_counts: dict[str, int] = {}
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT cell_coord, COUNT(*) AS c
        FROM growth_intents WHERE status = 'fulfilled'
        GROUP BY cell_coord ORDER BY cell_coord
        """
    ).fetchall()
    for r in rows:
        cell_counts[str(r["cell_coord"]) or "_"] = int(r["c"])
    proof["per_cell_promotions"] = cell_counts

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "mass_autogrowth_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    cp.close()
    return proof


def _summarise(proof: dict) -> str:
    lines = [
        "Phase 12 — Mass-safe self-starting autogrowth proof",
        "=" * 52,
        f"schema_version       = {proof['schema_version']}",
        f"primary_teacher_lane = {proof['primary_teacher_lane_id']}",
        f"self_starting        = {proof['self_starting']}",
        "",
        f"signals_recorded     = {proof['signals_recorded']}",
        f"intents_created      = {proof['intents_created']}",
        f"intents_enqueued     = {proof['intents_enqueued']}",
        f"scheduler.drained    = {proof['scheduler']['drained_count']}",
        f"scheduler.promoted   = {proof['scheduler']['auto_promoted']}",
        f"scheduler.rejected   = {proof['scheduler']['rejected']}",
        f"scheduler.errored    = {proof['scheduler']['errored']}",
        "",
        "by_family_promoted:",
    ]
    for k, v in sorted(proof["scheduler"]["by_family_promoted"].items()):
        lines.append(f"  {k:30s} = {v}")
    lines.append("")
    lines.append("per_cell_promotions:")
    for k, v in sorted(proof["per_cell_promotions"].items()):
        lines.append(f"  {k:30s} = {v}")
    lines.append("")
    lines.append("KPIs:")
    for k in ("candidates_total", "auto_promotions_total",
               "validation_runs_total", "shadow_evaluations_total",
               "growth_intents_total", "growth_intents_fulfilled",
               "autogrowth_runs_total", "growth_events_total"):
        lines.append(f"  {k:30s} = {proof['kpis'][k]}")
    lines.append("")
    lines.append("dispatch_results:")
    for d in proof["dispatch_results"]:
        lines.append(
            f"  {d['family_kind']:30s} matched={d['matched']}  "
            f"output={d['output']!r}  solver={d['solver_name']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs" / "phase12_self_starting_autogrowth_2026_04_30",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = args.db or (args.out_dir / "mass_autogrowth_proof.db")
    proof = run(args.out_dir, db_path)
    print(_summarise(proof))
    print(f"\nWrote {args.out_dir / 'mass_autogrowth_proof.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
