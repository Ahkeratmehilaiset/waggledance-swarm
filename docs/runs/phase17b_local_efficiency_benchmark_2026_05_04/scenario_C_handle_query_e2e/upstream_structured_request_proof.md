Phase 16A — Upstream structured_request propagation proof
============================================================
selected_upstream_caller     = waggledance.application.services.autonomy_service.AutonomyService.handle_query
corpus_total                 = 128
manual_structured_in_input   = False
manual_low_risk_hint_in_in   = False
proof_built_runtime_q        = False
proof_bypassed_caller        = False
proof_bypassed_handle_query  = False

Derivation:
  structured_request_derived_total = 128
  low_risk_hint_derived_total      = 128
  rejected_total                   = 5
  skipped_total                    = 2

Pass 1 (before harvest):
  served       = 0
  miss/fallback= 128
  buffered_flushed = 19

Harvest cycle:
  intents_created   = 128
  scheduler_drained = 128
  promoted          = 128
  rejected          = 0
  errored           = 0

Pass 2 (after harvest, cold cache):
  served                       = 128
  served_via_capability_lookup = 128
  miss                         = 0

Negative cases passed: 7 / 7

Latency:
  pass1 service.handle_query p50 / p99 = 12.6336 / 441.1616 ms
  pass2 cold p50 / p99               = 12.9475 / 57.0188 ms
  pass3 warm p50 / p99               = 12.0474 / 53.6429 ms
  upstream extractor only p50 / p99  = 0.0121 / 0.0374 ms

Hot path:
  warm_hits        = 390
  cold_hits_warmed = 122
  misses           = 128

KPIs:
  auto_promotions_total            = 128
  growth_events_total              = 512
  provider_jobs_delta_during_proof = 0
  builder_jobs_delta_during_proof  = 0