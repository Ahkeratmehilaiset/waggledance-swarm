# ADR-030 — zstd-at-rest compression for older MAGMA events

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-021 (progressive replay), ADR-022 (snapshot rotation), ADR-024 (compact card), ADR-025 (delta chain), ADR-029 (cold-tier read-through)

## Context

MAGMA event storage grows linearly with operational time. Forensic + snapshot tiers retain history indefinitely. Cold-tier storage costs are proportional to total chain size. ADR-025 (L13 delta-chain) already gives 3-5× reduction for high-churn supersedes; combined with **zstd compression on cold-tier records**, the 50-leaps menu (L18) projects **5-10× additional storage reduction** with **decompress-on-hydration** (the warm-promotion in ADR-029 keeps decompressed copies for 24h).

## Decision

Events stored on disk for longer than `cold_threshold_days=7` are **zstd-compressed at rest**:

* Compression level: `zstd_level=3` (default fast/medium balance). Operator-tunable per profile.
* Compression is applied at the BATCH level (per ADR-028 Merkle batch of 1024 events), not per-event, to maximize dictionary sharing.
* Hash invariant unchanged: leaf hashes are computed on the UNCOMPRESSED event canonical bytes. Compression is a STORAGE concern, not a hash-domain change. Per ADR-028 MBH-002 the sha256 hash function and per-event hashing rules stay identical.
* Decompression happens lazily on hydration. The warm-promotion cache (ADR-029) caches the DECOMPRESSED form so repeated reads in 24h don't re-decompress.

## Consequences

### Storage

* 5-10× reduction on cold-tier events (typical zstd ratio on JSON-shaped data).
* Combined with ADR-025 delta-chains: ~15-50× total reduction vs full-record uncompressed.

### Read latency

* First read of an older event pays decompression cost (~100 µs per 1024-event batch on modern CPU).
* Warm-promotion (ADR-029) keeps decompressed for 24h → repeated reads stay fast.
* Snapshot equivalence checks (ADR-022 + ADR-028) MUST verify hash against UNCOMPRESSED canonical bytes; the compressed payload is opaque.

### Operational

* Compression is a one-time decision per batch (when the batch ages past `cold_threshold_days`). No re-compression on existing records.
* Operators can disable compression per profile via `zstd_at_rest_enabled=false`. Default ON.
* Decompression failure (corruption, library mismatch) fails closed: read returns the COMPRESSED bytes with a logged ERROR; replay path falls back to raw-chain walk per ADR-021. No silent data loss.

## Invariants

Pinned in `docs/eig2/contracts/zstd_at_rest.json` and verified by `tests/contracts/test_zstd_at_rest.py`.

1. **Age threshold.** Events younger than `cold_threshold_days=7` are stored UNCOMPRESSED. Older events go through the compression pipeline.
2. **Compression level.** `zstd_level=3` default. Per-profile override allowed (range 1-22).
3. **Batch-level granularity.** Compression applied per ADR-028 Merkle batch (1024 events), not per-event. Maximizes shared dictionary across the batch.
4. **Hash domain unchanged.** sha256 hashes are computed on UNCOMPRESSED event canonical bytes. Compression is purely a storage artifact. ADR-028 MBH-002 semantics unchanged.
5. **Decompress-on-read.** Reads of compressed batches decompress lazily. Warm-promotion (ADR-029) caches the decompressed form.
6. **Failure fails closed.** Decompression failure -> ERROR log + fall back to raw replay per ADR-021. No data returned that has not been hash-verified.
7. **Disable-per-profile.** Operator can set `zstd_at_rest_enabled=false` per profile to skip compression entirely (useful for low-memory profiles or debugging).

## Out of scope (this ADR)

* Implementation of `ZstdAtRestEncoder` / `ZstdAtRestDecoder` — separate PR.
* Migration tooling to compress existing uncompressed historical batches — separate PR.
* zstd dictionary training (long-tail repeat patterns) — future ADR if measured benefit warrants.
* Alternative codecs (lz4, snappy) — explicitly NOT planned; zstd is the only encoding pinned.

## References

* ADR-021 (progressive replay L0–L4, fallback contract)
* ADR-022 (forensic snapshot rotation, hash-anchored boundaries)
* ADR-024 (compact decision card schema, per-card canonical bytes)
* ADR-025 (delta-encoded supersedes chain, complementary storage saving)
* ADR-028 (Merkle batch boundary aligns with compression unit)
* ADR-029 (cold-tier read-through cache, decompressed cache layer)
* 50-leaps menu: L18 (this), L13 (delta chain), L17 (cold-tier cache), L19 (snapshot rotation)
