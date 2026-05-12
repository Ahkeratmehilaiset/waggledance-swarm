# ADR-043 — Curiosity-gradient routing

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-031 (confidence-bin gap mining), ADR-038 (tunnel overlay)

## Context

Today's routing favors HOT cells (high success rate, high invocation count). This is greedy: the system keeps exploiting known-good cells while uncertain/cold cells stagnate. The 50-leaps menu (L6) proposes **curiosity-gradient routing**: boost routes through cells with HIGH gap_miner activity (uncertain → learning frontier). Reverses the greedy bias so the system grows where it doesn't know.

## Decision

Routing scoring gets a curiosity bonus: `score = base_score + curiosity_weight * normalized_gap_signals`. Cell with many recent runtime_gap_signals → boosted ranking in select_origin_cells_top_k (ADR-039 portfolio) or as tiebreaker in select_origin_cell.

Defaults: `curiosity_weight=0.15` (modest), normalization = `cell_gap_count / max(cell_gap_count across all cells)`.

## Invariants (CGR-001..CGR-007)

1. **Curiosity weight ≤ 0.20**: curiosity is an INFLUENCE not a DICTATOR. Cell base_score still dominates.
2. **Normalized over fleet**: gap_signal counts normalized so curiosity adds [0, curiosity_weight]. No raw counts.
3. **Configurable per profile**: default 0.15, range [0.0, 0.20].
4. **Curiosity decays with success**: a cell that succeeds repeatedly (gap_signals drop) loses its boost organically.
5. **No interaction with hard veto**: a curiosity-boosted negative-tunnel-targeted route is still vetoed (ADR-040 NTU-004).
6. **Read-only signal**: curiosity scorer never modifies gap_signal records.
7. **Hot-path bound**: scoring lookup < 5 µs (matches TUN-005 budget).

Contract at `docs/eig2/contracts/curiosity_gradient_routing.json`. Tests at `tests/contracts/test_curiosity_gradient_routing.py`.
