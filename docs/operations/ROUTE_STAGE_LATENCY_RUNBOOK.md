# Route-stage latency operator runbook

**Status:** read-only operator runbook for WD Image1 route-stage latency
**Scope:** Prometheus p95/p99 panels and alert thresholds for sanitized route-stage metrics
**Companion docs:** `docs/API.md`, `docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md`

## Purpose

This runbook turns sanitized route-stage runtime metrics into operator-visible
latency panels and alert thresholds. It does not store raw traces, raw queries,
profiles, language hints, context, user identifiers, hostnames, filesystem
paths, or exception strings.

The metric source is the auth-exempt `/metrics` endpoint. `/api/ops` and the
hologram Ops panel expose the PromQL templates as read-only metadata for
operator dashboards; they do not connect to Prometheus directly and do not add
runtime controls.

## Metrics

| Metric | Meaning |
| --- | --- |
| `waggledance_route_stage_request_latency_histogram_ms_bucket` | Cumulative histogram buckets by whitelisted `stage` label. |
| `waggledance_route_stage_request_latency_histogram_ms_sum` | Histogram sum of request latency for requests where the stage was observed. |
| `waggledance_route_stage_request_latency_histogram_ms_count` | Histogram count for requests where the stage was observed. |
| `waggledance_route_stage_observations_total` | Counter for sanitized requests where the stage was observed. |

The histogram uses request latency correlated to observed route stages. It is
not an internal per-stage span timer.

## Dashboard panels

| Panel | PromQL | Unit |
| --- | --- | --- |
| Route-stage p95 latency | `histogram_quantile(0.95, sum by (le, stage) (rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))` | ms |
| Route-stage p99 latency | `histogram_quantile(0.99, sum by (le, stage) (rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))` | ms |
| Route-stage request rate | `sum by (stage) (rate(waggledance_route_stage_observations_total[5m]))` | requests/s |

## Alert thresholds

Use these PromQL rules as the first operator threshold set. They are
conservative and read-only.

| Alert | PromQL | For | Severity | Operator action |
| --- | --- | --- | --- | --- |
| `RouteStageLatencyP95Warning` | `histogram_quantile(0.95, sum by (le, stage) (rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m]))) > 2500` | `10m` | warning | Inspect the slow stage, current release, and upstream model/runtime logs. |
| `RouteStageLatencyP99Critical` | `histogram_quantile(0.99, sum by (le, stage) (rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m]))) > 5000` | `10m` | critical | Hold latency-sensitive releases and capture metrics/log evidence before more routing changes. |

## Guardrails

- No panel or alert rule should call a mutating endpoint.
- No panel or alert rule should start, stop, enable, disable, or reconfigure
  route-stage routing, autogrowth, solvers, or dashboard state.
- No panel or alert rule should auto-merge, auto-promote, or create solver
  authority.
- Alert payloads must not include raw queries, profile names, language hints,
  raw route traces, user IDs, hostnames, filesystem paths, or exception strings.
  Metric names, stage labels from the fixed allowlist, and numeric values are
  enough.
