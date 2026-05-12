# ADR-050 — Domain-bridging incentive for cross-domain solvers

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-048 (solver portfolio promotion), ADR-038 (tunnel overlay)

## Context

Solver promotion today scores accuracy in a single domain. Solvers that successfully bridge TWO domains (e.g., apiary→thermal) get the same score as same-domain solvers — no incentive for cross-domain capability growth. The 50-leaps menu (L26) calls for a **1.5× promotion-score bonus** for cross-domain solvers.

## Decision

Promotion scoring gets a `domain_bridge_multiplier=1.5` when the solver's training/canary distribution covers ≥ 2 distinct domains (per `agent.domain` attribute):

* `final_score = base_score * (1.0 + (domain_bridge_multiplier - 1.0) * bridging_indicator)`
* `bridging_indicator = 1 if cross-domain else 0`

Multi-domain coverage measured by training set: ≥ N=10 examples from ≥ 2 distinct domains required to qualify.

## Invariants (DBI-001..DBI-007)

1. **Multiplier 1.5**: Default, pinned in contract. Operator-tunable [1.0, 2.0].
2. **Cross-domain threshold**: ≥ 10 examples from ≥ 2 distinct domains.
3. **Domain enum from agent.domain**: existing field; no new taxonomy.
4. **Applied at promotion**: bonus modifies score AT promotion-gate evaluation, not runtime routing.
5. **Anti-cargo-cult still applies**: bonus does NOT bypass ADR-034. Cross-domain solver must still pass adversarial probe set.
6. **Auditable**: promotion log includes `bridging_indicator=true/false` and the set of domains covered.
7. **No retroactive boost**: solver promoted with bonus retains it; not recomputed on each query.

Contract: `docs/eig2/contracts/domain_bridging_incentive.json`. Tests: `tests/contracts/test_domain_bridging_incentive.py`.
