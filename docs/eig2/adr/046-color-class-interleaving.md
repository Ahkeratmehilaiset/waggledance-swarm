# ADR-046 — Color-class interleaving (hexacon 3-color partition)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-038 (tunnel overlay), `docs/architecture/HEX_TOPOLOGIES.md`

## Context

Synthesis tasks today concentrate in one hex cell at a time. The 50-leaps menu (L9) proposes a hexacon-track 3-color partition: cells are 3-colored such that no adjacent cells share a color; synthesis rotates across colors so each round samples a different color set. Guarantees cross-domain breadth.

## Decision

Each hex cell gets a `color_class ∈ {A, B, C}` derived from `(coord.q + 2 * coord.r) mod 3`. Synthesis tasks request cells via `select_origin_cells_by_color(color)`. Round-robin or weighted-rotation across colors per synthesis tick.

## Invariants (CCI-001..CCI-007)

1. **3-color enum**: `{A, B, C}` only.
2. **Coloring formula**: `(coord.q + 2*coord.r) mod 3` → maps to A/B/C deterministically.
3. **Adjacent non-share**: no two ring-1 neighbors share color (mathematical property of the formula on axial hex grid).
4. **Rotation policy default**: round-robin across A→B→C per synthesis tick. Operator-tunable to weighted.
5. **No interaction with veto**: color rotation is INDEPENDENT of negative tunnels — both apply, color first filters candidate set, veto then prunes.
6. **Computed at __init__**: color_class precomputed and stored in HexCellDefinition (read-only after load).
7. **Bridge events emit color**: synthesis events log `color_class` field for analysis.

Contract: `docs/eig2/contracts/color_class_interleaving.json`. Tests: `tests/contracts/test_color_class_interleaving.py`.
