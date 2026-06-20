"""Prometheus ``/metrics`` endpoint.

Closes F5-002 from the Release Polish Run 20260409_054702 by giving
the ``prometheus-client`` dependency a real job: expose the existing
v3.5.6 efficiency counters (preflight skips, budget exhaustions,
neighbor-assist ratios, etc.) and the low-risk autogrowth ticker
boundary in Prometheus text format so the operator's standard
observability stack can scrape them.

Design notes
------------

- We use a **separate**, dedicated ``CollectorRegistry`` instead of
  the default global one. That keeps waggledance metrics isolated
  from any process-level collectors prometheus-client enables by
  default, which would leak file-descriptor counts and Python GC
  stats that operators haven't asked for.
- Metrics are collected on-demand via a tiny ``Collector`` subclass
  that reads the container's ``hex_neighbor_assist.get_metrics()``,
  route-stage component flags, and ``autogrowth_background_ticker.stats``
  each time ``/metrics`` is
  scraped. This avoids the "metrics go stale" trap of snapshotting into
  Gauges at startup.
- If ``hex_neighbor_assist`` is None (feature disabled) or raises,
  we emit the gauges with value ``0`` and a ``_up`` gauge set to
  ``0`` so alerts can fire on "metrics source unhealthy".
- Public endpoint: no Bearer token required. Prometheus scrapers
  typically run out-of-band and cannot easily pass auth headers;
  the data we expose is counts, not secrets.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable

from fastapi import APIRouter, Request
from fastapi.responses import Response

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
)

from waggledance.adapters.http.routes.chat import CHAT_ROUTE_STAGE_ORDER
from waggledance.adapters.http.routes.compat_dashboard import (
    _autogrowth_section,
    _magma_share_import_handoff_section,
    _route_stage_latency_feed_state,
)
from waggledance.core.autonomy_growth.counterfactual_replay import (
    COUNTERFACTUAL_OBSERVABILITY_DIRECTIONS,
    COUNTERFACTUAL_OBSERVABILITY_STATES,
    summarize_counterfactual_observability,
)

logger = logging.getLogger(__name__)

router = APIRouter()


_COUNTER_NAMES: tuple[str, ...] = (
    "preflight_skips",
    "preflight_passes",
    "skipped_local_attempts",
    "skipped_neighbor_attempts",
    "budget_exhaustions",
    "origin_cell_resolutions",
    "local_only_resolutions",
    "neighbor_assist_resolutions",
    "global_escalations",
    "llm_last_resolutions",
    "completed_hex_neighbor_batches",
    "neighbors_consulted_total",
    "self_heal_events",
    "magma_traces_written",
    "ttl_exhaustions",
)

_GAUGE_NAMES: tuple[str, ...] = (
    "cells_loaded",
    "quarantined_cells",
)

_AUTOGROWTH_COUNTER_NAMES: tuple[str, ...] = (
    "wakeups_total",
    "non_idle_ticks",
    "errors_total",
)

_PRE_HEX_ROUTE_STAGE_NAMES: tuple[str, ...] = (
    "language_detection",
    "hot_cache",
    "memory_context",
    "route_selection",
    "deterministic_solver",
)
_HEX_BACKED_ROUTE_STAGE_NAMES: tuple[str, ...] = (
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
)
_ROUTE_STAGE_COMPONENT_ATTRS: dict[str, str] = {
    "hybrid_retrieval_8_cell": "hybrid_retrieval",
    "hex_neighbor_assist_7_cell": "hex_neighbor_assist",
}
_MAGMA_HANDOFF_PROVIDER_STATUSES: tuple[str, ...] = (
    "not_configured",
    "nominal",
    "warning",
)
_MAGMA_HANDOFF_SNAPSHOT_KINDS: tuple[str, ...] = (
    "none",
    "handoff",
    "history",
    "latest",
    "invalid",
)
_MAGMA_HANDOFF_FRESHNESS_STATES: tuple[str, ...] = (
    "fresh",
    "stale",
    "unknown",
)
_MAGMA_HANDOFF_PROVIDER_ALERT_IDS: tuple[str, ...] = (
    "MagmaShareImportHandoffProviderUnavailable",
    "MagmaShareImportHandoffProviderInvalid",
    "MagmaShareImportHandoffProviderRetentionDropped",
    "MagmaShareImportHandoffProviderRetentionTruncated",
    "MagmaShareImportHandoffProviderRetentionLimitReached",
    "MagmaShareImportHandoffProviderFreshnessWarning",
    "MagmaShareImportHandoffFreshnessSourceUnavailable",
    "MagmaShareImportHandoffFreshnessSourceInvalid",
)
_ROUTE_STAGE_LATENCY_FEED_STATUSES: tuple[str, ...] = (
    "not_configured",
    "nominal",
    "warning",
)
_ROUTE_STAGE_LATENCY_FEED_FAILURE_REASONS: tuple[str, ...] = (
    "none",
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_SHAPE_REFUSED",
    "RESPONSE_BODY_REFUSED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "RESPONSE_SOURCE_URL_REFUSED",
    "PROMETHEUS_STATUS_REFUSED",
    "PROMETHEUS_DATA_REFUSED",
    "PROMETHEUS_RESULT_REFUSED",
    "ALERTMANAGER_RESULT_REFUSED",
    "ROUTE_STAGE_LATENCY_FEED_UNAVAILABLE",
    "HTTP_STATUS_REFUSED",
    "FEED_READ_FAILED",
)
_MAGMA_HANDOFF_ALERT_FEED_STATUSES: tuple[str, ...] = (
    "not_configured",
    "nominal",
    "warning",
)
_MAGMA_HANDOFF_ALERT_FEED_FAILURE_REASONS: tuple[str, ...] = (
    "none",
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_SHAPE_REFUSED",
    "RESPONSE_BODY_REFUSED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "RESPONSE_SOURCE_URL_REFUSED",
    "ALERTMANAGER_RESULT_REFUSED",
    "MAGMA_HANDOFF_METRICS_ALERT_FEED_UNAVAILABLE",
    "FEED_READ_FAILED",
)
_AUTOGROWTH_ALERT_FEED_STATUSES: tuple[str, ...] = (
    "not_configured",
    "nominal",
    "warning",
)
_AUTOGROWTH_ALERT_FEED_FAILURE_REASONS: tuple[str, ...] = (
    "none",
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_SHAPE_REFUSED",
    "RESPONSE_BODY_REFUSED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "RESPONSE_SOURCE_URL_REFUSED",
    "ALERTMANAGER_RESULT_REFUSED",
    "AUTOGROWTH_ALERT_FEED_UNAVAILABLE",
    "FEED_READ_FAILED",
)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _as_bool_float(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _as_nonnegative_float(value: Any) -> float:
    numeric = _as_float(value)
    if numeric is None or numeric < 0:
        return 0.0
    return numeric


def _as_finite_float(value: Any) -> float:
    numeric = _as_float(value)
    if numeric is None or not math.isfinite(numeric):
        return 0.0
    return numeric


class _WaggleCollector:
    """On-demand collector that reads the live container state.

    Registered against a private ``CollectorRegistry`` so that the
    default prometheus-client process collectors are NOT enabled —
    operators get exactly the metrics we document here, no more.
    """

    def __init__(self, get_container) -> None:  # noqa: ANN001
        self._get_container = get_container

    def collect(self) -> Iterable[Any]:
        container = None
        try:
            container = self._get_container()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("metrics: failed to fetch container: %s", exc)

        # _up gauge reports whether we could read the stats at all.
        up = GaugeMetricFamily(
            "waggledance_up",
            "1 if the metrics collector could read hex_neighbor_assist stats this scrape",
            value=0.0,
        )

        if container is None:
            yield up
            return

        yield from self._collect_hex_metrics(container, up)
        yield from self._collect_hex_topology_boundary_metrics(container)
        yield from self._collect_route_stage_metrics(container)
        yield from self._collect_route_stage_runtime_metrics(container)
        yield from self._collect_runtime_receipt_metrics(container)
        yield from self._collect_route_stage_latency_feed_metrics(container)
        yield from self._collect_autogrowth_metrics(container)
        yield from self._collect_counterfactual_replay_metrics(container)
        yield from self._collect_magma_handoff_metrics(container)

    def _collect_hex_metrics(
        self,
        container: Any,
        up: GaugeMetricFamily,
    ) -> Iterable[Any]:
        try:
            hex_assist = getattr(container, "hex_neighbor_assist", None)
        except Exception as exc:
            logger.warning("metrics: failed to fetch hex_neighbor_assist: %s", exc)
            yield up
            return
        if hex_assist is None:
            yield up
            return

        stats: dict[str, Any]
        try:
            stats = hex_assist.get_metrics() or {}
        except Exception as exc:
            logger.warning("metrics: hex_neighbor_assist.get_metrics() raised: %s", exc)
            yield up
            return

        up = GaugeMetricFamily(
            "waggledance_up",
            "1 if the metrics collector could read hex_neighbor_assist stats this scrape",
            value=1.0,
        )
        yield up

        # hex_mesh.enabled as a gauge so operators can alert on an
        # accidental flag flip.
        yield GaugeMetricFamily(
            "waggledance_hex_mesh_enabled",
            "1 if the hex-mesh runtime is enabled in settings",
            value=1.0 if stats.get("enabled") else 0.0,
        )

        # Monotonic counters.
        for name in _COUNTER_NAMES:
            val = stats.get(name)
            if val is None:
                continue
            numeric = _as_float(val)
            if numeric is None:
                continue
            # Prometheus convention: counter names end in ``_total``.
            # A few source keys (e.g. ``neighbors_consulted_total``)
            # already carry that suffix; don't double it up.
            base = name[:-6] if name.endswith("_total") else name
            yield CounterMetricFamily(
                f"waggledance_hex_{base}_total",
                f"v3.5.6 hex-mesh counter: {name}",
                value=numeric,
            )

        # Instantaneous gauges.
        for name in _GAUGE_NAMES:
            val = stats.get(name)
            if val is None:
                continue
            numeric = _as_float(val)
            if numeric is None:
                continue
            yield GaugeMetricFamily(
                f"waggledance_hex_{name}",
                f"v3.5.6 hex-mesh gauge: {name}",
                value=numeric,
            )

    def _collect_hex_topology_boundary_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_hex_topology_boundary_up",
            (
                "1 if the metrics collector could read the active "
                "hex topology boundary this scrape"
            ),
            value=0.0,
        )
        try:
            registry = getattr(container, "hex_topology_registry", None)
        except Exception as exc:
            logger.warning("metrics: failed to fetch hex_topology_registry: %s", exc)
            yield up
            return
        if registry is None:
            yield up
            return

        try:
            stats_fn = getattr(registry, "stats", None)
            stats = stats_fn() if callable(stats_fn) else {}
            if not isinstance(stats, dict):
                stats = {}
            raw_cells = getattr(registry, "cells", {})
            cells = raw_cells if isinstance(raw_cells, dict) else {}
            cell_items = list(cells.items())
            configured_count = len(cell_items)
            if configured_count <= 0:
                configured_count = int(_as_nonnegative_float(stats.get("cells_loaded")))
            enabled_count = sum(
                1
                for _cell_id, cell in cell_items
                if bool(_safe_getattr(cell, "enabled", False))
            )
            total_agents = _as_nonnegative_float(stats.get("total_agents_mapped"))
            neighbor_links = self._hex_topology_neighbor_link_count(
                registry,
                [cell_id for cell_id, _cell in cell_items],
            )
        except Exception as exc:
            logger.warning(
                "metrics: hex topology boundary collection raised: %s",
                exc,
            )
            yield up
            return

        yield GaugeMetricFamily(
            "waggledance_hex_topology_boundary_up",
            (
                "1 if the metrics collector could read the active "
                "hex topology boundary this scrape"
            ),
            value=1.0,
        )

        cell_metric = GaugeMetricFamily(
            "waggledance_hex_topology_cells",
            "Configured and enabled active hex topology cell counts.",
            labels=["state"],
        )
        cell_metric.add_metric(["configured"], float(configured_count))
        cell_metric.add_metric(["enabled"], float(enabled_count))
        yield cell_metric

        yield GaugeMetricFamily(
            "waggledance_hex_topology_agents_mapped",
            "Total agents mapped into the active hex topology registry.",
            value=total_agents,
        )
        yield GaugeMetricFamily(
            "waggledance_hex_topology_neighbor_links",
            "Directed ring-1 neighbor links visible in the active hex topology.",
            value=float(neighbor_links),
        )

        hex_assist = _safe_getattr(container, "hex_neighbor_assist")
        yield GaugeMetricFamily(
            "waggledance_hex_topology_runtime_dispatch_enabled",
            "1 if the runtime hex neighbor dispatch gate is enabled.",
            value=_as_bool_float(_safe_getattr(hex_assist, "enabled", False)),
        )
        yield GaugeMetricFamily(
            "waggledance_hex_topology_runtime_mutation_authority",
            (
                "1 if runtime topology mutation authority is enabled; "
                "the current boundary collector exposes read-only state only"
            ),
            value=0.0,
        )

    @staticmethod
    def _hex_topology_neighbor_link_count(
        registry: Any,
        cell_ids: list[str],
    ) -> int:
        get_neighbors = _safe_getattr(registry, "get_neighbor_cells")
        if not callable(get_neighbors):
            return 0
        count = 0
        for cell_id in cell_ids:
            neighbors = get_neighbors(cell_id)
            if not isinstance(neighbors, list):
                continue
            count += len(neighbors)
        return count

    def _collect_route_stage_metrics(self, container: Any) -> Iterable[Any]:
        disabled_optional = [
            stage
            for stage, attr in _ROUTE_STAGE_COMPONENT_ATTRS.items()
            if not self._route_stage_component_enabled(container, attr)
        ]
        metric = GaugeMetricFamily(
            "waggledance_route_stage_count",
            "Privacy-safe chat route-stage counts by group.",
            labels=["group"],
        )
        metric.add_metric(["expected"], float(len(CHAT_ROUTE_STAGE_ORDER)))
        metric.add_metric(
            ["enabled"],
            float(len(CHAT_ROUTE_STAGE_ORDER) - len(disabled_optional)),
        )
        metric.add_metric(["pre_hex"], float(len(_PRE_HEX_ROUTE_STAGE_NAMES)))
        metric.add_metric(["hex_backed"], float(len(_HEX_BACKED_ROUTE_STAGE_NAMES)))
        metric.add_metric(["optional"], float(len(_ROUTE_STAGE_COMPONENT_ATTRS)))
        metric.add_metric(["disabled_optional"], float(len(disabled_optional)))
        yield metric

    @staticmethod
    def _route_stage_component_enabled(container: Any, attr: str) -> bool:
        component = _safe_getattr(container, attr)
        if component is None:
            return False
        return bool(_safe_getattr(component, "enabled", False))

    def _collect_route_stage_runtime_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        runtime_metrics = _safe_getattr(container, "route_stage_runtime_metrics")
        snapshot = {}
        if runtime_metrics is not None:
            snapshot_fn = _safe_getattr(runtime_metrics, "snapshot")
            if callable(snapshot_fn):
                try:
                    snapshot = snapshot_fn() or {}
                except Exception as exc:
                    logger.warning(
                        "metrics: route_stage_runtime_metrics.snapshot() raised: %s",
                        exc,
                    )
                    snapshot = {}

        observations = snapshot.get("observations_total")
        if not isinstance(observations, dict):
            observations = {}
        latency_sums = snapshot.get("request_latency_ms_total")
        if not isinstance(latency_sums, dict):
            latency_sums = {}
        bucket_labels = snapshot.get("request_latency_ms_bucket_labels")
        if not isinstance(bucket_labels, list) or not bucket_labels:
            bucket_labels = [
                "50",
                "100",
                "250",
                "500",
                "1000",
                "2500",
                "5000",
                "10000",
                "+Inf",
            ]
        buckets_by_stage = snapshot.get("request_latency_ms_buckets")
        if not isinstance(buckets_by_stage, dict):
            buckets_by_stage = {}

        observed = CounterMetricFamily(
            "waggledance_route_stage_observations_total",
            "Total sanitized chat requests where the route stage was observed.",
            labels=["stage"],
        )
        latency = CounterMetricFamily(
            "waggledance_route_stage_request_latency_ms_total",
            (
                "Total request latency in milliseconds for sanitized chat "
                "requests where the route stage was observed."
            ),
            labels=["stage"],
        )
        latency_histogram = HistogramMetricFamily(
            "waggledance_route_stage_request_latency_histogram_ms",
            (
                "Histogram of request latency in milliseconds for sanitized "
                "chat requests where the route stage was observed."
            ),
            labels=["stage"],
        )
        for stage in CHAT_ROUTE_STAGE_ORDER:
            latency_sum = _as_float(latency_sums.get(stage)) or 0.0
            observed.add_metric(
                [stage],
                _as_float(observations.get(stage)) or 0.0,
            )
            latency.add_metric([stage], latency_sum)
            stage_buckets = buckets_by_stage.get(stage)
            if not isinstance(stage_buckets, dict):
                stage_buckets = {}
            latency_histogram.add_metric(
                [stage],
                [
                    (str(label), _as_float(stage_buckets.get(label)) or 0.0)
                    for label in bucket_labels
                ],
                sum_value=latency_sum,
            )
        yield observed
        yield latency
        yield latency_histogram

    def _collect_runtime_receipt_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_runtime_receipt_metrics_up",
            (
                "1 if the metrics collector could read runtime receipt "
                "coverage counters this scrape"
            ),
            value=0.0,
        )
        service = _safe_getattr(container, "autonomy_service")
        runtime = _safe_getattr(service, "_runtime") if service is not None else None
        snapshot_fn = _safe_getattr(runtime, "runtime_receipt_metrics_snapshot")
        if not callable(snapshot_fn):
            yield up
            return
        try:
            snapshot = snapshot_fn()
        except Exception as exc:
            logger.warning(
                "metrics: runtime_receipt_metrics_snapshot() raised: %s",
                exc,
            )
            yield up
            return
        if not isinstance(snapshot, dict):
            yield up
            return

        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_metrics_up",
            (
                "1 if the metrics collector could read runtime receipt "
                "coverage counters this scrape"
            ),
            value=1.0,
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_sink_configured",
            (
                "1 if AutonomyRuntime has a configured runtime receipt sink; "
                "default remains off when this is 0"
            ),
            value=_as_bool_float(snapshot.get("sink_configured")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_coverage_ratio",
            (
                "Runtime receipt success ratio over finalized handle_query "
                "paths in this process"
            ),
            value=_as_finite_float(snapshot.get("coverage_ratio")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_solver_trace_presence_ratio",
            (
                "Ratio of finalized handle_query paths with a solver-call "
                "trace in this process"
            ),
            value=_as_finite_float(snapshot.get("solver_trace_presence_ratio")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_last_solver_trace_count",
            (
                "Sanitized solver-call trace count from the last finalized "
                "handle_query path"
            ),
            value=_as_nonnegative_float(snapshot.get("last_solver_trace_count")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_last_result_present",
            "1 if the last receipt sink attempt returned a result object.",
            value=_as_bool_float(snapshot.get("last_result_present")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_last_verifier_ok",
            "1 if the last receipt sink result reported verifier_report.ok=true.",
            value=_as_bool_float(snapshot.get("last_verifier_ok")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_last_receipt_count",
            "Receipt count reported by the last receipt sink result.",
            value=_as_nonnegative_float(snapshot.get("last_receipt_count")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_verifier_ok_ratio",
            (
                "Ratio of successful receipt sink results whose verifier "
                "report was ok."
            ),
            value=_as_finite_float(snapshot.get("verifier_ok_ratio")),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_default_emission_changed",
            (
                "1 if this metrics path changed default runtime receipt "
                "emission semantics; must remain 0"
            ),
            value=_as_bool_float(
                snapshot.get("default_runtime_receipt_emission_changed")
            ),
        )
        yield GaugeMetricFamily(
            "waggledance_runtime_receipt_runtime_authority_changed",
            "1 if this metrics path changed runtime authority; must remain 0",
            value=_as_bool_float(snapshot.get("runtime_authority_changed")),
        )

        for key, metric_name, description in (
            (
                "handle_query_total",
                "waggledance_runtime_receipt_handle_query_finalized_total",
                "Finalized handle_query paths observed by runtime receipt metrics.",
            ),
            (
                "solver_trace_present_total",
                "waggledance_runtime_receipt_solver_trace_present_total",
                "Finalized handle_query paths with a solver-call trace.",
            ),
            (
                "sink_not_configured_total",
                "waggledance_runtime_receipt_sink_not_configured_total",
                "Finalized handle_query paths where no receipt sink was configured.",
            ),
            (
                "attempt_total",
                "waggledance_runtime_receipt_attempt_total",
                "Runtime receipt sink attempts.",
            ),
            (
                "success_total",
                "waggledance_runtime_receipt_success_total",
                "Runtime receipt sink attempts that returned without raising.",
            ),
            (
                "failure_total",
                "waggledance_runtime_receipt_failure_total",
                "Runtime receipt sink attempts that raised.",
            ),
            (
                "verifier_ok_total",
                "waggledance_runtime_receipt_verifier_ok_total",
                "Runtime receipt sink results with verifier_report.ok=true.",
            ),
            (
                "verifier_not_ok_total",
                "waggledance_runtime_receipt_verifier_not_ok_total",
                "Runtime receipt sink results without verifier_report.ok=true.",
            ),
            (
                "receipt_count_total",
                "waggledance_runtime_receipt_receipt_count_total",
                "Total receipts reported by runtime receipt sink results.",
            ),
        ):
            yield CounterMetricFamily(
                metric_name[:-6],
                description,
                value=_as_nonnegative_float(snapshot.get(key)),
            )

    def _collect_route_stage_latency_feed_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_route_stage_latency_feed_up",
            "1 if route-stage latency feed health could be built this scrape.",
            value=0.0,
        )
        try:
            feed_state = _route_stage_latency_feed_state(container)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "metrics: route_stage_latency_feed health raised: %s",
                exc,
            )
            yield up
            return
        feed_health = feed_state.get("feed_health")
        if not isinstance(feed_health, dict):
            yield up
            return

        yield GaugeMetricFamily(
            "waggledance_route_stage_latency_feed_up",
            "1 if route-stage latency feed health could be built this scrape.",
            value=1.0,
        )

        bool_gauges = {
            "configured": feed_health.get("configured"),
            "available": feed_health.get("available"),
            "cache_enabled": feed_health.get("cache_enabled"),
            "cache_present": feed_health.get("cache_present"),
            "cache_stale": feed_health.get("cache_stale"),
            "backoff_active": feed_health.get("backoff_active"),
            "controls_present": feed_health.get("controls_present"),
            "runtime_authority_granted": feed_health.get("runtime_authority_granted"),
            "external_writes_applied": feed_health.get("external_writes_applied"),
        }
        for name, value in bool_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_route_stage_latency_feed_{name}",
                (
                    "read-only route-stage latency feed provider-health "
                    f"gauge: {name}"
                ),
                value=_as_bool_float(value),
            )

        numeric_gauges = {
            "cache_ttl_seconds": feed_health.get("cache_ttl_seconds"),
            "failure_backoff_seconds": feed_health.get("failure_backoff_seconds"),
            "timeout_seconds": feed_health.get("timeout_seconds"),
            "max_response_bytes": feed_health.get("max_response_bytes"),
            "last_response_bytes": feed_health.get("last_response_bytes"),
        }
        for name, value in numeric_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_route_stage_latency_feed_{name}",
                (
                    "read-only route-stage latency feed provider-health "
                    f"gauge: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        counter_values = {
            "cache_hits": feed_health.get("cache_hit_count"),
            "cache_misses": feed_health.get("cache_miss_count"),
            "fetch_successes": feed_health.get("fetch_success_count"),
            "fetch_failures": feed_health.get("fetch_failure_count"),
            "backoff_skips": feed_health.get("backoff_skip_count"),
        }
        for name, value in counter_values.items():
            yield CounterMetricFamily(
                f"waggledance_route_stage_latency_feed_{name}_total",
                (
                    "read-only route-stage latency feed provider-health "
                    f"counter: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        status_metric = GaugeMetricFamily(
            "waggledance_route_stage_latency_feed_status",
            "Current route-stage latency feed status as fixed-state gauges.",
            labels=["status"],
        )
        current_status = feed_health.get("status")
        for status in _ROUTE_STAGE_LATENCY_FEED_STATUSES:
            status_metric.add_metric(
                [status],
                1.0 if current_status == status else 0.0,
            )
        yield status_metric

        reason_metric = GaugeMetricFamily(
            "waggledance_route_stage_latency_feed_failure_reason",
            (
                "Current sanitized route-stage latency feed failure reason as "
                "fixed-state gauges."
            ),
            labels=["reason"],
        )
        current_reason = feed_health.get("last_failure_reason") or "none"
        for reason in _ROUTE_STAGE_LATENCY_FEED_FAILURE_REASONS:
            reason_metric.add_metric(
                [reason],
                1.0 if current_reason == reason else 0.0,
            )
        yield reason_metric

    def _collect_autogrowth_metrics(self, container: Any) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_autogrowth_up",
            "1 if the metrics collector could read autogrowth ticker stats this scrape",
            value=0.0,
        )
        disabled = GaugeMetricFamily(
            "waggledance_autogrowth_background_enabled",
            "1 if the low-risk autogrowth background ticker is configured",
            value=0.0,
        )
        try:
            ticker = getattr(container, "autogrowth_background_ticker", None)
        except Exception as exc:
            logger.warning(
                "metrics: failed to fetch autogrowth_background_ticker: %s",
                exc,
            )
            yield up
            yield from self._collect_autogrowth_alert_feed_metrics(container)
            return
        if ticker is None:
            yield up
            yield disabled
            yield from self._collect_autogrowth_alert_feed_metrics(container)
            return

        try:
            stats_obj = getattr(ticker, "stats", None)
        except Exception as exc:
            logger.warning("metrics: autogrowth ticker stats raised: %s", exc)
            yield up
            yield GaugeMetricFamily(
                "waggledance_autogrowth_background_enabled",
                "1 if the low-risk autogrowth background ticker is configured",
                value=1.0,
            )
            yield from self._collect_autogrowth_alert_feed_metrics(container)
            return
        if stats_obj is None:
            yield up
            yield GaugeMetricFamily(
                "waggledance_autogrowth_background_enabled",
                "1 if the low-risk autogrowth background ticker is configured",
                value=1.0,
            )
            yield from self._collect_autogrowth_alert_feed_metrics(container)
            return

        yield GaugeMetricFamily(
            "waggledance_autogrowth_up",
            "1 if the metrics collector could read autogrowth ticker stats this scrape",
            value=1.0,
        )
        yield GaugeMetricFamily(
            "waggledance_autogrowth_background_enabled",
            "1 if the low-risk autogrowth background ticker is configured",
            value=1.0,
        )

        gauge_values = {
            "background_running": _safe_getattr(ticker, "is_running", False),
            "background_interval_seconds": _safe_getattr(ticker, "interval_seconds"),
            "background_max_ticks_per_wake": _safe_getattr(
                ticker, "max_ticks_per_wake"
            ),
        }
        for name, value in gauge_values.items():
            numeric = _as_float(value)
            if numeric is None:
                continue
            yield GaugeMetricFamily(
                f"waggledance_autogrowth_{name}",
                f"low-risk autogrowth runtime-boundary gauge: {name}",
                value=numeric,
            )

        for name in _AUTOGROWTH_COUNTER_NAMES:
            val = _safe_getattr(stats_obj, name)
            numeric = _as_float(val)
            if numeric is None:
                continue
            base = name[:-6] if name.endswith("_total") else name
            yield CounterMetricFamily(
                f"waggledance_autogrowth_{base}_total",
                f"low-risk autogrowth runtime-boundary counter: {name}",
                value=numeric,
            )

        yield from self._collect_autogrowth_alert_feed_metrics(container)

    def _collect_autogrowth_alert_feed_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_autogrowth_alert_feed_up",
            "1 if autogrowth alert feed health could be built this scrape.",
            value=0.0,
        )
        try:
            section = _autogrowth_section(container)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "metrics: autogrowth alert feed health raised: %s",
                exc,
            )
            yield up
            return
        alert_state = section.get("alert_state")
        if not isinstance(alert_state, dict):
            yield up
            return
        feed_health = alert_state.get("feed_health")
        if not isinstance(feed_health, dict):
            yield up
            return

        yield GaugeMetricFamily(
            "waggledance_autogrowth_alert_feed_up",
            "1 if autogrowth alert feed health could be built this scrape.",
            value=1.0,
        )

        bool_gauges = {
            "configured": feed_health.get("configured"),
            "available": feed_health.get("available"),
            "cache_enabled": feed_health.get("cache_enabled"),
            "cache_present": feed_health.get("cache_present"),
            "cache_stale": feed_health.get("cache_stale"),
            "backoff_active": feed_health.get("backoff_active"),
            "controls_present": feed_health.get("controls_present"),
            "runtime_authority_granted": feed_health.get("runtime_authority_granted"),
            "external_writes_applied": feed_health.get("external_writes_applied"),
        }
        for name, value in bool_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_autogrowth_alert_feed_{name}",
                (
                    "read-only autogrowth Alertmanager adapter "
                    f"provider-health gauge: {name}"
                ),
                value=_as_bool_float(value),
            )

        numeric_gauges = {
            "cache_ttl_seconds": feed_health.get("cache_ttl_seconds"),
            "failure_backoff_seconds": feed_health.get("failure_backoff_seconds"),
            "timeout_seconds": feed_health.get("timeout_seconds"),
            "max_response_bytes": feed_health.get("max_response_bytes"),
            "last_response_bytes": feed_health.get("last_response_bytes"),
        }
        for name, value in numeric_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_autogrowth_alert_feed_{name}",
                (
                    "read-only autogrowth Alertmanager adapter "
                    f"provider-health gauge: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        counter_values = {
            "cache_hits": feed_health.get("cache_hit_count"),
            "cache_misses": feed_health.get("cache_miss_count"),
            "fetch_successes": feed_health.get("fetch_success_count"),
            "fetch_failures": feed_health.get("fetch_failure_count"),
            "backoff_skips": feed_health.get("backoff_skip_count"),
        }
        for name, value in counter_values.items():
            yield CounterMetricFamily(
                f"waggledance_autogrowth_alert_feed_{name}_total",
                (
                    "read-only autogrowth Alertmanager adapter "
                    f"provider-health counter: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        status_metric = GaugeMetricFamily(
            "waggledance_autogrowth_alert_feed_status",
            (
                "Current autogrowth Alertmanager adapter status as "
                "fixed-state gauges."
            ),
            labels=["status"],
        )
        current_status = feed_health.get("status")
        for status in _AUTOGROWTH_ALERT_FEED_STATUSES:
            status_metric.add_metric(
                [status],
                1.0 if current_status == status else 0.0,
            )
        yield status_metric

        reason_metric = GaugeMetricFamily(
            "waggledance_autogrowth_alert_feed_failure_reason",
            (
                "Current sanitized autogrowth Alertmanager adapter failure "
                "reason as fixed-state gauges."
            ),
            labels=["reason"],
        )
        current_reason = feed_health.get("last_failure_reason") or "none"
        for reason in _AUTOGROWTH_ALERT_FEED_FAILURE_REASONS:
            reason_metric.add_metric(
                [reason],
                1.0 if current_reason == reason else 0.0,
            )
        yield reason_metric

    def _collect_counterfactual_replay_metrics(
        self,
        container: Any,
    ) -> Iterable[Any]:
        snapshot = _counterfactual_replay_snapshot(container)
        try:
            status = summarize_counterfactual_observability(snapshot)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "metrics: counterfactual replay status sanitization raised: %s",
                exc,
            )
            status = summarize_counterfactual_observability(None)

        yield GaugeMetricFamily(
            "waggledance_counterfactual_replay_up",
            (
                "1 if the metrics collector could read a counterfactual replay "
                "status snapshot this scrape."
            ),
            value=_as_bool_float(status.get("source_available")),
        )

        bool_gauges = {
            "same_sample_set": status.get("same_sample_set"),
            "deterministic": status.get("deterministic"),
            "no_delta": status.get("no_delta"),
            "delta_digest_present": status.get("delta_digest_present"),
            "controls_present": status.get("controls_present"),
            "runtime_authority_granted": status.get("runtime_authority_granted"),
            "external_writes_applied": status.get("external_writes_applied"),
            "payload_fields_exported": status.get("payload_fields_exported"),
        }
        for name, value in bool_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_counterfactual_replay_{name}",
                f"read-only counterfactual replay observability gauge: {name}",
                value=_as_bool_float(value),
            )

        numeric_gauges = {
            "sample_count": status.get("sample_count"),
            "divergence_count": status.get("divergence_count"),
            "improvement_count": status.get("improvement_count"),
            "regression_count": status.get("regression_count"),
            "neutral_divergence_count": status.get("neutral_divergence_count"),
        }
        for name, value in numeric_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_counterfactual_replay_{name}",
                f"read-only counterfactual replay observability gauge: {name}",
                value=_as_nonnegative_float(value),
            )

        yield GaugeMetricFamily(
            "waggledance_counterfactual_replay_oracle_agreement_advantage",
            (
                "read-only counterfactual replay observability gauge: "
                "oracle_agreement_advantage"
            ),
            value=_as_finite_float(status.get("oracle_agreement_advantage")),
        )

        status_metric = GaugeMetricFamily(
            "waggledance_counterfactual_replay_status",
            (
                "Current counterfactual replay observability status as "
                "fixed-state gauges."
            ),
            labels=["status"],
        )
        current_status = status.get("status")
        for state in COUNTERFACTUAL_OBSERVABILITY_STATES:
            status_metric.add_metric(
                [state],
                1.0 if current_status == state else 0.0,
            )
        yield status_metric

        direction_metric = GaugeMetricFamily(
            "waggledance_counterfactual_replay_oracle_direction",
            (
                "Current counterfactual replay net oracle-agreement direction "
                "as fixed-state gauges."
            ),
            labels=["direction"],
        )
        current_direction = status.get("net_oracle_agreement_direction")
        for direction in COUNTERFACTUAL_OBSERVABILITY_DIRECTIONS:
            direction_metric.add_metric(
                [direction],
                1.0 if current_direction == direction else 0.0,
            )
        yield direction_metric

    def _collect_magma_handoff_metrics(self, container: Any) -> Iterable[Any]:
        up = GaugeMetricFamily(
            "waggledance_magma_handoff_provider_up",
            "1 if the metrics collector could build MAGMA handoff provider health this scrape.",
            value=0.0,
        )
        try:
            section = _magma_share_import_handoff_section(container)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "metrics: MAGMA handoff provider health raised: %s",
                exc,
            )
            yield up
            return
        provider_health = section.get("provider_health")
        if not isinstance(provider_health, dict):
            yield up
            return

        yield GaugeMetricFamily(
            "waggledance_magma_handoff_provider_up",
            "1 if the metrics collector could build MAGMA handoff provider health this scrape.",
            value=1.0,
        )

        bool_gauges = {
            "provider_configured": provider_health.get("provider_configured"),
            "snapshot_available": provider_health.get("snapshot_available"),
            "snapshot_valid": provider_health.get("snapshot_valid"),
            "history_feed_present": provider_health.get("history_feed_present"),
            "freshness_source_configured": provider_health.get(
                "freshness_source_configured"
            ),
            "freshness_source_available": provider_health.get(
                "freshness_source_available"
            ),
            "freshness_source_valid": provider_health.get("freshness_source_valid"),
            "freshness_source_stale": (
                provider_health.get("feed_staleness_state") == "stale"
            ),
            "controls_present": provider_health.get("controls_present"),
            "runtime_authority_granted": provider_health.get(
                "runtime_authority_granted"
            ),
            "local_paths_recorded": provider_health.get("local_paths_recorded"),
        }
        for name, value in bool_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_magma_handoff_{name}",
                f"read-only MAGMA handoff provider-health gauge: {name}",
                value=_as_bool_float(value),
            )

        numeric_gauges = {
            "snapshot_count": provider_health.get("snapshot_count"),
            "history_limit": provider_health.get("history_limit"),
            "history_retained_count": provider_health.get("history_retained_count"),
            "history_dropped_count": provider_health.get("history_dropped_count"),
            "active_alerts": provider_health.get("active_count"),
            "freshness_warning_after_seconds": provider_health.get(
                "freshness_warning_after_seconds"
            ),
            "freshness_source_item_count": provider_health.get("feed_item_count"),
            "freshness_source_window_seconds": provider_health.get(
                "feed_window_seconds"
            ),
            "payload_files_imported": provider_health.get("payload_files_imported"),
        }
        for name, value in numeric_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_magma_handoff_{name}",
                f"read-only MAGMA handoff provider-health gauge: {name}",
                value=_as_nonnegative_float(value),
            )

        status_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_provider_status",
            "Current MAGMA handoff provider status as fixed-state gauges.",
            labels=["status"],
        )
        current_status = provider_health.get("status")
        for status in _MAGMA_HANDOFF_PROVIDER_STATUSES:
            status_metric.add_metric(
                [status],
                1.0 if current_status == status else 0.0,
            )
        yield status_metric

        snapshot_kind_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_provider_snapshot_kind",
            "Current MAGMA handoff provider snapshot kind as fixed-state gauges.",
            labels=["kind"],
        )
        current_kind = provider_health.get("snapshot_kind")
        for kind in _MAGMA_HANDOFF_SNAPSHOT_KINDS:
            snapshot_kind_metric.add_metric(
                [kind],
                1.0 if current_kind == kind else 0.0,
            )
        yield snapshot_kind_metric

        freshness_state_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_freshness_state",
            "Current MAGMA handoff freshness source state as fixed-state gauges.",
            labels=["state"],
        )
        current_freshness_state = provider_health.get("feed_staleness_state")
        for state in _MAGMA_HANDOFF_FRESHNESS_STATES:
            freshness_state_metric.add_metric(
                [state],
                1.0 if current_freshness_state == state else 0.0,
            )
        yield freshness_state_metric

        active_items = provider_health.get("active")
        if not isinstance(active_items, list):
            active_items = []
        active_alert_ids = {
            item.get("id") for item in active_items if isinstance(item, dict)
        }
        alert_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_provider_alert_active",
            "Current sanitized MAGMA handoff provider alert state by fixed alert id.",
            labels=["alert_id"],
        )
        for alert_id in _MAGMA_HANDOFF_PROVIDER_ALERT_IDS:
            alert_metric.add_metric(
                [alert_id],
                1.0 if alert_id in active_alert_ids else 0.0,
            )
        yield alert_metric

        metrics_alert_state = provider_health.get("metrics_alert_state")
        feed_health = (
            metrics_alert_state.get("feed_health")
            if isinstance(metrics_alert_state, dict)
            else None
        )
        if not isinstance(feed_health, dict):
            feed_health = {}

        bool_gauges = {
            "configured": feed_health.get("configured"),
            "available": feed_health.get("available"),
            "cache_enabled": feed_health.get("cache_enabled"),
            "cache_present": feed_health.get("cache_present"),
            "cache_stale": feed_health.get("cache_stale"),
            "backoff_active": feed_health.get("backoff_active"),
            "controls_present": feed_health.get("controls_present"),
            "runtime_authority_granted": feed_health.get("runtime_authority_granted"),
            "external_writes_applied": feed_health.get("external_writes_applied"),
        }
        for name, value in bool_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_magma_handoff_alert_feed_{name}",
                (
                    "read-only MAGMA handoff Alertmanager adapter "
                    f"provider-health gauge: {name}"
                ),
                value=_as_bool_float(value),
            )

        numeric_gauges = {
            "cache_ttl_seconds": feed_health.get("cache_ttl_seconds"),
            "failure_backoff_seconds": feed_health.get("failure_backoff_seconds"),
            "timeout_seconds": feed_health.get("timeout_seconds"),
            "max_response_bytes": feed_health.get("max_response_bytes"),
            "last_response_bytes": feed_health.get("last_response_bytes"),
        }
        for name, value in numeric_gauges.items():
            yield GaugeMetricFamily(
                f"waggledance_magma_handoff_alert_feed_{name}",
                (
                    "read-only MAGMA handoff Alertmanager adapter "
                    f"provider-health gauge: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        counter_values = {
            "cache_hits": feed_health.get("cache_hit_count"),
            "cache_misses": feed_health.get("cache_miss_count"),
            "fetch_successes": feed_health.get("fetch_success_count"),
            "fetch_failures": feed_health.get("fetch_failure_count"),
            "backoff_skips": feed_health.get("backoff_skip_count"),
        }
        for name, value in counter_values.items():
            yield CounterMetricFamily(
                f"waggledance_magma_handoff_alert_feed_{name}_total",
                (
                    "read-only MAGMA handoff Alertmanager adapter "
                    f"provider-health counter: {name}"
                ),
                value=_as_nonnegative_float(value),
            )

        feed_status_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_alert_feed_status",
            (
                "Current MAGMA handoff Alertmanager adapter status as "
                "fixed-state gauges."
            ),
            labels=["status"],
        )
        current_feed_status = feed_health.get("status")
        for status in _MAGMA_HANDOFF_ALERT_FEED_STATUSES:
            feed_status_metric.add_metric(
                [status],
                1.0 if current_feed_status == status else 0.0,
            )
        yield feed_status_metric

        feed_failure_metric = GaugeMetricFamily(
            "waggledance_magma_handoff_alert_feed_failure_reason",
            (
                "Current sanitized MAGMA handoff Alertmanager adapter "
                "failure reason as fixed-state gauges."
            ),
            labels=["reason"],
        )
        current_reason = feed_health.get("last_failure_reason") or "none"
        for reason in _MAGMA_HANDOFF_ALERT_FEED_FAILURE_REASONS:
            feed_failure_metric.add_metric(
                [reason],
                1.0 if current_reason == reason else 0.0,
            )
        yield feed_failure_metric


def _counterfactual_replay_snapshot(container: Any) -> Any:
    for name in (
        "counterfactual_replay_status",
        "counterfactual_replay_observability",
        "last_counterfactual",
        "counterfactual",
    ):
        value = _safe_getattr(container, name)
        if value is not None:
            return value

    promotion = _safe_getattr(container, "promotion")
    if promotion is not None:
        value = _safe_getattr(promotion, "counterfactual")
        if value is not None:
            return value
    return None


def _build_registry(get_container) -> CollectorRegistry:  # noqa: ANN001
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_WaggleCollector(get_container))  # type: ignore[arg-type]
    return registry


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus text-format metrics endpoint.

    Scraped by Prometheus at the default 15s interval. Output is
    plain text (``text/plain; version=0.0.4; charset=utf-8``) per
    the exposition format. Always returns 200 — a failure to read
    container state surfaces as the ``waggledance_up`` gauge at 0.
    """
    app = request.app

    def _get_container():
        return getattr(app.state, "container", None)

    # Build the registry per-request. The collector is stateless and
    # the registry is cheap; this keeps concurrency simple and avoids
    # cross-request caching subtleties.
    registry = _build_registry(_get_container)
    body = generate_latest(registry)
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
