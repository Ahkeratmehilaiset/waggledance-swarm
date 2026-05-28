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
from typing import Any, Iterable

from fastapi import APIRouter, Request
from fastapi.responses import Response

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from waggledance.adapters.http.routes.chat import CHAT_ROUTE_STAGE_ORDER

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
        yield from self._collect_route_stage_metrics(container)
        yield from self._collect_route_stage_runtime_metrics(container)
        yield from self._collect_autogrowth_metrics(container)

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
        for stage in CHAT_ROUTE_STAGE_ORDER:
            observed.add_metric(
                [stage],
                _as_float(observations.get(stage)) or 0.0,
            )
            latency.add_metric(
                [stage],
                _as_float(latency_sums.get(stage)) or 0.0,
            )
        yield observed
        yield latency

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
            return
        if ticker is None:
            yield up
            yield disabled
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
            return
        if stats_obj is None:
            yield up
            yield GaugeMetricFamily(
                "waggledance_autogrowth_background_enabled",
                "1 if the low-risk autogrowth background ticker is configured",
                value=1.0,
            )
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
            "background_interval_seconds": _safe_getattr(
                ticker, "interval_seconds"
            ),
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
