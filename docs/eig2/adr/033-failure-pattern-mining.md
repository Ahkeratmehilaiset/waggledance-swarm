# ADR-033 — Failure-pattern mining for gap-miner anti-features

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-031 (confidence-bin gap mining), ADR-032 (cross-agent failed broadcast)

## Context

ADR-031 generates new candidates per confidence bin; ADR-032 broadcasts FAILURES across agents. What is missing: **structural learning** from the failures. Each rejected candidate carries information about WHY it failed — e.g., "all rejected solvers had > 2 conditional branches", or "all rejected route_classifiers had < 3 training examples". This structural signal can feed back into the gap-miner as ANTI-features so the miner stops producing the same rejected shape.

The 50-leaps menu (L25) calls for **failure-pattern mining**: extract structural patterns from rejected candidates, feed them back as anti-features.

## Decision

A new component, `FailurePatternMiner`, runs on a periodic schedule (default hourly) over the local agent's rejected-candidate history:

1. Reads N most recent rejected candidates (default N=200).
2. Extracts STRUCTURAL features (e.g., `n_conditional_branches`, `n_training_examples`, `feature_vector_dim`, `solver_family_complexity_score`).
3. Identifies "anti-feature" patterns where ≥ K of N rejections share the same structural value (default K=20).
4. Writes anti-features to `configs/gap_miner_anti_features.yaml` for the gap-miner to consume on the next tick.

Anti-features are **advisory**, not blocking. The gap-miner may still propose a candidate with an anti-feature value, but the proposal's promotion score gets a `-anti_feature_penalty=0.3` adjustment. Allows occasional retry of patterns that may now succeed due to other changes.

## Consequences

### Mining quality

* Gap-miner stops producing the same rejected shape, freeing canary cycles for novel patterns.
* Anti-features are HUMAN-READABLE (YAML) so operators can audit + override.

### Operational

* Anti-feature YAML is auto-rotated: entries older than 30 days are removed unless re-evidenced.
* Operator can manually pin an anti-feature with `permanent: true` to prevent rotation.

### Storage

* Anti-feature YAML ≈ 10-50 entries × ~200 bytes = ~10 KB total. Negligible.
* Rejected-candidate history (input) lives in autogrowth_scheduler state; no new storage layer.

## Invariants

Pinned in `docs/eig2/contracts/failure_pattern_mining.json` and verified by `tests/contracts/test_failure_pattern_mining.py`.

1. **N most-recent window.** Mining input is the LAST N rejected candidates (default 200). Older rejections fall out of the window.
2. **K-of-N threshold.** A structural feature value becomes an anti-feature when K of N rejections share it. Default K=20, N=200 (10%).
3. **Anti-feature TTL.** Anti-features auto-expire after 30 days unless re-evidenced by new rejections or marked `permanent`.
4. **Advisory not blocking.** Anti-features apply a penalty to candidate promotion score, NOT a hard ban. Default penalty 0.3.
5. **YAML persistence.** Anti-features written to `configs/gap_miner_anti_features.yaml`. Human-readable, version-controllable.
6. **Operator override.** Operator-set `permanent: true` flag on a YAML entry prevents automatic rotation.
7. **Mining cadence.** Default mining cadence: hourly. Configurable in [10min, 24h] range.

## Out of scope (this ADR)

* Implementation of `FailurePatternMiner` — separate PR.
* Cross-agent anti-feature sharing — covered by ADR-032 broadcast.
* Adaptive K threshold based on rejection volume — future work.

## References

* ADR-031 (confidence-bin gap mining, candidate-generation context)
* ADR-032 (cross-agent failed broadcast, complementary anti-knowledge transfer)
* 50-leaps menu: L25 (this), L21 (gap mining), L22 (cross-agent broadcast), L29 (anti-cargo-cult)
