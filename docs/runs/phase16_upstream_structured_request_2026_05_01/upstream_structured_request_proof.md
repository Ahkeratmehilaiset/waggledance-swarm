Phase 16A — Upstream structured_request propagation proof
============================================================
selected_upstream_caller     = waggledance.application.services.autonomy_service.AutonomyService.handle_query
corpus_total                 = 98
manual_structured_in_input   = False
manual_low_risk_hint_in_in   = False
proof_built_runtime_q        = False
proof_bypassed_caller        = False
proof_bypassed_handle_query  = False

Derivation:
  structured_request_derived_total = 98
  low_risk_hint_derived_total      = 98
  rejected_total                   = 5
  skipped_total                    = 2

Pass 1 (before harvest):
  served       = 0
  miss/fallback= 98
  buffered_flushed = 49

Harvest cycle:
  intents_created   = 98
  scheduler_drained = 98
  promoted          = 98
  rejected          = 0
  errored           = 0

Pass 2 (after harvest, cold cache):
  served                       = 98
  served_via_capability_lookup = 98
  miss                         = 0

Negative cases passed: 7 / 7

Latency:
  pass1 service.handle_query p50 / p99 = 10.0509 / 42.2303 ms
  pass2 cold p50 / p99               = 10.1727 / 15.0071 ms
  pass3 warm p50 / p99               = 9.3572 / 17.5943 ms
  upstream extractor only p50 / p99  = 0.0091 / 0.0164 ms

Hot path:
  warm_hits        = 300
  cold_hits_warmed = 92
  misses           = 98

KPIs:
  auto_promotions_total            = 98
  growth_events_total              = 392
  provider_jobs_delta_during_proof = 0
  builder_jobs_delta_during_proof  = 0