# v3.10.2-mined-solver-dispatch-alpha — Phase 18C

**Status:** PRERELEASE (alpha). Not GitHub Latest. `v3.8.0` remains GitHub Latest.

## What this release does

Phase 18C wires the Phase 18B mined ALLOWLISTED solver specs through the **real**
Phase 11–17A runtime dispatch path (`RuntimeQueryRouter` →
`LowRiskSolverDispatcher.dispatch_by_features`). No new dispatcher, executor,
router, or promotion engine was introduced.

* `register_mined_solver_specs(...)` performs the same 4-step
  `ControlPlaneDB` registration the Phase 17A 10k-scale proof exercises:
  `upsert_solver_family` → `upsert_solver(status='auto_promoted')` →
  `set_solver_capability_features` → `upsert_solver_artifact`.
* `LowRiskSolverDispatcher.dispatch_by_features` performs the SQL-backed
  capability superset lookup and returns the registered mined solver in
  every dispatch case, with `reason="hit_by_features"`.
* Six-family low-risk allowlist coverage: `scalar_unit_conversion`,
  `lookup_table`, `threshold_rule`, `interval_bucket_classifier`,
  `linear_arithmetic`, `bounded_interpolation` (6/6).
* Proof harness: 18/18 dispatch cases hit the mined solver, 8/8
  non-allowlisted candidates rejected, builder-handoff stays quarantined
  (0 solver rows).
* `provider_jobs_delta == 0`, `builder_jobs_delta == 0`,
  `allowlist_unchanged == true`.

## Gates green

* Phase 18C tests: 33/33 PASS in 5.41 s.
* Carry-forward targeted suite: 203/203 PASS in 16.37 s
  (Phase 18C 33 + 18B 19 + 18A 15 + phase10 14 + storage 50 +
  ui_hologram 22 + solver_router 50).
* Docker `--network none` Phase 18C proof: PASS.
* Docker `--network none` Phase 18B carry-forward: PASS.
* Docker `--network none` Phase 18A bundle validation: PASS.
* All 7 prior tag SHAs unchanged.

## What this release does NOT do

* Does NOT modify any of the 7 prior tags.
* Does NOT introduce a stable-tagged release.
* Does NOT widen the six-family allowlist.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT pull or download any Ollama model; does NOT call any cloud
  LLM API; does NOT execute any live builder lane.
* Does NOT make any cross-vendor ranking claim or raw-intelligence
  superiority claim.
* Does NOT add any new pip dependency.

See `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/release_decision.md`
for the full gate evaluation.
