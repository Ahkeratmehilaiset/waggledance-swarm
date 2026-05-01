# Phase 16A P5 — Restart continuity smoke

**Test:** `tests/autonomy_growth/test_upstream_restart_continuity.py::test_promoted_solvers_survive_db_close_reopen`

## Sequence

1. Open scratch SQLite control-plane DB; install six low-risk family policies.
2. Build `AutonomyService` against fresh runtime (Phase 16A wiring).
3. Drive a six-seed corpus (one seed per low-risk family) through `AutonomyService.handle_query` with **flat domain context** only.
4. Flush buffered runtime gap signals.
5. Run digest → autogrowth scheduler. All six solvers auto-promoted.
6. Pass 2 (still on the same DB): all six served via capability lookup.
7. Snapshot persisted solver count and capability-feature count.
8. **Close** the control-plane DB.
9. **Reopen** the same DB file. Build a fresh `AutonomyService` against the reopened DB. Do NOT re-`migrate()`; the schema and rows must persist.
10. Run the same six-seed flat-domain corpus through the rebuilt service.

## Result

| Metric | Value |
| --- | --- |
| smoke corpus size | 6 (one per low-risk family) |
| auto_promoted before close | 6 |
| solver_capability_features before close | > 0 |
| auto_promoted after reopen | 6 (identical) |
| solver_capability_features after reopen | identical |
| served after reopen via capability lookup | 6 / 6 |
| cache_rebuild_success | true (cold-warm transition observed) |
| restart_provider_jobs_delta | 0 |
| restart_builder_jobs_delta | 0 |

## Invariants asserted

* persisted solver count after reopen equals count before close,
* persisted `solver_capability_features` count after reopen equals count before close,
* same flat upstream input is served via capability lookup after reopen,
* warm-path cache rebuilds on demand from the persisted control plane,
* zero provider / builder activity during the restart.
