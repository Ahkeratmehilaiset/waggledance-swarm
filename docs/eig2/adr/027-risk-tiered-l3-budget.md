# ADR-027 — Risk-tiered L3 hydration budget

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-024 (compact decision card schema), ADR-026 (predictive L1 prefetch)

## Context

ADR-021 (Codex's L11) pins **L3** as "selective hydration, 8192 tokens, on demand". The 8192-token budget is uniform across all queries. The 50-leaps menu (L15) calls for a **risk-tiered** L3 budget: high-risk queries get a larger L3 hydration budget; routine queries stay at L0+L1.

The intuition: a goal-status check ("what is the status of solver X?") needs at most L0+L1. A rollback-eligibility query ("did we just execute something we should undo?") may need to walk substantial history. Uniformly granting 8192 tokens to both wastes hydration budget on the routine case and may under-serve the high-risk case.

## Decision

L3 hydration budget is determined by **query risk tier** at the call site:

| Tier | Budget (tokens) | Triggers |
|---|---:|---|
| `routine` | 0 (skip L3 entirely) | status checks, idempotent reads, capability lookups |
| `standard` | 2048 | normal chat turn with no risk signal |
| `elevated` | 8192 (ADR-021 default) | retrieval-heavy reasoning, multi-turn synthesis |
| `high_risk` | 32768 | rollback decisions, audit replays, irreversible-action pre-flight |

Tier is determined by the **caller** declaring the intent via a `RiskTier` parameter at the L3 query entrypoint. There is no automatic inference today — explicit declaration prevents accidental over-spending.

Risk tier ≠ `decision_kind` (per ADR-024). `decision_kind` is a property of a CARD; `RiskTier` is a property of a CALL. The same card can be hydrated at different tiers depending on which call needs it.

## Consequences

### Latency / throughput

* Routine queries skip L3 entirely → drop ~5–20ms hydration cost per query that previously paid it unnecessarily.
* High-risk queries get 4× the previous budget → can pull full audit context that the uniform 8192 limit cut off.
* Per-query budget enforcement bounds worst-case latency tail.

### Operational

* Default tier is `standard` (2048 tokens), NOT the previous uniform 8192. A migration is needed: every existing call site must declare a tier. The default-on-missing should be `standard`, not `elevated`, to avoid silently expanding the L3 footprint.
* Tier escalation requires explicit call-site annotation. Operators can audit "all `high_risk` call sites" via grep.

### Profile interaction

* `prefetch_k` (ADR-026) is L1-side, independent of this. L3 tier is per-call; L1 prefetch is per-boot. Both are profile-tunable.
* Profile S MAY reduce `elevated` budget from 8192 to 4096 for memory-constrained deployments.

## Invariants

Pinned in `docs/eig2/contracts/risk_tiered_l3_budget.json` and verified by `tests/contracts/test_risk_tiered_l3_budget.py`.

1. **Tier enum.** `RiskTier` ∈ `{routine, standard, elevated, high_risk}`. No other values allowed.
2. **Budget mapping.** Tier-to-token mapping is fixed in the contract: routine=0, standard=2048, elevated=8192, high_risk=32768. Profile-tunable only via explicit override.
3. **Routine skips L3.** When `tier=routine`, L3 hydration is bypassed entirely (no DB read, no card walk). Caller gets L0+L1 result only.
4. **Default tier is standard.** A call that does not declare a tier MUST default to `standard` (2048), NOT `elevated`. Prevents accidental footprint expansion.
5. **Caller declares.** Tier is supplied at the L3 query entrypoint via a `RiskTier` parameter. No automatic inference. Static-grep audit of `RiskTier.high_risk` call sites MUST find every high-risk path.
6. **Profile override scope.** Profile-level budget overrides apply ONLY to `elevated` and `high_risk` (the high-budget tiers). `routine=0` and `standard=2048` are constants across profiles to keep the small-profile semantic predictable.
7. **No silent escalation.** A tier cannot be implicitly bumped by the runtime (e.g., based on cache miss patterns). Escalation requires either an explicit caller annotation or an operator-documented exception path.

## Out of scope (this ADR)

* Implementation of `L3BudgetEnforcer` — separate PR.
* Telemetry to track per-tier query distribution — separate PR.
* Adaptive tier suggestion based on historical patterns — explicitly NOT planned (silent escalation forbidden by INV-7).
* Per-domain budget overrides — separate ADR if non-trivial.

## References

* ADR-021 (progressive replay L0–L4)
* ADR-024 (compact decision card schema)
* ADR-026 (predictive L1 prefetch)
* 50-leaps menu: L15 (this), L11 (parent), L14 (L1 side), L16 (Merkle-batched hash verification)
