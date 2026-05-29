#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phase 13 — runtime-integrated harvest + capability uptake proof.

End-to-end before/after demonstration that uses the real runtime
seam. The corpus is the canonical seed library; each seed becomes
a structured runtime query envelope. Every query goes through
:class:`RuntimeQueryRouter` exactly the way a real runtime call site
would.

Sequence:

1. Initial pass: route the entire corpus. With nothing promoted yet,
   every query misses. The router automatically emits one
   ``runtime_gap_signal`` per query class (deduped).
2. Harvest cycle: digest signals into growth_intents, enqueue, and
   drain the autogrowth_queue with ``AutogrowthScheduler.run_until_idle``.
3. Second pass: re-route the corpus. Capability-aware lookup
   (``dispatch_by_features``) now serves auto-promoted solvers.

The proof artifact reports per-family / per-cell breakdowns, before/
after counts, and KPIs.

Reproduce:

    python tools/run_runtime_harvest_proof.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    RuntimeQuery,
    RuntimeQueryRouter,
    all_canonical_seeds,
    digest_signals_into_intents,
    extract_features,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


def _runtime_query_for_seed(seed: dict) -> RuntimeQuery:
    """Build a structured runtime query envelope from one canonical seed.

    The first validation case's inputs serve as the runtime payload —
    that lets the second pass actually compute a real result we can
    cross-check against the seed's expected output.
    """

    family = seed["_family_kind"]
    cell = seed["cell_id"]
    intent_seed = seed["_intent_seed"]
    case = seed["validation_cases"][0]
    inputs = dict(case["inputs"])
    features = extract_features(family, dict(seed["spec"]))
    return RuntimeQuery(
        family_kind=family,
        inputs=inputs,
        cell_coord=cell,
        intent_seed=intent_seed,
        features=features,
        spec_seed=seed,
        weight=1.0,
    )


def _harvest_signals_for_intents(seeds: Iterable[dict]) -> list[GapSignal]:
    """Build the digest's input batch from the same seed set the
    runtime queries came from. The router has already persisted one
    runtime_gap_signal per intent_key; here we feed digest with
    in-memory GapSignals carrying the spec_seed payload so the
    scheduler can construct GapInputs."""

    sigs: list[GapSignal] = []
    for s in seeds:
        sigs.append(GapSignal(
            kind="runtime_miss",
            family_kind=s["_family_kind"],
            cell_coord=s["cell_id"],
            intent_seed=s["_intent_seed"],
            weight=1.0,
            spec_seed=s,
        ))
    return sigs


def run(out_dir: Path, db_path: Path) -> dict:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    grower = LowRiskGrower(cp)
    grower.ensure_low_risk_policies(max_auto_promote=200)

    seeds = all_canonical_seeds()
    queries = [_runtime_query_for_seed(s) for s in seeds]

    # --- Pass 1: nothing promoted yet; every query misses.
    router = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
    before_results: list[dict] = []
    for q, seed in zip(queries, seeds):
        res = router.route(q)
        before_results.append({
            "family_kind": q.family_kind,
            "intent_seed": q.intent_seed,
            "cell_coord": q.cell_coord,
            "served": res.served,
            "source": res.source,
            "miss_reason": res.miss_reason,
        })

    before_served = sum(1 for r in before_results if r["served"])
    before_misses = sum(1 for r in before_results if not r["served"])
    signals_after_pass1 = cp.count_runtime_gap_signals()

    # --- Harvest cycle: digest + scheduler drains everything.
    sigs = _harvest_signals_for_intents(seeds)
    digest = digest_signals_into_intents(
        cp, candidate_signals=sigs, min_signals_per_intent=1, autoenqueue=True,
    )
    scheduler = AutogrowthScheduler(cp, scheduler_id="phase13_proof_scheduler")
    drained = scheduler.run_until_idle(max_ticks=500)

    # --- Pass 2: re-route. Capability-aware lookup now serves them.
    router2 = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
    after_results: list[dict] = []
    for q in queries:
        res = router2.route(q)
        after_results.append({
            "family_kind": q.family_kind,
            "intent_seed": q.intent_seed,
            "cell_coord": q.cell_coord,
            "served": res.served,
            "source": res.source,
            "output": res.output,
            "solver_name": res.solver_name,
        })

    after_served = sum(1 for r in after_results if r["served"])
    after_via_features = sum(
        1 for r in after_results
        if r["served"] and r["source"] == "auto_promoted_solver"
    )
    after_misses = sum(1 for r in after_results if not r["served"])

    # Per-family / per-cell breakdowns
    per_family_promoted: dict[str, int] = {}
    per_cell_promoted: dict[str, int] = {}
    for r in after_results:
        if r["served"]:
            f = r["family_kind"]
            c = r["cell_coord"] or "_"
            per_family_promoted[f] = per_family_promoted.get(f, 0) + 1
            per_cell_promoted[c] = per_cell_promoted.get(c, 0) + 1

    proof: dict[str, Any] = {
        "proof_version": 1,
        "schema_version": cp.schema_version(),
        "primary_teacher_lane_id": grower.primary_teacher_lane_id,
        "corpus_size": len(queries),
        "before": {
            "served_total": before_served,
            "fallback_or_miss_total": before_misses,
            "signals_emitted_total": signals_after_pass1,
        },
        "harvest": {
            "intents_created": digest.intents_created,
            "intents_enqueued": digest.intents_enqueued,
            "scheduler_drained": drained,
            "scheduler_promoted": scheduler.stats.auto_promoted,
            "scheduler_rejected": scheduler.stats.rejected,
            "scheduler_errored": scheduler.stats.errored,
            "by_family_promoted": dict(scheduler.stats.by_family_promoted),
        },
        "after": {
            "served_total": after_served,
            "served_via_capability_lookup_total": after_via_features,
            "fallback_or_miss_total": after_misses,
            "per_family_served": per_family_promoted,
            "per_cell_served": per_cell_promoted,
        },
        "kpis": {
            "harvested_signals_total": cp.count_runtime_gap_signals(),
            "distinct_growth_intents_total": cp.count_growth_intents(),
            "queued_rows_total": cp.count_queue_rows(),
            "autogrowth_runs_total": cp.stats().table_counts["autogrowth_runs"],
            "auto_promotions_total": cp.count_solvers(status="auto_promoted"),
            "rejections_total": len(
                cp.list_promotion_decisions(decision="rejected", limit=10000)
            ),
            "rollbacks_total": len(
                cp.list_promotion_decisions(decision="rollback", limit=10000)
            ),
            "validation_runs_total": cp.stats().table_counts["validation_runs"],
            "shadow_evaluations_total": cp.stats().table_counts["shadow_evaluations"],
            "growth_events_total": cp.stats().table_counts["growth_events"],
            "solver_capability_features_total":
                cp.stats().table_counts["solver_capability_features"],
            "provider_jobs_total": cp.stats().table_counts["provider_jobs"],
            "builder_jobs_total": cp.stats().table_counts["builder_jobs"],
        },
        "control_plane_table_counts": dict(cp.stats().table_counts),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "runtime_harvest_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    cp.close()
    return proof


def _summarise(proof: dict) -> str:
    lines = [
        "Phase 13 — Runtime-integrated harvest + capability uptake proof",
        "=" * 64,
        f"schema_version       = {proof['schema_version']}",
        f"primary_teacher_lane = {proof['primary_teacher_lane_id']}",
        f"corpus_size          = {proof['corpus_size']}",
        "",
        "Before harvest (pass 1):",
        f"  served               = {proof['before']['served_total']}",
        f"  fallback_or_miss     = {proof['before']['fallback_or_miss_total']}",
        f"  signals_emitted      = {proof['before']['signals_emitted_total']}",
        "",
        "Harvest cycle:",
        f"  intents_created      = {proof['harvest']['intents_created']}",
        f"  intents_enqueued     = {proof['harvest']['intents_enqueued']}",
        f"  scheduler_drained    = {proof['harvest']['scheduler_drained']}",
        f"  scheduler_promoted   = {proof['harvest']['scheduler_promoted']}",
        f"  scheduler_rejected   = {proof['harvest']['scheduler_rejected']}",
        f"  scheduler_errored    = {proof['harvest']['scheduler_errored']}",
        "",
        "After harvest (pass 2):",
        f"  served                       = {proof['after']['served_total']}",
        f"  served_via_capability_lookup = {proof['after']['served_via_capability_lookup_total']}",
        f"  fallback_or_miss             = {proof['after']['fallback_or_miss_total']}",
        "",
        "Per-family served (after):",
    ]
    for f, n in sorted(proof["after"]["per_family_served"].items()):
        lines.append(f"  {f:30s} = {n}")
    lines.append("")
    lines.append("Per-cell served (after):")
    for c, n in sorted(proof["after"]["per_cell_served"].items()):
        lines.append(f"  {c:30s} = {n}")
    lines.append("")
    lines.append("KPIs:")
    for k in (
        "harvested_signals_total",
        "distinct_growth_intents_total",
        "auto_promotions_total",
        "rejections_total",
        "rollbacks_total",
        "validation_runs_total",
        "shadow_evaluations_total",
        "growth_events_total",
        "solver_capability_features_total",
        "provider_jobs_total",
        "builder_jobs_total",
    ):
        lines.append(f"  {k:34s} = {proof['kpis'][k]}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs" / "phase13_runtime_harvest_2026_04_30",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = args.db or (args.out_dir / "runtime_harvest_proof.db")
    proof = run(args.out_dir, db_path)
    print(_summarise(proof))
    print(f"\nWrote {args.out_dir / 'runtime_harvest_proof.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
