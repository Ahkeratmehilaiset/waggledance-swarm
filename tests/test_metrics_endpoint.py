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
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from waggledance.adapters.http.middleware.auth import PUBLIC_PATHS, BearerAuthMiddleware
from waggledance.adapters.http.routes.chat import RouteStageRuntimeMetrics
from waggledance.adapters.http.routes.metrics import router as metrics_router
from waggledance.core.magma.share_manifest import (
    IMPORT_REPORT_VERSION,
    build_magma_share_import_peer_review_handoff,
)


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
    def __init__(
        self,
        hex_neighbor_assist,
        autogrowth_background_ticker=None,
        hybrid_retrieval=None,
        route_stage_runtime_metrics=None,
    ) -> None:
        self.hex_neighbor_assist = hex_neighbor_assist
        self.autogrowth_background_ticker = autogrowth_background_ticker
        self.hybrid_retrieval = hybrid_retrieval
        self.route_stage_runtime_metrics = route_stage_runtime_metrics


def _magma_import_report(share_id: str = "magma:share:metrics") -> dict:
    return {
        "report_version": IMPORT_REPORT_VERSION,
        "ok": True,
        "blockers": [],
        "source_receipt_verification_ok": True,
        "context_verified": True,
        "context_drift_detected": False,
        "replay_metadata_only": True,
        "no_authority_import": True,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "share_id": share_id,
        "purpose": "cross_instance_replay",
        "share_manifest_digest": "sha256:" + "a" * 64,
        "source_manifest_digest": "sha256:" + "b" * 64,
        "replay_plan": {
            "mode": "no_authority_metadata_replay",
            "entry_count": 1,
            "entries": [{
                "entry_id": "entry-001",
                "receipt_digest": "sha256:" + "c" * 64,
                "evaluation_result_digest": "sha256:" + "d" * 64,
                "subject_type": "solver_trace",
                "risk_class": "low",
                "expected_gate": "review",
                "actual_gate": "review",
                "verdict": "accepted",
            }],
        },
    }


def _magma_handoff(label: str, minute: int) -> dict:
    return build_magma_share_import_peer_review_handoff(
        import_report=_magma_import_report(),
        operator_decision_id=f"operator:decision:metrics:{label}",
        operator_agent_id=f"operator:metrics:{label}",
        bridge_event_ref=f"bridge:metrics:{label}",
        import_decision="accepted_for_peer_review",
        decision_reason_ref=f"reason:metrics:{label}",
        now_utc=datetime(2026, 5, 28, 9, minute, tzinfo=timezone.utc),
    )


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


def test_metrics_body_contains_route_stage_count_gauges():
    container = _FakeContainer(
        _FakeHexAssist({"enabled": False}),
        hybrid_retrieval=types.SimpleNamespace(enabled=True),
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "# HELP waggledance_route_stage_count" in body
    assert 'waggledance_route_stage_count{group="expected"} 8.0' in body
    assert 'waggledance_route_stage_count{group="enabled"} 7.0' in body
    assert 'waggledance_route_stage_count{group="pre_hex"} 5.0' in body
    assert 'waggledance_route_stage_count{group="hex_backed"} 2.0' in body
    assert 'waggledance_route_stage_count{group="optional"} 2.0' in body
    assert (
        'waggledance_route_stage_count{group="disabled_optional"} 1.0'
        in body
    )


def test_metrics_route_stage_counts_disable_missing_optional_components():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert 'waggledance_route_stage_count{group="expected"} 8.0' in body
    assert 'waggledance_route_stage_count{group="enabled"} 6.0' in body
    assert (
        'waggledance_route_stage_count{group="disabled_optional"} 2.0'
        in body
    )


def test_metrics_body_contains_route_stage_runtime_counters():
    runtime_metrics = RouteStageRuntimeMetrics()
    runtime_metrics.record(
        [
            {
                "stage": "language_detection",
                "query": "WD_IMAGE1_PRIVATE_QUERY_MARKER",
            },
            {"stage": "hot_cache", "hit": False},
            {"stage": "not_an_allowed_stage"},
        ],
        12.5,
    )
    runtime_metrics.record(
        [
            {"stage": "language_detection", "detected_language": "custom"},
            {"stage": "orchestrator_llm_fallback", "source": "llm"},
        ],
        20.0,
    )
    container = _FakeContainer(
        _FakeHexAssist({"enabled": False}),
        hybrid_retrieval=types.SimpleNamespace(enabled=True),
        route_stage_runtime_metrics=runtime_metrics,
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "# HELP waggledance_route_stage_observations_total" in body
    assert (
        'waggledance_route_stage_observations_total{'
        'stage="language_detection"} 2.0'
    ) in body
    assert (
        'waggledance_route_stage_observations_total{stage="hot_cache"} 1.0'
        in body
    )
    assert (
        'waggledance_route_stage_observations_total{'
        'stage="orchestrator_llm_fallback"} 1.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_ms_total{'
        'stage="language_detection"} 32.5'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_ms_total{'
        'stage="hot_cache"} 12.5'
    ) in body
    assert (
        "# HELP waggledance_route_stage_request_latency_histogram_ms"
        in body
    )
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_bucket{'
        'le="50",stage="language_detection"} 2.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_bucket{'
        'le="50",stage="orchestrator_llm_fallback"} 1.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_count{'
        'stage="language_detection"} 2.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_sum{'
        'stage="language_detection"} 32.5'
    ) in body
    assert "not_an_allowed_stage" not in body
    assert "WD_IMAGE1_PRIVATE_QUERY_MARKER" not in body
    assert "query=" not in body


def test_metrics_route_stage_runtime_counters_default_to_zero():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert (
        'waggledance_route_stage_observations_total{'
        'stage="language_detection"} 0.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_ms_total{'
        'stage="language_detection"} 0.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_bucket{'
        'le="+Inf",stage="language_detection"} 0.0'
    ) in body
    assert (
        'waggledance_route_stage_request_latency_histogram_ms_count{'
        'stage="language_detection"} 0.0'
    ) in body


def test_metrics_body_contains_autogrowth_boundary_gauges():
    ticker = types.SimpleNamespace(
        stats=types.SimpleNamespace(
            wakeups_total=2,
            non_idle_ticks=1,
            errors_total=0,
        ),
        is_running=False,
        interval_seconds=30.0,
        max_ticks_per_wake=20,
    )
    container = _FakeContainer(
        _FakeHexAssist({"enabled": True}),
        autogrowth_background_ticker=ticker,
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_autogrowth_up 1.0" in body
    assert "waggledance_autogrowth_background_enabled 1.0" in body
    assert "waggledance_autogrowth_background_running 0.0" in body
    assert "waggledance_autogrowth_background_interval_seconds 30.0" in body
    assert "waggledance_autogrowth_background_max_ticks_per_wake 20.0" in body


def test_metrics_body_contains_autogrowth_boundary_counters():
    ticker = types.SimpleNamespace(
        stats=types.SimpleNamespace(
            wakeups_total=8,
            non_idle_ticks=3,
            errors_total=1,
        ),
        is_running=True,
        interval_seconds=15.0,
        max_ticks_per_wake=4,
    )
    container = _FakeContainer(
        _FakeHexAssist({"enabled": True}),
        autogrowth_background_ticker=ticker,
    )
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_autogrowth_wakeups_total 8.0" in body
    assert "waggledance_autogrowth_non_idle_ticks_total 3.0" in body
    assert "waggledance_autogrowth_errors_total 1.0" in body
    assert "autogrowth_wakeups_total_total" not in body


def test_metrics_body_contains_magma_handoff_provider_health_gauges():
    older = _magma_handoff("older", 0)
    newer = _magma_handoff("newer", 5)
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    container.magma_share_import_handoff_history = [older, newer]
    container.magma_share_import_handoff_feed_freshness = {
        "latest_created_at_utc": older["created_at_utc"],
        "observed_at_utc": "2026-05-28T09:10:00Z",
        "item_count": 2,
        "window_seconds": 300,
        "staleness_state": "stale",
        "source": "C:/private/operator-feed.json",
    }
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_magma_handoff_provider_up 1.0" in body
    assert "waggledance_magma_handoff_provider_configured 1.0" in body
    assert "waggledance_magma_handoff_snapshot_valid 1.0" in body
    assert "waggledance_magma_handoff_history_feed_present 1.0" in body
    assert "waggledance_magma_handoff_history_retained_count 2.0" in body
    assert "waggledance_magma_handoff_history_dropped_count 0.0" in body
    assert "waggledance_magma_handoff_freshness_source_configured 1.0" in body
    assert "waggledance_magma_handoff_freshness_source_valid 1.0" in body
    assert "waggledance_magma_handoff_freshness_source_stale 1.0" in body
    assert "waggledance_magma_handoff_freshness_source_item_count 2.0" in body
    assert "waggledance_magma_handoff_freshness_source_window_seconds 300.0" in body
    assert "waggledance_magma_handoff_controls_present 0.0" in body
    assert "waggledance_magma_handoff_runtime_authority_granted 0.0" in body
    assert "waggledance_magma_handoff_payload_files_imported 0.0" in body
    assert (
        'waggledance_magma_handoff_provider_status{status="warning"} 1.0'
        in body
    )
    assert (
        'waggledance_magma_handoff_provider_snapshot_kind{kind="history"} 1.0'
        in body
    )
    assert 'waggledance_magma_handoff_freshness_state{state="stale"} 1.0' in body
    assert (
        'waggledance_magma_handoff_provider_alert_active{'
        'alert_id="MagmaShareImportHandoffProviderFreshnessWarning"} 1.0'
    ) in body
    assert "operator:decision:metrics" not in body
    assert "C:/private/operator-feed.json" not in body


def test_metrics_magma_handoff_freshness_failure_is_sanitized():
    handoff = _magma_handoff("unavailable", 15)

    class Feed:
        def snapshot(self):
            raise RuntimeError("C:/private/feed-state.json")

    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    container.magma_share_import_handoff_status = handoff
    container.magma_share_import_handoff_feed_freshness = Feed()
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_magma_handoff_provider_up 1.0" in body
    assert "waggledance_magma_handoff_freshness_source_configured 1.0" in body
    assert "waggledance_magma_handoff_freshness_source_available 0.0" in body
    assert "waggledance_magma_handoff_freshness_source_valid 0.0" in body
    assert (
        'waggledance_magma_handoff_provider_alert_active{'
        'alert_id="MagmaShareImportHandoffFreshnessSourceUnavailable"} 1.0'
    ) in body
    assert "C:/private/feed-state.json" not in body
    assert "operator:decision:metrics" not in body


def test_metrics_body_contains_magma_alert_feed_cache_gauges():
    from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
        MagmaHandoffMetricsAlertFeedHttpResponse,
        MagmaHandoffMetricsAlertmanagerFeed,
    )

    calls = []

    def transport(url, headers, timeout_seconds, params):
        calls.append((url, dict(headers), timeout_seconds, dict(params)))
        return MagmaHandoffMetricsAlertFeedHttpResponse(
            body=b"[]",
            content_type="application/json",
            status_code=200,
            source_url=url,
        )

    feed = MagmaHandoffMetricsAlertmanagerFeed(
        alertmanager_base_url="http://127.0.0.1:9093",
        allowed_private_hosts=["127.0.0.1"],
        cache_ttl_seconds=60,
        failure_backoff_seconds=5,
        monotonic=lambda: 0.0,
        transport=transport,
    )
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    container.magma_share_import_handoff_metrics_alert_feed = feed
    client = TestClient(_make_app(container))

    client.get("/metrics")
    body = client.get("/metrics").text

    assert len(calls) == 1
    assert "waggledance_magma_handoff_alert_feed_configured 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_available 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_enabled 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_present 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_backoff_active 0.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_ttl_seconds 60.0" in body
    assert "waggledance_magma_handoff_alert_feed_failure_backoff_seconds 5.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_hits_total 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_misses_total 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_fetch_successes_total 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_fetch_failures_total 0.0" in body
    assert (
        'waggledance_magma_handoff_alert_feed_status{status="nominal"} 1.0'
        in body
    )
    assert (
        'waggledance_magma_handoff_alert_feed_failure_reason{reason="none"} 1.0'
        in body
    )
    assert "127.0.0.1" not in body


def test_metrics_magma_alert_feed_backoff_failure_is_sanitized():
    from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
        MagmaHandoffMetricsAlertFeedHttpResponse,
        MagmaHandoffMetricsAlertmanagerFeed,
    )

    calls = []
    now = [0.0]

    def transport(url, headers, timeout_seconds, params):
        calls.append((url, dict(params)))
        if len(calls) > 1:
            raise RuntimeError("C:/private/alertmanager-token")
        return MagmaHandoffMetricsAlertFeedHttpResponse(
            body=b"[]",
            content_type="application/json",
            status_code=200,
            source_url=url,
        )

    feed = MagmaHandoffMetricsAlertmanagerFeed(
        alertmanager_base_url="http://127.0.0.1:9093",
        allowed_private_hosts=["127.0.0.1"],
        cache_ttl_seconds=1,
        failure_backoff_seconds=10,
        monotonic=lambda: now[0],
        transport=transport,
    )
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    container.magma_share_import_handoff_metrics_alert_feed = feed
    client = TestClient(_make_app(container))

    client.get("/metrics")
    now[0] = 2.0
    client.get("/metrics")
    now[0] = 3.0
    body = client.get("/metrics").text

    assert len(calls) == 2
    assert "waggledance_magma_handoff_alert_feed_available 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_cache_stale 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_backoff_active 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_fetch_successes_total 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_fetch_failures_total 1.0" in body
    assert "waggledance_magma_handoff_alert_feed_backoff_skips_total 1.0" in body
    assert (
        'waggledance_magma_handoff_alert_feed_status{status="warning"} 1.0'
        in body
    )
    assert (
        "waggledance_magma_handoff_alert_feed_failure_reason{"
        'reason="NETWORK_REQUEST_FAILED"} 1.0'
    ) in body
    assert "C:/private/alertmanager-token" not in body
    assert "127.0.0.1" not in body


def test_metrics_reports_autogrowth_disabled_when_ticker_missing():
    container = _FakeContainer(_FakeHexAssist({"enabled": True}))
    client = TestClient(_make_app(container))

    body = client.get("/metrics").text

    assert "waggledance_autogrowth_up 0.0" in body
    assert "waggledance_autogrowth_background_enabled 0.0" in body


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


def test_metrics_hex_assist_property_raising_reports_down():
    class _Container:
        @property
        def hex_neighbor_assist(self):
            raise RuntimeError("constructor failed")

    client = TestClient(_make_app(_Container()))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "waggledance_up 0.0" in resp.text


def test_metrics_autogrowth_property_raising_reports_autogrowth_down():
    class _Container:
        hex_neighbor_assist = _FakeHexAssist({"enabled": True})

        @property
        def autogrowth_background_ticker(self):
            raise RuntimeError("ticker constructor failed")

    client = TestClient(_make_app(_Container()))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "waggledance_up 1.0" in resp.text
    assert "waggledance_autogrowth_up 0.0" in resp.text


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
