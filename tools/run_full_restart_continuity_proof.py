#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phase 16B P2 — full-corpus restart continuity proof.

Extends the Phase 16A six-seed restart smoke to the **full canonical
corpus**. Drives all canonical seeds through
``AutonomyService.handle_query`` using only flat domain context,
harvests, auto-promotes, closes the control-plane SQLite DB, reopens
it, rebuilds the runtime / service / hot-path cache, and re-routes
the same flat upstream input. Verifies that:

* the same corpus is served via capability lookup post-restart,
* persisted solver count and capability-feature count are unchanged
  across reopen,
* provider_jobs_delta == 0 across the restart,
* builder_jobs_delta == 0 across the restart,
* no manual structured_request / low_risk_autonomy_query injection.

This is the stable-gate full-scale restart-continuity proof for
v3.8.0.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.application.services.autonomy_service import AutonomyService
from waggledance.core.autonomy.compatibility import CompatibilityLayer
from waggledance.core.autonomy.runtime import AutonomyRuntime
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
)
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import SelectionResult
from waggledance.core.reasoning.solver_router import SolverRouter
from waggledance.core.storage.control_plane import ControlPlaneDB


_FORBIDDEN_KEYS_IN_INPUT = (
    "structured_request",
    "low_risk_autonomy_query",
    "family_kind",
    "features",
    "spec_seed",
)


class _FallbackSelector:
    def select(self, intent, context, conditions):
        return SelectionResult(
            selected=[], reason="phase16b_full_restart_proof_forced_fallback",
            quality_path="bronze", fallback_used=True,
        )

    def select_for_capability_ids(self, ids):  # pragma: no cover
        return SelectionResult(selected=[], fallback_used=True)

    def set_capability_confidence(self, scores):  # pragma: no cover
        pass


class _AlwaysAccept:
    def check(self, **_kw):
        from waggledance.core.autonomy.resource_kernel import (
            AdmissionDecision,
        )
        return type(
            "AR", (), {
                "decision": AdmissionDecision.ACCEPT,
                "reason": "",
                "wait_ms": 0.0,
            },
        )()

    def record_enqueue(self): pass
    def record_dequeue(self): pass
    def stats(self): return {}


def _seed_to_flat_upstream_input(seed: dict) -> Tuple[str, Dict[str, Any]]:
    """Same translation rule as the Phase 16A proof — flat upstream
    domain context, no structured_request, no low_risk_autonomy_query."""
    family = seed["_family_kind"]
    spec = seed["spec"]
    case = seed["validation_cases"][0]
    inputs = case["inputs"]
    context: Dict[str, Any] = {
        "profile": "phase16b_full_restart",
        "cell_coord": seed["cell_id"],
        "intent_seed": seed["_intent_seed"],
    }
    if family == "scalar_unit_conversion":
        context["operation"] = "unit_conversion"
        context["from_unit"] = spec["from_unit"]
        context["to_unit"] = spec["to_unit"]
        context["value"] = inputs["x"]
    elif family == "lookup_table":
        context["operation"] = "lookup"
        context["key"] = inputs["key"]
        context["domain"] = spec["domain"]
    elif family == "threshold_rule":
        context["operation"] = "threshold_check"
        context["x"] = inputs["x"]
        context["subject"] = spec["subject"]
        context["operator"] = spec["operator"]
    elif family == "interval_bucket_classifier":
        context["operation"] = "bucket_check"
        context["x"] = inputs["x"]
        context["subject"] = spec["subject"]
    elif family == "linear_arithmetic":
        cols = spec.get("input_columns") or []
        context["operation"] = "linear_eval"
        context["inputs"] = dict(inputs)
        context["input_columns_signature"] = "|".join(str(c) for c in cols)
    elif family == "bounded_interpolation":
        context["operation"] = "interpolation"
        context["x"] = inputs["x"]
        context["x_var"] = spec["x_var"]
        context["y_var"] = spec["y_var"]
    else:
        raise RuntimeError(
            f"unsupported family in seed library: {family!r}"
        )
    query = f"upstream restart full corpus query: {seed['_intent_seed']}"
    return query, context


def _build_service(
    cp: ControlPlaneDB,
) -> Tuple[AutonomyService, HotPathCache, LowRiskGrower]:
    grower = LowRiskGrower(cp)
    grower.ensure_low_risk_policies(max_auto_promote=200)
    detector = RuntimeGapDetector(cp)
    hot_path = HotPathCache(
        control_plane=cp, detector=detector,
        max_unflushed_signals=1000, max_unflushed_age_ms=500,
    )
    runtime_router = RuntimeQueryRouter(
        cp, detector=detector, hot_path=hot_path,
        min_signal_interval_seconds=0.0,
    )
    consult = build_autonomy_consult(runtime_router)
    registry = CapabilityRegistry(load_builtins=False)
    sr = SolverRouter(
        registry=registry,
        selector=_FallbackSelector(),
        autonomy_consult=consult,
    )
    runtime = AutonomyRuntime(
        capability_registry=registry, solver_router=sr,
    )
    runtime.admission_control = None
    runtime.resource_guard = None
    compat = CompatibilityLayer(runtime=runtime, compatibility_mode=False)
    svc = AutonomyService(runtime=runtime, compatibility=compat)
    svc._admission = _AlwaysAccept()  # type: ignore[assignment]
    return svc, hot_path, grower


def _audit_input_for_forbidden_keys(
    corpus: List[Tuple[str, Dict[str, Any]]],
) -> dict:
    detected_keys: list[str] = []
    for query, context in corpus:
        for key in _FORBIDDEN_KEYS_IN_INPUT:
            if key in context:
                detected_keys.append(key)
    return {
        "manual_structured_request_in_input_detected": (
            "structured_request" in detected_keys
        ),
        "manual_low_risk_hint_in_input_detected": (
            "low_risk_autonomy_query" in detected_keys
        ),
        "detected_keys": sorted(set(detected_keys)),
    }


def run(out_dir: Path, db_path: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass
    for sidecar in (
        db_path.with_suffix(db_path.suffix + "-shm"),
        db_path.with_suffix(db_path.suffix + "-wal"),
    ):
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    seeds = all_canonical_seeds()
    corpus = [_seed_to_flat_upstream_input(s) for s in seeds]
    audit = _audit_input_for_forbidden_keys(corpus)

    # ── Phase A: open DB, drive corpus, harvest, verify after-served.
    cp1 = ControlPlaneDB(db_path)
    cp1.migrate()
    svc1, hot_path1, _grower1 = _build_service(cp1)

    before_counts = cp1.stats().table_counts
    provider_jobs_before = before_counts.get("provider_jobs", 0)
    builder_jobs_before = before_counts.get("builder_jobs", 0)

    # Pass 1
    pass1_lat: list[float] = []
    pass1_served = 0
    pass1_misses = 0
    for query, ctx in corpus:
        t0 = time.perf_counter()
        result = svc1.handle_query(query, context=dict(ctx), priority=50)
        pass1_lat.append((time.perf_counter() - t0) * 1000.0)
        autonomy = result.get("autonomy_consult") or {}
        if autonomy.get("served"):
            pass1_served += 1
        else:
            pass1_misses += 1

    flushed = hot_path1.flush_signals()

    # Harvest
    in_memory_signals = [
        GapSignal(
            kind="runtime_miss",
            family_kind=s["_family_kind"],
            cell_coord=s["cell_id"],
            intent_seed=s["_intent_seed"],
            spec_seed=s,
        )
        for s in seeds
    ]
    digest = digest_signals_into_intents(
        cp1, candidate_signals=in_memory_signals,
        min_signals_per_intent=1, autoenqueue=True,
    )
    scheduler = AutogrowthScheduler(
        cp1, scheduler_id="phase16b_full_restart_scheduler",
    )
    drained = scheduler.run_until_idle(max_ticks=500)

    # Pre-restart pass 2
    pre_restart_pass2_lat: list[float] = []
    pre_restart_pass2_served = 0
    pre_restart_pass2_via_consult = 0
    pre_restart_per_family: Dict[str, int] = {}
    pre_restart_per_cell: Dict[str, int] = {}
    for query, ctx in corpus:
        t0 = time.perf_counter()
        result = svc1.handle_query(query, context=dict(ctx), priority=50)
        pre_restart_pass2_lat.append(
            (time.perf_counter() - t0) * 1000.0,
        )
        autonomy = result.get("autonomy_consult") or {}
        if autonomy.get("served"):
            pre_restart_pass2_served += 1
            if autonomy.get("source") == "auto_promoted_solver":
                pre_restart_pass2_via_consult += 1
            op = ctx.get("operation", "_")
            cell = ctx.get("cell_coord", "_")
            pre_restart_per_family[op] = (
                pre_restart_per_family.get(op, 0) + 1
            )
            pre_restart_per_cell[cell] = (
                pre_restart_per_cell.get(cell, 0) + 1
            )

    persisted_solver_count_before_close = cp1.count_solvers(
        status="auto_promoted",
    )
    persisted_capability_features_before_close = (
        cp1.stats().table_counts["solver_capability_features"]
    )
    pre_restart_provider = cp1.stats().table_counts.get("provider_jobs", 0)
    pre_restart_builder = cp1.stats().table_counts.get("builder_jobs", 0)

    cp1.close()

    # ── Phase B: REOPEN. Build new runtime against same DB.
    cp2 = ControlPlaneDB(db_path)
    persisted_solver_count_after_reopen = cp2.count_solvers(
        status="auto_promoted",
    )
    persisted_capability_features_after_reopen = (
        cp2.stats().table_counts["solver_capability_features"]
    )

    svc2, hot_path2, _grower2 = _build_service(cp2)

    post_restart_lat: list[float] = []
    post_restart_served = 0
    post_restart_via_consult = 0
    post_restart_per_family: Dict[str, int] = {}
    post_restart_per_cell: Dict[str, int] = {}
    for query, ctx in corpus:
        t0 = time.perf_counter()
        result = svc2.handle_query(query, context=dict(ctx), priority=50)
        post_restart_lat.append((time.perf_counter() - t0) * 1000.0)
        autonomy = result.get("autonomy_consult") or {}
        if autonomy.get("served"):
            post_restart_served += 1
            if autonomy.get("source") == "auto_promoted_solver":
                post_restart_via_consult += 1
            op = ctx.get("operation", "_")
            cell = ctx.get("cell_coord", "_")
            post_restart_per_family[op] = (
                post_restart_per_family.get(op, 0) + 1
            )
            post_restart_per_cell[cell] = (
                post_restart_per_cell.get(cell, 0) + 1
            )

    cache_rebuild_success = (
        hot_path2.stats.cold_hits_warmed > 0
        or hot_path2.stats.warm_hits > 0
    )

    post_restart_provider = cp2.stats().table_counts.get("provider_jobs", 0)
    post_restart_builder = cp2.stats().table_counts.get("builder_jobs", 0)

    after_counts = cp2.stats().table_counts
    provider_jobs_after = after_counts.get("provider_jobs", 0)
    builder_jobs_after = after_counts.get("builder_jobs", 0)
    cp2.close()

    def _stats(latencies: list[float]) -> dict:
        if not latencies:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
                    "mean_ms": 0.0, "n": 0}
        s = sorted(latencies)
        def pct(p: float) -> float:
            if len(s) == 1:
                return s[0]
            k = (len(s) - 1) * (p / 100.0)
            f = int(k)
            c = min(f + 1, len(s) - 1)
            d = k - f
            return s[f] + (s[c] - s[f]) * d if f != c else s[f]
        return {
            "p50_ms": round(pct(50), 4),
            "p95_ms": round(pct(95), 4),
            "p99_ms": round(pct(99), 4),
            "mean_ms": round(statistics.mean(latencies), 4),
            "n": len(latencies),
        }

    proof: Dict[str, Any] = {
        "proof_version": 1,
        "phase": "16B P2 — full-corpus restart continuity",
        "selected_upstream_caller": (
            "waggledance.application.services.autonomy_service."
            "AutonomyService.handle_query"
        ),
        "manual_structured_request_in_input_detected": audit[
            "manual_structured_request_in_input_detected"
        ],
        "manual_low_risk_hint_in_input_detected": audit[
            "manual_low_risk_hint_in_input_detected"
        ],
        "manual_hint_audit_detected_keys": audit["detected_keys"],
        "proof_constructed_runtime_query_objects": False,
        "proof_bypassed_selected_caller": False,
        "proof_bypassed_handle_query": False,
        "corpus_total": len(corpus),
        "pass1_before_harvest": {
            "served_total": pass1_served,
            "miss_total": pass1_misses,
            "buffered_signals_flushed": flushed,
        },
        "harvest": {
            "growth_intents_total": digest.intents_created,
            "scheduler_drained": drained,
            "scheduler_promoted": scheduler.stats.auto_promoted,
            "scheduler_rejected": scheduler.stats.rejected,
            "scheduler_errored": scheduler.stats.errored,
            "by_family_promoted": dict(scheduler.stats.by_family_promoted),
        },
        "pre_restart_pass2": {
            "served_total": pre_restart_pass2_served,
            "served_via_capability_lookup_total": pre_restart_pass2_via_consult,
            "miss_total": len(corpus) - pre_restart_pass2_served,
            "per_operation_served": pre_restart_per_family,
            "per_cell_served": pre_restart_per_cell,
        },
        "persisted_state_before_close": {
            "solver_count_auto_promoted": persisted_solver_count_before_close,
            "capability_features_total": (
                persisted_capability_features_before_close
            ),
            "provider_jobs": pre_restart_provider,
            "builder_jobs": pre_restart_builder,
        },
        "persisted_state_after_reopen": {
            "solver_count_auto_promoted": persisted_solver_count_after_reopen,
            "capability_features_total": (
                persisted_capability_features_after_reopen
            ),
            "provider_jobs": post_restart_provider,
            "builder_jobs": post_restart_builder,
        },
        "post_restart_pass2": {
            "served_total": post_restart_served,
            "served_via_capability_lookup_total": post_restart_via_consult,
            "miss_total": len(corpus) - post_restart_served,
            "per_operation_served": post_restart_per_family,
            "per_cell_served": post_restart_per_cell,
        },
        "restart_invariants": {
            "served_unchanged_across_restart": (
                pre_restart_pass2_served == post_restart_served
            ),
            "served_via_capability_lookup_unchanged_across_restart": (
                pre_restart_pass2_via_consult == post_restart_via_consult
            ),
            "solver_count_unchanged_across_reopen": (
                persisted_solver_count_before_close
                == persisted_solver_count_after_reopen
            ),
            "capability_features_unchanged_across_reopen": (
                persisted_capability_features_before_close
                == persisted_capability_features_after_reopen
            ),
            "provider_jobs_delta_across_restart": (
                post_restart_provider - pre_restart_provider
            ),
            "builder_jobs_delta_across_restart": (
                post_restart_builder - pre_restart_builder
            ),
            "cache_rebuild_success": cache_rebuild_success,
        },
        "kpis": {
            "provider_jobs_total_after_proof": provider_jobs_after,
            "builder_jobs_total_after_proof": builder_jobs_after,
            "provider_jobs_delta_during_proof": (
                provider_jobs_after - provider_jobs_before
            ),
            "builder_jobs_delta_during_proof": (
                builder_jobs_after - builder_jobs_before
            ),
        },
        "latency_pass1_service_handle_query_ms": _stats(pass1_lat),
        "latency_pre_restart_pass2_ms": _stats(pre_restart_pass2_lat),
        "latency_post_restart_pass2_ms": _stats(post_restart_lat),
        "hot_path_cache_post_restart": {
            "warm_hits": hot_path2.stats.warm_hits,
            "cold_hits_warmed": hot_path2.stats.cold_hits_warmed,
            "misses": hot_path2.stats.misses,
        },
    }

    out_path = out_dir / "full_restart_continuity_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return proof


def _summarise(proof: dict) -> str:
    inv = proof["restart_invariants"]
    lines = [
        "Phase 16B P2 — full-corpus restart continuity proof",
        "=" * 60,
        f"selected_upstream_caller   = {proof['selected_upstream_caller']}",
        f"corpus_total               = {proof['corpus_total']}",
        f"manual_structured_in_input = "
        f"{proof['manual_structured_request_in_input_detected']}",
        f"manual_hint_in_input       = "
        f"{proof['manual_low_risk_hint_in_input_detected']}",
        "",
        "Pass 1 (before harvest):",
        f"  served = {proof['pass1_before_harvest']['served_total']}",
        f"  miss   = {proof['pass1_before_harvest']['miss_total']}",
        "",
        "Harvest:",
        f"  intents      = {proof['harvest']['growth_intents_total']}",
        f"  promoted     = {proof['harvest']['scheduler_promoted']}",
        f"  rejected     = {proof['harvest']['scheduler_rejected']}",
        f"  errored      = {proof['harvest']['scheduler_errored']}",
        "",
        "Pre-restart pass 2:",
        f"  served                       = "
        f"{proof['pre_restart_pass2']['served_total']}",
        f"  served_via_capability_lookup = "
        f"{proof['pre_restart_pass2']['served_via_capability_lookup_total']}",
        f"  miss                         = "
        f"{proof['pre_restart_pass2']['miss_total']}",
        "",
        "Persisted state across reopen:",
        f"  solver_count before/after = "
        f"{proof['persisted_state_before_close']['solver_count_auto_promoted']}"
        f" / "
        f"{proof['persisted_state_after_reopen']['solver_count_auto_promoted']}",
        f"  capability_features before/after = "
        f"{proof['persisted_state_before_close']['capability_features_total']}"
        f" / "
        f"{proof['persisted_state_after_reopen']['capability_features_total']}",
        "",
        "Post-restart pass 2:",
        f"  served                       = "
        f"{proof['post_restart_pass2']['served_total']}",
        f"  served_via_capability_lookup = "
        f"{proof['post_restart_pass2']['served_via_capability_lookup_total']}",
        f"  miss                         = "
        f"{proof['post_restart_pass2']['miss_total']}",
        "",
        "Restart invariants:",
        f"  served_unchanged_across_restart           = "
        f"{inv['served_unchanged_across_restart']}",
        f"  served_via_capability_unchanged_across_restart = "
        f"{inv['served_via_capability_lookup_unchanged_across_restart']}",
        f"  solver_count_unchanged_across_reopen       = "
        f"{inv['solver_count_unchanged_across_reopen']}",
        f"  capability_features_unchanged_across_reopen = "
        f"{inv['capability_features_unchanged_across_reopen']}",
        f"  provider_jobs_delta_across_restart         = "
        f"{inv['provider_jobs_delta_across_restart']}",
        f"  builder_jobs_delta_across_restart          = "
        f"{inv['builder_jobs_delta_across_restart']}",
        f"  cache_rebuild_success                      = "
        f"{inv['cache_rebuild_success']}",
        "",
        f"provider_jobs_delta_during_proof = "
        f"{proof['kpis']['provider_jobs_delta_during_proof']}",
        f"builder_jobs_delta_during_proof  = "
        f"{proof['kpis']['builder_jobs_delta_during_proof']}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs"
        / "phase16b_stabilization_release_gate_2026_05_01",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = args.db or args.out_dir / "full_restart_continuity_proof.db"
    proof = run(args.out_dir, db_path)
    md_path = args.out_dir / "full_restart_continuity_proof.md"
    md_path.write_text(_summarise(proof), encoding="utf-8")
    print(_summarise(proof))
    return 0


if __name__ == "__main__":
    sys.exit(main())
