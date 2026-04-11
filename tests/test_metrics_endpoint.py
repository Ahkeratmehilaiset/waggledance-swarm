"""Tests for the Prometheus ``/metrics`` endpoint (F5-002).

Verifies:

- ``/metrics`` is reachable without a Bearer token.
- Response is Prometheus text exposition format.
- The documented ``waggledance_up`` gauge is present.
- At least one ``waggledance_hex_*_total`` counter is emitted when the
  container's ``hex_neighbor_assist`` publishes live stats.
- Failure mode: if ``hex_neighbor_assist`` raises or is missing, the
  endpoint still returns 200 with ``waggledance_up 0.0``.
- The endpoint never leaks the api_key.
"""

from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS, BearerAuthMiddleware
from waggledance.adapters.http.routes.metrics import router as metrics_router


API_KEY = "f5002-metrics-sentinel-DO-NOT-LEAK"


def _make_app(container) -> FastAPI:
    app = FastAPI()
    app.state.container = container
    app.add_middleware(BearerAuthMiddleware, api_key=API_KEY)
    app.include_router(metrics_router)
    return app


class _FakeHexAssist:
    def __init__(self, stats: dict) -> None:
        self._stats = stats

    def get_metrics(self) -> dict:
        return dict(self._stats)


class _FakeContainer:
    def __init__(self, hex_neighbor_assist) -> None:
        self.hex_neighbor_assist = hex_neighbor_assist


def test_metrics_is_in_public_paths():
    assert "/metrics" in PUBLIC_PATHS


def test_metrics_no_auth_required_returns_200():
    container = _FakeContainer(
        _FakeHexAssist(
            {
                "enabled": True,
                "cells_loaded": 7,
                "preflight_skips": 12,
            }
        )
    )
    app = _make_app(container)
    client = TestClient(app)

    # No Authorization header on purpose.
    resp = client.get("/metrics")

    assert resp.status_code == 200


def test_metrics_content_type_is_prometheus_text_format():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    resp = client.get("/metrics")

    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]


def test_metrics_body_contains_up_gauge_set_to_one():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    # Gauge HELP + TYPE lines.
    assert "# HELP waggledance_up" in body
    assert "# TYPE waggledance_up gauge" in body
    # Value is 1.0 since the fake get_metrics() succeeded.
    assert "waggledance_up 1.0" in body


def test_metrics_body_contains_hex_mesh_enabled_gauge():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_hex_mesh_enabled 1.0" in body


def test_metrics_body_contains_counter_values():
    container = _FakeContainer(
        _FakeHexAssist(
            {
                "enabled": True,
                "preflight_skips": 42,
                "budget_exhaustions": 3,
                "neighbor_assist_resolutions": 17,
            }
        )
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_hex_preflight_skips_total 42.0" in body
    assert "waggledance_hex_budget_exhaustions_total 3.0" in body
    assert "waggledance_hex_neighbor_assist_resolutions_total 17.0" in body


def test_metrics_body_contains_gauge_values():
    container = _FakeContainer(
        _FakeHexAssist(
            {
                "enabled": True,
                "cells_loaded": 11,
                "quarantined_cells": 2,
            }
        )
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    # These are instantaneous gauges, NOT counters, so no _total suffix.
    assert "waggledance_hex_cells_loaded 11.0" in body
    assert "waggledance_hex_quarantined_cells 2.0" in body


def test_metrics_ignores_non_numeric_stats_silently():
    """A malformed entry in stats must not break the whole scrape."""
    container = _FakeContainer(
        _FakeHexAssist(
            {
                "enabled": True,
                "preflight_skips": "not-a-number",
                "preflight_passes": 5,
            }
        )
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    # The good one is still published.
    assert "waggledance_hex_preflight_passes_total 5.0" in body
    # The bad one is silently dropped (no HELP line).
    assert "waggledance_hex_preflight_skips_total" not in body


def test_metrics_hex_mesh_disabled_reports_zero():
    container = _FakeContainer(_FakeHexAssist({"enabled": False}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_up 1.0" in body
    assert "waggledance_hex_mesh_enabled 0.0" in body


def test_metrics_missing_hex_neighbor_assist_reports_down():
    container = types.SimpleNamespace(hex_neighbor_assist=None)
    client = TestClient(_make_app(container))

    resp = client.get("/metrics")
    assert resp.status_code == 200

    body = resp.text
    # _up must be 0 so operators can alert.
    assert "waggledance_up 0.0" in body
    # No counter lines should be emitted at all.
    assert "waggledance_hex_" not in body


def test_metrics_hex_assist_raising_reports_down():
    class _BoomHex:
        def get_metrics(self):
            raise RuntimeError("chroma offline")

    container = _FakeContainer(_BoomHex())
    client = TestClient(_make_app(container))

    resp = client.get("/metrics")
    assert resp.status_code == 200

    body = resp.text
    assert "waggledance_up 0.0" in body
    # No counters emitted when the source raised.
    assert "waggledance_hex_preflight_skips_total" not in body


def test_metrics_missing_container_attribute_reports_down():
    """``app.state.container`` absent entirely."""
    app = FastAPI()
    # Deliberately do NOT set app.state.container.
    app.add_middleware(BearerAuthMiddleware, api_key=API_KEY)
    app.include_router(metrics_router)
    client = TestClient(app)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "waggledance_up 0.0" in resp.text


def test_metrics_body_never_leaks_api_key():
    container = _FakeContainer(
        _FakeHexAssist(
            {"enabled": True, "preflight_skips": 1}
        )
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert API_KEY not in body
    assert "api_key" not in body.lower()


def test_metrics_does_not_emit_default_process_collectors():
    """We use a private CollectorRegistry to avoid leaking FD/GC/memory
    counters that prometheus-client enables by default on the global
    registry. Operators explicitly asked for waggledance metrics only."""
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    # Sanity-check that none of the default python collectors leaked.
    for forbidden in (
        "python_gc_objects_collected_total",
        "process_virtual_memory_bytes",
        "process_open_fds",
        "process_cpu_seconds_total",
    ):
        assert forbidden not in body, f"default collector leaked: {forbidden}"


@pytest.mark.parametrize(
    ("source_key", "exposed_name"),
    [
        ("preflight_skips", "waggledance_hex_preflight_skips_total"),
        ("preflight_passes", "waggledance_hex_preflight_passes_total"),
        ("skipped_local_attempts", "waggledance_hex_skipped_local_attempts_total"),
        ("skipped_neighbor_attempts", "waggledance_hex_skipped_neighbor_attempts_total"),
        ("budget_exhaustions", "waggledance_hex_budget_exhaustions_total"),
        ("origin_cell_resolutions", "waggledance_hex_origin_cell_resolutions_total"),
        ("local_only_resolutions", "waggledance_hex_local_only_resolutions_total"),
        ("neighbor_assist_resolutions", "waggledance_hex_neighbor_assist_resolutions_total"),
        ("global_escalations", "waggledance_hex_global_escalations_total"),
        ("llm_last_resolutions", "waggledance_hex_llm_last_resolutions_total"),
        ("completed_hex_neighbor_batches", "waggledance_hex_completed_hex_neighbor_batches_total"),
        # Source key already ends in _total; must NOT be double-suffixed.
        ("neighbors_consulted_total", "waggledance_hex_neighbors_consulted_total"),
        ("self_heal_events", "waggledance_hex_self_heal_events_total"),
        ("magma_traces_written", "waggledance_hex_magma_traces_written_total"),
        ("ttl_exhaustions", "waggledance_hex_ttl_exhaustions_total"),
    ],
)
def test_metrics_documents_all_v356_efficiency_counters(source_key, exposed_name):
    stats = {"enabled": True, source_key: 1}
    container = _FakeContainer(_FakeHexAssist(stats))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert f"{exposed_name} 1.0" in body
    # Double-suffix regression: neighbors_consulted_total must not
    # appear as ``neighbors_consulted_total_total``.
    assert "neighbors_consulted_total_total" not in body
