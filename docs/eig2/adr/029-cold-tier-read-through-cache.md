# ADR-029 — Cold-tier read-through cache with 24h warm promotion

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-022 (forensic snapshot rotation), ADR-023 (provenance tip cache)

## Context

ADR-022 (L19) rotates forensic snapshots so L4 replay is bounded by snapshot+delta rather than full chain. ADR-023 (L20) layers a TTL'd warm cache in front of `MagmaProvenanceAdapter._tracker` for provenance lookups.

What is NOT yet addressed: **cold-tier forensic reads**. When the operator runs an audit query or rollback investigation, the L4 forensic replay path reads from the **cold tier** (older events, possibly zstd-compressed per future L18). Every cold-tier read pays the full deserialize+verify cost. For an incident-response session that re-reads the same forensic window multiple times in 30 minutes, the cost is paid repeatedly.

The 50-leaps menu (L17) calls for **cold-tier read-through cache with 24h warm promotion**: when an L4 forensic event is hydrated from cold tier, promote it to the warm tier for 24 hours. Subsequent reads within that window hit warm cache, not cold storage.

## Decision

A read-through cache layer between L4 hydration and cold-tier storage:

* On L4 read: check warm-promotion cache first; if hit, return immediately.
* On miss: read from cold tier, **promote** to warm cache with a 24h TTL, return the value.
* Warm cache size bounded by `cold_promo_max_entries=10000` events. LRU eviction.
* Promotion happens AUTOMATICALLY on every cold read (no opt-in by caller).
* Eviction respects TTL: entries older than 24h are evicted regardless of access count, freeing memory for fresh forensic windows.

This is a SEPARATE cache layer from ADR-023's provenance tip cache (which caches FACT provenance, not raw forensic events). The two layers serve different read patterns and do not conflict.

## Consequences

### Read latency

* p99 forensic replay during incident response drops dramatically: first read warms the cache; subsequent reads are O(1) dict lookups.
* Hot incident windows (operator drilling into same chain segment) stay hot for the duration of the session + 24h.
* Cold-storage access count drops: zstd decompression (future L18) doesn't repeat for warm-promoted events.

### Memory

* 10,000 events × ~2 KB average = ~20 MB working set worst case. Tunable per profile.
* 24h TTL bounds the working set: stale forensic windows drop out automatically.

### Operational

* Cache is read-through and write-invisible: writes to the raw chain (production path) do NOT promote to warm. Promotion is a READ artifact only.
* On promotion-on-miss failure (disk error, decompression error), the read still returns from cold tier; the warm-cache miss is logged at WARNING but does NOT propagate as a hard error.
* Per ADR-022 fail-closed contract: any hash mismatch on a promoted entry causes invalidation + re-read from cold tier.

## Invariants

Pinned in `docs/eig2/contracts/cold_tier_read_through_cache.json` and verified by `tests/contracts/test_cold_tier_read_through_cache.py`.

1. **Read-through semantics.** Cold read → promote → return. No fire-and-forget; the read itself populates the cache atomically.
2. **24h TTL.** Promotion timestamp stored per entry. Eviction at read time when `now - entry_ts > 86400 seconds`. TTL configurable but pinned in contract.
3. **LRU bounded.** Max `cold_promo_max_entries=10000` entries. Oldest-by-access eviction.
4. **No write-side population.** Writes to the raw chain (`magma_append`) do NOT populate this cache. Cache only fills via cold-tier reads.
5. **Failure invisible to caller.** Cache populate failure logs WARNING but does NOT propagate. The read still returns the cold value.
6. **Hash mismatch invalidation.** A promoted entry whose hash mismatches the cold tier's stored hash is evicted and a fresh read happens. Logged.
7. **Layer separation from ADR-023.** This cache holds RAW FORENSIC EVENTS. ADR-023 holds PROVENANCE RECORDS. Implementations MUST keep the two storage paths separate.

## Out of scope (this ADR)

* Implementation of `ColdTierWarmPromoter` — separate PR.
* zstd interaction (when L18 lands, promoted entries are stored decompressed for hot access) — covered when L18's ADR is authored.
* Cross-process cache sharing — explicitly NOT planned.
* Predictive pre-promotion based on access pattern — future ADR if pursued.

## References

* ADR-021 (progressive replay L0–L4, L4 forensic semantics)
* ADR-022 (forensic snapshot rotation, hash-anchored boundaries)
* ADR-023 (provenance tip cache, sister TTL pattern at a different layer)
* 50-leaps menu: L17 (this), L18 (zstd-at-rest), L19 (snapshot rotation)
