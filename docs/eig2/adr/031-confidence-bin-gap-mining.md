# ADR-031 — Confidence-bin gap mining

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-021 (progressive replay), ADR-024 (compact decision card), Phase 18F runtime-gap mining (existing in-tree)

## Context

Today's autogrowth_scheduler mines runtime-gap signals uniformly across confidence levels: a low-confidence chat (`confidence=0.15`) and a borderline-confidence chat (`confidence=0.55`) both feed the same gap-miner pipeline and produce candidates with the same scoring weight.

This is suboptimal. A confidence-0.15 response is a "deep gap" — the system has near-zero coverage. A confidence-0.55 response is a "rough edge" — coverage exists but is marginal. Mining strategies should differ: deep gaps want NEW solvers (more capability), rough edges want REFINEMENTS to existing solvers (better routing or precision).

The 50-leaps menu (L21) calls for **confidence-bin gap mining**: bucket low-confidence chats into bands (`0.0-0.2`, `0.2-0.4`, `0.4-0.6`), mine each band separately with band-specific candidate strategies.

## Decision

Gap signals from runtime are tagged with a `confidence_bin` derived from the chat's response confidence:

| Bin | Range | Strategy |
|---|---|---|
| `deep` | 0.0 – 0.2 | propose NEW capability_id candidates (new solver family) |
| `borderline` | 0.2 – 0.4 | propose NEW solver within EXISTING capability (refinement) |
| `marginal` | 0.4 – 0.6 | propose ROUTING tweaks (signal-registry additions, tunnel mining) |
| (skipped) | 0.6 – 1.0 | no gap signal -- confidence is good enough |

Confidence ≥ 0.6 produces NO gap signal at all (current behavior). The new behavior only changes what happens for `confidence < 0.6`.

Each bin has independent candidate pipelines so deep-gap candidates do not crowd out routing refinements (or vice versa).

## Consequences

### Mining quality

* Deep gaps drive capability growth (system learns NEW things).
* Borderline gaps drive solver diversification within known capabilities (specialist solvers).
* Marginal gaps drive routing precision (no new solvers, better selection).

### Storage / Scheduler

* `runtime_gap_signal.confidence_bin` is a new optional field on the existing signal record. Default `borderline` for backward compat.
* `autogrowth_scheduler` reads per-bin candidate budget from `configs/autogrowth_budgets.yaml`. Each bin gets `bin_candidates_per_tick=10` by default.
* Bin budgets are operator-tunable per profile.

### Operational

* Per-bin telemetry visible: operator can see "deep-bin produced 3 candidates this tick; 2 promoted to canary". Helps tune mining behavior.

## Invariants

Pinned in `docs/eig2/contracts/confidence_bin_gap_mining.json` and verified by `tests/contracts/test_confidence_bin_gap_mining.py`.

1. **Bin enum.** `confidence_bin` ∈ `{deep, borderline, marginal}`. No other values.
2. **Bin ranges fixed.** deep=[0.0, 0.2), borderline=[0.2, 0.4), marginal=[0.4, 0.6). Pinned in contract.
3. **Confidence ≥ 0.6 produces no signal.** No bin assigned; gap not mined. Matches current behavior.
4. **Per-bin candidate budgets.** Each bin has its own `bin_candidates_per_tick` budget. Default 10 per bin. Operator-tunable per profile.
5. **Independent pipelines.** Deep candidates do NOT crowd out borderline/marginal candidates. Each bin's budget is committed before any bin's budget is exceeded.
6. **Bin-specific strategy.** deep → new capability_id; borderline → new solver within capability; marginal → routing tweak. Strategy registry maps bin → candidate generator.
7. **Backward compat.** Existing gap signals without `confidence_bin` field default to `borderline`. Migration is non-breaking.

## Out of scope (this ADR)

* Implementation of bin-specific candidate generators — separate PRs per generator.
* Migration of historical gap signals — none required (new field optional).
* Cross-bin candidate de-duplication — separate ADR if measured benefit warrants.

## References

* ADR-024 (compact decision card schema, `gap_signal` decision_kind)
* Phase 18F runtime-gap replay (existing in-tree)
* 50-leaps menu: L21 (this), L22 (cross-agent broadcast), L25 (failure-pattern mining)
