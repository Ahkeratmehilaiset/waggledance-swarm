# ADR-048 — Solver-portfolio promotion

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-034 (anti-cargo-cult), ADR-031 (confidence-bin gap mining)

## Context

Today's auto-promotion promotes a SINGLE solver winner per capability slot. Monoculture: if the winner has a blind spot, the system has no fallback. The 50-leaps menu (L23) proposes **solver-portfolio promotion**: promote a portfolio of N solvers with weighted voting on 1k Axis-B probes; hedged correctness.

## Decision

Promotion produces a `SolverPortfolio` (top-N solvers) instead of a single winner:

* Default `portfolio_n=3` for production, `portfolio_n=1` for cottage profile.
* Each solver gets `vote_weight = candidate_accuracy / sum_of_accuracies` (normalized softmax-style).
* At execution: query routed to all N solvers; final answer is `weighted_majority_vote` (or unanimous-required for high_risk per ADR-027).
* Portfolio members must INDIVIDUALLY pass anti-cargo-cult (ADR-034) — no free riders.

## Invariants (SPP-001..SPP-007)

1. **Portfolio N range**: `[1, 5]` default 3. Profile-tunable.
2. **Vote weight sums to 1**: normalized softmax over candidate accuracies.
3. **Individual ACC pass**: every portfolio member passes ADR-034 anti-cargo-cult gate.
4. **Unanimous for high_risk**: when caller's risk tier is `high_risk` (ADR-027), portfolio MUST be unanimous; one disagreement → BLOCK.
5. **Weighted majority for routine/standard**: normal stages use vote-weighted majority.
6. **Member replaceable**: a member can be retired (per ADR-049 L27 future) without dissolving portfolio; remaining members re-normalize weights.
7. **Portfolio audit trail**: every dispatch logs member solver_ids + their individual answers + final aggregate.

Contract: `docs/eig2/contracts/solver_portfolio_promotion.json`. Tests: `tests/contracts/test_solver_portfolio_promotion.py`.
