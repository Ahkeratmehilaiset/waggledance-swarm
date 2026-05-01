Phase 16B P2 — full-corpus restart continuity proof
============================================================
selected_upstream_caller   = waggledance.application.services.autonomy_service.AutonomyService.handle_query
corpus_total               = 104
manual_structured_in_input = False
manual_hint_in_input       = False

Pass 1 (before harvest):
  served = 0
  miss   = 104

Harvest:
  intents      = 104
  promoted     = 104
  rejected     = 0
  errored      = 0

Pre-restart pass 2:
  served                       = 104
  served_via_capability_lookup = 104
  miss                         = 0

Persisted state across reopen:
  solver_count before/after = 104 / 104
  capability_features before/after = 180 / 180

Post-restart pass 2:
  served                       = 104
  served_via_capability_lookup = 104
  miss                         = 0

Restart invariants:
  served_unchanged_across_restart           = True
  served_via_capability_unchanged_across_restart = True
  solver_count_unchanged_across_reopen       = True
  capability_features_unchanged_across_reopen = True
  provider_jobs_delta_across_restart         = 0
  builder_jobs_delta_across_restart          = 0
  cache_rebuild_success                      = True

provider_jobs_delta_during_proof = 0
builder_jobs_delta_during_proof  = 0


## Tooling and tests

* Tool: `tools/run_full_restart_continuity_proof.py`
* Smoke test: `tests/autonomy_growth/test_full_restart_continuity_smoke.py` — 12/12 pass
* Artifact JSON: `full_restart_continuity_proof.json`

## What this proves

Phase 16A shipped a six-seed restart smoke. This phase scales it to the **full canonical corpus (104 seeds across all six low-risk families and eight cells)** and verifies that the persisted control plane survives a real close+reopen with the same flat upstream input still served via capability lookup.

The proof routes the full corpus through `AutonomyService.handle_query` using only flat domain context (no `structured_request`, no `low_risk_autonomy_query`). After harvest + auto-promotion, the proof closes the control-plane SQLite DB, reopens it, builds a fresh `AutonomyService` against the reopened DB, and re-routes the same flat upstream corpus. Persisted solver count and `solver_capability_features` count are identical across reopen, the same 104 / 104 are served via capability lookup, and zero provider / builder activity occurred across the restart.

## Stable gate disposition

This proof **passes both the g04 (100+ corpus) and g14 (full-corpus restart continuity) stable gates** at the post-Phase-16B-P4 104-seed canonical corpus.