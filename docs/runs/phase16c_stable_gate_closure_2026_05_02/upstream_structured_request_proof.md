Phase 16A — Upstream structured_request propagation proof
============================================================
selected_upstream_caller     = waggledance.application.services.autonomy_service.AutonomyService.handle_query
corpus_total                 = 104
manual_structured_in_input   = False
manual_low_risk_hint_in_in   = False
proof_built_runtime_q        = False
proof_bypassed_caller        = False
proof_bypassed_handle_query  = False

Derivation:
  structured_request_derived_total = 104
  low_risk_hint_derived_total      = 104
  rejected_total                   = 5
  skipped_total                    = 2

Pass 1 (before harvest):
  served       = 0
  miss/fallback= 104
  buffered_flushed = 13

Harvest cycle:
  intents_created   = 104
  scheduler_drained = 104
  promoted          = 104
  rejected          = 0
  errored           = 0

Pass 2 (after harvest, cold cache):
  served                       = 104
  served_via_capability_lookup = 104
  miss                         = 0

Negative cases passed: 7 / 7

Latency:
  pass1 service.handle_query p50 / p99 = 11.0651 / 420.2008 ms
  pass2 cold p50 / p99               = 11.0468 / 16.9745 ms
  pass3 warm p50 / p99               = 10.3522 / 20.0777 ms
  upstream extractor only p50 / p99  = 0.0073 / 0.0258 ms

Hot path:
  warm_hits        = 318
  cold_hits_warmed = 98
  misses           = 104

KPIs:
  auto_promotions_total            = 104
  growth_events_total              = 416
  provider_jobs_delta_during_proof = 0
  builder_jobs_delta_during_proof  = 0