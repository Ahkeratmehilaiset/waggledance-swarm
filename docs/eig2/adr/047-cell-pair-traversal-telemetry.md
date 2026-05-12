# ADR-047 — Cell-pair traversal telemetry

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-042 (tunnel co-occurrence mining), ADR-038 (tunnel overlay)

## Context

ADR-042 (L3) mines tunnels from solver co-fire telemetry. The telemetry input (events.jsonl) already records solver dispatches, but not the PER-EDGE traversal: which from_cell → to_solver edge fired per query, with what trust/confidence. Tunnel mining today must reconstruct edges from dispatches; explicit edge events would feed mining cleaner.

The 50-leaps menu (L10) calls for **cell-pair traversal telemetry**: emit one event per cross-cell route describing the (from_cell, to_solver, query_class_hash, success) tuple. The autogrowth night-tick reads the edge histogram and feeds tunnel mining.

## Decision

A new bridge event type `cell_pair_traversal`:

```json
{
  "event_type": "cell_pair_traversal",
  "from_cell": "<cell_id>",
  "to_solver": "<solver_id>",
  "query_class_hash": "<sha256>",
  "success": true,
  "latency_ms": 42,
  "trust_at_dispatch": 0.85,
  "color_class": "A",
  "stage": "broad",
  "ts_utc": "<ISO8601>"
}
```

Emitted at solver dispatch completion (success OR failure). Sampled at 100% by default; profile can reduce sampling rate.

## Invariants (CPT-001..CPT-007)

1. **Event type pinned**: `cell_pair_traversal`.
2. **Required fields**: event_type, from_cell, to_solver, query_class_hash, success, latency_ms, ts_utc.
3. **Optional fields**: trust_at_dispatch, color_class (ADR-046), stage (ADR-045).
4. **Emit on completion**: both success and failure path emit. Allows mining anti-tunnels (ADR-040).
5. **Sampling default 100%**: `sampling_rate=1.0`. Profile-tunable down to 0.01 (1%).
6. **No on-path overhead**: emission is fire-and-forget (bridge append). Routing latency unaffected.
7. **Edge histogram retention 30 days**: events.jsonl rotation policy keeps cell-pair events for at least 30 days for tunnel mining.

Contract: `docs/eig2/contracts/cell_pair_traversal_telemetry.json`. Tests: `tests/contracts/test_cell_pair_traversal_telemetry.py`.
