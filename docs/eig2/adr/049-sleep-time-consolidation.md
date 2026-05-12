# ADR-049 — Sleep-time consolidation for shadow solver promotion

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-034 (anti-cargo-cult), ADR-048 (portfolio promotion)

## Context

Today's promotion gate fires at FIRST PASS: a candidate that scores well in the initial canary gets promoted. This hedges against early flakes — a candidate that wins by luck on day 1 may regress on day 2. The 50-leaps menu (L24) calls for **sleep-time consolidation**: overnight ticker re-evaluates shadow solvers against rolling-window quality; promotes only those that beat 7-day median.

## Decision

A `SleepConsolidator` background task runs nightly (cron-style, default 02:00 local):

1. Reads all shadow solvers (post-canary, pre-promotion).
2. For each, computes 7-day rolling-window accuracy on Axis-B probes.
3. Promotion condition: solver's 7-day median ≥ 7-day median of CURRENT production solver, AND solver's 7-day STDDEV ≤ production solver's 7-day STDDEV.
4. Passing candidates are atomically promoted; failing candidates remain shadow.
5. Shadows that don't promote for 14 days auto-archive (consolidation gives them time to mature; 14d ceiling prevents stagnation).

## Invariants (STC-001..STC-007)

1. **Nightly schedule**: default `02:00 local` (configurable cron expression).
2. **7-day rolling window**: median + STDDEV computed over last 7 days. Fixed.
3. **Median dominance**: candidate median ≥ production median REQUIRED.
4. **STDDEV non-regression**: candidate stddev ≤ production stddev REQUIRED. Prevents promoting noisier solvers.
5. **Atomic promotion**: shadow→prod swap is atomic; no partial state where two solvers serve same capability.
6. **14-day ceiling**: shadow not promoted for 14 days auto-archives to `retired` state (not deleted; can be revived).
7. **Audit log**: every promotion + archive logged with reason codes (PROMOTE_MEDIAN_PASS / ARCHIVE_14D_CEILING / etc).

Contract: `docs/eig2/contracts/sleep_time_consolidation.json`. Tests: `tests/contracts/test_sleep_time_consolidation.py`.
