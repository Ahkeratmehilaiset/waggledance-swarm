# ADR-042 — Tunnel co-occurrence learning (Hebbian mining)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-038 (tunnel overlay registry), ADR-040 (negative tunnels mining)

## Context

ADR-038 (L2) pins the TunnelRegistry shape (sparse audited graph). ADR-040 (L5) pins NEGATIVE tunnel mining from contradiction events. Missing: the FORWARD tunnel mining side — how new shortcut tunnels are PROPOSED from telemetry.

The 50-leaps menu (L3) calls for **Hebbian co-fire learning**: when two solvers consistently co-fire on the same query class, mine a tunnel between their cells. Substrate: bridge `events.jsonl` already records all solver dispatches, so the learning input is in place.

## Decision

A new `TunnelCoOccurrenceMiner` runs hourly:

1. Read solver dispatch events from `events.jsonl` over the last `mining_window_hours=168` (1 week).
2. Group dispatches by `(query_class_hash, solver_id)` pairs (matching ADR-032 normalization).
3. For each solver pair `(S_A, S_B)` that co-fire on ≥ `min_cofire_count=10` distinct query_class_hashes:
   * Compute Hebbian-like trust: `trust = cofire_count / max(S_A.invocations, S_B.invocations)` (Jaccard-like).
   * If `trust >= 0.70` (per TUN-001 default min_trust_score), propose a tunnel from `S_A's cell` to `S_B` (and another from `S_B's cell` to `S_A`).
4. Append to `configs/tunnel_overlay.yaml` per ADR-038 schema.

## Consequences

### Routing intelligence

* Tunnels are LEARNED from actual cross-solver coordination, not hand-curated.
* Sparse-by-design: only high-trust co-fires become tunnels.
* Same hot-path budget as forward routing (TUN-005, <5 µs lookup).

### Storage / bandwidth

* Mining input: `events.jsonl` already accumulates dispatch events (no new infra).
* Mining output: 0..N new tunnels per cycle. Bounded by min_trust_score threshold.

### Operational

* Operator can disable mining per profile via `tunnel_mining_enabled=false`.
* Mined tunnels follow ADR-038 lifecycle: revalidation after 30 days, archive after 90.

## Invariants

Pinned in `docs/eig2/contracts/tunnel_co_occurrence_mining.json` and verified by `tests/contracts/test_tunnel_co_occurrence_mining.py`.

1. **Mining window 7 days.** Default `mining_window_hours=168`. Operator-tunable [24, 720] (1 day to 30 days).
2. **Min co-fire count 10.** Pair below 10 co-fires in window not considered for tunnel mining.
3. **Jaccard-like trust formula.** `trust = cofire_count / max(invocations_A, invocations_B)`. Pinned in contract.
4. **Threshold matches ADR-038.** Mined tunnel's trust_score MUST equal Jaccard trust; tunnel accepted only when ≥ 0.70 (ADR-038 TUN-001 default).
5. **Bidirectional emission.** Co-fire mines BOTH directions (A→B AND B→A). Each gets its own tunnel record with its own provenance_event_id.
6. **Same lifecycle.** Mined tunnels use ADR-038 TUN-007 lifecycle (30-day revalidation, 90-day archive).
7. **Hourly cadence.** Default `mining_cadence_seconds=3600`. Range [600, 86400].

## Out of scope (this ADR)

* Implementation of `TunnelCoOccurrenceMiner` — separate PR.
* Query-class normalization helper (assumed shared with ADR-032 candidate hash) — separate ADR if not yet pinned.
* Operator dashboard for mined-tunnel inspection — future PR.

## References

* ADR-038 (L2 tunnel overlay registry)
* ADR-040 (L5 negative tunnel mining, complementary direction)
* ADR-032 (cross-agent failed broadcast, normalization precedent)
* 50-leaps menu: L3 (this), L2, L5, L10 (cell-pair telemetry)
