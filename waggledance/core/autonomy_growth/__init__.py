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

from .auto_promotion_engine import (
    PROMOTION_DECIDED_BY,
    AutoPromotionEngine,
    PromotionOutcome,
    PromotionRequest,
)
from .autogrowth_scheduler import (
    OUTCOME_AUTO_PROMOTED,
    OUTCOME_BAD_SEED,
    OUTCOME_FAMILY_NOT_LOW_RISK,
    OUTCOME_NO_INTENT,
    OUTCOME_NO_ORACLE,
    OUTCOME_REJECTED,
    OUTCOME_SPEC_INVALID,
    AutogrowthScheduler,
    SchedulerStats,
    TickResult,
)
from .family_oracles import FAMILY_ORACLES, OracleFn as FamilyOracleFn, get_oracle
from .gap_intake import (
    GapSignal,
    IntakeStats,
    RuntimeGapDetector,
    digest_signals_into_intents,
)
from .low_risk_grower import (
    PRIMARY_TEACHER_LANE_ID,
    GapInput,
    GapOutcome,
    LowRiskGrower,
)
from .low_risk_policy import (
    LOW_RISK_FAMILY_KINDS,
    LOW_RISK_POLICY_VERSION,
    is_low_risk_family,
)
from .shadow_evaluator import (
    OracleFn,
    ShadowOutcome,
    ShadowSample,
    byte_identity_oracle,
    run_shadow_evaluation,
)
from .solver_dispatcher import (
    DispatchQuery,
    DispatchResult,
    DispatcherStats,
    LowRiskSolverDispatcher,
)
from .solver_executor import (
    ExecutorError,
    UnsupportedFamilyError,
    execute_artifact,
    supported_executor_kinds,
)
from .validation_runner import (
    ValidationCase,
    ValidationOutcome,
    run_validation,
)

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
    # autogrowth scheduler (Phase 12)
    "AutogrowthScheduler",
    "SchedulerStats",
    "TickResult",
    "OUTCOME_AUTO_PROMOTED",
    "OUTCOME_REJECTED",
    "OUTCOME_SPEC_INVALID",
    "OUTCOME_NO_INTENT",
    "OUTCOME_NO_ORACLE",
    "OUTCOME_BAD_SEED",
    "OUTCOME_FAMILY_NOT_LOW_RISK",
]
