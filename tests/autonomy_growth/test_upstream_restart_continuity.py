"""Phase 16A P5 — restart-continuity smoke.

Proves that solvers auto-promoted via the upstream service-layer
caller survive a control-plane close+reopen cycle. After the
runtime is rebuilt against the reopened DB, the same flat upstream
input continues to be served via capability lookup with no
provider/builder activity.

Scope intentionally narrow:
* one-cell, full six-family corpus is overkill — use a handful of
  seeds from each family to keep the smoke fast,
* no Docker, no FAISS, no Chroma,
* no provider lane, no builder lane.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

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


class _FallbackSelector:
    def select(self, intent, context, conditions):
        return SelectionResult(
            selected=[], reason="phase16a_restart_proof_forced_fallback",
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


def _seed_to_flat_input(seed: dict) -> Tuple[str, Dict]:
    """Same translation rule the proof uses, in test scope."""
    family = seed["_family_kind"]
    spec = seed["spec"]
    case = seed["validation_cases"][0]
    inputs = case["inputs"]
    ctx: Dict = {
        "profile": "phase16a_restart",
        "cell_coord": seed["cell_id"],
        "intent_seed": seed["_intent_seed"],
    }
    if family == "scalar_unit_conversion":
        ctx["operation"] = "unit_conversion"
        ctx["from_unit"] = spec["from_unit"]
        ctx["to_unit"] = spec["to_unit"]
        ctx["value"] = inputs["x"]
    elif family == "lookup_table":
        ctx["operation"] = "lookup"
        ctx["key"] = inputs["key"]
        ctx["domain"] = spec["domain"]
    elif family == "threshold_rule":
        ctx["operation"] = "threshold_check"
        ctx["x"] = inputs["x"]
        ctx["subject"] = spec["subject"]
        ctx["operator"] = spec["operator"]
    elif family == "interval_bucket_classifier":
        ctx["operation"] = "bucket_check"
        ctx["x"] = inputs["x"]
        ctx["subject"] = spec["subject"]
    elif family == "linear_arithmetic":
        cols = spec.get("input_columns") or []
        ctx["operation"] = "linear_eval"
        ctx["inputs"] = dict(inputs)
        ctx["input_columns_signature"] = "|".join(str(c) for c in cols)
    elif family == "bounded_interpolation":
        ctx["operation"] = "interpolation"
        ctx["x"] = inputs["x"]
        ctx["x_var"] = spec["x_var"]
        ctx["y_var"] = spec["y_var"]
    return f"upstream restart query: {seed['_intent_seed']}", ctx


def _build_service(cp: ControlPlaneDB) -> Tuple[AutonomyService, HotPathCache]:
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
    return svc, hot_path


@pytest.fixture
def scratch_db_path(tmp_path: Path) -> Path:
    return tmp_path / "phase16a_restart.db"


def test_promoted_solvers_survive_db_close_reopen(scratch_db_path: Path):
    seeds = all_canonical_seeds()
    # Trim corpus to one seed per family to keep the smoke fast.
    seen_families: set[str] = set()
    smoke_seeds = []
    for s in seeds:
        if s["_family_kind"] not in seen_families:
            smoke_seeds.append(s)
            seen_families.add(s["_family_kind"])
        if len(seen_families) == 6:
            break
    assert len(smoke_seeds) == 6
    corpus = [_seed_to_flat_input(s) for s in smoke_seeds]

    # ── Phase A: open DB, drive corpus, harvest, verify after-served.
    cp1 = ControlPlaneDB(scratch_db_path)
    cp1.migrate()
    svc1, hot_path1 = _build_service(cp1)

    # Pass 1
    for query, ctx in corpus:
        svc1.handle_query(query, context=dict(ctx), priority=50)

    flushed = hot_path1.flush_signals()
    assert flushed >= 0  # bounded — at least non-negative

    # Harvest
    in_memory = [
        GapSignal(
            kind="runtime_miss",
            family_kind=s["_family_kind"],
            cell_coord=s["cell_id"],
            intent_seed=s["_intent_seed"],
            spec_seed=s,
        )
        for s in smoke_seeds
    ]
    digest = digest_signals_into_intents(
        cp1, candidate_signals=in_memory,
        min_signals_per_intent=1, autoenqueue=True,
    )
    scheduler = AutogrowthScheduler(
        cp1, scheduler_id="phase16a_restart_scheduler",
    )
    drained = scheduler.run_until_idle(max_ticks=200)
    assert digest.intents_created == 6
    assert scheduler.stats.auto_promoted == 6

    # Pass 2 (cold, in same DB)
    pass2_a_served = 0
    for query, ctx in corpus:
        result = svc1.handle_query(query, context=dict(ctx), priority=50)
        if (result.get("autonomy_consult") or {}).get("served"):
            pass2_a_served += 1
    assert pass2_a_served == 6

    persisted_solver_count_before_close = cp1.count_solvers(
        status="auto_promoted",
    )
    persisted_capability_features_before_close = (
        cp1.stats().table_counts["solver_capability_features"]
    )
    assert persisted_solver_count_before_close == 6
    assert persisted_capability_features_before_close > 0

    # Snapshot provider/builder counts before restart
    pre_restart_provider = cp1.stats().table_counts.get("provider_jobs", 0)
    pre_restart_builder = cp1.stats().table_counts.get("builder_jobs", 0)

    cp1.close()

    # ── Phase B: REOPEN. Build new runtime against same DB. Same input
    #              must still be served. No provider/builder activity.
    cp2 = ControlPlaneDB(scratch_db_path)
    # Do NOT call migrate() again — schema must already exist from
    # cp1.migrate() above; reopen is a continuity test, not a fresh
    # bootstrap.
    persisted_solver_count_after_reopen = cp2.count_solvers(
        status="auto_promoted",
    )
    persisted_capability_features_after_reopen = (
        cp2.stats().table_counts["solver_capability_features"]
    )
    assert (
        persisted_solver_count_after_reopen
        == persisted_solver_count_before_close
    )
    assert (
        persisted_capability_features_after_reopen
        == persisted_capability_features_before_close
    )

    svc2, hot_path2 = _build_service(cp2)

    # The reopened HotPathCache starts cold; the cold-fill path
    # rebuilds the warm capability index from the persisted control
    # plane on first dispatch.
    restart_served = 0
    restart_misses = 0
    for query, ctx in corpus:
        result = svc2.handle_query(query, context=dict(ctx), priority=50)
        autonomy = result.get("autonomy_consult") or {}
        if autonomy.get("served"):
            restart_served += 1
        else:
            restart_misses += 1

    assert restart_served == 6, (
        f"restart continuity broken: only {restart_served}/6 served "
        f"after reopen; misses={restart_misses}"
    )
    # Cache rebuild used at least one cold-warm transition.
    cache_rebuild_success = (
        hot_path2.stats.cold_hits_warmed > 0
        or hot_path2.stats.warm_hits > 0
    )
    assert cache_rebuild_success

    # Provider/builder delta must remain zero across the restart.
    post_restart_provider = cp2.stats().table_counts.get("provider_jobs", 0)
    post_restart_builder = cp2.stats().table_counts.get("builder_jobs", 0)
    assert post_restart_provider - pre_restart_provider == 0
    assert post_restart_builder - pre_restart_builder == 0

    cp2.close()
