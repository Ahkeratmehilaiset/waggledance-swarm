# ADR-026 — Predictive L1 prefetch policy

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-024 (compact decision card schema)

## Context

ADR-021 (Codex's L11) pins the **L1** strata as "compact cards, 512 tokens, **boot-time top-K**". The "top-K" choice is mentioned but not specified: top-K by what metric? Most recent? Most accessed? Most likely-to-be-used in the first wall-clock minute after boot?

Today (substrate-only), there is no `progressive_replay.py` and therefore no prefetch policy. When the implementation lands, an unguarded "load the K most recent cards" policy gives the first cold-startup query worse latency than a thoughtful prefetch policy would.

The 50-leaps menu (L14) calls for **predictive L1 prefetch**: at boot, load the K cards most likely to be needed in the first wall-clock minute, based on **yesterday's distribution from `autogrowth_scheduler.stats`**.

This ADR pins the policy invariants. The implementation is a later PR.

## Decision

L1 boot-time prefetch loads `K` cards, selected by the **predictive policy**:

1. **Primary signal — recency**: each compact card has `produced_at_utc`. Cards within the last `recency_window_hours=24` form the initial candidate pool.
2. **Ranking signal — yesterday's access count**: each subject_id has an access counter from `autogrowth_scheduler.stats.access_history` (or equivalent) measured over the prior 24h. Higher count → higher prefetch priority.
3. **Diversity floor**: at most `same_subject_max_share=0.10` of the K prefetched cards may share the same `subject_id`. Prevents one runaway subject from monopolizing the prefetch budget.
4. **decision_kind balance**: every allowed `decision_kind` (per ADR-024 CDC-007) MUST have at least one card in the prefetch set IF such a card exists in the candidate pool. Prevents a single high-volume kind (e.g., `gap_signal`) from crowding out essential kinds (e.g., `rollback`).
5. **K is configurable**: `prefetch_k` defaults to **100** but is operator-tunable per profile. Profile S may use a smaller K (e.g., 32) to keep boot lean.
6. **Fallback on no history**: when `autogrowth_scheduler.stats` is empty (fresh deployment, restart after data loss), prefetch falls back to **pure recency ordering** — newest K cards within the recency window. Logged as INFO, not WARNING (expected first-boot behavior).

## Consequences

### Cold-start latency

* First 10 queries after boot are p99-good (cache-hot for top access patterns) instead of p99-bad (lazy-load every card).
* For Profile S with `prefetch_k=32`, boot overhead is ~16 KB extra (32 cards × ~512 tokens × ~1 byte/token). Negligible.

### Predictability

* Operators can inspect the prefetch decision via a dump command (future): "what would prefetch load for this profile right now?" This is critical for incident investigation.

### Operational

* Stats source must be deterministic and side-effect-free at the prefetch path. The prefetcher MUST NOT mutate `autogrowth_scheduler.stats` while reading it.
* If a card pointed to by stats no longer exists in the card store (e.g., garbage-collected), the prefetcher skips it and continues. No hard failure.

## Invariants

Pinned in `docs/eig2/contracts/predictive_l1_prefetch.json` and verified by `tests/contracts/test_predictive_l1_prefetch.py`.

1. **K bounded.** `prefetch_k` MUST be a positive integer. Default 100. Profile-tunable but pinned in this contract as required field.
2. **Recency window.** Cards selected for prefetch MUST have `produced_at_utc` within `recency_window_hours=24` of boot time. Older cards are ignored regardless of access count.
3. **Diversity floor.** At most `same_subject_max_share=0.10` (10% of K) cards may share the same `subject_id`.
4. **decision_kind coverage.** Every allowed `decision_kind` (per ADR-024 CDC-007) MUST have at least one card in the prefetch set, IF such a card exists in the candidate pool.
5. **No history → recency fallback.** When stats are empty, fall back to pure-recency top-K. INFO log, not WARNING.
6. **Read-only stats.** Prefetcher MUST NOT mutate stats while reading. Tests verify by snapshot-before, snapshot-after equality.
7. **Missing card → skip.** If a card pointed to by stats is absent from the card store, prefetcher skips and continues. No hard failure; logs at DEBUG.

## Out of scope (this ADR)

* Implementation of `L1Prefetcher` — separate PR.
* Stats-source contract (what fields does `autogrowth_scheduler.stats` expose?) — separate ADR if non-trivial.
* Per-profile K tuning UI / dashboard — separate PR.
* Adaptive K (auto-tune based on miss rate) — future work, not L14.

## References

* ADR-021 (progressive replay L0–L4)
* ADR-024 (compact decision card schema)
* 50-leaps menu: L14 (this), L11 (parent), L12 (card schema), L15 (risk-tiered budgets)
