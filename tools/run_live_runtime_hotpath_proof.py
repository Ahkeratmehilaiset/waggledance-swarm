#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phase 14 — live runtime hot-path proof.

Exercises the actual production reasoning entrypoint
:meth:`waggledance.core.reasoning.solver_router.SolverRouter.route`
with a structured ``low_risk_autonomy_query`` context hint. Pass 1
produces misses + auto-emitted (buffered) signals. Pass 2 (after one
harvest cycle + warm-cache priming) serves the same corpus through
the same real entrypoint via the autonomy consult lane, with the
warm path doing zero SQLite reads and zero JSON parses.

Reproduce:

    python tools/run_live_runtime_hotpath_proof.py

The proof writes ``live_runtime_hotpath_proof.json`` next to this
file's `--out-dir`. The scratch SQLite uses a `.db` extension so
.gitignore excludes it.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    HotPathCache,
    LowRiskGrower,
    RuntimeGapDetector,
    RuntimeQueryRouter,
    all_canonical_seeds,
    build_autonomy_consult,
    digest_signals_into_intents,
    extract_features,
)
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.storage.control_plane import ControlPlaneDB


def _runtime_hint_for_seed(seed: dict) -> dict:
    family = seed["_family_kind"]
    case = seed["validation_cases"][0]
    return {
        "family_kind": family,
        "cell_coord": seed["cell_id"],
        "intent_seed": seed["_intent_seed"],
        "inputs": dict(case["inputs"]),
        "features": extract_features(family, dict(seed["spec"])),
        "spec_seed": seed,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    d = k - f
    return s[f] + (s[c] - s[f]) * d


def _run_corpus(
    sr: SolverRouter, hints: list[dict], iterations: int = 1
) -> tuple[list[dict], list[float]]:
    results: list[dict] = []
    latencies_ms: list[float] = []
    for _ in range(iterations):
        for hint in hints:
            t0 = time.perf_counter()
            res = sr.route(
                intent="autonomy_low_risk",
                query=f"runtime_query:{hint['intent_seed']}",
                context={"low_risk_autonomy_query": hint},
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)
            results.append({
                "family_kind": hint["family_kind"],
                "intent_seed": hint["intent_seed"],
                "cell_coord": hint["cell_coord"],
                "fallback_used": res.selection.fallback_used,
                "autonomy_served": res.autonomy_served,
                "autonomy_source": (
                    res.autonomy_consult.source
                    if res.autonomy_consult else None
                ),
            })
    return results, latencies_ms


def run(out_dir: Path, db_path: Path) -> dict:
    # The proof requires a pristine DB so pass 1 truthfully misses and
    # pass-pre-cache truthfully measures the SQLite-bound dispatch
    # cost. The default db_path uses a `.db` extension so it is
    # gitignored; deleting it before each run is safe.
    out_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass
    for sidecar in (db_path.with_suffix(db_path.suffix + "-shm"),
                     db_path.with_suffix(db_path.suffix + "-wal")):
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    grower = LowRiskGrower(cp)
    grower.ensure_low_risk_policies(max_auto_promote=200)

    seeds = all_canonical_seeds()
    hints = [_runtime_hint_for_seed(s) for s in seeds]

    detector = RuntimeGapDetector(cp)
    # Two routers: one WITHOUT the hot-path cache (synchronous SQLite
    # dispatch + sync write on miss — Phase 13 baseline), one WITH the
    # Phase 14 hot-path cache + buffered sink. The proof's baseline
    # latency comes from the no-cache path so the warm-vs-pre-cache
    # ratio measures a truthful before/after of the cache layer.
    runtime_router_no_cache = RuntimeQueryRouter(
        cp, detector=detector, min_signal_interval_seconds=0.0,
    )
    consult_no_cache = build_autonomy_consult(runtime_router_no_cache)
    sr_no_cache = SolverRouter(
        registry=CapabilityRegistry(load_builtins=False),
        autonomy_consult=consult_no_cache,
    )

    hot_path = HotPathCache(
        control_plane=cp, detector=detector,
        max_unflushed_signals=1000, max_unflushed_age_ms=500,
    )
    runtime_router = RuntimeQueryRouter(
        cp, detector=detector, hot_path=hot_path,
        min_signal_interval_seconds=0.0,
    )
    consult = build_autonomy_consult(runtime_router)

    sr = SolverRouter(
        registry=CapabilityRegistry(load_builtins=False),
        autonomy_consult=consult,
    )

    # Snapshot provider/builder counts before the proof window so the
    # delta is honest even if the DB is not pristine.
    before_counts = cp.stats().table_counts
    provider_jobs_before = before_counts.get("provider_jobs", 0)
    builder_jobs_before = before_counts.get("builder_jobs", 0)

    # ── Pass 1: nothing promoted → all queries miss → buffered signals
    pass1, pass1_lat = _run_corpus(sr, hints)
    # Force-flush the sink so the harvest cycle sees every signal
    flushed = hot_path.flush_signals()
    pass1_baseline_buffered_lat = list(pass1_lat)

    pass1_served = sum(1 for r in pass1 if r["autonomy_served"])
    pass1_misses = sum(1 for r in pass1 if not r["autonomy_served"])
    signals_emitted = cp.count_runtime_gap_signals()

    # ── Harvest cycle: digest + scheduler drain
    in_memory_signals = [
        GapSignal(
            kind="runtime_miss",
            family_kind=h["family_kind"],
            cell_coord=h["cell_coord"],
            intent_seed=h["intent_seed"],
            spec_seed=h["spec_seed"],
        )
        for h in hints
    ]
    digest = digest_signals_into_intents(
        cp, candidate_signals=in_memory_signals,
        min_signals_per_intent=1, autoenqueue=True,
    )
    scheduler = AutogrowthScheduler(cp, scheduler_id="phase14_proof_scheduler")
    drained = scheduler.run_until_idle(max_ticks=500)

    # ── Pass pre_cache: same corpus, but routed through SolverRouter
    # WITHOUT the hot-path cache attached. This measures the Phase 13
    # baseline cost (SQLite SELECT + JOIN + JSON parse + execute per
    # query). The warm-vs-pre-cache ratio compares pass3 against
    # this baseline, not against the buffered miss path.
    #
    # We run 3 iterations of the corpus through the no-cache path so
    # the pre_cache median is stable against single-call OS scheduling
    # noise (microsecond-scale measurements are otherwise jittery).
    pass_pre_cache, pre_cache_lat = _run_corpus(sr_no_cache, hints, iterations=3)
    pre_cache_served = sum(1 for r in pass_pre_cache if r["autonomy_served"])

    # ── Pass 2: warm cache cold for first hit, warm thereafter
    pass2_cold, pass2_cold_lat = _run_corpus(sr, hints, iterations=1)
    # Pass 3: warm-only (artifact + index already cached). 5 iterations
    # for the same noise-stability reason as pre_cache.
    pass3_warm, pass3_warm_lat = _run_corpus(sr, hints, iterations=5)

    pass2_served = sum(1 for r in pass2_cold if r["autonomy_served"])
    pass2_via_consult = sum(
        1 for r in pass2_cold
        if r["autonomy_served"]
        and r["autonomy_source"] == "auto_promoted_solver"
    )
    pass2_misses = sum(1 for r in pass2_cold if not r["autonomy_served"])

    # Per-family / per-cell breakdowns from pass 2
    per_family: dict[str, int] = {}
    per_cell: dict[str, int] = {}
    for r in pass2_cold:
        if r["autonomy_served"]:
            per_family[r["family_kind"]] = (
                per_family.get(r["family_kind"], 0) + 1
            )
            per_cell[r["cell_coord"] or "_"] = (
                per_cell.get(r["cell_coord"] or "_", 0) + 1
            )

    after_counts = cp.stats().table_counts
    provider_jobs_after = after_counts.get("provider_jobs", 0)
    builder_jobs_after = after_counts.get("builder_jobs", 0)

    # ── Latency stats
    def _stats(latencies: list[float]) -> dict:
        if not latencies:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
                    "mean_ms": 0.0, "n": 0}
        return {
            "p50_ms": round(_percentile(latencies, 50), 4),
            "p95_ms": round(_percentile(latencies, 95), 4),
            "p99_ms": round(_percentile(latencies, 99), 4),
            "mean_ms": round(statistics.mean(latencies), 4),
            "n": len(latencies),
        }

    pre_cache_baseline = _stats(pre_cache_lat)
    pass1_buffered_miss_stats = _stats(pass1_baseline_buffered_lat)
    cold_stats = _stats(pass2_cold_lat)
    warm_stats = _stats(pass3_warm_lat)
    warm_p50 = warm_stats["p50_ms"] or 0.001
    pre_p50 = pre_cache_baseline["p50_ms"] or warm_p50
    warm_vs_pre_ratio = round(pre_p50 / warm_p50, 2) if warm_p50 > 0 else 0.0

    # Threshold attainment
    floor = {
        "warm_p50_ms": 1.0, "warm_p99_ms": 10.0,
        "cold_p50_ms": 75.0, "cold_p99_ms": 250.0,
        "warm_vs_pre_cache_ratio_min": 5.0,
    }
    stretch = {
        "warm_p50_ms": 0.5, "warm_p99_ms": 5.0,
        "cold_p50_ms": 50.0, "cold_p99_ms": 200.0,
        "warm_vs_pre_cache_ratio_min": 10.0,
    }

    def _check(target: dict, kind: str) -> dict:
        return {
            "warm_p50_ms_met": warm_stats["p50_ms"] <= target["warm_p50_ms"],
            "warm_p99_ms_met": warm_stats["p99_ms"] <= target["warm_p99_ms"],
            "cold_p50_ms_met": cold_stats["p50_ms"] <= target["cold_p50_ms"],
            "cold_p99_ms_met": cold_stats["p99_ms"] <= target["cold_p99_ms"],
            "warm_vs_pre_cache_ratio_met": (
                warm_vs_pre_ratio >= target["warm_vs_pre_cache_ratio_min"]
            ),
        }

    floor_attainment = _check(floor, "floor")
    floor_all_met = all(floor_attainment.values())
    stretch_attainment = _check(stretch, "stretch")
    stretch_all_met = all(stretch_attainment.values())

    proof: dict[str, Any] = {
        "proof_version": 1,
        "schema_version": cp.schema_version(),
        "primary_teacher_lane_id": grower.primary_teacher_lane_id,
        "entrypoint": "waggledance.core.reasoning.solver_router.SolverRouter.route",
        "corpus_total": len(hints),
        "before": {
            "served_total": pass1_served,
            "fallback_or_miss_total": pass1_misses,
            "signals_emitted_total": signals_emitted,
            "buffered_signals_flushed": flushed,
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
            "served_total": pass2_served,
            "served_via_capability_lookup_total": pass2_via_consult,
            "miss_total": pass2_misses,
            "per_family_served": per_family,
            "per_cell_served": per_cell,
        },
        "latency_pre_cache_baseline_ms": pre_cache_baseline,
        "latency_pass1_buffered_miss_ms": pass1_buffered_miss_stats,
        "latency_cold_after_promote_ms": cold_stats,
        "latency_warm_ms": warm_stats,
        "latency_warm_vs_pre_cache_ratio": warm_vs_pre_ratio,
        "pre_cache_served_via_consult_total": pre_cache_served,
        "p3_threshold_attainment": {
            "minimum_floor": {
                "all_met": floor_all_met,
                "details": floor_attainment,
                "thresholds": floor,
            },
            "stretch": {
                "all_met": stretch_all_met,
                "details": stretch_attainment,
                "thresholds": stretch,
            },
        },
        "buffered_sink": {
            "max_unflushed_signals_configured": hot_path.sink.max_unflushed_signals,
            "max_unflushed_age_ms_configured": hot_path.sink.max_unflushed_age_ms,
            "max_unflushed_signals_observed": hot_path.sink.stats.max_observed_unflushed_signals,
            "max_unflushed_age_ms_observed": round(
                hot_path.sink.stats.max_observed_unflushed_age_ms, 4
            ),
            "documented_hardkill_loss_bound_signals":
                hot_path.sink.hardkill_loss_bound_signals,
            "enqueued_total": hot_path.sink.stats.enqueued_total,
            "flushed_total": hot_path.sink.stats.flushed_total,
            "dropped_total": hot_path.sink.stats.dropped_total,
        },
        "hot_path_cache_stats": {
            "warm_hits": hot_path.stats.warm_hits,
            "cold_hits_warmed": hot_path.stats.cold_hits_warmed,
            "misses": hot_path.stats.misses,
            "by_family_warm_hits": dict(hot_path.stats.by_family_warm_hits),
            "by_family_cold_hits": dict(hot_path.stats.by_family_cold_hits),
            "warm_index_size_after_proof": hot_path.capability_index.size,
            "artifact_cache_size_after_proof": hot_path.artifact_cache.size,
        },
        "kpis": {
            "harvested_signals_total": cp.count_runtime_gap_signals(),
            "distinct_growth_intents_total": cp.count_growth_intents(),
            "queued_rows_total": cp.count_queue_rows(),
            "autogrowth_runs_total": cp.stats().table_counts["autogrowth_runs"],
            "auto_promotions_total": cp.count_solvers(status="auto_promoted"),
            "rejections_total": len(
                cp.list_promotion_decisions(decision="rejected", limit=20000)
            ),
            "rollbacks_total": len(
                cp.list_promotion_decisions(decision="rollback", limit=20000)
            ),
            "validation_runs_total": cp.stats().table_counts["validation_runs"],
            "shadow_evaluations_total": cp.stats().table_counts["shadow_evaluations"],
            "growth_events_total": cp.stats().table_counts["growth_events"],
            "solver_capability_features_total":
                cp.stats().table_counts["solver_capability_features"],
            "provider_jobs_total_after_proof": provider_jobs_after,
            "builder_jobs_total_after_proof": builder_jobs_after,
            "provider_jobs_delta_during_proof": (
                provider_jobs_after - provider_jobs_before
            ),
            "builder_jobs_delta_during_proof": (
                builder_jobs_after - builder_jobs_before
            ),
        },
        "control_plane_table_counts_after_proof": dict(after_counts),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "live_runtime_hotpath_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    cp.close()
    return proof


def _summarise(proof: dict) -> str:
    lines = [
        "Phase 14 — Live runtime hot-path proof",
        "=" * 40,
        f"entrypoint           = {proof['entrypoint']}",
        f"corpus_total         = {proof['corpus_total']}",
        f"primary_teacher_lane = {proof['primary_teacher_lane_id']}",
        "",
        "Pass 1 (before harvest):",
        f"  served               = {proof['before']['served_total']}",
        f"  miss                 = {proof['before']['fallback_or_miss_total']}",
        f"  signals_emitted      = {proof['before']['signals_emitted_total']}",
        f"  buffered_flushed     = {proof['before']['buffered_signals_flushed']}",
        "",
        "Harvest cycle:",
        f"  intents_created      = {proof['harvest']['intents_created']}",
        f"  scheduler_drained    = {proof['harvest']['scheduler_drained']}",
        f"  scheduler_promoted   = {proof['harvest']['scheduler_promoted']}",
        f"  scheduler_rejected   = {proof['harvest']['scheduler_rejected']}",
        f"  scheduler_errored    = {proof['harvest']['scheduler_errored']}",
        "",
        "Pass 2 (after harvest, cold cache):",
        f"  served                       = {proof['after']['served_total']}",
        f"  served_via_capability_lookup = {proof['after']['served_via_capability_lookup_total']}",
        f"  miss                         = {proof['after']['miss_total']}",
        "",
        "Latency:",
        f"  pre_cache_baseline_p50_ms = {proof['latency_pre_cache_baseline_ms']['p50_ms']}",
        f"  cold_p50_ms / p99_ms      = {proof['latency_cold_after_promote_ms']['p50_ms']} / {proof['latency_cold_after_promote_ms']['p99_ms']}",
        f"  warm_p50_ms / p99_ms      = {proof['latency_warm_ms']['p50_ms']} / {proof['latency_warm_ms']['p99_ms']}",
        f"  warm_vs_pre_cache_ratio   = {proof['latency_warm_vs_pre_cache_ratio']}x",
        "",
        f"P3 floor met:    {proof['p3_threshold_attainment']['minimum_floor']['all_met']}  ({proof['p3_threshold_attainment']['minimum_floor']['details']})",
        f"P3 stretch met:  {proof['p3_threshold_attainment']['stretch']['all_met']}  ({proof['p3_threshold_attainment']['stretch']['details']})",
        "",
        "Buffered sink:",
        f"  max_unflushed_signals_configured = {proof['buffered_sink']['max_unflushed_signals_configured']}",
        f"  max_unflushed_age_ms_configured  = {proof['buffered_sink']['max_unflushed_age_ms_configured']}",
        f"  max_unflushed_signals_observed   = {proof['buffered_sink']['max_unflushed_signals_observed']}",
        f"  documented_hardkill_loss_bound   = {proof['buffered_sink']['documented_hardkill_loss_bound_signals']}",
        "",
        "Hot path:",
        f"  warm_hits         = {proof['hot_path_cache_stats']['warm_hits']}",
        f"  cold_hits_warmed  = {proof['hot_path_cache_stats']['cold_hits_warmed']}",
        f"  misses            = {proof['hot_path_cache_stats']['misses']}",
        "",
        "KPIs:",
        f"  auto_promotions_total           = {proof['kpis']['auto_promotions_total']}",
        f"  growth_events_total             = {proof['kpis']['growth_events_total']}",
        f"  provider_jobs_delta_during_proof = {proof['kpis']['provider_jobs_delta_during_proof']}",
        f"  builder_jobs_delta_during_proof  = {proof['kpis']['builder_jobs_delta_during_proof']}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs" / "phase14_live_runtime_hotpath_2026_05_01",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = args.db or (args.out_dir / "live_runtime_hotpath_proof.db")
    proof = run(args.out_dir, db_path)
    print(_summarise(proof))
    print(f"\nWrote {args.out_dir / 'live_runtime_hotpath_proof.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
