# Low-risk autogrowth operator runbook

**Status:** read-only operator runbook for the bounded low-risk autogrowth lane
**Scope:** Prometheus alert thresholds and optional read-only Alertmanager feed
for `waggledance_autogrowth_*`
**Companion docs:** `docs/API.md`, `docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md`

## Purpose

This runbook turns the low-risk autogrowth boundary metrics into operator
alerts. It does not add runtime controls, does not start or stop the ticker,
and does not grant new solver-growth authority. The alerts are observability
only: they tell an operator when to inspect logs, capture evidence, or pause
deployments through the normal release process.

The metric source is the auth-exempt `/metrics` endpoint. The dashboard
`/api/ops` and hologram Ops panel show the same boundary as read-only status.
When `autogrowth_alert_feed.enabled=true`, `/api/ops` can also show sanitized
Alertmanager state under `autogrowth.alert_state`: fixed autogrowth alert IDs,
fixed summaries, numeric values, `feed_health`, `slo_panels`, and
`drill_evidence`. It does not add mutating endpoints, start/stop controls,
runtime authority, raw Alertmanager labels, hostnames, URLs, filesystem paths,
headers, or exception text.

## Metrics

| Metric | Meaning |
| --- | --- |
| `waggledance_autogrowth_up` | `1` when the metrics source can read the ticker boundary, `0` when missing or unavailable. |
| `waggledance_autogrowth_background_enabled` | `1` when the low-risk background ticker is configured. |
| `waggledance_autogrowth_background_running` | `1` only while the runtime ticker is actively running. |
| `waggledance_autogrowth_background_interval_seconds` | Configured seconds between background wakes. Default is `30`. |
| `waggledance_autogrowth_background_max_ticks_per_wake` | Upper bound for scheduler ticks per wake. Default is `20`. |
| `waggledance_autogrowth_wakeups_total` | Total background wake attempts. |
| `waggledance_autogrowth_non_idle_ticks_total` | Wakeups that found work and ran at least one scheduler tick. |
| `waggledance_autogrowth_errors_total` | Background ticker error count. |
| `waggledance_autogrowth_alert_feed_up` | `1` when feed provider health could be built for the current scrape. |
| `waggledance_autogrowth_alert_feed_configured` | `1` when the optional Alertmanager feed is configured. |
| `waggledance_autogrowth_alert_feed_available` | `1` when the optional Alertmanager feed has a usable snapshot or cache. |
| `waggledance_autogrowth_alert_feed_status{status=...}` | Fixed-state gauges for `not_configured`, `nominal`, and `warning`. |
| `waggledance_autogrowth_alert_feed_failure_reason{reason=...}` | Fixed sanitized failure-reason gauges; no raw provider exception text. |
| `waggledance_autogrowth_alert_feed_cache_*` | Cache TTL, presence, stale state, hits, and misses for the optional feed. |
| `waggledance_autogrowth_alert_feed_fetch_*` | Fetch success/failure counters for the optional feed. |

## Alert thresholds

Use these PromQL rules as the first operator threshold set. They are deliberately
conservative because the lane is bounded and low-risk; the first response should
be evidence collection, not automatic mutation.

| Alert | PromQL | For | Severity | Operator action |
| --- | --- | --- | --- | --- |
| `AutogrowthSourceDown` | `waggledance_autogrowth_up == 0` | `5m` | warning | Check `/metrics`, `/api/ops`, and runtime logs. Treat as missing observability until proven otherwise. |
| `AutogrowthErrorsObserved` | `increase(waggledance_autogrowth_errors_total[10m]) > 0` | `10m` | warning | Capture logs around the first error and verify the ticker still reports `up == 1`. |
| `AutogrowthErrorBurst` | `increase(waggledance_autogrowth_errors_total[10m]) >= 3` | `10m` | critical | Hold new low-risk autogrowth releases and open a blocking issue with logs, metrics, and the current commit. |
| `AutogrowthWakeupStalled` | `waggledance_autogrowth_background_enabled == 1 and waggledance_autogrowth_up == 1 and increase(waggledance_autogrowth_wakeups_total[30m]) == 0` | `30m` | warning | Compare `/api/ops` enabled/running state with process logs. Do not assume production authority changed. |
| `AutogrowthWakeupBurst` | `increase(waggledance_autogrowth_wakeups_total[10m]) > 40` | `10m` | warning | Default cadence is 30s, so 40 wakeups in 10m is about 2x expected. Check restart loops and scheduler cadence. |
| `AutogrowthNonIdleBurst` | `increase(waggledance_autogrowth_non_idle_ticks_total[10m]) > 20` | `10m` | warning | Inspect runtime gap sources and promoted solver receipts before accepting new growth claims. |

## Triage flow

1. Confirm the alert is from the same commit currently deployed.
2. Fetch `/metrics` and `/api/ops`; store both outputs with timestamps.
3. If `autogrowth.alert_state.feed_health.configured=true`, compare the
   `active` alert IDs with `feed_health`, `slo_panels`, and `drill_evidence`.
   Treat `AutogrowthAlertFeedUnavailable` or
   `AutogrowthAlertFeedInvalid` as observability warnings, not runtime
   authority changes.
4. Capture runtime logs for the alert window plus the preceding 10 minutes.
5. Check whether `waggledance_autogrowth_background_running` changed during
   the window. A restart can explain a short wakeup burst.
6. For any `errors_total` increase, open a blocking issue before merging more
   low-risk autogrowth runtime changes.
7. For wakeup or non-idle bursts, compare the observed rate with
   `waggledance_autogrowth_background_interval_seconds` and
   `waggledance_autogrowth_background_max_ticks_per_wake`.

## Guardrails

- No alert rule in this runbook should call a mutating endpoint.
- No alert rule should start, stop, enable, disable, or reconfigure the ticker.
- No alert rule should auto-merge, auto-promote, or create solver authority.
- Alert payloads must not include raw queries, user IDs, hostnames, filesystem
  paths, or exception strings. Metric names and numeric values are enough.
- The optional `autogrowth_alert_feed` only reads operator-owned Alertmanager
  `/api/v2/alerts`, is disabled by default, and must keep credential,
  private-host, timeout, cache, and backoff guardrails.
- Recovery actions stay manual and operator-owned. The read-only alert-state
  surface does not approve deployments, merge PRs, promote solvers, change
  config, or grant operational control.
