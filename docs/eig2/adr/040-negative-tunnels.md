# ADR-040 — Negative tunnels (anti-routing edges)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-038 (tunnel overlay, direction enum), ADR-031 (confidence-bin gap mining), ADR-032 (cross-agent failed broadcast)

## Context

ADR-038 (L2) pinned the `direction` enum with values `forward` and `negative`. Negative tunnels exist in the registry shape; this ADR (L5) specifies the MINING / DETECTION side: when does the system propose a NEW negative tunnel?

A negative tunnel is a "do-NOT-route X→Y" learning. Today, hallucination cross-contamination spreads silently: an answer from `cell_A` keeps being chosen for queries that ALWAYS produce wrong answers from that cell. Without negative tunnels, the system rediscovers the same mismatch repeatedly.

## Decision

A new `NegativeTunnelMiner` runs on a periodic schedule (default hourly) and produces negative tunnels from `contradiction_event` records in the bridge events stream:

1. Read `contradiction_event` entries from last `mining_window_hours=24`.
2. Group by `(from_cell, to_solver)` pair.
3. If a pair has ≥ `min_contradictions=5` contradiction events in the window AND a contradiction rate ≥ `min_contradiction_rate=0.30` (vs successful invocations), propose a negative tunnel with `trust_score = contradiction_rate`.
4. Append to `configs/tunnel_overlay.yaml` with `direction: negative` and the provenance_event_id pointing to the most recent contradiction event.

Negative tunnels do NOT remove an existing forward tunnel automatically. If both exist, routing path treats negative as a HARD VETO: regardless of forward trust_score, the route is skipped.

## Consequences

### Routing intelligence

* Known-bad routes are deterministically pruned.
* Cross-contamination patterns become visible (operator can read the YAML to see "what doesn't route to what").

### Operational

* Negative tunnels have lifecycle: revalidated every 30 days like forward tunnels (ADR-038 TUN-007). If contradictions stop, tunnel auto-archives.
* Operator can hand-edit YAML to ADD a negative tunnel for known issues (e.g., during incident response: "do NOT route ALL queries through this broken solver while we investigate").

## Invariants

Pinned in `docs/eig2/contracts/negative_tunnels.json` and verified by `tests/contracts/test_negative_tunnels.py`.

1. **Mining window 24h.** Default `mining_window_hours=24`. Operator-tunable [1, 168].
2. **Min contradictions 5.** Default `min_contradictions=5`. Below this no negative tunnel is mined (too few signals).
3. **Min contradiction rate 0.30.** Pair must have ≥ 30% contradiction rate vs successful invocations to mine. Pinned default.
4. **HARD VETO semantics.** A negative tunnel for (from_cell, to_solver) prevents ROUTING regardless of forward tunnel trust_score. Forward tunnel may co-exist but is dominated.
5. **Provenance required.** Each negative tunnel has `provenance_event_id` pointing to the most recent contradiction event. Same as TUN-002.
6. **Hourly mining cadence.** Default `mining_cadence_seconds=3600`. Configurable [600, 86400].
7. **Lifecycle alignment.** Negative tunnels follow same revalidation/archive lifecycle as forward tunnels (TUN-007). Stale negative tunnels archive automatically when contradictions stop.

## Out of scope (this ADR)

* Implementation of `NegativeTunnelMiner` — separate PR.
* `contradiction_event` schema (assumed pre-existing in bridge) — separate ADR if not yet pinned.
* Cross-agent negative-tunnel sharing (similar to ADR-032 failed-candidate broadcast) — future ADR if pursued.

## References

* ADR-038 (L2 tunnel overlay, direction enum)
* ADR-031 (L21 confidence-bin gap mining)
* ADR-032 (L22 cross-agent failed-candidate broadcast, complementary anti-knowledge)
* 50-leaps menu: L5 (this), L2, L3, L22
