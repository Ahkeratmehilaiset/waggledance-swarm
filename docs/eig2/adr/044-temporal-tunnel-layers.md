# ADR-044 — Temporal tunnel layers

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-038 (tunnel overlay)

## Context

Routing today is season-blind: apiary queries in winter route the same as in summer. Real beekeeping behavior changes seasonally; the routing graph should too. The 50-leaps menu (L7) calls for **temporal tunnel layers**: separate tunnel registries per time-of-day or per-season.

## Decision

Each tunnel record gets an optional `temporal_layer` field. Allowed values: `"all"` (default, no temporal restriction), `"season:spring"`, `"season:summer"`, `"season:autumn"`, `"season:winter"`, `"hour:0-6"`, `"hour:6-12"`, `"hour:12-18"`, `"hour:18-24"`. Routing path filters tunnels by current time-bucket; falls back to `"all"`-layer if no temporal match.

## Invariants (TTL-001..TTL-007)

1. **Default layer "all"**: backward-compat, no temporal restriction.
2. **Enum of valid layers** pinned in contract; arbitrary strings rejected.
3. **Hour buckets non-overlapping**: 4 × 6h windows covering 24h.
4. **Season buckets calendrical**: standard meteorological seasons (spring=Mar-May, summer=Jun-Aug, autumn=Sep-Nov, winter=Dec-Feb).
5. **Fallback to "all"**: when no temporal-layer tunnel matches, fall through to "all" layer.
6. **Single-layer-per-tunnel**: a tunnel record has exactly one temporal_layer (no multi-layer assignment).
7. **Lifecycle inheritance**: temporal tunnels follow ADR-038 lifecycle (30-day revalidation, 90-day archive).

Contract: `docs/eig2/contracts/temporal_tunnel_layers.json`. Tests: `tests/contracts/test_temporal_tunnel_layers.py`.
