# ADR-023 — Provenance tip cache with TTL

* Status: **substrate-only landing** (contract + ADR pinned; full implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-021 (progressive replay L0–L4), ADR-022 (forensic snapshot rotation)

## Context

Today `MagmaProvenanceAdapter.get_provenance(fact_id)` (at `waggledance/core/magma/provenance.py`) has a partial cache: an in-memory `self._records` dict for facts written through `record_provenance`. But for facts ingested via the legacy `self._tracker` path, every `get_provenance` call falls through to `_tracker.get_provenance_chain(fact_id)` — which is a DB-backed read.

At scale (M3+M4 substrate, autonomy_growth producing facts continuously), the legacy fallback dominates trust-check latency for fresh facts that have not yet been promoted into the in-memory dict. A spot-bench in the explosive-growth session estimated **~20× p99 reduction** with a TTL'd warm cache layered between the dict and the tracker.

## Decision

Layer a **TTL-bounded warm cache** between `MagmaProvenanceAdapter._records` (hot) and `MagmaProvenanceAdapter._tracker` (cold). Three-tier shape:

1. **L0 — hot dict** (`self._records`): facts written this session via `record_provenance`. Unbounded. Source of truth for sessions.
2. **L1 — warm TTL cache** (new): facts pulled from tracker. Bounded LRU (~1000 entries), 5-second TTL. Boot-time preload of last K tips from tracker.
3. **L2 — cold tracker** (`self._tracker`): the DB-backed provenance chain. Authority for facts not in L0.

`get_provenance(fact_id)` lookup order:
1. L0 hit → return immediately.
2. L1 hit (within TTL) → return cached.
3. L1 stale or miss → query tracker, populate L1, return.
4. Tracker miss → return None.

## Consequences

### Latency

* p50 lookup unchanged (L0 hot path is still O(1) dict).
* p99 lookup for "warm but recently-tracker-pulled" facts drops dramatically: 5-second window where re-lookup is L1 hit, not tracker round-trip.
* First-of-its-kind facts (not yet seen in this session) still pay tracker cost — the cache makes repeat lookups within 5s window cheap.

### Storage / memory

* ~1000 entries × ~200 bytes per ProvenanceRecord = ~200 KB working set. Negligible.
* No on-disk component.

### Operational

* Cache is **read-through**: tracker writes are not cached unless re-read via `get_provenance`.
* Cache is **invalidate-on-stale**: TTL expiry forces re-read; no need for cache-coherence protocol with the tracker.
* Cache **does NOT extend liveness** of provenance: facts deleted from tracker drop out of L1 within TTL window. Stale reads bounded by TTL.

## Invariants

Pinned in machine-readable contract `docs/eig2/contracts/provenance_tip_cache.json` and verified by `tests/contracts/test_provenance_tip_cache.py`.

1. **Tracker authoritative.** L1 cache is a read-through view; tracker is the source of truth. If tracker says None, cache must NOT return a stale hit (TTL must be respected).
2. **L0 supersedes L1.** Facts in `self._records` (session-local) are authoritative over any L1 cached entry with the same fact_id.
3. **TTL bound.** Cache entries expire after `_TTL_SECONDS` (default 5.0) and trigger a fresh tracker read on next access.
4. **LRU bounded.** Cache size MUST NOT exceed `_CACHE_MAX_ENTRIES` (default 1000). Oldest-by-access eviction.
5. **No write amplification.** Cache reads do NOT modify tracker state. Cache miss triggers exactly one tracker read.
6. **Boot preload optional.** If `prewarm_on_init=True`, constructor pulls last K tips from tracker into L1. Default off (no implicit DB read at Container instantiation).
7. **Cache invalidation hook.** `MagmaProvenanceAdapter.invalidate_cache(fact_id=None)` MUST be available: `None` flushes all, specific id flushes that entry. For operator-driven coherence after tracker rewrites.

## Out of scope (this ADR)

* Implementation of `_TipCache` class — separate PR.
* Cache statistics endpoint (`/admin/provenance_cache_stats`) — separate PR.
* Cross-process cache (Redis or similar) — explicitly NOT planned; this is a single-process LRU.
* Distributed cache coherence — not planned.

## References

* ADR-011 (compact-card write-storm breaker)
* ADR-021 (progressive replay L0–L4)
* ADR-022 (forensic snapshot rotation)
* 50-leaps menu, L20 (provenance tip cache 5s TTL), L17 (cold-tier read-through cache)
* `waggledance/core/magma/provenance.py` — current adapter implementation
