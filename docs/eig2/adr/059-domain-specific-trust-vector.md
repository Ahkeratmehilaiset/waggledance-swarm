# ADR-059 — Domain-specific trust vector

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-058 (cross-validation), ADR-037 (temporal trust decay)

## Context

Trust today is a SCALAR per agent. An agent that's an apiary expert but mediocre at home automation gets one composite — routing has no way to "trust this agent for apiary queries, not for home queries". The 50-leaps menu (L48) calls for a **trust VECTOR**: per-domain trust values stored alongside the scalar composite.

## Decision

Add `domain_trust: dict[str, float]` to AgentTrust (or as new field on TrustSignals depending on impl choice):

```python
domain_trust: dict[str, float] = field(default_factory=dict)
# {"apiary": 0.90, "home": 0.45, "factory": 0.10, ...}
```

Per-domain values updated when agent succeeds/fails in that domain. Routing path consults the domain key matching query.domain; falls back to composite if no domain-specific value exists.

## Invariants (DTV-001..DTV-007)

1. **Dict[str, float]**: keys are domain strings (matching agent.domain enum); values in [0.0, 1.0].
2. **Default empty dict**: new agents start with empty vector; composite used as fallback.
3. **Update on dispatch**: per-domain value updated when agent dispatched on a query of that domain. Exponential moving average with α=0.10.
4. **Fallback to composite**: missing domain key → use scalar composite.
5. **L51 contract update**: AgentTrust dataclass shape change requires L51 amendment.
6. **Bounded vector size**: at most 10 distinct domain keys per agent (matches PAB / breadth cap).
7. **Domain enum**: keys must be valid agent.domain values; arbitrary strings rejected.

Contract: `docs/eig2/contracts/domain_trust_vector.json`. Tests: `tests/contracts/test_domain_trust_vector.py`.
