# ADR-058 — Cross-validation score (held-out probes across domains)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-035 (stability_score), ADR-036 (latency_consistency), ADR-037 (temporal trust decay), ADR-048 (portfolio promotion)

## Context

Trust today is computed from agent's own-domain success. Specialized agents that look great in their domain may completely fail outside it; routing has no signal of cross-domain weakness. The 50-leaps menu (L46) calls for **cross-validation score**: agent's accuracy on held-out probes from OTHER agents' domains.

## Decision

`cross_validation_score` field on TrustSignals (9th field after stability_score, latency_consistency):

```python
cross_validation_score: float  # 0.0-1.0, higher = better cross-domain
```

Computation:

1. Sample 50 random probes from a held-out adversarial set spanning N=5 distinct domains (NOT including the agent's primary domain).
2. Run the agent on these probes.
3. Score = correct_count / 50.

## Invariants (CVS-001..CVS-007)

1. **Field name `cross_validation_score`**: TrustSignals field 9.
2. **Probe set 50**: default, range [20, 200].
3. **5 distinct domains**: probes span ≥ 5 different agent.domain values.
4. **Excludes own domain**: agent's primary domain NOT in probe sample (no easy mode).
5. **L51 contract update required**: as STB-006 and LCO-006 — adding the field needs L51 test amendment.
6. **Default 0.5 on insufficient probe coverage**: if < 5 distinct domains in held-out set, return 0.5.
7. **Refresh weekly**: cross-validation cached 7 days (re-run weekly per agent).

Contract: `docs/eig2/contracts/cross_validation_score.json`. Tests: `tests/contracts/test_cross_validation_score.py`.
