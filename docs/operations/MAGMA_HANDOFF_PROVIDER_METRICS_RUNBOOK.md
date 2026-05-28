# MAGMA handoff provider metrics operator runbook

**Status:** read-only operator runbook for MAGMA peer-review handoff provider metrics
**Scope:** Prometheus alert thresholds for `waggledance_magma_handoff_*`
**Companion docs:** `docs/API.md`, `docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md`

## Purpose

This runbook turns the sanitized MAGMA peer-review handoff provider metrics
into operator alerts. It does not add import controls, does not import
payloads, and does not grant runtime authority. The alerts are observability
only: they tell an operator when to inspect the explicit handoff feed, capture
evidence, or hold related releases through the normal release process.

The metric source is the auth-exempt `/metrics` endpoint. The gauges are
derived from the same sanitized `/api/ops.magma_share_import_handoff`
provider-health summary used by the hologram Ops panel.

`/api/ops.provider_health.metrics_alert_state` can also consume the optional
`magma_handoff_metrics_alert_feed` adapter. The adapter is disabled by default
and reads only the operator-owned Alertmanager `/api/v2/alerts` endpoint. It
refuses URL userinfo, query strings, fragments, credential-like headers,
redirects, oversized responses, and private or localhost hosts unless the host
appears exactly in `allowed_private_hosts`. It uses a bounded in-process TTL
cache and bounded failure backoff; when the operator Alertmanager read fails
after a prior good read, Ops can continue showing the last sanitized alert
snapshot while surfacing `backoff_active` and a fixed failure reason.

## Metrics

| Metric | Meaning |
| --- | --- |
| `waggledance_magma_handoff_provider_up` | `1` when the metrics collector can build the sanitized provider-health summary for this scrape. |
| `waggledance_magma_handoff_provider_configured` | `1` when an explicit handoff snapshot, history, or provider source is configured. |
| `waggledance_magma_handoff_snapshot_valid` | `1` when the configured handoff snapshot or selected history entry validates. |
| `waggledance_magma_handoff_history_feed_present` | `1` when the explicit bounded handoff history feed is present. |
| `waggledance_magma_handoff_history_retained_count` | Count of retained validated handoff history entries visible to operators. |
| `waggledance_magma_handoff_history_dropped_count` | Count of validated handoff history entries dropped by the bounded retention window. |
| `waggledance_magma_handoff_freshness_source_configured` | `1` when an explicit operator-owned feed freshness source is configured. |
| `waggledance_magma_handoff_freshness_source_valid` | `1` when that freshness source validates after sanitization. |
| `waggledance_magma_handoff_freshness_source_stale` | `1` when the freshness source reports stale state. |
| `waggledance_magma_handoff_active_alerts` | Count of sanitized active warning IDs reported by provider health. |
| `waggledance_magma_handoff_local_paths_recorded` | Must stay `0`; `1` means the sanitized summary detected local path material. |
| `waggledance_magma_handoff_runtime_authority_granted` | Must stay `0`; `1` means the handoff summary reports runtime authority. |
| `waggledance_magma_handoff_payload_files_imported` | Must stay `0`; positive values mean the no-payload boundary is violated. |
| `waggledance_magma_handoff_provider_status{status=...}` | Fixed status gauge for `not_configured`, `nominal`, and `warning`. |
| `waggledance_magma_handoff_freshness_state{state=...}` | Fixed freshness-state gauge for `fresh`, `stale`, and `unknown`. |
| `waggledance_magma_handoff_provider_alert_active{alert_id=...}` | Fixed alert-id gauge for sanitized provider warning IDs. |
| `waggledance_magma_handoff_alert_feed_configured` | `1` when the optional Alertmanager adapter is explicitly configured. |
| `waggledance_magma_handoff_alert_feed_available` | `1` when the adapter has a usable sanitized snapshot or current successful read. |
| `waggledance_magma_handoff_alert_feed_cache_present` | `1` when the adapter has a cached sanitized snapshot. |
| `waggledance_magma_handoff_alert_feed_cache_stale` | `1` when the cached snapshot is past its TTL. |
| `waggledance_magma_handoff_alert_feed_backoff_active` | `1` while the bounded failure backoff suppresses repeated feed reads. |
| `waggledance_magma_handoff_alert_feed_cache_hits_total` | Count of snapshot requests served from the in-process cache. |
| `waggledance_magma_handoff_alert_feed_cache_misses_total` | Count of snapshot requests that attempted a feed refresh. |
| `waggledance_magma_handoff_alert_feed_fetch_successes_total` | Count of successful Alertmanager reads. |
| `waggledance_magma_handoff_alert_feed_fetch_failures_total` | Count of failed Alertmanager reads after sanitization. |
| `waggledance_magma_handoff_alert_feed_backoff_skips_total` | Count of refreshes skipped by bounded backoff. |
| `waggledance_magma_handoff_alert_feed_status{status=...}` | Fixed adapter status gauge for `not_configured`, `nominal`, and `warning`. |
| `waggledance_magma_handoff_alert_feed_failure_reason{reason=...}` | Fixed sanitized failure reason gauge, including `none`, `NETWORK_TIMEOUT`, `NETWORK_REQUEST_FAILED`, and response-refusal categories. |

The exporter intentionally does not publish timestamps, share IDs, operator
decision IDs, filesystem paths, URLs, arbitrary source labels, raw provider
summaries, payload material, or exception details.

## Alert thresholds

Use these PromQL rules as the first operator threshold set. They are
deliberately conservative because the MAGMA handoff lane is local,
operator-owned, and no-authority; the first response should be evidence
collection, not automatic mutation.

| Alert | PromQL | For | Severity | Operator action |
| --- | --- | --- | --- | --- |
| `MagmaHandoffMetricsSourceDown` | `waggledance_magma_handoff_provider_up == 0` | `5m` | warning | Check `/metrics`, `/api/ops`, and runtime logs. Treat as missing observability until proven otherwise. |
| `MagmaHandoffSnapshotInvalid` | `waggledance_magma_handoff_snapshot_valid == 0` | `10m` | warning | Inspect the explicit handoff snapshot or history source and preserve the rejected artifact for review. |
| `MagmaHandoffFreshnessStale` | `waggledance_magma_handoff_freshness_source_stale == 1` | `15m` | warning | Compare the feed source timestamp with the operator clock and confirm whether the handoff history stopped updating. |
| `MagmaHandoffRetentionDropped` | `waggledance_magma_handoff_history_dropped_count > 0` | `30m` | warning | Capture the retained and dropped counts before changing retention settings or making merge decisions that rely on history completeness. |
| `MagmaHandoffPrivateMaterialRecorded` | `waggledance_magma_handoff_local_paths_recorded > 0` | `1m` | critical | Hold MAGMA handoff releases and open a blocking privacy issue with the sanitized metric sample and current commit. |
| `MagmaHandoffRuntimeAuthorityReported` | `waggledance_magma_handoff_runtime_authority_granted > 0` | `1m` | critical | Stop treating the handoff as no-authority evidence until the source artifact and summary builder are reviewed. |
| `MagmaHandoffPayloadImported` | `waggledance_magma_handoff_payload_files_imported > 0` | `1m` | critical | Treat as a no-payload boundary violation; preserve evidence and block related importer or exporter changes. |
| `MagmaHandoffProviderUnavailable` | `waggledance_magma_handoff_provider_alert_active{alert_id="MagmaShareImportHandoffProviderUnavailable"} == 1` | `10m` | warning | Confirm whether the explicit provider source is intentionally absent or failing. |
| `MagmaHandoffFreshnessSourceUnavailable` | `waggledance_magma_handoff_provider_alert_active{alert_id="MagmaShareImportHandoffFreshnessSourceUnavailable"} == 1` | `10m` | warning | Check the operator-owned freshness source and compare it with the bounded handoff history. |
| `MagmaHandoffAlertFeedBackoffActive` | `waggledance_magma_handoff_alert_feed_backoff_active == 1` | `10m` | warning | Inspect the operator Alertmanager endpoint and compare the cached sanitized snapshot with current `/api/ops` before making release claims from alert state. |
| `MagmaHandoffAlertFeedFetchFailures` | `increase(waggledance_magma_handoff_alert_feed_fetch_failures_total[15m]) > 0` | `15m` | warning | Review network reachability, response shape, content type, and size cap. Do not include URLs, headers, or exception text in alert payloads. |

## SLO panel templates

`/api/ops.provider_health.metrics_alert_state.slo_panels` exposes the same
operator SLOs as read-only PromQL templates. These panels are evidence aids
only; they do not start, stop, reconfigure, import, replay, or acknowledge
anything.

| Panel | PromQL | Objective |
| --- | --- | --- |
| `magma_alert_feed_availability_5m` | `avg_over_time(waggledance_magma_handoff_alert_feed_available[5m])` | Adapter availability remains `1`. |
| `magma_alert_feed_fetch_failures_15m` | `increase(waggledance_magma_handoff_alert_feed_fetch_failures_total[15m])` | No fetch failures in the review window. |
| `magma_alert_feed_backoff_15m` | `max_over_time(waggledance_magma_handoff_alert_feed_backoff_active[15m])` | Bounded backoff remains inactive. |
| `magma_alert_feed_cache_stale_15m` | `max_over_time(waggledance_magma_handoff_alert_feed_cache_stale[15m])` | Cached snapshot is not stale. |

## Triage flow

1. Confirm the alert is from the same commit currently deployed.
2. Fetch `/metrics` and `/api/ops`; store both outputs with timestamps.
3. Capture runtime logs for the alert window plus the preceding 10 minutes.
4. Compare `provider_status`, `freshness_state`, and
   `provider_alert_active` plus the optional
   `alert_feed_status`/`alert_feed_failure_reason` gauges before deciding
   whether the issue is source availability, stale feed state, retention
   pressure, Alertmanager adapter backoff, or a no-authority boundary
   violation.
5. For private-material, runtime-authority, or payload-import alerts, hold
   related MAGMA handoff changes until the artifact, summary builder, and
   metric sample have been reviewed.
6. For stale or unavailable source alerts, verify the operator-owned feed
   outside the application before making runtime claims from the handoff
   summary.
7. When `drill_evidence` is present in `/api/ops`, collect only the listed
   artifact classes. Do not add endpoint URLs, headers, hostnames, filesystem
   paths, raw Alertmanager labels, or exception strings to incident notes.

## Guardrails

- No alert rule in this runbook should call a mutating endpoint.
- No alert rule should import payloads or replay payload material.
- No alert rule should start, stop, enable, disable, or reconfigure MAGMA
  exporters, importers, handoff feeds, solvers, or dashboard state.
- No alert rule should auto-merge, auto-promote, or create runtime authority.
- Alert payloads must not include raw share IDs, operator decision IDs, raw
  source labels, URLs, hostnames, filesystem paths, payload material, or
  exception strings. Metric names, fixed labels, and numeric values are enough.
- Recovery actions stay manual and operator-owned. Read-only alert-state
  surfaces may display these fixed alert IDs, but they must not trigger import,
  replay, configuration, merge, or runtime-authority actions.
