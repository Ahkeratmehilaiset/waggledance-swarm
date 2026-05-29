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

## Manual release-gate examples

These examples consume the SLO panel templates and `drill_evidence` checklist
as operator-owned review evidence only. They must not auto-merge,
auto-promote, write configuration, restart feeds, control importers or
exporters, acknowledge alerts, or create runtime authority.

### Pre-merge MAGMA alert-feed observability gate

Collect the exact commit SHA, current `/metrics` scrape, current
`/api/ops.provider_health.metrics_alert_state.feed_health`, current
`/api/ops.provider_health.metrics_alert_state.slo_panels`, current
`/api/ops.provider_health.metrics_alert_state.drill_evidence`, and the CI run
identifier before approving a MAGMA handoff release.

Pass only when the operator evidence shows all of the following for the review
window:

| Evidence check | Manual pass condition |
| --- | --- |
| Availability | `avg_over_time(waggledance_magma_handoff_alert_feed_available[5m]) == 1` |
| Fetch failures | `increase(waggledance_magma_handoff_alert_feed_fetch_failures_total[15m]) == 0` |
| Bounded backoff | `max_over_time(waggledance_magma_handoff_alert_feed_backoff_active[15m]) == 0` |
| Cache freshness | `max_over_time(waggledance_magma_handoff_alert_feed_cache_stale[15m]) == 0` |
| Runtime authority boundary | `waggledance_magma_handoff_runtime_authority_granted == 0` |
| Payload boundary | `waggledance_magma_handoff_payload_files_imported == 0` |
| Local path boundary | `waggledance_magma_handoff_local_paths_recorded == 0` |

Hold the release when any pass condition fails or when `drill_evidence` is
missing during an incident review. Preserve the sanitized metric samples and
the listed `drill_evidence` artifact classes; do not add URLs, headers,
hostnames, filesystem paths, raw Alertmanager labels, raw annotations, payload
material, or exception strings.

The operator can package the collected local evidence with
`tools/package_magma_alert_feed_release_evidence.py`. The tool reads only an
explicit local `/api/ops` JSON snapshot and an explicit local `/metrics` scrape,
then writes `magma_alert_feed_release_evidence.json` and
`magma_alert_feed_release_evidence.md` into a new operator-specified directory.
The package stores artifact SHA-256 digests, allowlisted SLO panel metadata,
current metric samples, active alert IDs, and the manual hold reasons inferred
from current samples. It does not include raw payloads, raw scrapes, raw
Alertmanager labels, URLs, headers, hostnames, filesystem paths, payload
material, or exception strings, and it does not fetch endpoints.

Example:

```powershell
python tools\package_magma_alert_feed_release_evidence.py `
  --ops-json <collected>\ops.json `
  --metrics-scrape <collected>\metrics.prom `
  --out-dir <new-evidence-dir> `
  --release-ref pr:753 `
  --commit-sha <40-hex-commit-sha> `
  --operator-agent operator:wd-image1 `
  --bridge-event-ref bridge:wd-image1-magma-alert-feed-release `
  --ci-run-ref gh:run:example `
  --json
```

The package is evidence for a human/operator release review only. Its
`manual_gate.automatic_release_decision` field is always `false`; it must not
be wired to automatic merge, promotion, configuration, importer/exporter, feed
control, or runtime-authority actions.

Reviewers can validate a package with
`tools/validate_magma_alert_feed_release_evidence.py`. The validator reads an
explicit local package JSON and, optionally, the same explicit local
`/api/ops` JSON and `/metrics` scrape to confirm their SHA-256 digests. It
writes nothing, fetches no endpoints, does not transport artifacts, and still
reports `automatic_release_decision=false`.

```powershell
python tools\validate_magma_alert_feed_release_evidence.py `
  --package-json <new-evidence-dir>\magma_alert_feed_release_evidence.json `
  --ops-json <collected>\ops.json `
  --metrics-scrape <collected>\metrics.prom `
  --json
```

After validation, the operator can build a sanitized reviewer handoff summary
with `tools/build_magma_alert_feed_reviewer_handoff_summary.py`. The summary
reads only the explicit local evidence package and explicit local validator
JSON report, then prints reviewer context to stdout. It carries validation
status, digest-check status, manual-gate hold reasons, and authority-boundary
observations, but always reports `approval_granted=false`,
`release_decision_made=false`, and `automatic_release_decision=false`.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_summary.py `
  --package-json <new-evidence-dir>\magma_alert_feed_release_evidence.json `
  --validation-json <collected>\validation.json `
  --reviewer-agent reviewer:wd-image1 `
  --bridge-event-ref bridge:wd-image1-reviewer-handoff `
  --json
```

The handoff summary is not an approval artifact. It must not be wired to
automatic merge, promotion, configuration, importer/exporter, feed control,
artifact transport, endpoint fetches, or runtime-authority actions.

When a bridge handoff is useful, build an optional bridge-event template from
the sanitized summary with
`tools/build_magma_alert_feed_reviewer_bridge_event_template.py`. The template
validates as a bridge `handoff` event and carries only counts,
digest-check status, release identifiers, fixed authority-boundary flags, and
an optional operator decision-reference slot. That reference is context only:
`decision_reference_is_approval=false` and
`decision_reference_is_release_decision=false`. The tool prints JSON only; it
does not call `Write-AgentEvent.ps1`, append bridge events, transport
artifacts, fetch endpoints, approve a release, merge, promote, or grant
runtime authority.

```powershell
python tools\build_magma_alert_feed_reviewer_bridge_event_template.py `
  --summary-json <collected>\reviewer_handoff_summary.json `
  --agent codex-lead-1 `
  --task-id wd-image1-reviewer-handoff-template `
  --to operator,claude-rco-1 `
  --operator-decision-ref bridge:operator-decision:pending-review `
  --json
```

To bind the local review artifacts together for handoff, build a local
reviewer handoff bundle index with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_index.py`. The index
records only digest, size, role, and schema metadata for the release evidence
package, validator report, reviewer summary, and bridge-event template. It
keeps `artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `approval_granted=false`, and
`release_decision_made=false`.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_index.py `
  --package-json <new-evidence-dir>\magma_alert_feed_release_evidence.json `
  --validation-json <collected>\validation.json `
  --summary-json <collected>\reviewer_handoff_summary.json `
  --bridge-template-json <collected>\reviewer_bridge_event_template.json `
  --json
```

Before handoff, verify that index against the same explicit local artifacts
with `tools/verify_magma_alert_feed_reviewer_handoff_bundle_index.py`. The
local reviewer handoff bundle verifier recomputes digest, size, and
schema-version checks and still keeps `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`approval_granted=false`, and `release_decision_made=false`.

```powershell
python tools\verify_magma_alert_feed_reviewer_handoff_bundle_index.py `
  --bundle-index-json <collected>\reviewer_handoff_bundle_index.json `
  --package-json <new-evidence-dir>\magma_alert_feed_release_evidence.json `
  --validation-json <collected>\validation.json `
  --summary-json <collected>\reviewer_handoff_summary.json `
  --bridge-template-json <collected>\reviewer_bridge_event_template.json `
  --json
```

After verification, render a local reviewer handoff bundle verification
summary with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py`.
The summary reads only the explicit local verifier JSON, carries
`verification_ok`, digest/size/schema status, sanitized blockers, and the
operator handoff reference, and keeps `artifact_payloads_included=false`,
`local_paths_recorded=false`, `direct_bridge_write_performed=false`,
`transport_added=false`, `approval_granted=false`, and
`release_decision_made=false`.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py `
  --verification-json <collected>\bundle_verification.json `
  --reviewer-agent claude-rco-1 `
  --handoff-ref bridge:handoff:bundle-verification `
  --json
```

Validate the bundle's operator decision-reference locally with
`tools/validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py`.
The local operator decision-reference validator compares the bridge-event
template reference with the expected sanitized operator-owned reference and the
local verification summary. It reports `decision_reference_validated` as
reviewer context only and keeps `decision_reference_is_approval=false`,
`decision_reference_is_release_decision=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`, and
`release_decision_made=false`; it does not append bridge events, transport
artifacts, fetch endpoints, approve, merge, promote, or grant runtime
authority.

```powershell
python tools\validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py `
  --verification-summary-json <collected>\bundle_verification_summary.json `
  --bridge-template-json <collected>\reviewer_bridge_event_template.json `
  --expected-decision-ref bridge:operator-decision:pending-review `
  --json
```

Render the local operator decision-reference review summary from that validator
result with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py`.
The summary is reviewer context only: it carries
`decision_reference_validated`, the expected operator-owned reference, and the
verified bundle identity while keeping the operator decision separate. It does
not append bridge events, transport artifacts, fetch endpoints, approve,
merge, promote, include payloads, record local paths, or grant runtime
authority.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py `
  --decision-validation-json <collected>\operator_decision_reference_validation.json `
  --reviewer-agent claude-rco-1 `
  --handoff-ref bridge:handoff:decision-reference-review `
  --json
```

Build a local operator decision-reference review bundle index from that
validator report and review summary with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`.
The index records only the validation and review-summary digests and sizes; it
does not include artifact payloads, record local paths, append bridge events,
transport artifacts, approve, merge, promote, or grant runtime authority.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py `
  --decision-validation-json <collected>\operator_decision_reference_validation.json `
  --review-summary-json <collected>\operator_decision_reference_review_summary.json `
  --json
```

Verify the local operator decision-reference review bundle index with
`tools/verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`.
The local operator decision-reference review bundle verifier recomputes
`digest_checks`, size checks, and `schema_version_checks` from explicit local
artifacts. It does not include payloads, record local paths, append bridge
events, transport artifacts, approve, merge, promote, or grant runtime
authority.

```powershell
python tools\verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py `
  --review-bundle-index-json <collected>\operator_decision_reference_review_bundle_index.json `
  --decision-validation-json <collected>\operator_decision_reference_validation.json `
  --review-summary-json <collected>\operator_decision_reference_review_summary.json `
  --json
```

Render the local operator decision-reference review bundle verification
summary from that verifier result with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py`.
The summary is reviewer context only: it carries
`operator_decision_reference_review_bundle_verification`,
`source_contract_check`, `rebuilt_index_check`, and
`decision_reference_verified` while keeping the operator decision separate. It
does not include payloads, record local paths, append bridge events, transport
artifacts, approve, merge, promote, fetch endpoints, or grant runtime
authority.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py `
  --review-bundle-verification-json <collected>\operator_decision_reference_review_bundle_verification.json `
  --reviewer-agent claude-rco-1 `
  --handoff-ref bridge:handoff:decision-reference-review-bundle-verification `
  --json
```

Render an optional local operator decision-reference review bundle
verification bridge-event template from that verified summary with
`tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py`.
The template validates as bridge `handoff` JSON and carries
`operator_decision_reference_review_bundle_verification`,
`source_contract_check`, `rebuilt_index_check`, and
`decision_reference_verified`, but it is template-only. The tool does not
append bridge events, include payloads, record local paths, transport
artifacts, approve, merge, promote, fetch endpoints, or grant runtime
authority.

```powershell
python tools\build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py `
  --review-bundle-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_summary.json `
  --agent codex-lead-1 `
  --task-id wd-image1-decision-review-bundle-verification-template `
  --to operator,claude-rco-1 `
  --json
```

Build an optional local operator decision-reference review bundle verification
bridge-event template index entry from the verified summary and template
report with
`tools/build_magma_decision_review_verification_template_index_entry.py`.
The entry records `template_index_entry`, `bridge_event_schema_validated`,
`source_contract_check`, and `rebuilt_template_check` for manual review. It
does not append bridge events, include payloads, record local paths, transport
artifacts, approve, merge, promote, fetch endpoints, or grant runtime
authority.

```powershell
python tools\build_magma_decision_review_verification_template_index_entry.py `
  --review-bundle-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_summary.json `
  --bridge-event-template-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template.json `
  --json
```

Verify the local operator decision-reference review bundle verification
bridge-event template index entry with
`tools/verify_magma_decision_review_verification_template_index_entry.py`.
The verifier recomputes `digest_checks`, size checks,
`schema_version_checks`, `source_contract_check`,
`rebuilt_index_entry_check`, and `bridge_event_schema_check` from explicit
local artifacts. It does not append bridge events, include payloads, record
local paths, transport artifacts, approve, merge, promote, fetch endpoints, or
grant runtime authority.

Render the local operator decision-reference review bundle verification
bridge-event template index-entry verification summary from the verifier report:

```powershell
python tools/build_magma_decision_review_verification_template_index_entry_summary.py `
  --index-entry-verification-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification.json `
  --reviewer-agent claude-rco-1 `
  --handoff-ref bridge:handoff:decision-reference-review-template-index-verifier `
  --json
```

The summary carries
`operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification`,
`source_contract_check`, `rebuilt_index_entry_check`,
`bridge_event_schema_check`, `decision_reference_verified`, and
`template_only` as reviewer context. It keeps
`artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `direct_bridge_write_performed=false`,
`approval_granted=false`, `release_decision_made=false`, and
`runtime_controls_added=false`; it does not append bridge events, include
payloads, record local paths, transport artifacts, approve, merge, promote,
fetch endpoints, or grant runtime authority.

Render a local bridge-event template for that index-entry verification summary
without appending it:

```powershell
python tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py `
  --index-entry-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary.json `
  --agent codex-lead-1 `
  --task-id wd-image1-template-index-summary-ready `
  --to operator,claude-rco-1,codex-tools-1 `
  --json
```

The template validates the summary contract and renders schema-valid handoff
JSON while keeping `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`,
`release_decision_made=false`, and `runtime_controls_added=false`. It does not
append bridge events, include payloads, record local paths, transport artifacts,
approve, merge, promote, fetch endpoints, grant runtime authority, or make a
release decision.

Build a local index entry for that index-entry verification summary
bridge-event template without appending it:

```powershell
python tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py `
  --index-entry-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary.json `
  --summary-bridge-event-template-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template.json `
  --json
```

The index entry records `template_index_entry`,
`bridge_event_schema_validated`, `source_contract_check`, and
`rebuilt_template_check` while keeping `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`direct_bridge_write_performed=false`, `approval_granted=false`,
`release_decision_made=false`, and `runtime_controls_added=false`. It does not
append bridge events, include payloads, record local paths, transport artifacts,
approve, merge, promote, fetch endpoints, grant runtime authority, or make a
release decision.

Verify that local index entry from explicit local artifacts:

```powershell
python tools/verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry.py `
  --index-entry-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.json `
  --index-entry-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary.json `
  --summary-bridge-event-template-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template.json `
  --json
```

The verifier recomputes digest, size, schema, source-contract,
rebuilt-index-entry, and bridge-event-schema checks while keeping
`artifact_payloads_included=false`, `local_paths_recorded=false`,
`transport_added=false`, `direct_bridge_write_performed=false`,
`approval_granted=false`, `release_decision_made=false`, and
`runtime_controls_added=false`. It does not append bridge events, include
payloads, record local paths, transport artifacts, approve, merge, promote,
fetch endpoints, grant runtime authority, or make a release decision.

```powershell
python tools\verify_magma_decision_review_verification_template_index_entry.py `
  --index-entry-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry.json `
  --review-bundle-verification-summary-json <collected>\operator_decision_reference_review_bundle_verification_summary.json `
  --bridge-event-template-json <collected>\operator_decision_reference_review_bundle_verification_bridge_event_template.json `
  --json
```

### Post-failure release-hold review

When `MagmaHandoffAlertFeedBackoffActive`,
`MagmaHandoffAlertFeedFetchFailures`, `MagmaHandoffRuntimeAuthorityReported`,
`MagmaHandoffPayloadImported`, or `MagmaHandoffPrivateMaterialRecorded` fired
in the release window, compare the current `/api/ops` alert feed state with
the matching `/metrics` scrape and CI commit. Keep the hold until a follow-up
operator review records sanitized evidence that the failing metric returned to
the manual pass condition and that no runtime-authority, payload-import, or
local-path counter is nonzero.

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
