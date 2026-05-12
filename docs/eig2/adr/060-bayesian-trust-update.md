# ADR-060 — Bayesian trust update with credible intervals

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: PR #287 (L41-L44 base trust signals), ADR-035–037 (Phase 2 signals), ADR-059 (domain trust vector)

## Context

Trust updates today use heuristic moving averages: when an agent succeeds, score nudges up by a constant; when it fails, down. No notion of CONFIDENCE in the score itself. After 5 observations and after 5000, the value type is the same — just a number, no uncertainty.

The 50-leaps menu (L50) calls for **Bayesian trust update**: trust as a probability distribution (Beta prior + likelihood = Beta posterior for binomial success rate). Posterior gives both a POINT estimate AND a CREDIBLE INTERVAL. Promotion decisions become evidence-grade: "agent X has 0.85 trust with 95% credible interval [0.80, 0.89]".

## Decision

Each trust signal stored as a Beta(α, β) posterior:

* α (alpha) = successes + prior_alpha
* β (beta) = failures + prior_beta
* `point_estimate = α / (α + β)` (posterior mean)
* `credible_interval_95 = Beta(α, β).interval(0.95)` (lower, upper)

Default prior: `Beta(2, 2)` (weakly-informative; mean 0.5, low confidence).

Composite trust uses BOTH point estimate AND lower-CI:
* For routine routing: use point estimate.
* For high_risk gating: use lower-CI (conservative — require stronger evidence).

## Invariants (BTU-001..BTU-007)

1. **Beta(α, β) representation**: per-signal stored as 2 floats.
2. **Prior Beta(2, 2)**: pinned default (weakly-informative).
3. **Update formula**: `α += success`, `β += failure`. No decay (handled separately by ADR-037 temporal decay).
4. **Point + CI**: both exposed via API.
5. **Routine uses point**: ROUTINE/STANDARD risk tiers use point estimate.
6. **High_risk uses lower-CI 95%**: more conservative for irreversible actions.
7. **Backward compat**: existing scalar trust APIs continue to work; new BetaTrust is additive.

Contract: `docs/eig2/contracts/bayesian_trust_update.json`. Tests: `tests/contracts/test_bayesian_trust_update.py`.
