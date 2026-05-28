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

## Triage flow

1. Confirm the alert is from the same commit currently deployed.
2. Fetch `/metrics` and `/api/ops`; store both outputs with timestamps.
3. Capture runtime logs for the alert window plus the preceding 10 minutes.
4. Compare `provider_status`, `freshness_state`, and
   `provider_alert_active` before deciding whether the issue is source
   availability, stale feed state, retention pressure, or a no-authority
   boundary violation.
5. For private-material, runtime-authority, or payload-import alerts, hold
   related MAGMA handoff changes until the artifact, summary builder, and
   metric sample have been reviewed.
6. For stale or unavailable source alerts, verify the operator-owned feed
   outside the application before making runtime claims from the handoff
   summary.

## Guardrails

- No alert rule in this runbook should call a mutating endpoint.
- No alert rule should import payloads or replay payload material.
- No alert rule should start, stop, enable, disable, or reconfigure MAGMA
  exporters, importers, handoff feeds, solvers, or dashboard state.
- No alert rule should auto-merge, auto-promote, or create runtime authority.
- Alert payloads must not include raw share IDs, operator decision IDs, raw
  source labels, URLs, hostnames, filesystem paths, payload material, or
  exception strings. Metric names, fixed labels, and numeric values are enough.
- Recovery actions stay manual and operator-owned until a separate reviewed PR
  adds a read-only alert-state surface or an approved operational control.
