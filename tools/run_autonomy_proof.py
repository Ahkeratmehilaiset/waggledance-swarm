#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end proof of the Phase 11 low-risk autogrowth lane.

Runs three independent gaps (one each from three allowlisted families)
through the full no-human loop:

    gap → make_spec → compile → validation → shadow → auto_promote
        → dispatcher hit → KPI snapshot

Writes the result to
``docs/runs/phase11_autogrowth_2026_04_29/autonomy_proof.json``
and prints a human-readable summary.

Run from repo root:

    python tools/run_autonomy_proof.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth import (
    DispatchQuery,
    GapInput,
    LowRiskGrower,
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


# -- oracles (independent reference implementations) --------------------


def _scalar_unit_conversion_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _threshold_rule_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    op = artifact["operator"]
    x = float(inputs["x"])
    th = float(artifact["threshold"])
    fired = {
        ">":  x > th,  ">=": x >= th,
        "<":  x < th,  "<=": x <= th,
        "==": x == th, "!=": x != th,
    }[op]
    return artifact["true_label"] if fired else artifact["false_label"]


def _lookup_table_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    table = artifact["table"]
    key = inputs["key"]
    if key in table:
        return table[key]
    sk = str(key)
    if sk in table:
        return table[sk]
    return artifact.get("default")


# -- gap fixtures --------------------------------------------------------


def _gap_celsius_to_kelvin() -> GapInput:
    return GapInput(
        family_kind="scalar_unit_conversion",
        solver_name="celsius_to_kelvin_v1",
        cell_id="thermal",
        spec={"from_unit": "C", "to_unit": "K",
              "factor": 1.0, "offset": 273.15},
        source="phase11_autonomy_proof",
        source_kind="hand_authored_for_proof",
        validation_cases=[
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
            {"inputs": {"x": -40.0}, "expected": 233.15},
            {"inputs": {"x": 25.0}, "expected": 298.15},
            {"inputs": {"x": -273.15}, "expected": 0.0},
        ],
        shadow_samples=[{"x": float(i) * 0.93} for i in range(40)],
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    )


def _gap_hot_threshold() -> GapInput:
    return GapInput(
        family_kind="threshold_rule",
        solver_name="hot_threshold_30c_v1",
        cell_id="thermal",
        spec={"threshold": 30.0, "operator": ">",
              "true_label": "hot", "false_label": "cool"},
        source="phase11_autonomy_proof",
        source_kind="hand_authored_for_proof",
        validation_cases=[
            {"inputs": {"x": 50}, "expected": "hot"},
            {"inputs": {"x": 30.001}, "expected": "hot"},
            {"inputs": {"x": 30}, "expected": "cool"},
            {"inputs": {"x": -10}, "expected": "cool"},
        ],
        shadow_samples=[{"x": float(i)} for i in range(-20, 80)],
        oracle=_threshold_rule_oracle,
        oracle_kind="formula_recompute",
    )


def _gap_color_to_action() -> GapInput:
    return GapInput(
        family_kind="lookup_table",
        solver_name="color_to_action_v1",
        cell_id="general",
        spec={
            "table": {"red": "stop", "green": "go", "yellow": "slow"},
            "default": "unknown",
        },
        source="phase11_autonomy_proof",
        source_kind="hand_authored_for_proof",
        validation_cases=[
            {"inputs": {"key": "red"}, "expected": "stop"},
            {"inputs": {"key": "green"}, "expected": "go"},
            {"inputs": {"key": "yellow"}, "expected": "slow"},
            {"inputs": {"key": "blue"}, "expected": "unknown"},
        ],
        shadow_samples=[
            {"key": k} for k in
            ("red", "green", "yellow", "blue", "purple", "red", "green",
             "orange", "red", "yellow")
        ],
        oracle=_lookup_table_oracle,
        oracle_kind="dict_lookup_recompute",
    )


# -- entry point ---------------------------------------------------------


def run(out_dir: Path, db_path: Path) -> dict:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    grower = LowRiskGrower(cp)
    grower.ensure_low_risk_policies()

    proof: dict[str, Any] = {
        "proof_version": 1,
        "schema_version": cp.schema_version(),
        "primary_teacher_lane_id": grower.primary_teacher_lane_id,
        "gaps": [],
        "dispatcher_runs": [],
    }

    gaps = [
        _gap_celsius_to_kelvin(),
        _gap_hot_threshold(),
        _gap_color_to_action(),
    ]
    for gap in gaps:
        outcome = grower.grow_from_gap(gap)
        proof["gaps"].append({
            "family_kind": gap.family_kind,
            "solver_name": gap.solver_name,
            "cell_id": gap.cell_id,
            "validation_case_count": len(gap.validation_cases),
            "shadow_sample_count": len(gap.shadow_samples),
            "oracle_kind": gap.oracle_kind,
            "accepted": outcome.accepted,
            "reason": outcome.reason,
            "validation_pass_rate": (
                outcome.promotion.validation.pass_rate
                if outcome.promotion and outcome.promotion.validation
                else None
            ),
            "shadow_agreement_rate": (
                outcome.promotion.shadow.agreement_rate
                if outcome.promotion and outcome.promotion.shadow
                else None
            ),
            "decision_record_id": (
                outcome.promotion.decision_record_id if outcome.promotion
                else None
            ),
        })

    # Runtime path: dispatch into the auto-promoted solvers
    disp = LowRiskSolverDispatcher(cp)
    runtime_calls = [
        DispatchQuery(family_kind="scalar_unit_conversion",
                       inputs={"x": 0.0}),
        DispatchQuery(family_kind="scalar_unit_conversion",
                       inputs={"x": 100.0}),
        DispatchQuery(family_kind="threshold_rule", inputs={"x": 50}),
        DispatchQuery(family_kind="threshold_rule", inputs={"x": 10}),
        DispatchQuery(family_kind="lookup_table", inputs={"key": "red"}),
        DispatchQuery(family_kind="lookup_table", inputs={"key": "blue"}),
    ]
    for q in runtime_calls:
        result = disp.dispatch(q)
        proof["dispatcher_runs"].append({
            "family_kind": q.family_kind,
            "inputs": dict(q.inputs),
            "matched": result.matched,
            "reason": result.reason,
            "solver_name": result.solver_name,
            "output": result.output,
        })

    snapshot_id = disp.flush_kpi_snapshot()
    snap = cp.latest_autonomy_kpi()
    assert snap is not None and snap.id == snapshot_id

    # Aggregate KPI counts across the whole control plane
    proof["kpis"] = {
        "snapshot_id": snap.id,
        "snapshot_at": snap.snapshot_at,
        "candidates_total": sum(1 for _ in cp.iter_solvers()),
        "auto_promotions_total": cp.count_solvers(status="auto_promoted"),
        "rejections_total": len(
            cp.list_promotion_decisions(decision="rejected", limit=1000)
        ),
        "rollbacks_total": len(
            cp.list_promotion_decisions(decision="rollback", limit=1000)
        ),
        "validation_runs_total": cp.stats().table_counts["validation_runs"],
        "shadow_evaluations_total": cp.stats().table_counts["shadow_evaluations"],
        "dispatcher_hits_total": snap.dispatcher_hits_total,
        "dispatcher_misses_total": snap.dispatcher_misses_total,
        "per_family_dispatcher_hits_json": snap.per_family_counts_json,
    }
    proof["control_plane_table_counts"] = dict(cp.stats().table_counts)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "autonomy_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8"
    )
    cp.close()
    return proof


def _summarise(proof: dict) -> str:
    lines = [
        "Phase 11 — Low-risk autogrowth proof",
        "=" * 38,
        f"schema_version = {proof['schema_version']}",
        f"primary_teacher_lane_id = {proof['primary_teacher_lane_id']}",
        "",
        "Gaps:",
    ]
    for g in proof["gaps"]:
        lines.append(
            f"  {g['family_kind']:30s} {g['solver_name']:30s} "
            f"-> accepted={g['accepted']}  reason={g['reason']}  "
            f"val={g['validation_pass_rate']}  shadow={g['shadow_agreement_rate']}"
        )
    lines.append("")
    lines.append("Dispatcher runs:")
    for d in proof["dispatcher_runs"]:
        lines.append(
            f"  {d['family_kind']:30s} matched={d['matched']}  "
            f"output={d['output']!r}  solver={d['solver_name']}"
        )
    lines.append("")
    k = proof["kpis"]
    lines.append("KPIs:")
    for key in (
        "candidates_total", "auto_promotions_total",
        "rejections_total", "rollbacks_total",
        "validation_runs_total", "shadow_evaluations_total",
        "dispatcher_hits_total", "dispatcher_misses_total",
    ):
        lines.append(f"  {key:30s} = {k[key]}")
    lines.append(f"  per_family_dispatcher_hits = {k['per_family_dispatcher_hits_json']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs" / "phase11_autogrowth_2026_04_29",
        help="Where to write autonomy_proof.json",
    )
    parser.add_argument(
        "--db", type=Path,
        default=None,
        help="Path to control-plane SQLite (default: scratch in out_dir)",
    )
    args = parser.parse_args()
    # Default db file uses .db so .gitignore (*.db) excludes it; the
    # JSON proof artifact and the generator script are the committed
    # truth, the binary sqlite is just a reproducible scratch.
    db_path = args.db or (args.out_dir / "proof_control_plane.db")
    proof = run(args.out_dir, db_path)
    print(_summarise(proof))
    print(f"\nWrote {args.out_dir / 'autonomy_proof.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
