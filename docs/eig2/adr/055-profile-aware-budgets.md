# ADR-055 — Profile-aware budgets (per-profile resource quotas)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-027 (risk-tiered L3 budget), ADR-026 (predictive L1 prefetch)

## Context

Today's resource budgets (memory, replay concurrency, L1 prefetch K, L3 budget) are uniform across profiles. Profile S (cottage/gadget) gets the same memory ceiling as Profile L (factory). The 50-leaps menu (L38) calls for **profile-aware budgets**: small profile gets tighter quotas (max_replay_concurrency=2, max_memory_mb=128); large profile gets looser. Per Part 10.3.

## Decision

A `profile_budgets.yaml` config maps profile → budget keys:

```yaml
GADGET: {max_memory_mb: 128, max_replay_concurrency: 2, l1_prefetch_k: 32, l3_elevated_budget: 4096}
COTTAGE: {max_memory_mb: 256, max_replay_concurrency: 4, l1_prefetch_k: 64, l3_elevated_budget: 8192}
HOME:    {max_memory_mb: 1024, max_replay_concurrency: 8, l1_prefetch_k: 100, l3_elevated_budget: 8192}
FACTORY: {max_memory_mb: 4096, max_replay_concurrency: 16, l1_prefetch_k: 200, l3_elevated_budget: 16384}
```

Profile resolves at Container.__init__; budgets are immutable for the lifetime of the Container instance.

## Invariants (PAB-001..PAB-007)

1. **Profile enum**: `{GADGET, COTTAGE, HOME, FACTORY}` matches existing waggledance profile taxonomy.
2. **Monotonic budgets across profiles**: GADGET ≤ COTTAGE ≤ HOME ≤ FACTORY for each budget key.
3. **YAML at configs/profile_budgets.yaml**: single source of truth.
4. **Immutable per Container**: budgets resolved at __init__, no runtime mutation.
5. **L1 prefetch K matches ADR-026**: l1_prefetch_k respects ADR-026 PLP-001 range.
6. **L3 elevated matches ADR-027**: l3_elevated_budget respects ADR-027 RTB-002 (profile-tunable elevated tier).
7. **Validation at boot**: budget YAML loaded + validated at Container.__init__; missing profile entry → ERROR + boot fails.

Contract: `docs/eig2/contracts/profile_aware_budgets.json`. Tests: `tests/contracts/test_profile_aware_budgets.py`.
