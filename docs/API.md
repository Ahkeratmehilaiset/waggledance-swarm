# WaggleDance API Reference

*API-viite — All endpoints on port 8000*

## Overview

WaggleDance exposes a REST API via FastAPI. Two modes:
- **Production** (`python start_waggledance.py`) — full runtime, real data
- **Stub** (`start_waggledance.py --stub`) — mock backend for dashboard development, no Ollama needed

### Authentication

Protected `/api/*` endpoints accept two auth methods:

1. **Bearer token** (for cURL, scripts, CI):
   ```
   Authorization: Bearer <WAGGLE_API_KEY>
   ```
2. **HttpOnly session cookie** (for browser — set automatically by dashboard):
   ```
   Cookie: waggle_session=<opaque_token>
   ```

- Token auto-generated on first startup and saved to `.env` as `WAGGLE_API_KEY`
- **Public (no auth):** `/health`, `/ready`, `/api/status`, `/api/auth/check`, `/api/feeds`, `/api/hologram/state`, `/api/capabilities/state`, `/api/learning/state-machine`
- **Session endpoints:** `POST /api/auth/session` (create, requires Bearer), `GET /api/auth/check` (public), `DELETE /api/auth/session` (logout)
- **Hologram cookie bootstrap (v3.5.7):** `GET /hologram?token=<api_key>` mints an HttpOnly session cookie via 303 redirect to `/hologram`. The token is consumed and does not appear in the redirected URL. Wrong or missing token serves the page unchanged (no cookie, chat disabled). This is the standard way for browsers to establish a session for the hologram dashboard.
- **WebSocket:** session cookie (browser) or `?token=` query parameter (scripts)
- The API key value never appears in served HTML, inline JS, or browser storage

### Rate Limits & Input Validation

Rate limit: **60 requests/min** per IP (token bucket).
Input limits: chat message 10,000 chars, voice text 5,000 chars, voice audio 10MB.

---

## Health & Readiness

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Liveness probe. Returns `{"status": "ok"}` |
| `GET /ready` | GET | Readiness probe. Checks runtime running state |
| `GET /healthz` | GET | Kubernetes-convention alias of `/health` |
| `GET /readyz` | GET | Kubernetes-convention alias of `/ready` |
| `GET /version` | GET | Build identification (auth-exempt). Returns `{name, version, python, platform}` — stable shape for rolling-restart detection. No secrets, no filesystem paths. |
| `GET /metrics` | GET | Prometheus text-format exposition (auth-exempt). Exposes hex-mesh efficiency counters (15 counters + 2 gauges), privacy-safe route-stage count gauges, route-stage runtime observation/latency counters, low-risk autogrowth ticker boundary metrics, read-only MAGMA handoff provider-health/freshness gauges, plus source health gauges. Private `CollectorRegistry` — no default `python_gc_*` / `process_*` collector leakage. Content-Type `text/plain; version=0.0.4`. |

```json
// GET /health
{"status": "ok"}

// GET /ready
{"status": "ready", "hivemind": true}

// GET /version
{
  "name": "waggledance-swarm",
  "version": "3.5.7",
  "python": "3.13.0",
  "platform": "Windows-11-..."
}
```

```
# GET /metrics (text/plain; version=0.0.4)
# HELP waggledance_up Liveness gauge (1 = metrics source healthy).
# TYPE waggledance_up gauge
waggledance_up 1.0
# HELP waggledance_hex_preflight_skips_total Queries skipped via cheap preflight gating.
# TYPE waggledance_hex_preflight_skips_total counter
waggledance_hex_preflight_skips_total 0.0
# HELP waggledance_autogrowth_background_enabled 1 if the low-risk autogrowth background ticker is configured.
# TYPE waggledance_autogrowth_background_enabled gauge
waggledance_autogrowth_background_enabled 1.0
# HELP waggledance_autogrowth_wakeups_total low-risk autogrowth runtime-boundary counter: wakeups_total.
# TYPE waggledance_autogrowth_wakeups_total counter
waggledance_autogrowth_wakeups_total 0.0
# HELP waggledance_route_stage_count Privacy-safe chat route-stage counts by group.
# TYPE waggledance_route_stage_count gauge
waggledance_route_stage_count{group="expected"} 8.0
waggledance_route_stage_count{group="disabled_optional"} 1.0
# HELP waggledance_route_stage_observations_total Total sanitized chat requests where the route stage was observed.
# TYPE waggledance_route_stage_observations_total counter
waggledance_route_stage_observations_total{stage="language_detection"} 12.0
# HELP waggledance_route_stage_request_latency_ms_total Total request latency in milliseconds for sanitized chat requests where the route stage was observed.
# TYPE waggledance_route_stage_request_latency_ms_total counter
waggledance_route_stage_request_latency_ms_total{stage="language_detection"} 320.5
# HELP waggledance_route_stage_request_latency_histogram_ms Histogram of request latency in milliseconds for sanitized chat requests where the route stage was observed.
# TYPE waggledance_route_stage_request_latency_histogram_ms histogram
waggledance_route_stage_request_latency_histogram_ms_bucket{le="250",stage="language_detection"} 11.0
waggledance_route_stage_request_latency_histogram_ms_bucket{le="+Inf",stage="language_detection"} 12.0
waggledance_route_stage_request_latency_histogram_ms_count{stage="language_detection"} 12.0
waggledance_route_stage_request_latency_histogram_ms_sum{stage="language_detection"} 320.5
# HELP waggledance_magma_handoff_provider_up 1 if the metrics collector could build MAGMA handoff provider health this scrape.
# TYPE waggledance_magma_handoff_provider_up gauge
waggledance_magma_handoff_provider_up 1.0
waggledance_magma_handoff_provider_status{status="warning"} 1.0
waggledance_magma_handoff_freshness_state{state="stale"} 1.0
...
```

Route-stage count groups are derived from the static chat route-stage allowlist
and current optional component flags. They do not record query text, language
hints, profile names, context, or route-stage trace payloads, and they do not
enable disabled hex paths.

Route-stage runtime counters are recorded only after the chat response has
been sanitized through the route-stage allowlist. `observations_total` supports
Prometheus `rate(...)` by stage. `request_latency_ms_total` is total request
latency for requests where a stage was observed; divide it by
`observations_total` for stage-correlated request latency. The
`waggledance_route_stage_request_latency_histogram_ms` histogram supports p95/p99
Prometheus panels with `histogram_quantile(...)`. It is not an internal span timer and does not store raw queries, profiles, language hints, context, or full trace payloads.

Route-stage latency panel and alert templates are documented in
`docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md`. The initial operator rules
watch p95 and p99 request latency by route-stage label using
`waggledance_route_stage_request_latency_histogram_ms_bucket`; they are read-only
Prometheus checks and do not add mutating endpoints or runtime routing
authority.

Low-risk autogrowth alert thresholds are documented in
`docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md`. The initial operator rules
watch `waggledance_autogrowth_up`, `waggledance_autogrowth_errors_total`,
`waggledance_autogrowth_wakeups_total`, and
`waggledance_autogrowth_non_idle_ticks_total`; they are read-only Prometheus
checks and do not add start/stop or configuration controls.

MAGMA handoff provider-health metrics are also emitted from the same
sanitized `/api/ops` summary. They use the `waggledance_magma_handoff_*`
namespace for configured/valid/history/freshness/count gauges, fixed-state
status labels, fixed-state freshness labels, and fixed alert IDs. They do not
publish timestamps, share IDs, operator decision IDs, local paths, URLs,
arbitrary source labels, raw provider summaries, or exception details, and
they do not add import controls or runtime authority.

MAGMA handoff provider metrics alert thresholds are documented in
`docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md`. The initial
operator rules watch `waggledance_magma_handoff_provider_up`,
`waggledance_magma_handoff_snapshot_valid`,
`waggledance_magma_handoff_freshness_source_stale`,
`waggledance_magma_handoff_history_dropped_count`,
`waggledance_magma_handoff_local_paths_recorded`,
`waggledance_magma_handoff_runtime_authority_granted`, and
`waggledance_magma_handoff_payload_files_imported`; they are read-only
Prometheus checks and do not import payloads, add import controls, or grant
runtime authority.

The same runbook alert IDs can be surfaced through the read-only
`provider_health.metrics_alert_state` field in `/api/ops` when the runtime
container supplies an explicit
`magma_share_import_handoff_metrics_alert_feed` provider. The feed accepts only
fixed MAGMA handoff metric alert IDs, warning/critical severities, finite
numeric samples, and ISO timestamps; it drops raw Alertmanager labels,
annotations, URLs, paths, hostnames, unknown alert IDs, resolved alerts, and
provider exception details.

The optional adapter is configured under `magma_handoff_metrics_alert_feed` in
`configs/settings.yaml` and is disabled by default. It performs only bounded
read-only GETs to an operator-owned Alertmanager `/api/v2/alerts` endpoint.
URL userinfo, query strings, fragments, credential-like headers, redirects,
oversized responses, and private or localhost hosts without an exact
`allowed_private_hosts` entry are refused. It also keeps a bounded in-process
TTL cache (`cache_ttl_s`) and failure backoff (`failure_backoff_s`) so repeated
Ops scrapes can reuse the last sanitized snapshot when the operator feed is
temporarily unavailable. The cache/backoff state appears only as sanitized
provider-health booleans, fixed status/reason labels, counters, and byte/count
gauges; URLs, hosts, headers, and exception text are not surfaced. The same
`metrics_alert_state` object includes read-only `slo_panels` PromQL templates
for availability, fetch failures, active backoff, and stale cache state, plus a
`drill_evidence` checklist naming only safe artifact classes for operator
incident review.

The metrics runbook also includes manual release-gate examples that consume
those SLO panels and `drill_evidence`; they are documentation-only and must
not be wired to automatic merge, promotion, configuration, or runtime controls.
For operator-owned release review, `tools/package_magma_alert_feed_release_evidence.py`
can package an explicit local `/api/ops` JSON snapshot and explicit local
`/metrics` scrape into sanitized JSON/Markdown evidence artifacts; it performs
no endpoint fetches and makes no automatic release decision.
`tools/validate_magma_alert_feed_release_evidence.py` can validate that package
and optional local artifact digests for reviewer-side checks; it writes nothing
and also makes no automatic release decision.
`tools/build_magma_alert_feed_reviewer_handoff_summary.py` can then render a
sanitized operator-owned reviewer handoff summary from the local package and
local validation report; it prints context only and keeps
`approval_granted=false`, `release_decision_made=false`, and
`automatic_release_decision=false`.
`tools/build_magma_alert_feed_reviewer_bridge_event_template.py` can render an
optional sanitized bridge-event template from that summary; the template is
valid bridge `handoff` JSON but the tool does not append bridge events. Its
optional operator decision-reference slot is context only:
`decision_reference_is_approval=false`,
`decision_reference_is_release_decision=false`, and the template keeps
`direct_bridge_write_performed=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/build_magma_alert_feed_reviewer_handoff_bundle_index.py` can then build
a local reviewer handoff bundle index tying the package, validation report,
reviewer summary, and bridge-event template digests. The index records
`artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/verify_magma_alert_feed_reviewer_handoff_bundle_index.py` can verify
that local reviewer handoff bundle index by recomputing digest, size, and
schema-version checks from explicit local artifacts. The verifier records
`digest_checks`, `schema_version_checks`, and keeps
`artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py`
can render a local reviewer handoff bundle verification summary from that
verifier JSON. It carries `verification_ok`, digest/size/schema status, and
sanitized blocker tokens for reviewer handoff while keeping
`direct_bridge_write_performed=false`, `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`approval_granted=false`, and `release_decision_made=false`.
`tools/validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py`
can validate the local bundle bridge-event template's operator
decision-reference against an expected sanitized operator-owned reference and
the local verification summary. It reports `decision_reference_validated`
only as reviewer context and keeps `decision_reference_is_approval=false`,
`decision_reference_is_release_decision=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py`
can render that validator result as a local operator decision-reference review
summary for reviewers. It carries `decision_reference_validated` and the
expected operator-owned reference as context only, keeps the operator decision
separate, and still reports `direct_bridge_write_performed=false`,
`transport_added=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`
can build a local operator decision-reference review bundle index that ties the
validation report and review summary digests without including payloads or
recording local paths. It keeps `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`, and
`release_decision_made=false`.
`tools/verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`
can verify that local operator decision-reference review bundle index by
recomputing `digest_checks`, size checks, and `schema_version_checks` from
explicit local artifacts. The local operator decision-reference review bundle
verifier keeps `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`,
`release_decision_made=false`, and `runtime_controls_added=false`.
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py`
can render that verifier result as a local operator decision-reference review
bundle verification summary for reviewers. It carries
`operator_decision_reference_review_bundle_verification`,
`source_contract_check`, `rebuilt_index_check`, and
`decision_reference_verified` as context only while keeping
`artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `direct_bridge_write_performed=false`,
`approval_granted=false`, `release_decision_made=false`, and
`runtime_controls_added=false`.

`GET /api/ops` also includes `route_stage_latency`, a read-only list of
PromQL panel and alert templates for route-stage p95/p99 latency. It reports
`source="prometheus_query_templates"`, metric names, `panels`, and
`alert_thresholds`. It also reports `feed_state`, a sanitized read-only
Prometheus/Alertmanager snapshot when the runtime container provides a
`route_stage_latency_feed` provider. The live feed accepts only known panel
IDs, known alert IDs, fixed route-stage labels, numeric values, and timestamps;
it drops raw summaries, invalid labels, and unknown fields. The feed state does
not add mutating endpoints or runtime routing controls.

The optional provider is configured under `route_stage_latency_feed` in
`configs/settings.yaml` and is disabled by default. It only performs bounded
read-only GETs to operator-owned Prometheus `/api/v1/query` and Alertmanager
`/api/v2/alerts` endpoints. URL userinfo, query-string URLs, credential-like
headers, redirects, and private/localhost hosts without an exact
`allowed_private_hosts` entry are refused.

`GET /api/ops` also includes `autogrowth.alert_state`, a read-only local
snapshot for the hologram Ops panel. It reports `status`, `severity`,
`source="local_ops_snapshot"`, `active` alert summaries, and `deferred_rules`
that require a Prometheus/Alertmanager time-window feed. The field is derived
from existing Ops counters only and does not add mutating endpoints or
configuration controls.

`GET /api/ops` also includes `magma_share_import_handoff`, a read-only
operator summary for an explicitly supplied
`share_import_peer_review_handoff.json` snapshot or an explicitly supplied
bounded history of those snapshots. It reports only status/severity, digest
refs, share ID, purpose, decision category, handoff scope, entry counts,
retention metadata (`history_limit`, retained/dropped counts), provider health
for the explicit handoff/history feed, and no-authority privacy flags. The
default state is `source="not_configured"` with
`provider_health.source="not_configured"`. Provider health is a local
categorical snapshot only: configured/available/valid booleans, snapshot kind
and count, latest handoff timestamp, retained/dropped counts, read-only
freshness/retention alert thresholds, optional operator-owned feed freshness
source state, optional `metrics_alert_state` from the explicit MAGMA handoff
metrics Alertmanager feed, and sanitized warning IDs when the provider is
unavailable, invalid, stale, or the retained history window drops entries. The
metrics alert-state feed is disabled by default and exposes only fixed alert
IDs from `MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md`, WD-generated summaries,
fixed metric names, numeric samples, and sanitized timestamps. The feed
freshness source accepts only explicit provider snapshots, allowlisted
timestamps/counts/window/state fields, and the fixed source label
`operator_peer_review_handoff_feed`; raw paths, URLs, arbitrary source labels,
and exception details are not exposed. The freshness threshold remains
operator-clock dependent metadata; the API does not perform mutating refreshes
or runtime control actions. The summary validates every supplied history entry
before truncating the operator-visible list. It does not scan disk, import
payloads, record local paths, expose raw operator decision IDs or exception
details, add runtime authority, or add dashboard controls.

---

## Core

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/status` | GET | System status, uptime, agent count, metrics |
| `POST /api/chat` | POST | Send message to runtime (auto FI/EN detection) |
| `GET /api/heartbeat` | GET | Latest agent activity feed entries |
| `GET /api/hardware` | GET | Live CPU/GPU/VRAM/RAM stats |
| `GET /api/system` | GET | psutil CPU% + nvidia-smi GPU% |
| `POST /api/language` | POST | Set language preference |
| `GET /api/language` | GET | Get current language |
| `POST /api/confusion` | POST | Report confusing response for correction |

### Chat

```json
// POST /api/chat
// Request
{"query": "Miten optimoin energiankulutusta?"}

// Response
{
  "response": "Energiankulutuksen optimointiin...",
  "agent": "energy_advisor",
  "confidence": 0.87,
  "source": "chromadb",
  "language": "fi",
  "response_time_ms": 142
}
```

**Auth behavior (v3.5.7):**
- Without auth: returns `401 Unauthorized` with `WWW-Authenticate: Bearer` header and JSON body containing `reason` (`missing_credentials` or `invalid_credentials`) plus `hint`.
- With auth but invalid body (no `query` or `message` field): returns `422 Unprocessable Entity`.
- The `message` field is accepted as an alias for `query` (OpenAI-compatible clients).

Every non-cached chat response (solver and LLM routes) creates a `CaseTrajectory`
row in the learning funnel. Hot-cache hits are excluded. The case records the
query, response, confidence, source, and route type for downstream night learning.

---

## Storage Health (v3.3.9)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/storage/health` | GET | Per-database sizes, WAL sizes, row counts, growth warnings |
| `POST /api/storage/wal-checkpoint` | POST | Trigger WAL checkpoint on all SQLite databases |

`/api/status` also returns `degraded` (bool) and `degraded_components` (list) when circuit breakers are open.
`/api/learning` returns `llm_degraded` (bool) when the LLM circuit breaker is open.

---

## Autonomy Runtime (v3.3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/status` | GET | Full autonomy runtime status, KPIs, resource kernel |
| `GET /api/autonomy/kpis` | GET | 13 autonomy KPIs with targets and current values |
| `POST /api/autonomy/learning/run` | POST | Trigger night learning cycle. Auto-loads pending cases from store when day_cases not provided. Watermark prevents reprocessing. |
| `GET /api/autonomy/learning/status` | GET | Night learning pipeline status: cycles, pending_cases, scheduler state, last result |
| `POST /api/autonomy/goals/check-proactive` | POST | Check world model for proactive goal opportunities |
| `GET /api/autonomy/safety-cases` | GET | Recent safety cases (optional `?limit=N`) |
| `GET /api/autonomy/capability-confidence` | GET | Per-solver capability confidence scores, trends |
| `GET /api/autonomy/prediction-ledger` | GET | Prediction error ledger analysis |
| `GET /api/autonomy/user-model` | GET | Lightweight user model: interactions, corrections, pending promises |
| `GET /api/autonomy/safety-cases/stats` | GET | Safety case verdict distribution |

```json
// GET /api/autonomy/kpis
{
  "kpis": {
    "route_accuracy": {"value": 0.92, "target": 0.90},
    "llm_fallback_rate": {"value": 0.25, "target": 0.30},
    "specialist_accuracy": {"value": 0.88, "target": 0.85}
  }
}

// POST /api/autonomy/goals/check-proactive
// Request
{"observations": {"zone1.temperature": 25.0}, "threshold": 2.0}
// Response
{"goals_proposed": 1, "goal_ids": ["goal-abc123"]}
```

---

## Autonomy v3.3 — User Model Lite

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/user-model` | GET | User entity state + pending promises from GoalEngine |

```json
// GET /api/autonomy/user-model
{
  "available": true,
  "interaction_count": 42,
  "explicit_correction_count": 0,
  "verification_fail_count": 3,
  "promises_pending": [
    {"goal_id": "goal-abc", "description": "Fix sensor", "priority": 70, "status": "executing"}
  ],
  "preferred_language": "",
  "last_interaction_at": 1711100000.0,
  "last_user_correction_at": 0.0
}
```

---

## Autonomy v3.2 — Self-Entity & Projections

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/epistemic-uncertainty` | GET | Uncertainty report: total uncertainty, observability gaps, per-entity uncertainty |
| `GET /api/autonomy/attention-budget` | GET | Current attention allocation across 4 buckets (critical/normal/background/reflection) |
| `GET /api/autonomy/dream-mode/latest` | GET | Latest dream session results: simulations run, insights generated |
| `GET /api/autonomy/memory/consolidation-stats` | GET | Memory consolidation stats: episodes consolidated, significance distribution |
| `GET /api/autonomy/introspection` | GET | Self-introspection snapshot (profile-gated: FACTORY=full, GADGET=counts only) |
| `GET /api/autonomy/narrative` | GET | Human-readable self-narrative in en or fi (60s cache) |

---

## Agents & Learning

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/agent_levels` | GET | All agents with current trust levels |
| `GET /api/agents/levels` | GET | All agents with level/trust/hallucination rate |
| `GET /api/agents/leaderboard` | GET | Top agents by trust, queries, reliability |
| `GET /api/consciousness` | GET | Memory engine state + user model summary (v3.3: interaction count, corrections, promises) |
| `GET /api/hologram/state` | GET | Hologram brain state: 32 nodes (4 rings), node_meta, edges, events, hex_mesh overlay, magma_timeline, ops overlay |
| `GET /hologram` | GET | Hologram brain v6 HTML page (32 nodes, docked panels, FI/EN i18n) |
| `GET /api/profile/impact` | GET | Profile impact: target environment, enabled/disabled capabilities, risk mode, learning permissions |
| `GET /api/capabilities/state` | GET | Per-family capability state: state/device/quality/source_class (shared derivation with hologram) |
| `GET /api/learning/state-machine` | GET | Current learning lifecycle state (awake/replay/consolidation/dream/training/canary/morning_report) |
| `GET /api/swarm/scores` | GET | SwarmScheduler agent scores |
| `GET /api/learning` | GET | LearningEngine status + leaderboard |
| `GET /api/ops` | GET | OpsAgent status, model recommendations, read-only route-stage latency panel templates, read-only autogrowth alert state, and read-only MAGMA import handoff status |
| `GET /api/micro_model` | GET | MicroModel V1/V2 status and promotion stats |

---

## Hybrid Retrieval (v3.4)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/hybrid/status` | GET | Hybrid retrieval enabled/disabled, mode, hit/miss rates, FAISS stats |
| `GET /api/hybrid/topology` | GET | Hex-cell topology: all 8 cells, ring-1 neighbors, per-cell document counts |
| `GET /api/hybrid/cells` | GET | Per-cell FAISS collection sizes |
| `GET /api/hybrid/test-assign` | GET | Debug: test cell assignment for a query+intent (`?query=...&intent=...`) |

All hybrid endpoints require authentication (Bearer token or session cookie).

**Feature flag:** `hybrid_retrieval.enabled` in `configs/settings.yaml` (default: `false`).

When hybrid is enabled, `/api/status` and `/api/ops` include `hybrid_retrieval` section with hit counters.
`/api/hologram/state` includes additive `hybrid` overlay section.

### Hologram Mesh Observatory (v3.5.5)

`/api/hologram/state` now includes three additional sections when hex_mesh is enabled:

```json
{
  "hex_mesh": {
    "enabled": true,
    "cells": [{"id": "hub", "coord": {"q": 0, "r": 0}, "domain": "...", "state": "idle", "health_score": 1.0, "load": 0, "quarantined": false, "self_heal_pending": false, "agent_count": 41}],
    "links": [{"source": "hub", "target": "bee_ops"}],
    "active_trace": {"trace_id": "...", "origin_cell_id": "hub", "local_confidence": 0.65, "neighbor_cells": ["bee_ops"], "escalated_global": true},
    "counters": {"origin_cell_resolutions": 7, "global_escalations": 7, "neighbor_assist_resolutions": 0},
    "health": {"tracked_cells": 7, "quarantined_cells": 0}
  },
  "magma_timeline": [{"event_type": "HEX_QUERY_COMPLETED", "source": "hub", "timestamp": 1712345678}],
  "ops": {
    "llm_parallel": {},
    "hex_mesh": {"origin_cell_resolutions": 7, "global_escalations": 7},
    "cache": {"size": 1, "hits": 1, "misses": 11},
    "request_counters": {"total_queries": 0, "solver_hits": 0, "llm_calls": 0}
  }
}
```

When `hex_mesh.enabled=false`, `hex_mesh` returns `{"enabled": false}`, and other sections return empty defaults.
MAGMA timeline sanitizes entries: `api_key` and `token` fields are stripped.

### Adaptive Runtime Efficiency (v3.5.6)

`hex_mesh` now includes an `efficiency` section with preflight/skip metrics:

```json
{
  "hex_mesh": {
    "counters": {
      "preflight_skips": 14,
      "preflight_passes": 1,
      "skipped_local_attempts": 14,
      "skipped_neighbor_attempts": 15,
      "budget_exhaustions": 0
    },
    "efficiency": {
      "total_hex_queries": 29,
      "preflight_skip_ratio": 0.48,
      "skipped_local_attempts": 14,
      "skipped_neighbor_attempts": 15,
      "local_success_ratio": 0.0,
      "neighbor_success_ratio": 0.0,
      "escalation_ratio": 0.52,
      "cell_success_memory": {"hub": 0.0, "environment": 0.0}
    }
  }
}
```

`HexResolutionTrace.to_dict()` now includes: `preflight_score`, `preflight_skipped`, `neighbor_skipped`, `budget_exhausted`.

New settings in `configs/settings.yaml` under `hex_mesh`:
- `local_budget_ms` (default: 15000) — max ms for local LLM attempt
- `neighbor_budget_ms` (default: 10000) — max ms for neighbor LLM attempt
- `total_hex_budget_ms` (default: 25000) — max total hex time
- `skip_low_value_neighbor_when_sequential` (default: true) — skip sequential neighbor for low-value queries
- `preflight_min_score` (default: 0.3) — minimum preflight score to attempt local

## Hybrid Backfill (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/hybrid/backfill/status` | GET | Backfill service status: running, total_runs, indexed_ids_count, last_result |
| `POST /api/hybrid/backfill/run` | POST | Trigger idempotent backfill run. Body: `{"dry_run": true, "limit": 5000}` |

All backfill endpoints require authentication. Backfill is NOT auto-run on boot — must be triggered manually via admin API.

## Solver Candidate Lab (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/candidate_lab/status` | GET | Lab status: total_analyses, llm_available, registry stats |
| `GET /api/candidate_lab/recent` | GET | Recent solver candidates (`?limit=10`). Returns candidate_id, domain, state, confidence |

All candidate lab endpoints require authentication. The candidate lab does **NOT** auto-modify production routing. Candidates are structured specs for review only.

## Learning Accelerator (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/learning/accelerator` | GET | Synthetic training accelerator status: total_runs, gpu_available, device_used, last_metrics |

Requires authentication. GPU acceleration is optional and off by default. CPU fallback is always safe.

`/api/status` includes additive `backfill` and `candidate_lab` summary sections.
`/api/ops` includes additive `backfill` and `accelerator` metrics sections.

## Gemma 4 Profiles (v3.5.1)

Optional dual-tier Gemma 4 model profiles. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `gemma_profiles.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include a `gemma_profiles` section:

```json
{
  "gemma_profiles": {
    "enabled": true,
    "active_profile": "dual_tier",
    "active_fast_model": "gemma4:e4b",
    "active_heavy_model": "gemma4:26b",
    "fast_model_calls": 42,
    "heavy_model_calls": 3,
    "heavy_reasoning_calls": 3,
    "default_fallback_calls": 0,
    "gemma_fast_degraded": false,
    "gemma_heavy_degraded": false
  }
}
```

When disabled: `{"gemma_profiles": {"enabled": false}}`.

**Profiles:** `disabled` (default), `fast_only`, `heavy_only`, `dual_tier`.

**Degradation:** If a Gemma model is unavailable, falls back to the default model (phi4-mini) when `degrade_to_default: true`.

---

## Parallel LLM Dispatch (v3.5.2+)

Optional bounded concurrent LLM dispatch with per-model inflight limits. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `llm_parallel.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include an `llm_parallel` section:

```json
{
  "llm_parallel": {
    "enabled": true,
    "queue_depth": 0,
    "inflight_total": 0,
    "inflight_fast": 0,
    "inflight_heavy": 0,
    "inflight_default": 0,
    "completed_parallel_batches": 12,
    "total_dispatched": 48,
    "total_completed": 48,
    "timeout_count": 0,
    "cancelled_count": 0,
    "deduped_requests": 2,
    "degrade_to_sequential_count": 0
  }
}
```

When disabled: `{"llm_parallel": {"enabled": false}}`.

**Configuration keys** (in `configs/settings.yaml` under `llm_parallel`):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Master switch |
| `max_concurrent` | `4` | Global asyncio semaphore limit |
| `max_inflight_per_model` | `2` | Per-model-line semaphore (fast/heavy/default) |
| `request_timeout_s` | `120` | Timeout per LLM call |
| `round_table_parallel_first_pass` | `false` | Parallel agent first pass in round table |
| `dream_batch_parallelism` | `1` | Dream mode batch size (1 = sequential) |
| `candidate_lab_parallelism` | `1` | Candidate enrichment batch size |
| `verifier_advisory_parallelism` | `1` | Verifier advisory batch size |
| `dedupe_identical_prompts` | `true` | SHA-256 dedup of identical concurrent requests |

---

## Hex Neighbor Mesh (v3.5.4+)

Honeycomb topology for domain-aware cooperative resolution. Queries route: local cell → ring-1 neighbor assist → global/swarm → LLM. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `hex_mesh.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include a `hex_mesh` section:

```json
{
  "hex_mesh": {
    "enabled": true,
    "cells_loaded": 7,
    "origin_cell_resolutions": 0,
    "local_only_resolutions": 0,
    "neighbor_assist_resolutions": 0,
    "global_escalations": 0,
    "llm_last_resolutions": 0,
    "completed_hex_neighbor_batches": 0,
    "neighbors_consulted_total": 0,
    "avg_neighbors_per_assist": 0.0,
    "quarantined_cells": 0,
    "self_heal_events": 0,
    "magma_traces_written": 0,
    "ttl_exhaustions": 0
  }
}
```

When disabled: `{"hex_mesh": {"enabled": false, "cells_loaded": 7, ...}}` (counters stay 0).

**Configuration keys** (in `configs/settings.yaml` under `hex_mesh`):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Master switch |
| `cell_config_path` | `configs/hex_cells.yaml` | Topology definition file |
| `local_confidence_threshold` | `0.72` | Min confidence for local-only resolution |
| `neighbor_confidence_threshold` | `0.82` | Min confidence for neighbor-merged resolution |
| `global_escalation_threshold` | `0.90` | Min confidence to avoid global escalation |
| `ttl_default` | `2` | Hop limit to prevent loops |
| `max_neighbors_per_hop` | `2` | Max ring-1 neighbors consulted per hop |
| `parallel_neighbor_assist` | `true` | Use ParallelLLMDispatcher for neighbor queries |
| `self_heal_probe_enabled` | `true` | Probe quarantined cells for recovery |
| `magma_trace_enabled` | `true` | Record MAGMA events for hex resolution |
| `neighbor_merge_policy` | `weighted_confidence` | Merge policy for neighbor responses |

**Topology** (defined in `configs/hex_cells.yaml`):

7 cells in axial coordinates: hub (0,0), bee_ops (1,0), environment (0,-1), home_comfort (-1,0), safety_security (-1,1), production (0,1), logistics (1,-1).

---

## Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/analytics/trends` | GET | 7-day performance trends (halluc, cache, RT) |
| `GET /api/analytics/routes` | GET | Route breakdown (cache/memory/LLM) |
| `GET /api/analytics/models` | GET | Model usage percentages |
| `GET /api/analytics/facts` | GET | Fact growth timeline |

---

## Round Table

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/round-table/recent` | GET | Latest Round Table discussions with transcripts |
| `GET /api/round-table/stats` | GET | Aggregate stats, most active agents |

---

## Sensors & IoT

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/sensors` | GET | Sensor hub overview (all subsystems) |
| `GET /api/sensors/home` | GET | Home Assistant entity states |
| `GET /api/sensors/camera/events` | GET | Frigate NVR camera events |
| `GET /api/sensors/audio` | GET | Audio monitor status + recent events |
| `GET /api/sensors/audio/analysis` | GET | Audio analysis (anomaly detection, pattern recognition) |

---

## Voice Interface

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/voice/status` | GET | Voice interface status (STT/TTS availability) |
| `POST /api/voice/text` | POST | Send text for TTS synthesis |
| `POST /api/voice/audio` | POST | Send audio for STT transcription |

```json
// POST /api/voice/text
// Request
{"text": "Tervetuloa järjestelmään"}

// POST /api/voice/audio
// Request
{"audio_base64": "<base64 WAV data>"}
```

---

## Data Feeds

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/feeds` | GET | Data feed status — public, no auth required |
| `POST /api/feeds/{feed_name}/refresh` | POST | Force refresh a specific feed (auth required) |

`GET /api/feeds` returns per-source status (v3.5.7 semantics):
- `sources[]`: each entry has `name`, `type`, `state` (`active`/`stale`/`idle`/`error`), `items_count`, `latest_value` or `latest_items`.
- `state` reflects honest freshness: sources that exceed their publishing cadence show `stale`, not `active`. DNS-blocked sources show `error`. Idle sources that have never fetched show `idle`.
- Config-driven: sources defined in `configs/settings.yaml` under `feeds.sources`.

---

## Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/settings` | GET | Feature toggles from settings.yaml |
| `POST /api/settings/toggle` | POST | Toggle a feature on/off |

```json
// POST /api/settings/toggle
{"feature": "voice", "enabled": true}
```

---

## MAGMA Memory Architecture

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/magma/stats` | GET | Audit log + replay store statistics |
| `GET /api/magma/audit` | GET | Last 24h audit entries |
| `GET /api/magma/audit/agent/{agent_id}` | GET | Audit entries for specific agent |
| `GET /api/magma/overlays` | GET | Active memory overlays |
| `GET /api/magma/branches` | GET | Overlay branches and status |
| `POST /api/magma/branches/{name}/activate` | POST | Activate an overlay branch |
| `POST /api/magma/branches/deactivate` | POST | Deactivate current branch |

---

## Trust & Reputation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/trust/ranking` | GET | All agents ranked by reputation score |
| `GET /api/trust/agent/{agent_id}` | GET | Full reputation breakdown for agent |
| `GET /api/trust/domain/{domain}` | GET | Top agents for a domain |
| `GET /api/trust/signals/{agent_id}` | GET | Raw trust signal history |

---

## Cross-Agent Memory

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/cross/channels` | GET | Agent communication channels |
| `GET /api/cross/channels/{name}/history` | GET | Channel message history |
| `GET /api/cross/provenance/{fact_id}` | GET | Fact provenance chain |
| `GET /api/cross/agent/{agent_id}/contributions` | GET | Agent's memory contributions |
| `GET /api/cross/consensus` | GET | Consensus records |

---

## Cognitive Graph

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/graph/node/{node_id}` | GET | Node details + edges |
| `GET /api/graph/path/{source}/{target}` | GET | Shortest path between nodes |
| `GET /api/graph/stats` | GET | Graph node/edge counts |

---

## Reports & Code Review

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/meta_report` | GET | Latest weekly meta-learning report |
| `GET /api/code_suggestions` | GET | Code self-review suggestions |
| `POST /api/code_suggestions/{index}/accept` | POST | Accept a suggestion |
| `POST /api/code_suggestions/{index}/reject` | POST | Reject a suggestion |
| `GET /api/code-review` | GET | Code review status |
| `GET /api/code-review/suggestions` | GET | Full suggestions list |

---

## Dashboard Features

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/models` | GET | Ollama model status — loaded models, VRAM usage, role mapping |
| `GET /api/history` | GET | Conversation history list |
| `GET /api/history/recent/messages` | GET | Recent chat messages across all conversations |
| `GET /api/history/{conversation_id}` | GET | Full conversation by ID |
| `POST /api/feedback` | POST | User feedback on AI response (thumbs up/down) |

```json
// GET /api/models
{
  "models": [
    {"name": "phi4-mini:latest", "role": "chat", "vram_mb": 2400, "loaded": true}
  ],
  "total_vram_used_mb": 4200,
  "total_vram_available_mb": 8192
}

// POST /api/feedback
// Request
{"message_id": "msg_abc123", "rating": "up", "comment": "Good answer"}
```

---

## Profiles

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/profile` | GET | Current deployment profile |
| `POST /api/profile` | POST | Switch deployment profile |
| `GET /api/monitor/history` | GET | Performance monitoring history |

---

## WebSocket

```
ws://localhost:8000/ws
```

Real-time event stream. Messages are JSON with `type` field:

| Type | Description |
|------|-------------|
| `heartbeat` | Agent activity update |
| `chat_response` | Streaming chat response |
| `round_table_start` | Round Table debate begins |
| `round_table_turn` | Agent contribution in debate |
| `round_table_end` | Debate conclusion + consensus |
| `night_learning` | Night mode learning event |
| `alert` | Sensor/system alert |
| `ops_decision` | OpsAgent scaling decision |

```json
// Example heartbeat message
{
  "type": "heartbeat",
  "agent": "disease_monitor",
  "action": "proactive_think",
  "result": "Checked scheduled maintenance timing",
  "timestamp": "2026-03-07T14:30:00"
}
```
