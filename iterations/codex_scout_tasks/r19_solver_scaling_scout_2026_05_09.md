# R19 — Phase D Priority 3 solver-scaling scout (2026-05-09)

- timestamp: 2026-05-09T18:55Z
- scout: claude (Codex silent for ~30 min during Phase D Priority 2 wrap-up; resilience-driven takeover per operator R14 plan + R20 master prompt)
- task: Phase D Priority 3 — 10k+ solver scaling concrete blockers
- ordering: MAGMA latencies (Priority 1 ✅) > hexagon delays (Priority 2 ✅) > **10k+ scaling (this scout)**

## Context

Codex's R17 MAGMA scout (PR #164) explicitly noted:

> Existing Phase 17A tests already prove the real 10k capability lookup
> path through `RuntimeQueryRouter` and `ControlPlaneDB`. The new risk
> from this scout is adjacent: MAGMA trust/vector bookkeeping around
> those solvers has no equivalent 10k regression guard yet.

Phase D Priority 1 (R17 #165 / #166 / #167) closed all three measured
MAGMA-bookkeeping risks Codex flagged. Phase D Priority 2 (R18 #170 /
#171, plus #168/#169 scout artifacts and the Cand 2 abandon doc) closed
the measurable hex-topology hot paths.

So Priority 3 is now: are there concrete remaining 10k+ scaling
blockers that R17/R18 did not cover?

## Source-of-truth measurement: existing 10k canonical run

The canonical `solver_scale_proof.json` from
`docs/runs/phase17a_producer_fabric_scale_2026_05_04/` (commit-archived
in `.codex-audit/`) shows the 10k baseline:

| Metric | Value at 10000 descriptors |
|---|---:|
| `build_index_time_seconds` | 147.25 s |
| `build_descriptors_per_second` | 67.9 |
| `lookup_p50_ms` | 4.24 ms |
| `lookup_p95_ms` | 10.78 ms |
| `lookup_p99_ms` | **14.10 ms** |

**Lookup p95 (10.78 ms) and p99 (14.10 ms) cross the 10 ms operator
threshold at 10k**. Lookup p50 (4.24 ms) is fine.

**Build at 10k takes 147 s** — acceptable for one-shot release artifact
generation but a real productivity tax for any iterative test/CI cycle
that wants to verify at full scale.

### Repeatability on this machine

Re-ran at 1000 descriptors (5x scaled-down) on this machine with the
existing tool (no changes): `tools/run_solver_scale_proof.py
--descriptors 1000 --lookup-pass-count 200`.

| Metric | This machine, 1000 descriptors |
|---|---:|
| `build_index_time_seconds` | 15.69 s |
| `build_descriptors_per_second` | 63.7 |
| `lookup_p50_ms` | 0.46 ms |
| `lookup_p95_ms` | 1.35 ms |
| `lookup_p99_ms` | 6.03 ms |

Build rate is consistent with the canonical machine (67.9 vs 63.7 desc/s).
Lookup latency at 1000 is well under threshold — extrapolating linearly
to 10k would put p99 around 60 ms, which contradicts the canonical
14 ms at 10k. So the lookup path is sub-linear at scale (likely O(log N)
indexed hits), and 1000-scale doesn't surface its real high-percentile
behavior. Use the canonical 10k run as the BEFORE baseline for any
lookup-targeted change.

## Candidates

### Candidate 1 — Build phase: redundant `get_solver` SELECT (this PR)

- target: `tools/run_solver_scale_proof.py :: bulk_load_descriptors`
- evidence: each descriptor does
  `db.upsert_solver(...)` then `db.get_solver(name)`. But
  `upsert_solver` already returns the `SolverRecord`
  (`waggledance/core/storage/control_plane.py:540-546` — it does the
  `INSERT ... ON CONFLICT DO UPDATE` then a `SELECT ... WHERE name`
  internally and returns). The follow-up `db.get_solver(name)` is a
  pure duplicate read.
- fix: capture `rec = db.upsert_solver(...)` directly. Drops 1 SELECT
  round-trip per descriptor → 10000 SELECTs at full scale.
- before/after at 1000 descriptors (3x AFTER runs vs prior BEFORE):
  noise-dominated at this scale (build rate 62.8 / 59.9 / 63.9 /s
  AFTER vs 63.7 /s BEFORE — under the run-to-run jitter floor of the
  bench). Code-hygiene win is real (one less SQLite read per row); a
  10k-scale re-run is needed to validate the projected ~5–10 s saved
  on the 147 s build.
- risk: zero. `SolverRecord` returned from `upsert_solver` and
  fetched by `get_solver(name)` are identical (same SELECT, same
  `_row_to_solver`). All 21 `tests/autonomy_growth/test_solver_scale_proof.py`
  cases pass after the change.

### Candidate 2 — Build phase: implicit per-row transaction commit

- target: `bulk_load_descriptors` outer loop + `ControlPlaneDB`
  call sites
- evidence: SQLite without an explicit `BEGIN` runs in autocommit
  mode — every `execute()` triggers an implicit transaction
  commit + fsync. With ~4 SQL writes per descriptor at default
  `synchronous=FULL` on Windows (~5–10 ms per fsync), that explains
  most of the 14.7 ms/descriptor observed build time. At 10k: ~150 s
  is dominated by 30000+ fsyncs.
- proposed fix: add an explicit `transaction()` context manager to
  `ControlPlaneDB` so callers can batch a build pass in a single
  transaction. Also expose a `db.bulk_upsert_solvers(...)` /
  `db.bulk_set_capability_features(...)` pair that uses
  `executemany` under one BEGIN/COMMIT, falling back to per-row
  semantics for callers that prefer them.
- estimated gain: 5–20× build-phase speedup (147 s → 7–30 s at 10k)
  based on the SQLite cost model. Needs a real bench at 10k to
  confirm.
- risk: medium. Transaction rollback semantics change — partial
  failure becomes "all or nothing". For a build script run as a
  one-shot before release artifact generation, this is acceptable;
  for production runtime callers the per-row API stays.
- est. PR size: 60–120 LoC implementation + 30–50 LoC tests +
  before/after artifact at 10k (one full ~150 s baseline run + one
  AFTER run).
- **This is the largest measurable Priority 3 win available.**
  Recommended as Candidate 2 follow-up if the operator wants
  another measurable Phase D PR before R20 begins.

### Candidate 3 — Lookup p99 at 10k (informational)

- target: `RuntimeQueryRouter.route()` and downstream
  `ControlPlaneDB.lookup_solver_capability_*`
- evidence: canonical 10k run shows p95=10.78 ms, p99=14.10 ms —
  both above the 10 ms operator threshold. p50 is fine (4.24 ms),
  so the regression is in the tail.
- proposed scout (NOT THIS PR): profile a real 10k run with
  `cProfile` and rank the top 5 functions by cumulative time at
  the p99 operations. Likely candidates: feature matching,
  family-kind lookup, or per-call dict construction in `RuntimeQuery`.
- risk if missing: medium-to-high. The threshold-crossing is
  already measured and shipped — runtime SLOs for capability
  lookup at 10k are at the edge.
- est. effort: 1–2 hours of profiling-driven investigation, then
  a focused fix. **Defer to R20 or post-R20 round.**

## Self-assessment

The R17 + R18 scout rounds already covered the MAGMA-bookkeeping risk
Codex flagged in their original Priority 3 note. What remains is:

1. **This PR**: Cand 1 redundant-SELECT removal — small but correct
   code-hygiene improvement; expected to surface as a measurable
   reduction only at full 10k scale.
2. **Cand 2 (transaction batching)**: largest available Priority 3
   win, but not a 30-minute PR — needs a real 10k baseline run plus
   the bench-and-evidence cycle. Recommend deferring to operator's
   discretion (could be one more Phase D PR before R20, or can be
   bundled into the R20.4 deployment-profiles work since
   profile-S/M/L will care about build-time too).
3. **Cand 3 (lookup p99)**: defer; needs profiling investigation
   and likely belongs in an R21 round once R20 lands.

So Phase D Priority 3 is **not fully closed** but the remaining
work is documented, sized, and prioritised — ready to hand off to
R20 routing.

## References

- Canonical 10k baseline:
  `.codex-audit/archive-pr124-validator-fix/docs/runs/phase17a_producer_fabric_scale_2026_05_04/solver_scale_proof.json`
- Existing scale proof tool: `tools/run_solver_scale_proof.py`
- Existing test: `tests/autonomy_growth/test_solver_scale_proof.py`
- This-machine 1000-descriptor BEFORE baseline:
  `.codex-audit/r19_scale_check/solver_scale_proof.json`
- This-machine 1000-descriptor AFTER (Cand 1) runs:
  `.codex-audit/r19_after_{1,2,3}/solver_scale_proof.json`
- R17 scout note on Priority 3:
  `iterations/codex_scout_tasks/r17_magma_latency_scout_2026_05_09.md`
  (last section `## 10k+ solver scaling note`)
