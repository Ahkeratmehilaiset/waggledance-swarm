Phase 16B P2 — full-corpus restart continuity proof
============================================================
selected_upstream_caller   = waggledance.application.services.autonomy_service.AutonomyService.handle_query
corpus_total               = 128
manual_structured_in_input = False
manual_hint_in_input       = False

Pass 1 (before harvest):
  served = 0
  miss   = 128

Harvest:
  intents      = 128
  promoted     = 128
  rejected     = 0
  errored      = 0

Pre-restart pass 2:
  served                       = 128
  served_via_capability_lookup = 128
  miss                         = 0

Persisted state across reopen:
  solver_count before/after = 128 / 128
  capability_features before/after = 220 / 220

Post-restart pass 2:
  served                       = 128
  served_via_capability_lookup = 128
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