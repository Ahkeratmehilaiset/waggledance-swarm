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
operator dashboards. When the runtime container provides a
`route_stage_latency_feed` snapshot provider, `/api/ops` also exposes sanitized
Prometheus/Alertmanager panel values and active alerts under `feed_state`; it
does not add runtime controls.

## Metrics

| Metric | Meaning |
| --- | --- |
| `waggledance_route_stage_request_latency_histogram_ms_bucket` | Cumulative histogram buckets by whitelisted `stage` label. |
| `waggledance_route_stage_request_latency_histogram_ms_sum` | Histogram sum of request latency for requests where the stage was observed. |
| `waggledance_route_stage_request_latency_histogram_ms_count` | Histogram count for requests where the stage was observed. |
| `waggledance_route_stage_observations_total` | Counter for sanitized requests where the stage was observed. |
| `waggledance_route_stage_latency_feed_available` | Read-only provider health gauge for the optional Prometheus/Alertmanager feed. |
| `waggledance_route_stage_latency_feed_cache_hits_total` | Count of feed snapshots served from the bounded in-process cache. |
| `waggledance_route_stage_latency_feed_fetch_failures_total` | Count of failed provider refresh attempts collapsed to fixed reasons. |
| `waggledance_route_stage_latency_feed_backoff_active` | Gauge showing whether the bounded failure backoff is active. |

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

## Read-only feed state

The optional `/api/ops.route_stage_latency.feed_state` object is a sanitized
view of a container-provided Prometheus/Alertmanager snapshot. It may include:

- `panel_values`: known panel IDs, fixed route-stage labels, numeric values,
  units, and derived nominal/warning/critical status.
- `active`: known alert IDs, fixed route-stage labels, severity, numeric
  values, and WD-generated summaries.
- `updated_at`: the provider timestamp when it is a string.
- `feed_health`: sanitized provider health, cache/backoff counters, fixed
  failure reasons, and read-only no-authority flags.

It intentionally does not forward Alertmanager annotations, descriptions,
external URLs, raw label sets, raw query text, hostnames, filesystem paths, or
exception strings.

## Optional feed provider

`configs/settings.yaml` includes a disabled-by-default
`route_stage_latency_feed` section. When an operator enables it, the runtime
container builds a read-only provider that performs bounded GET requests to:

- Prometheus `GET /api/v1/query` for the fixed p95, p99, and request-rate
  PromQL templates in this runbook.
- Alertmanager `GET /api/v2/alerts` for active alert labels.

The provider does not accept credentials, custom authorization headers,
userinfo URLs, query-string URLs, redirects, or unbounded responses. Private,
loopback, and localhost targets are refused unless the exact hostname is listed
in `allowed_private_hosts`. Provider failures are collapsed to
`prometheus_alertmanager_unavailable`; exception details are not forwarded to
the Ops API or dashboard. The adapter also keeps a bounded TTL cache and bounded
failure backoff so repeated Ops or `/metrics` scrapes can reuse the last
sanitized snapshot during temporary feed outages.

Example local operator config:

```yaml
route_stage_latency_feed:
  enabled: true
  prometheus_base_url: "http://127.0.0.1:9090"
  alertmanager_base_url: "http://127.0.0.1:9093"
  timeout_s: 3
  max_response_bytes: 1000000
  cache_ttl_s: 30
  failure_backoff_s: 30
  allowed_private_hosts:
    - "127.0.0.1"
```

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
