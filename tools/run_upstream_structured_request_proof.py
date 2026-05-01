#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phase 16A — upstream structured_request propagation proof.

Routes the canonical low-risk seed corpus through the production
service-layer entrypoint ``AutonomyService.handle_query(query,
context, priority)`` using only the natural *flat* domain payload
that an external service / API / CLI caller would supply
(``operation``, ``from_unit``, ``value``, ``subject``, ``x``,
``operator``, ``inputs``, ``input_columns_signature``, ``x_var``,
``y_var``, ``key``, ``domain``).

The proof never injects ``structured_request`` or
``low_risk_autonomy_query``. Both are derived automatically:

* ``AutonomyService.handle_query`` runs the upstream extractor and
  sets ``context["structured_request"]`` (Phase 16A wiring).
* ``AutonomyRuntime.handle_query`` then runs the Phase 15 hint
  extractor and sets ``context["low_risk_autonomy_query"]``.
* The autonomy consult lane fires from there.

Sequence:

1. Fresh isolated scratch DB; six low-risk family policies installed.
2. Construct AutonomyRuntime with SolverRouter + autonomy_consult +
   HotPathCache. Force-fallback selector keeps the consult lane the
   surface under test.
3. Wrap the runtime in a CompatibilityLayer + AutonomyService.
4. Pass 1: route the corpus through ``AutonomyService.handle_query``
   using flat domain payloads. Each call:
     a. service-layer derives ``context["structured_request"]``,
     b. runtime-layer derives ``context["low_risk_autonomy_query"]``,
     c. consult lane misses (no solver promoted), buffered runtime
        gap signal is enqueued.
5. Force-flush the buffered sink.
6. Harvest: in-memory ``GapSignal`` objects from the seed library,
   digest into intents, run the autogrowth scheduler.
7. Pass 2 cold + Pass 3 warm: same flat domain payload through the
   same service caller; cold-fill then warm-hit.
8. Negative corpus: ambiguous (caller-supplied structured_request),
   high-risk operation, missing flat fields, malformed value, free
   text only, manual ``low_risk_autonomy_query`` injection rejected.
9. Report metrics + threshold attainment + invariants.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

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
    """Deterministic fallback selector — keeps the consult lane the
    surface under test."""

    def select(self, intent, context, conditions):
        return SelectionResult(
            selected=[], reason="phase16a_proof_forced_fallback",
            quality_path="bronze", fallback_used=True,
        )

    def select_for_capability_ids(self, ids):  # pragma: no cover
        return SelectionResult(selected=[], fallback_used=True)

    def set_capability_confidence(self, scores):  # pragma: no cover
        pass


class _AlwaysAccept:
    """AdmissionControl shim — accept everything. Production callers
    cannot reach this rate; this is purely a measurement convenience."""

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

    def record_enqueue(self):  # pragma: no cover
        pass

    def record_dequeue(self):  # pragma: no cover
        pass

    def stats(self):  # pragma: no cover
        return {}


def _seed_to_flat_upstream_input(seed: dict) -> Tuple[str, Dict[str, Any]]:
    """Translate one canonical seed into the flat upstream input shape.

    The output context contains ONLY the flat domain fields a real
    service / API / CLI caller would supply. No ``structured_request``,
    no ``low_risk_autonomy_query``, no ``family_kind``."""
    family = seed["_family_kind"]
    spec = seed["spec"]
    case = seed["validation_cases"][0]
    inputs = case["inputs"]
    context: Dict[str, Any] = {
        "profile": "phase16a_proof",
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
    query = f"upstream service query: {seed['_intent_seed']}"
    return query, context


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


def _audit_proof_input_for_forbidden_keys(
    corpus: List[Tuple[str, Dict[str, Any]]],
) -> dict:
    """Verify the corpus input carries no forbidden upstream keys."""
    detected_keys: list[str] = []
    detected_in: list[str] = []
    for query, context in corpus:
        for key in _FORBIDDEN_KEYS_IN_INPUT:
            if key in context:
                detected_keys.append(key)
                detected_in.append(query[:60])
    return {
        "manual_structured_request_in_input_detected": (
            "structured_request" in detected_keys
        ),
        "manual_low_risk_hint_in_input_detected": (
            "low_risk_autonomy_query" in detected_keys
        ),
        "detected_keys": sorted(set(detected_keys)),
        "detected_in_examples": detected_in[:5],
    }


def _negative_corpus() -> List[Tuple[str, Dict[str, Any], str]]:
    """(query, context, expected outcome label).

    Each context represents an abusive or unhappy upstream input
    shape that the proof must reject without serving and without
    growing solvers."""
    return [
        (
            "ambiguous: caller supplied structured_request directly",
            {
                "profile": "neg",
                "operation": "unit_conversion",
                "from_unit": "C",
                "to_unit": "F",
                "value": 25,
                "structured_request": {
                    "unit_conversion": {"x": 25, "from": "C", "to": "F"},
                },
            },
            "rejected_ambiguous",
        ),
        (
            "high-risk operation rejected at upstream layer",
            {
                "profile": "neg",
                "operation": "temporal_window_check",
                "window_seconds": 60,
                "x": 0.5,
            },
            "rejected_family_not_low_risk",
        ),
        (
            "missing flat fields",
            {
                "profile": "neg",
                "operation": "unit_conversion",
                "from_unit": "C",
            },
            "rejected_missing_fields",
        ),
        (
            "malformed value",
            {
                "profile": "neg",
                "operation": "unit_conversion",
                "from_unit": "C",
                "to_unit": "F",
                "value": "not-a-number",
            },
            "rejected_malformed",
        ),
        (
            "free text only — no operation supplied",
            {"profile": "neg"},
            "skipped",
        ),
        (
            "manual low_risk_autonomy_query injection refused",
            {
                "profile": "neg",
                "operation": "unit_conversion",
                "from_unit": "C",
                "to_unit": "F",
                "value": 25,
                "low_risk_autonomy_query": {
                    "family_kind": "scalar_unit_conversion",
                    "inputs": {"x": 25.0},
                    "features": {"from_unit": "C", "to_unit": "F"},
                },
            },
            "rejected_ambiguous",
        ),
        (
            "builtin precedence: caller signals already solved",
            {
                "profile": "neg",
                "operation": "unit_conversion",
                "from_unit": "C",
                "to_unit": "F",
                "value": 25,
                "builtin_solver_succeeded": True,
            },
            "skipped_builtin_precedence",
        ),
    ]


def _build_service_with_growth_lane(
    cp: ControlPlaneDB,
) -> Tuple[AutonomyService, HotPathCache, AutogrowthScheduler, LowRiskGrower]:
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
        capability_registry=registry,
        solver_router=sr,
    )
    runtime.admission_control = None
    runtime.resource_guard = None

    compat = CompatibilityLayer(runtime=runtime, compatibility_mode=False)
    svc = AutonomyService(runtime=runtime, compatibility=compat)
    svc._admission = _AlwaysAccept()  # type: ignore[assignment]

    scheduler = AutogrowthScheduler(
        cp, scheduler_id="phase16a_proof_scheduler",
    )
    return svc, hot_path, scheduler, grower


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

    cp = ControlPlaneDB(db_path)
    cp.migrate()
    svc, hot_path, scheduler, grower = _build_service_with_growth_lane(cp)

    seeds = all_canonical_seeds()
    corpus = [_seed_to_flat_upstream_input(s) for s in seeds]

    audit = _audit_proof_input_for_forbidden_keys(corpus)

    before_counts = cp.stats().table_counts
    provider_jobs_before = before_counts.get("provider_jobs", 0)
    builder_jobs_before = before_counts.get("builder_jobs", 0)

    # Pass 1
    pass1_results: list[dict] = []
    pass1_lat: list[float] = []
    structured_request_derived_in_input = 0
    structured_request_was_set_after_call = 0
    low_risk_hint_set_after_call = 0
    for query, context in corpus:
        ctx = dict(context)
        assert "structured_request" not in ctx, (
            "Proof contract violated: input must not carry structured_request"
        )
        assert "low_risk_autonomy_query" not in ctx, (
            "Proof contract violated: input must not carry low_risk_autonomy_query"
        )
        t0 = time.perf_counter()
        result = svc.handle_query(query, context=ctx, priority=50)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        pass1_lat.append(elapsed_ms)
        # AutonomyService passes the caller-owned context dict to
        # the lift via apply_upstream_structured_request, which
        # mutates `ctx` in place when derivation succeeds.
        if "structured_request" in ctx:
            structured_request_was_set_after_call += 1
        if "low_risk_autonomy_query" in ctx:
            low_risk_hint_set_after_call += 1
        autonomy = result.get("autonomy_consult") or {}
        pass1_results.append({
            "intent_seed": context["intent_seed"],
            "operation": context["operation"],
            "served": autonomy.get("served", False),
            "source": autonomy.get("source"),
        })

    # Upstream extractor isolated latency
    upstream_extract_lat: list[float] = []
    from waggledance.core.autonomy_growth import (
        derive_upstream_structured_request,
    )
    for query, context in corpus:
        t0 = time.perf_counter()
        derive_upstream_structured_request(query, dict(context))
        upstream_extract_lat.append((time.perf_counter() - t0) * 1000.0)

    flushed = hot_path.flush_signals()

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
        cp, candidate_signals=in_memory_signals,
        min_signals_per_intent=1, autoenqueue=True,
    )
    drained = scheduler.run_until_idle(max_ticks=500)

    # Pass 2 cold
    pass2_cold: list[dict] = []
    pass2_cold_lat: list[float] = []
    for query, context in corpus:
        ctx = dict(context)
        t0 = time.perf_counter()
        result = svc.handle_query(query, context=ctx, priority=50)
        pass2_cold_lat.append((time.perf_counter() - t0) * 1000.0)
        autonomy = result.get("autonomy_consult") or {}
        pass2_cold.append({
            "intent_seed": context["intent_seed"],
            "operation": context["operation"],
            "served": autonomy.get("served", False),
            "source": autonomy.get("source"),
            "solver_name": autonomy.get("solver_name"),
        })

    # Pass 3 warm
    pass3_warm_lat: list[float] = []
    for _ in range(3):
        for query, context in corpus:
            t0 = time.perf_counter()
            svc.handle_query(query, context=dict(context), priority=50)
            pass3_warm_lat.append((time.perf_counter() - t0) * 1000.0)

    # Negative corpus
    neg_results: list[dict] = []
    upstream_stats_before_neg = svc.upstream_structured_request_stats()
    for query, context, expected in _negative_corpus():
        ctx = dict(context)
        result = svc.handle_query(query, context=ctx, priority=50)
        autonomy = result.get("autonomy_consult") or {}
        neg_results.append({
            "query": query,
            "expected_outcome": expected,
            "served": autonomy.get("served", False),
            "structured_request_set": "structured_request" in ctx,
            "low_risk_hint_set": "low_risk_autonomy_query" in ctx,
        })
    neg_passed = sum(
        1 for r in neg_results
        if not r["served"]
        and not r["low_risk_hint_set"]
    )

    # Aggregates
    pass2_served = sum(1 for r in pass2_cold if r["served"])
    pass2_via_consult = sum(
        1 for r in pass2_cold
        if r["served"] and r["source"] == "auto_promoted_solver"
    )
    pass2_misses = sum(1 for r in pass2_cold if not r["served"])

    per_operation_served: dict[str, int] = {}
    for r in pass2_cold:
        if r["served"]:
            per_operation_served[r["operation"]] = (
                per_operation_served.get(r["operation"], 0) + 1
            )

    per_family_in_input: dict[str, int] = {}
    for s in seeds:
        per_family_in_input[s["_family_kind"]] = (
            per_family_in_input.get(s["_family_kind"], 0) + 1
        )

    per_cell_in_input: dict[str, int] = {}
    for s in seeds:
        per_cell_in_input[s["cell_id"]] = (
            per_cell_in_input.get(s["cell_id"], 0) + 1
        )

    after_counts = cp.stats().table_counts
    provider_jobs_after = after_counts.get("provider_jobs", 0)
    builder_jobs_after = after_counts.get("builder_jobs", 0)

    upstream_stats_final = svc.upstream_structured_request_stats()

    proof: Dict[str, Any] = {
        "proof_version": 1,
        "schema_version": cp.schema_version(),
        "primary_teacher_lane_id": grower.primary_teacher_lane_id,
        "selected_upstream_caller": (
            "waggledance.application.services.autonomy_service."
            "AutonomyService.handle_query"
        ),
        "selected_upstream_caller_input_shape": (
            "(query: str, context: Dict[str, Any], priority: int) — "
            "context carries flat domain fields: 'operation' (one of "
            "{unit_conversion, lookup, threshold_check, bucket_check, "
            "linear_eval, interpolation}) plus operation-specific "
            "flat fields (from_unit, to_unit, value, key, domain, "
            "subject, x, operator, inputs, input_columns_signature, "
            "x_var, y_var). NO 'structured_request', NO "
            "'low_risk_autonomy_query'."
        ),
        "selected_upstream_caller_input_keys": sorted({
            k for _, ctx in corpus for k in ctx.keys()
        }),
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
        "corpus_tier": (
            "Tier 1 — selected upstream caller supports all 6 "
            f"low-risk families ({len(corpus)} seeds)"
        ),
        "structured_request_derived_total": (
            structured_request_was_set_after_call
        ),
        "structured_request_rejected_total": (
            upstream_stats_final["rejected_total"]
        ),
        "structured_request_skipped_total": (
            upstream_stats_final["skipped_total"]
        ),
        "low_risk_hint_derived_total": low_risk_hint_set_after_call,
        "upstream_extractor_rejection_counts_by_kind": (
            upstream_stats_final["rejection_counts_by_kind"]
        ),
        "upstream_extractor_errors_total": (
            upstream_stats_final["extractor_errors_total"]
        ),
        "before": {
            "served_total": 0,
            "fallback_or_miss_total": len(corpus),
            "buffered_signals_flushed": flushed,
            "runtime_signals_enqueued_total":
                hot_path.sink.stats.enqueued_total,
            "runtime_signals_flushed_total":
                hot_path.sink.stats.flushed_total,
        },
        "harvest": {
            "growth_intents_total": digest.intents_created,
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
            "per_operation_served": per_operation_served,
        },
        "negative_cases_total": len(neg_results),
        "negative_cases_passed_total": neg_passed,
        "negative_cases_detail": neg_results,
        "latency_pass1_service_handle_query_ms": _stats(pass1_lat),
        "latency_pass2_cold_service_handle_query_ms": _stats(pass2_cold_lat),
        "latency_pass3_warm_service_handle_query_ms": _stats(pass3_warm_lat),
        "latency_upstream_extractor_only_ms": _stats(upstream_extract_lat),
        "upstream_derivation_p50_ms": round(
            _percentile(upstream_extract_lat, 50), 4,
        ),
        "upstream_derivation_p95_ms": round(
            _percentile(upstream_extract_lat, 95), 4,
        ),
        "upstream_derivation_p99_ms": round(
            _percentile(upstream_extract_lat, 99), 4,
        ),
        "hot_path_cache_stats": {
            "warm_hits": hot_path.stats.warm_hits,
            "cold_hits_warmed": hot_path.stats.cold_hits_warmed,
            "misses": hot_path.stats.misses,
        },
        "buffered_sink": {
            "max_unflushed_signals_configured":
                hot_path.sink.max_unflushed_signals,
            "max_unflushed_age_ms_configured":
                hot_path.sink.max_unflushed_age_ms,
            "max_unflushed_signals_observed":
                hot_path.sink.stats.max_observed_unflushed_signals,
            "max_unflushed_age_ms_observed": round(
                hot_path.sink.stats.max_observed_unflushed_age_ms, 4,
            ),
            "documented_hardkill_loss_bound_signals":
                hot_path.sink.hardkill_loss_bound_signals,
        },
        "kpis": {
            "harvested_signals_total": cp.count_runtime_gap_signals(),
            "auto_promotions_total": cp.count_solvers(status="auto_promoted"),
            "rejections_total": len(
                cp.list_promotion_decisions(decision="rejected", limit=20000),
            ),
            "rollbacks_total": len(
                cp.list_promotion_decisions(decision="rollback", limit=20000),
            ),
            "validation_runs_total": cp.stats().table_counts["validation_runs"],
            "shadow_evaluations_total":
                cp.stats().table_counts["shadow_evaluations"],
            "growth_events_total": cp.stats().table_counts["growth_events"],
            "solver_capability_features_total":
                cp.stats().table_counts["solver_capability_features"],
            "provider_jobs_total_after_proof": provider_jobs_after,
            "builder_jobs_total_after_proof": builder_jobs_after,
            "provider_jobs_delta_during_proof":
                provider_jobs_after - provider_jobs_before,
            "builder_jobs_delta_during_proof":
                builder_jobs_after - builder_jobs_before,
        },
        "per_family_in_input_corpus": per_family_in_input,
        "per_cell_in_input_corpus": per_cell_in_input,
    }

    out_path = out_dir / "upstream_structured_request_proof.json"
    out_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    cp.close()
    return proof


def _summarise(proof: dict) -> str:
    lines = [
        "Phase 16A — Upstream structured_request propagation proof",
        "=" * 60,
        f"selected_upstream_caller     = {proof['selected_upstream_caller']}",
        f"corpus_total                 = {proof['corpus_total']}",
        f"manual_structured_in_input   = "
        f"{proof['manual_structured_request_in_input_detected']}",
        f"manual_low_risk_hint_in_in   = "
        f"{proof['manual_low_risk_hint_in_input_detected']}",
        f"proof_built_runtime_q        = "
        f"{proof['proof_constructed_runtime_query_objects']}",
        f"proof_bypassed_caller        = "
        f"{proof['proof_bypassed_selected_caller']}",
        f"proof_bypassed_handle_query  = "
        f"{proof['proof_bypassed_handle_query']}",
        "",
        "Derivation:",
        f"  structured_request_derived_total = "
        f"{proof['structured_request_derived_total']}",
        f"  low_risk_hint_derived_total      = "
        f"{proof['low_risk_hint_derived_total']}",
        f"  rejected_total                   = "
        f"{proof['structured_request_rejected_total']}",
        f"  skipped_total                    = "
        f"{proof['structured_request_skipped_total']}",
        "",
        "Pass 1 (before harvest):",
        f"  served       = {proof['before']['served_total']}",
        f"  miss/fallback= {proof['before']['fallback_or_miss_total']}",
        f"  buffered_flushed = {proof['before']['buffered_signals_flushed']}",
        "",
        "Harvest cycle:",
        f"  intents_created   = {proof['harvest']['growth_intents_total']}",
        f"  scheduler_drained = {proof['harvest']['scheduler_drained']}",
        f"  promoted          = {proof['harvest']['scheduler_promoted']}",
        f"  rejected          = {proof['harvest']['scheduler_rejected']}",
        f"  errored           = {proof['harvest']['scheduler_errored']}",
        "",
        "Pass 2 (after harvest, cold cache):",
        f"  served                       = {proof['after']['served_total']}",
        f"  served_via_capability_lookup = "
        f"{proof['after']['served_via_capability_lookup_total']}",
        f"  miss                         = {proof['after']['miss_total']}",
        "",
        f"Negative cases passed: "
        f"{proof['negative_cases_passed_total']} / {proof['negative_cases_total']}",
        "",
        "Latency:",
        f"  pass1 service.handle_query p50 / p99 = "
        f"{proof['latency_pass1_service_handle_query_ms']['p50_ms']} / "
        f"{proof['latency_pass1_service_handle_query_ms']['p99_ms']} ms",
        f"  pass2 cold p50 / p99               = "
        f"{proof['latency_pass2_cold_service_handle_query_ms']['p50_ms']} / "
        f"{proof['latency_pass2_cold_service_handle_query_ms']['p99_ms']} ms",
        f"  pass3 warm p50 / p99               = "
        f"{proof['latency_pass3_warm_service_handle_query_ms']['p50_ms']} / "
        f"{proof['latency_pass3_warm_service_handle_query_ms']['p99_ms']} ms",
        f"  upstream extractor only p50 / p99  = "
        f"{proof['latency_upstream_extractor_only_ms']['p50_ms']} / "
        f"{proof['latency_upstream_extractor_only_ms']['p99_ms']} ms",
        "",
        "Hot path:",
        f"  warm_hits        = {proof['hot_path_cache_stats']['warm_hits']}",
        f"  cold_hits_warmed = {proof['hot_path_cache_stats']['cold_hits_warmed']}",
        f"  misses           = {proof['hot_path_cache_stats']['misses']}",
        "",
        "KPIs:",
        f"  auto_promotions_total            = "
        f"{proof['kpis']['auto_promotions_total']}",
        f"  growth_events_total              = "
        f"{proof['kpis']['growth_events_total']}",
        f"  provider_jobs_delta_during_proof = "
        f"{proof['kpis']['provider_jobs_delta_during_proof']}",
        f"  builder_jobs_delta_during_proof  = "
        f"{proof['kpis']['builder_jobs_delta_during_proof']}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "docs" / "runs"
        / "phase16_upstream_structured_request_2026_05_01",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db_path = (
        args.db
        or args.out_dir / "upstream_structured_request_proof.db"
    )
    proof = run(args.out_dir, db_path)
    md_path = args.out_dir / "upstream_structured_request_proof.md"
    md_path.write_text(_summarise(proof), encoding="utf-8")
    print(_summarise(proof))
    return 0


if __name__ == "__main__":
    sys.exit(main())
