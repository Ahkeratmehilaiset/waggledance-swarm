# ADR-052 — Multi-objective promotion (4-axis fitness)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-048 (portfolio promotion), ADR-050 (domain-bridging incentive)

## Context

Today's promotion is single-objective (accuracy). The 50-leaps menu (L28) calls for **multi-objective promotion**: accuracy + latency + breadth (domains covered) + novelty (uncovered intent classes). Portfolio diversity instead of one-dimensional optimum.

## Decision

Promotion score is a weighted sum over four axes:

| Axis | Weight | Measure |
|---|---:|---|
| Accuracy | 0.50 | canary pass rate |
| Latency | 0.20 | inverse p50 ms (normalized) |
| Breadth | 0.15 | # distinct domains covered (cap 5, normalized to 1.0) |
| Novelty | 0.15 | # uncovered intent classes captured (cap 10, normalized to 1.0) |

Final: `score = 0.50*accuracy + 0.20*latency + 0.15*breadth + 0.15*novelty`. All axes in [0, 1].

## Invariants (MOP-001..MOP-007)

1. **4 axes**: accuracy, latency, breadth, novelty.
2. **Weights sum to 1.0**: pinned 0.50 + 0.20 + 0.15 + 0.15 = 1.00.
3. **Each axis [0, 1]**: normalized at measurement.
4. **Per-profile weight override**: cottage may favor latency higher; factory accuracy higher. Default pinned.
5. **Accuracy dominant**: accuracy weight MUST be > sum of other weights. Catches degenerate fast-but-wrong solvers.
6. **Cap normalization**: breadth caps at 5 domains, novelty at 10 classes; values above cap → 1.0.
7. **Auditable**: promotion log records all four axis values + final score.

Contract: `docs/eig2/contracts/multi_objective_promotion.json`. Tests: `tests/contracts/test_multi_objective_promotion.py`.
