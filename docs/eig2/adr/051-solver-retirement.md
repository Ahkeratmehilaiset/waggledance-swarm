# ADR-051 — Solver retirement (auto-demote chronic underperformers)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-049 (sleep-time consolidation), ADR-048 (solver portfolio)

## Context

Solvers persist forever today. Once promoted, a solver lives in production unless explicitly removed. Capacity caps reached → no slot for new candidates. The 50-leaps menu (L27) calls for **solver retirement**: solvers below quality floor for > 30 days auto-demoted to shadow. Recycles capacity for new candidates.

## Decision

A `RetirementWatcher` task runs nightly (alongside L24 sleep consolidator):

1. For each production solver, compute 30-day median accuracy.
2. If median < `quality_floor=0.55` AND has been below floor for ≥ 30 consecutive days → demote to `shadow` state.
3. Demoted solver can re-promote via L24 sleep consolidator if it recovers.
4. Solver demoted twice within 90 days → archive (no auto-revival; operator must explicitly resurrect).

## Invariants (SOR-001..SOR-007)

1. **Quality floor 0.55**: default. Operator-tunable [0.30, 0.80].
2. **30 consecutive days below floor**: not 30 cumulative days; must be consecutive.
3. **Demote to shadow, not archive**: first demotion is recoverable.
4. **Three-strike archive**: 2 demotions within 90 days → archive (cumulative count).
5. **Atomic state transition**: prod→shadow or shadow→archive happens in single transaction.
6. **Audit log with reason**: every transition logs `DEMOTE_QUALITY_FLOOR / ARCHIVE_THREE_STRIKE / REVIVE_OPERATOR`.
7. **Capacity slot release**: demotion releases the production slot; new candidate can claim it on next promotion cycle.

Contract: `docs/eig2/contracts/solver_retirement.json`. Tests: `tests/contracts/test_solver_retirement.py`.
