# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Autonomous low-risk solver growth lane (Phase 11).

This package implements the closed no-human loop that lets WaggleDance
auto-promote a bounded allowlist of deterministic, side-effect-free
solver families. The policy envelope is documented in
``docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md``. Built-in
authoritative solvers retain precedence; auto-promoted solvers sit in a
bounded safe lane between built-ins and LLM fallback.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    # policy
    "LOW_RISK_FAMILY_KINDS": (".low_risk_policy", "LOW_RISK_FAMILY_KINDS"),
    "LOW_RISK_POLICY_VERSION": (".low_risk_policy", "LOW_RISK_POLICY_VERSION"),
    "is_low_risk_family": (".low_risk_policy", "is_low_risk_family"),
    # executor
    "ExecutorError": (".solver_executor", "ExecutorError"),
    "UnsupportedFamilyError": (".solver_executor", "UnsupportedFamilyError"),
    "execute_artifact": (".solver_executor", "execute_artifact"),
    "supported_executor_kinds": (".solver_executor", "supported_executor_kinds"),
    # validation
    "ValidationCase": (".validation_runner", "ValidationCase"),
    "ValidationOutcome": (".validation_runner", "ValidationOutcome"),
    "run_validation": (".validation_runner", "run_validation"),
    "validation_runner": (".validation_runner", None),
    # shadow
    "OracleFn": (".shadow_evaluator", "OracleFn"),
    "ShadowOutcome": (".shadow_evaluator", "ShadowOutcome"),
    "ShadowSample": (".shadow_evaluator", "ShadowSample"),
    "byte_identity_oracle": (".shadow_evaluator", "byte_identity_oracle"),
    "run_shadow_evaluation": (".shadow_evaluator", "run_shadow_evaluation"),
    "shadow_evaluator": (".shadow_evaluator", None),
    # dispatcher
    "DispatchQuery": (".solver_dispatcher", "DispatchQuery"),
    "DispatchResult": (".solver_dispatcher", "DispatchResult"),
    "DispatcherStats": (".solver_dispatcher", "DispatcherStats"),
    "LowRiskSolverDispatcher": (".solver_dispatcher", "LowRiskSolverDispatcher"),
    # promotion engine
    "PROMOTION_DECIDED_BY": (".auto_promotion_engine", "PROMOTION_DECIDED_BY"),
    "AutoPromotionEngine": (".auto_promotion_engine", "AutoPromotionEngine"),
    "PromotionOutcome": (".auto_promotion_engine", "PromotionOutcome"),
    "PromotionRequest": (".auto_promotion_engine", "PromotionRequest"),
    # grower
    "PRIMARY_TEACHER_LANE_ID": (".low_risk_grower", "PRIMARY_TEACHER_LANE_ID"),
    "GapInput": (".low_risk_grower", "GapInput"),
    "GapOutcome": (".low_risk_grower", "GapOutcome"),
    "LowRiskGrower": (".low_risk_grower", "LowRiskGrower"),
    # gap intake
    "GapSignal": (".gap_intake", "GapSignal"),
    "IntakeStats": (".gap_intake", "IntakeStats"),
    "RuntimeGapDetector": (".gap_intake", "RuntimeGapDetector"),
    "digest_signals_into_intents": (".gap_intake", "digest_signals_into_intents"),
    # family oracles
    "FAMILY_ORACLES": (".family_oracles", "FAMILY_ORACLES"),
    "FamilyOracleFn": (".family_oracles", "OracleFn"),
    "get_oracle": (".family_oracles", "get_oracle"),
    # capability features
    "extract_features": (".family_features", "extract_features"),
    "feature_dimensions": (".family_features", "feature_dimensions"),
    # canonical seed library
    "all_canonical_seeds": (".low_risk_seed_library", "all_canonical_seeds"),
    "expected_per_family_counts": (
        ".low_risk_seed_library",
        "expected_per_family_counts",
    ),
    "seeds_for_family": (".low_risk_seed_library", "seeds_for_family"),
    # autogrowth scheduler
    "AutogrowthScheduler": (".autogrowth_scheduler", "AutogrowthScheduler"),
    "AutogrowthBackgroundTicker": (
        ".autogrowth_scheduler",
        "AutogrowthBackgroundTicker",
    ),
    "BackgroundTickerStats": (".autogrowth_scheduler", "BackgroundTickerStats"),
    "SchedulerStats": (".autogrowth_scheduler", "SchedulerStats"),
    "TickResult": (".autogrowth_scheduler", "TickResult"),
    "OUTCOME_AUTO_PROMOTED": (".autogrowth_scheduler", "OUTCOME_AUTO_PROMOTED"),
    "OUTCOME_REJECTED": (".autogrowth_scheduler", "OUTCOME_REJECTED"),
    "OUTCOME_SPEC_INVALID": (".autogrowth_scheduler", "OUTCOME_SPEC_INVALID"),
    "OUTCOME_NO_INTENT": (".autogrowth_scheduler", "OUTCOME_NO_INTENT"),
    "OUTCOME_NO_ORACLE": (".autogrowth_scheduler", "OUTCOME_NO_ORACLE"),
    "OUTCOME_BAD_SEED": (".autogrowth_scheduler", "OUTCOME_BAD_SEED"),
    "OUTCOME_FAMILY_NOT_LOW_RISK": (
        ".autogrowth_scheduler",
        "OUTCOME_FAMILY_NOT_LOW_RISK",
    ),
    # runtime query router
    "RouterStats": (".runtime_query_router", "RouterStats"),
    "RuntimeQuery": (".runtime_query_router", "RuntimeQuery"),
    "RuntimeQueryRouter": (".runtime_query_router", "RuntimeQueryRouter"),
    "RuntimeRouteResult": (".runtime_query_router", "RuntimeRouteResult"),
    # hot-path cache + buffered sink
    "BufferedSignalSink": (".hot_path_cache", "BufferedSignalSink"),
    "BufferedSinkStats": (".hot_path_cache", "BufferedSinkStats"),
    "DEFAULT_MAX_UNFLUSHED_AGE_MS": (
        ".hot_path_cache",
        "DEFAULT_MAX_UNFLUSHED_AGE_MS",
    ),
    "DEFAULT_MAX_UNFLUSHED_SIGNALS": (
        ".hot_path_cache",
        "DEFAULT_MAX_UNFLUSHED_SIGNALS",
    ),
    "HotPathCache": (".hot_path_cache", "HotPathCache"),
    "HotPathCacheStats": (".hot_path_cache", "HotPathCacheStats"),
    "ParsedArtifactCache": (".hot_path_cache", "ParsedArtifactCache"),
    "WarmCapabilityIndex": (".hot_path_cache", "WarmCapabilityIndex"),
    "WarmDispatchResult": (".hot_path_cache", "WarmDispatchResult"),
    "build_autonomy_consult": (
        ".autonomy_consult_adapter",
        "build_autonomy_consult",
    ),
    # runtime hint extractor
    "HintExtractionResult": (".runtime_hint_extractor", "HintExtractionResult"),
    "RESULT_DERIVED": (".runtime_hint_extractor", "RESULT_DERIVED"),
    "RESULT_REJECTED_AMBIGUOUS": (
        ".runtime_hint_extractor",
        "RESULT_REJECTED_AMBIGUOUS",
    ),
    "RESULT_REJECTED_FAMILY_NOT_LOW_RISK": (
        ".runtime_hint_extractor",
        "RESULT_REJECTED_FAMILY_NOT_LOW_RISK",
    ),
    "RESULT_REJECTED_MALFORMED": (
        ".runtime_hint_extractor",
        "RESULT_REJECTED_MALFORMED",
    ),
    "RESULT_REJECTED_MISSING_FIELDS": (
        ".runtime_hint_extractor",
        "RESULT_REJECTED_MISSING_FIELDS",
    ),
    "RESULT_REJECTED_NOT_STRUCTURED": (
        ".runtime_hint_extractor",
        "RESULT_REJECTED_NOT_STRUCTURED",
    ),
    "RESULT_SKIPPED": (".runtime_hint_extractor", "RESULT_SKIPPED"),
    "derive_low_risk_autonomy_hint": (
        ".runtime_hint_extractor",
        "derive_low_risk_autonomy_hint",
    ),
    "supported_subkeys": (".runtime_hint_extractor", "supported_subkeys"),
    # upstream structured_request extractor
    "UPSTREAM_DERIVED": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_DERIVED",
    ),
    "UPSTREAM_REJECTED_AMBIGUOUS": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_REJECTED_AMBIGUOUS",
    ),
    "UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK",
    ),
    "UPSTREAM_REJECTED_MALFORMED": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_REJECTED_MALFORMED",
    ),
    "UPSTREAM_REJECTED_MISSING_FIELDS": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_REJECTED_MISSING_FIELDS",
    ),
    "UPSTREAM_REJECTED_NOT_STRUCTURED": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_REJECTED_NOT_STRUCTURED",
    ),
    "UPSTREAM_SKIPPED": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_SKIPPED",
    ),
    "UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE": (
        ".upstream_structured_request_extractor",
        "UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE",
    ),
    "UpstreamExtractionResult": (
        ".upstream_structured_request_extractor",
        "UpstreamExtractionResult",
    ),
    "apply_upstream_structured_request": (
        ".upstream_structured_request_extractor",
        "apply_upstream_structured_request",
    ),
    "derive_upstream_structured_request": (
        ".upstream_structured_request_extractor",
        "derive_upstream_structured_request",
    ),
}

__all__ = [
    # policy
    "LOW_RISK_FAMILY_KINDS",
    "LOW_RISK_POLICY_VERSION",
    "is_low_risk_family",
    # executor
    "ExecutorError",
    "UnsupportedFamilyError",
    "execute_artifact",
    "supported_executor_kinds",
    # validation
    "ValidationCase",
    "ValidationOutcome",
    "run_validation",
    # shadow
    "OracleFn",
    "ShadowOutcome",
    "ShadowSample",
    "byte_identity_oracle",
    "run_shadow_evaluation",
    # dispatcher
    "DispatchQuery",
    "DispatchResult",
    "DispatcherStats",
    "LowRiskSolverDispatcher",
    # promotion engine
    "PROMOTION_DECIDED_BY",
    "AutoPromotionEngine",
    "PromotionOutcome",
    "PromotionRequest",
    # grower (primary teacher-lane surface)
    "PRIMARY_TEACHER_LANE_ID",
    "GapInput",
    "GapOutcome",
    "LowRiskGrower",
    # gap intake (Phase 12)
    "GapSignal",
    "IntakeStats",
    "RuntimeGapDetector",
    "digest_signals_into_intents",
    # family oracles (Phase 12)
    "FAMILY_ORACLES",
    "FamilyOracleFn",
    "get_oracle",
    # capability features (Phase 13)
    "extract_features",
    "feature_dimensions",
    # canonical seed library (Phase 13)
    "all_canonical_seeds",
    "expected_per_family_counts",
    "seeds_for_family",
    # autogrowth scheduler (Phase 12)
    "AutogrowthScheduler",
    "AutogrowthBackgroundTicker",
    "BackgroundTickerStats",
    "SchedulerStats",
    "TickResult",
    "OUTCOME_AUTO_PROMOTED",
    "OUTCOME_REJECTED",
    "OUTCOME_SPEC_INVALID",
    "OUTCOME_NO_INTENT",
    "OUTCOME_NO_ORACLE",
    "OUTCOME_BAD_SEED",
    "OUTCOME_FAMILY_NOT_LOW_RISK",
    # runtime query router (Phase 13)
    "RouterStats",
    "RuntimeQuery",
    "RuntimeQueryRouter",
    "RuntimeRouteResult",
    # hot-path cache + buffered sink (Phase 14)
    "BufferedSignalSink",
    "BufferedSinkStats",
    "DEFAULT_MAX_UNFLUSHED_AGE_MS",
    "DEFAULT_MAX_UNFLUSHED_SIGNALS",
    "HotPathCache",
    "HotPathCacheStats",
    "ParsedArtifactCache",
    "WarmCapabilityIndex",
    "WarmDispatchResult",
    "build_autonomy_consult",
    # runtime hint extractor (Phase 15)
    "HintExtractionResult",
    "RESULT_DERIVED",
    "RESULT_REJECTED_AMBIGUOUS",
    "RESULT_REJECTED_FAMILY_NOT_LOW_RISK",
    "RESULT_REJECTED_MALFORMED",
    "RESULT_REJECTED_MISSING_FIELDS",
    "RESULT_REJECTED_NOT_STRUCTURED",
    "RESULT_SKIPPED",
    "derive_low_risk_autonomy_hint",
    "supported_subkeys",
    # upstream structured_request extractor (Phase 16A)
    "UPSTREAM_DERIVED",
    "UPSTREAM_REJECTED_AMBIGUOUS",
    "UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK",
    "UPSTREAM_REJECTED_MALFORMED",
    "UPSTREAM_REJECTED_MISSING_FIELDS",
    "UPSTREAM_REJECTED_NOT_STRUCTURED",
    "UPSTREAM_SKIPPED",
    "UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE",
    "UpstreamExtractionResult",
    "apply_upstream_structured_request",
    "derive_upstream_structured_request",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_EXPORTS))
