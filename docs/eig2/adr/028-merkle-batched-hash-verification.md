# ADR-028 — Merkle-batched hash verification for raw MAGMA events

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-022 (forensic snapshot rotation), ADR-024 (compact decision card schema)

## Context

The progressive-replay strata (ADR-021) and the forensic-snapshot mechanism (ADR-022) both rely on **hash verification** to detect chain corruption. Today's pattern is **per-event** verification:

* For each event being replayed or compared, compute its sha256 and compare to the stored hash.
* This is O(N) hash computations + O(N) lookups for N events.

At forensic-replay scale (N=1M+), per-event hash verification dominates the replay latency budget even AFTER L19 snapshot rotation reduces the replay-from-snapshot delta.

The 50-leaps menu (L16) projects a **log(N)** verification cost using a **Merkle tree** built over event-batches. Per-batch root hash is stored alongside the event chain; verification of a batch reduces to comparing one root hash + log2(batch_size) intermediate hashes for any specific event proof.

## Decision

Raw MAGMA events are organized into **Merkle batches** of `batch_size=1024` events. For each batch:

* A Merkle tree is computed over the batch's event hashes (sha256 each, then sha256 pairs upward).
* The **root hash** is stored as `magma_merkle_batch_root` alongside the batch boundary marker.
* The chain stores `merkle_root_hash[batch_index]` so verifying batch `k` requires only the root.

For **per-event proof** (e.g., "is event E in batch k?"):

* Walker requests Merkle proof = log2(batch_size) = 10 sibling hashes.
* Verifier reconstructs path from leaf to root in 10 hash ops.
* Total cost: 10 ops, not 1024.

For **batch integrity** (e.g., "is batch k uncorrupted?"):

* Single root-hash compare. Fast path for snapshot equivalence checks.

## Consequences

### Replay cost

* Forensic replay over N events: hash verification drops from O(N) to O(N/1024) batches × O(log batch_size) per proof. For N=1M, the cost drops from 1M hash ops to ~10k batch-root checks plus ~10k × 10 proof-hash ops = ~110k ops. **~9× reduction**.
* Snapshot equivalence (ADR-022) becomes O(1) per snapshot: compare snapshot's stored root vs raw chain's recomputed root for the same range.

### Storage

* Merkle root per 1024-event batch: 32 bytes × (N/1024) = ~32 KB per million events. Negligible.
* If proof caching is added (future), interior hashes can be re-derived on demand — no need to store the full tree.

### Operational

* Hash mismatch at the batch root: walker descends one level, identifies the corrupt subtree, narrows to the leaf in O(log batch_size). Operator gets EXACTLY the corrupt event id, not "somewhere in 1024 events".
* Per ADR-021 fail-closed contract: a Merkle root mismatch triggers raw-replay fallback. This ADR specifies HOW to detect; the fallback decision is unchanged.

## Invariants

Pinned in `docs/eig2/contracts/merkle_batched_hash_verification.json` and verified by `tests/contracts/test_merkle_batched_hash_verification.py`.

1. **Fixed batch size.** `batch_size=1024` events per Merkle batch. Constant across profiles (small/large) so verification math is uniform.
2. **sha256 hash function.** Both leaf hashes (per-event) and interior hashes (parent = sha256(left || right)) MUST use sha256. No SHA-1, no truncation.
3. **Canonical pair concatenation.** When computing parent hash from two children, concatenate as `left_hash_bytes || right_hash_bytes` (raw 32-byte each). Documented bit-for-bit so two implementations get the same root.
4. **Odd-leaf handling.** When a batch has fewer than `batch_size` events at the chain tip (e.g., last batch has 743), pad with the hash of an empty byte string. This is the canonical Merkle-tree odd-leaf-handling per Bitcoin / Ethereum / standard practice — eliminates ambiguity.
5. **Root stored on batch close.** A batch's root hash is written EXACTLY ONCE, when the batch closes (1024 events accumulated OR snapshot rotation forces flush). No re-computation on read.
6. **Per-event proof requires batch lookup.** To verify a specific event, walker first identifies the batch by `event_index // batch_size`, fetches the root + sibling hashes, then verifies the proof. No global merge of all roots; each batch is independent.
7. **Mismatch fails closed.** Per ADR-021, any root or proof mismatch triggers raw-replay fallback. This ADR does NOT change the fallback decision; it only specifies the verification mechanism.

## Out of scope (this ADR)

* Implementation of `MagmaMerkleBatcher` — separate PR.
* Proof caching (interior hashes) — separate ADR if needed.
* Cross-batch global Merkle (e.g., for chain-wide signature) — explicitly NOT planned for L16.
* Hash function migration (e.g., SHA-3) — future ADR if cryptographic requirements change.

## References

* ADR-021 (progressive replay L0–L4, fallback contract)
* ADR-022 (forensic snapshot rotation, hash-anchored snapshot boundary)
* ADR-024 (compact decision card schema, per-card hash semantics)
* 50-leaps menu: L16 (this), L18 (zstd-at-rest), L19 (snapshot rotation)
