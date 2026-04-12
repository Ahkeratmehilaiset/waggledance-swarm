"""Prometheus ``/metrics`` endpoint.

Closes F5-002 from the Release Polish Run 20260409_054702 by giving
the ``prometheus-client`` dependency a real job: expose the existing
v3.5.6 efficiency counters (preflight skips, budget exhaustions,
neighbor-assist ratios, etc.) in Prometheus text format so the
operator's standard observability stack can scrape them.

Design notes
------------

- We use a **separate**, dedicated ``CollectorRegistry`` instead of
  the default global one. That keeps waggledance metrics isolated
  from any process-level collectors prometheus-client enables by
  default, which would leak file-descriptor counts and Python GC
  stats that operators haven't asked for.
- Metrics are collected on-demand via a tiny ``Collector`` subclass
  that reads the container's ``hex_neighbor_assist.get_metrics()``
  each time ``/metrics`` is scraped. This avoids the "metrics go
  stale" trap of snapshotting into Gauges at startup.
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

        hex_assist = getattr(container, "hex_neighbor_assist", None)
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
            try:
                numeric = float(val)
            except (TypeError, ValueError):
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
            try:
                numeric = float(val)
            except (TypeError, ValueError):
                continue
            yield GaugeMetricFamily(
                f"waggledance_hex_{name}",
                f"v3.5.6 hex-mesh gauge: {name}",
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
