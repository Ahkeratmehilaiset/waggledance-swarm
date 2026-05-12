# ADR-025 — Delta-encoded supersedes chain for compact decision cards

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-024 (compact decision card schema), ADR-022 (forensic snapshot rotation)

## Context

ADR-024 (L12) pins the compact-card-v1 schema and asserts immutability: **updates produce a NEW card with `supersedes_card_id` pointing at the old one**. The current pin shape says nothing about what the NEW card contains.

Two natural options:

1. **Full-record copy.** Each supersedes-update writes a full new card with the entire summary re-emitted. Simple, but every churned solver/promotion produces a fresh 512-token card. At high-churn rates (autogrowth_scheduler proposing 100s of variant solvers per cycle), L1 storage explodes.

2. **Delta encoding.** The supersedes card contains ONLY the changed `summary` fields plus pointers to the previous card. Replay reconstructs the full record by walking the supersedes chain backward.

The 50-leaps menu (L13) projects a **3–5× cache-size reduction** with delta encoding for high-churn solvers.

This ADR (L13) specifies the delta-encoding semantics. Implementation lives in a future PR.

## Decision

Compact-card-v1 supersedes-updates use **partial-summary delta encoding** with these semantics:

* The supersedes card's `summary` field contains ONLY the changed key/value pairs. Keys absent from the new card MUST be looked up via the supersedes chain.
* Two new fields are added to compact-card-v1 to make the delta semantics explicit:
  - `delta_mode`: enum `{full, partial, tombstone}`
    - `full`: card carries the complete summary (no chain walk needed). Initial card of a chain MUST be `full`.
    - `partial`: card carries only changed keys; walk chain backward for missing keys.
    - `tombstone`: card marks the chain as retired. Reading the subject after a tombstone returns None.
  - `delta_removed_keys`: list of summary keys explicitly DELETED in this update. Empty list when no keys are removed.
* Chain walk MUST be bounded to `chain_max_depth=64` to prevent O(N) reconstruction on pathological churn. Exceeding the depth → fall back to raw replay per ADR-021 fallback contract.
* Hash anchoring: each card's `card_hash` is computed over its OWN content (including the delta-encoded summary), not the reconstructed-full record. Verification operates per-card.
* Tombstoning is non-reversible: once a chain is tombstoned, future updates MUST start a NEW chain with a new `card_id` and `supersedes_card_id=null`.

## Consequences

### Storage

* High-churn chains shrink: an 8-field summary that changes 1 field per update writes ~64 bytes per supersedes card (delta) vs ~512 tokens (full). Projected 3-5× reduction matches the menu estimate.
* Cold-tier compounding: zstd-at-rest (L18) compresses delta records very well (small repetitive payload). Combined save vs raw-full-copy: 10-20×.

### Replay cost

* Replay of subject-id requires walking the supersedes chain backward to the most-recent `full` card. Bounded by `chain_max_depth=64`.
* L1 prefetch loads `full` cards preferentially (boot policy). Delta cards are pulled on-demand at L2/L3.

### Operational

* Chain corruption (a missing intermediate card, a hash mismatch in the middle of the chain) fails closed per ADR-021: fall back to raw replay.
* Tombstone allows clean retirement of a subject (e.g., retired solver) without rewriting the chain.

### Writer ergonomics

* Writer API: `write_card_update(subject_id, new_summary, removed_keys=[])` — writer computes the delta vs the last card in the chain and emits a `partial` card.
* The first card in a chain is always `full` and `supersedes_card_id=null`.

## Invariants

Pinned in `docs/eig2/contracts/delta_supersedes_chain.json` and verified by `tests/contracts/test_delta_supersedes_chain.py`.

1. **delta_mode enum.** Cards declare `delta_mode` ∈ `{full, partial, tombstone}`. No other values allowed.
2. **First card is full.** The first card of any chain (`supersedes_card_id=null`) MUST be `delta_mode=full`.
3. **Partial requires supersedes.** A card with `delta_mode=partial` MUST have `supersedes_card_id != null`.
4. **Chain depth bound.** Replay walks chain backward to find a `full` card. Depth > `chain_max_depth=64` → fail-closed to raw replay.
5. **Tombstone terminates.** No card may declare `supersedes_card_id` pointing to a card whose `delta_mode=tombstone`. Once tombstoned, a new chain starts fresh.
6. **Per-card hash.** `card_hash` is computed over the card's OWN content (per ADR-024 CDC-003). Verification is per-card; no need to reconstruct the full record to verify.
7. **Delta removed keys.** `delta_removed_keys` is required on every card (may be empty list). Reconstruction skips keys listed here when walking the chain.

## Out of scope (this ADR)

* Implementation of `CompactCardChain` walker — separate PR.
* L1 prefetch policy (prefer `full` cards) — separate ADR if non-trivial.
* Tombstone-driven retraining of derived indices — separate ADR.
* Garbage collection of fully-superseded cards — separate ADR.

## References

* ADR-021 (progressive replay L0–L4, fallback contract)
* ADR-024 (compact decision card schema, the base v1 record)
* ADR-022 (forensic snapshot rotation, chain anchoring patterns)
* 50-leaps menu: L13 (this), L12 (card schema), L14 (predictive prefetch), L18 (zstd at rest)
