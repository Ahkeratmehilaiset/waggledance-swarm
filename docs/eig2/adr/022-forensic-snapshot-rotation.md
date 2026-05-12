# ADR-022 — Forensic snapshot rotation for L4 MAGMA replay

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Supersedes: none
* Related: ADR-011 (compact-card write-storm breaker), ADR-014 (queue+backpressure before breaker), ADR-021 (progressive replay L0–L4)

## Context

MAGMA's L4 tier is the **forensic** layer — the full event chain, available for audit, rollback, and high-risk-action replay. ADR-021 (Codex's L11) defines the progressive-replay strata: L0/L1 at boot, L2/L3 lazy, L4 only for audit / rollback / high-risk.

Today, any L4 forensic replay walks the **entire chain** from the origin event. As chain length grows (`N` events), forensic replay cost is **O(N)**. At the 1M-event scale the EIG2 trajectory is targeting, a single audit query becomes operationally expensive — possibly minutes — which discourages running audit-grade replay during incident response when it is most needed.

## Decision

L4 forensic replay reads from **N-hourly snapshots**, not the raw chain. The full chain remains the source of truth and stays available, but replay begins from the **nearest preceding snapshot** and reapplies only the delta of events since that snapshot.

Snapshots are immutable, hash-anchored to the raw event at the snapshot boundary, and stored separately from the live chain (warm tier for the last K snapshots, cold tier for older).

## Consequences

### Replay cost

* Forensic replay cost becomes **O(K + delta)** where K is snapshot deserialization cost and delta is the number of events since the snapshot. With snapshots taken every hour, delta is bounded by the per-hour event rate.
* At steady state (e.g., 10k events/hour), replay walks ~10k events instead of N. For N=1M, that is a 100× reduction.

### Storage cost

* Snapshots add storage proportional to the working-set size of the world model. Estimated: 1–10 MB per snapshot for current scale, growing with M3+M4 substrate.
* zstd-at-rest (L18 from the 50-leaps menu) compounds: cold snapshots compress 5–10×.

### Operational

* Snapshot generation is a background tick (proposed: hourly), not on the replay critical path.
* Snapshot failure (disk full, hash mismatch) **fails closed**: forensic replay falls back to raw-chain walk with a logged WARNING. No silent data loss.
* Snapshot integrity is hash-anchored to the raw event at the snapshot boundary, so corruption is detectable.

## Invariants

The forensic-snapshot mechanism MUST maintain the following invariants. These are pinned in the machine-readable contract file at `docs/eig2/contracts/forensic_snapshot_rotation.json` and verified by `tests/contracts/test_forensic_snapshot_rotation.py`.

1. **Raw chain authoritative.** The raw event chain is the source of truth. Snapshots are derived; if a snapshot conflicts with the raw chain (hash mismatch), the raw chain wins and a WARNING is logged.
2. **Snapshot boundary hash.** Every snapshot stores the hash of the raw event at the snapshot boundary. Replay verifies the hash before trusting the snapshot.
3. **Fail-closed fallback.** Snapshot read failure causes replay to fall back to raw-chain walk. No silent skip; a WARNING is logged with the snapshot ID.
4. **Replay equivalence.** Replay-from-snapshot + delta MUST produce the same observable state as raw-chain walk for the same event range. Tested via property-style equivalence check (small synthetic chains).
5. **No write amplification.** Snapshot generation does not modify the raw chain. It is read-only from the chain's perspective.
6. **Rotation policy is policy, not code.** The rotation cadence (every N hours), retention window (keep K snapshots warm, older to cold), and tier transitions are operator-configurable via `configs/forensic_snapshots.yaml` (file to be created in implementation PR). No magic numbers in code.

## Out of scope (this ADR)

* Implementation of `MagmaSnapshotEngine` (the rotation tick) — separate PR.
* zstd-at-rest compression — L18 in the 50-leaps menu, separate ADR if pursued.
* Snapshot-driven incremental retraining of derived layers (L1/L2/L3 caches) — separate ADR.

## References

* ADR-021 (progressive replay L0–L4)
* 50-leaps menu, L19 (forensic-snapshot rotation), L18 (zstd-at-rest)
* `docs/architecture/MAGMA_VECTOR_STAGE2.md` for MAGMA tier definitions
