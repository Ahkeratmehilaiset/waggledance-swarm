# ADR-024 — Compact decision card schema

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-011 (compact-card write-storm breaker), ADR-014 (queue+backpressure before breaker), ADR-021 (progressive replay L0–L4), ADR-023 (provenance tip cache)

## Context

ADR-021 (Codex's L11) defines the progressive replay strata: **L1** is "compact cards, 512 tokens, boot-time top-K". The strata are now contractually pinned but the **schema** of a compact card is not. Without a stable schema, multiple writers can produce subtly-different card shapes, defeating the bounded-token guarantee at L1 and breaking the delta-chain (L13) that depends on consistent card structure.

ADR-011 (compact-card write-storm breaker) already governs the WRITE PATH risk. This ADR (L12) governs the CARD CONTENT — what a card is, what fields it has, what guarantees it makes.

## Decision

A compact decision card is a **deterministically-hashed** record with the following fixed shape:

```json
{
  "card_id": "<uuid4>",
  "card_version": "compact-card-v1",
  "supersedes_card_id": "<uuid4 | null>",
  "source_event_hash": "<sha256 of raw MAGMA event at card boundary>",
  "produced_at_utc": "<ISO8601>",
  "producer": "<short identifier of the derived-write subsystem>",
  "decision_kind": "<solver_dispatch | promotion | rollback | snapshot_anchor | ...>",
  "subject_id": "<fact_id | solver_id | goal_id>",
  "summary": "<<= 8 short fields describing the decision>",
  "card_hash": "<sha256(canonical_json(self minus card_hash))>"
}
```

Key constraints:
* **Immutable.** Once written, a card is never edited. Updates produce a NEW card with `supersedes_card_id` pointing to the old one (delta-chain pattern from L13).
* **Deterministically hashed.** `card_hash` is `sha256(canonical_json(record minus card_hash))`. Two consumers computing the hash of the same card get the same value. Catches in-transit corruption.
* **Anchored to raw.** `source_event_hash` MUST equal the sha256 of the raw MAGMA event the card summarizes. If mismatch, card is invalid -> fail-closed to raw replay (per ADR-021 fallback contract).
* **Bounded.** `summary` has at most 8 fields, each at most 256 bytes. The total card serializes to <= 512 tokens (the L1 strata budget).

## Consequences

### Storage / replay

* L1 cache becomes a uniform collection of compact-card-v1 records. Boot-time prefetch of top-K cards is well-typed.
* Hash mismatch is detectable per-card. Per ADR-021, hash mismatch falls back to raw replay; this ADR makes the mismatch ATOMICALLY observable (single-card hash check, not chain walk).
* Delta-chain (future L13) becomes implementable: card X' replaces card X with `supersedes_card_id=X.card_id` + only-changed-`summary`-fields-as-delta. Pre-condition for storage reduction.

### Writer ergonomics

* Writers in the derived-write subsystem (autogrowth_scheduler, auto_promotion_engine, shadow_evaluator) MUST emit compact-card-v1 records. The breaker (ADR-011) catches write-storm; this ADR catches schema-drift writes that would invalidate the boot prefetch.

### Operational

* Card-version bump (compact-card-v2) requires:
  * New ADR documenting the rationale.
  * Schema test failure unless ADR is referenced from this ADR's supersedes chain.
  * Migration plan from v1 -> v2 (operator decision).

## Invariants

Pinned in machine-readable contract `docs/eig2/contracts/compact_decision_card.json` and verified by `tests/contracts/test_compact_decision_card_schema.py`.

1. **Stable schema name.** `card_version="compact-card-v1"` exactly. Bumping to v2 requires a new ADR and an updated contract.
2. **Required fields.** card_id, card_version, source_event_hash, produced_at_utc, producer, decision_kind, subject_id, summary, card_hash. (supersedes_card_id is required field name but may be null.)
3. **Card hash determinism.** card_hash = sha256(canonical_json(record minus card_hash field)). Tested by hash-twice-same-result.
4. **Source hash anchoring.** source_event_hash MUST equal sha256 of the referenced raw event. Validators MUST check this before trusting the card.
5. **Immutability.** No in-place edits. Updates produce a new card with supersedes_card_id set. No writer API to mutate an existing card_id record.
6. **Bounded summary.** summary dict has at most 8 keys, each value at most 256 bytes when serialized. Total card serializes to <= 512 tokens (L1 strata budget per ADR-021).
7. **Allowed decision_kinds.** Initial set: `{solver_dispatch, promotion, rollback, snapshot_anchor, gap_signal, capability_bind}`. Extensions require an ADR amendment + contract update.

## Out of scope (this ADR)

* Implementation of `CompactCardWriter` and `CompactCardStore` — separate PR.
* Delta-encoding logic for `supersedes` chains (L13) — separate ADR.
* Predictive prefetch policy (L14) — separate ADR.
* Storage tier (warm vs cold, zstd-at-rest) — covered by L18 / future ADR.

## References

* ADR-011 (compact-card write-storm breaker)
* ADR-014 (queue+backpressure before breaker)
* ADR-021 (progressive replay L0–L4)
* ADR-023 (provenance tip cache)
* 50-leaps menu: L12 (this), L13 (delta-encoded supersedes chain), L14 (predictive prefetch), L18 (zstd-at-rest)
