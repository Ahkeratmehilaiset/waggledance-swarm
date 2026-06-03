"""Legacy Consolidation tests — Phases 1-4.

Phase 1: Container wiring, /api/ops, /api/settings, no split-brain
Phase 2: Hologram Ops tab FlexHW + AutoThrottle rendering
Phase 3: MAGMA/graph/trust/cross-agent/analytics endpoints
Phase 4: Backend archival verification
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
from waggledance.core.autonomy.resource_kernel import ResourceKernel


# ── Helpers ──────────────────────────────────────────────

def _make_container(tier="standard"):
    """Create a stub Container with explicit tier (avoids nvidia-smi)."""
    s = WaggleSettings(profile="HOME", hardware_tier=tier, api_key="test-key-123")
    return Container(settings=s, stub=True)


# Patch ElasticScaler.detect() to avoid real hardware calls in tests
_FAKE_HARDWARE = MagicMock(
    gpu_name="NVIDIA RTX A2000",
    gpu_vram_gb=8.0,
    cpu_name="Intel i7-1270P",
    cpu_cores=12,
    cpu_threads=16,
    ram_gb=16.0,
    disk_free_gb=45.0,
    os_name="Windows",
)


def _patch_scaler(container):
    """Replace elastic_scaler with a mocked version."""
    from core.elastic_scaler import TierConfig

    mock_scaler = MagicMock()
    mock_scaler.hardware = _FAKE_HARDWARE
    mock_scaler.tier = TierConfig(
        tier="standard",
        chat_model="phi4-mini",
        bg_model="llama3.2:1b",
        max_agents=6,
        vision=False,
        micro_tier="V1+V2+V3",
        hardware=_FAKE_HARDWARE,
        reason="VRAM=8.0GB>=4GB, RAM=16GB>=16GB",
    )
    mock_scaler.get_vram_usage_pct.return_value = 62.5
    mock_scaler.detect.return_value = mock_scaler.tier

    # Override the cached_property
    container.__dict__["elastic_scaler"] = mock_scaler
    return mock_scaler


# ── Container wiring ─────────────────────────────────────

class TestContainerInfrastructureWiring:
    """Verify Container wires ElasticScaler, AdaptiveThrottle, ResourceGuard."""

    def test_container_has_elastic_scaler(self):
        c = _make_container()
        _patch_scaler(c)
        assert c.elastic_scaler is not None

    def test_container_has_adaptive_throttle(self):
        c = _make_container()
        _patch_scaler(c)
        from core.adaptive_throttle import AdaptiveThrottle
        assert isinstance(c.adaptive_throttle, AdaptiveThrottle)

    def test_container_has_resource_guard(self):
        c = _make_container()
        _patch_scaler(c)
        from core.resource_guard import ResourceGuard
        assert isinstance(c.resource_guard, ResourceGuard)

    def test_resource_kernel_receives_elastic_scaler(self):
        c = _make_container()
        mock = _patch_scaler(c)
        svc = c.autonomy_service
        rk = svc._resource_kernel
        assert rk._elastic_scaler is mock

    def test_resource_kernel_receives_adaptive_throttle(self):
        c = _make_container()
        _patch_scaler(c)
        svc = c.autonomy_service
        rk = svc._resource_kernel
        assert rk._adaptive_throttle is c.adaptive_throttle

    def test_resource_kernel_receives_resource_guard(self):
        c = _make_container()
        _patch_scaler(c)
        svc = c.autonomy_service
        rk = svc._resource_kernel
        assert hasattr(rk, "resource_guard")
        from core.resource_guard import ResourceGuard
        assert isinstance(rk.resource_guard, ResourceGuard)


# ── Tier detection (no split-brain) ──────────────────────

class TestNoSplitBrain:
    """Verify single source of truth for hardware tier."""

    def test_settings_loader_no_detect_method(self):
        """SettingsLoader must NOT have _detect_hardware_tier."""
        assert not hasattr(WaggleSettings, "_detect_hardware_tier")

    def test_auto_tier_returns_auto(self):
        """get_hardware_tier() returns 'auto' string for container to resolve."""
        s = WaggleSettings(hardware_tier="auto")
        assert s.get_hardware_tier() == "auto"

    def test_container_resolves_auto_via_elastic_scaler(self):
        """Container resolves 'auto' tier via ElasticScaler.detect()."""
        c = _make_container("auto")
        _patch_scaler(c)
        svc = c.autonomy_service
        # Should get tier from ElasticScaler, not from settings
        assert svc._resource_kernel.tier.value == "standard"

    def test_container_uses_explicit_tier_when_set(self):
        """Container uses explicit tier when not 'auto'."""
        c = _make_container("professional")
        _patch_scaler(c)
        svc = c.autonomy_service
        assert svc._resource_kernel.tier.value == "professional"


# ── /api/ops FlexHW + Throttle ───────────────────────────

class TestApiOpsExtended:
    """Verify /api/ops returns flexhw and throttle sections."""

    @classmethod
    def _get_client(cls):
        from starlette.testclient import TestClient
        c = _make_container()
        _patch_scaler(c)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)
        return client, c._settings.api_key

    def test_ops_returns_flexhw_section(self):
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "flexhw" in data
        fhw = data["flexhw"]
        assert fhw.get("tier") == "standard"
        assert fhw.get("gpu_name") == "NVIDIA RTX A2000"
        assert fhw.get("gpu_vram_gb") == 8.0
        assert fhw.get("cpu_cores") == 12
        assert fhw.get("ram_gb") == 16.0

    def test_ops_flexhw_has_tiers_list(self):
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        tiers = data["flexhw"].get("tiers", [])
        assert len(tiers) == 5
        names = [t["name"] for t in tiers]
        assert names == ["minimal", "light", "standard", "professional", "enterprise"]

    def test_ops_flexhw_active_tier_index(self):
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        assert data["flexhw"]["active_tier_index"] == 2  # standard

    def test_ops_returns_throttle_section(self):
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        assert "throttle" in data
        throttle = data["throttle"]
        assert "machine_class" in throttle
        assert "max_concurrent" in throttle

    def test_ops_returns_autogrowth_section(self):
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        assert "autogrowth" in data
        autogrowth = data["autogrowth"]
        for field in [
            "enabled",
            "up",
            "running",
            "interval_seconds",
            "max_ticks_per_wake",
            "wakeups_total",
            "non_idle_ticks",
            "errors_total",
            "alert_state",
        ]:
            assert field in autogrowth
        assert isinstance(autogrowth["enabled"], bool)
        assert isinstance(autogrowth["running"], bool)
        assert autogrowth["alert_state"]["source"] == "local_ops_snapshot"
        assert autogrowth["alert_state"]["controls_present"] is False
        assert "last_error" not in autogrowth
        route_stage_latency = data["route_stage_latency"]
        assert route_stage_latency["source"] == "prometheus_query_templates"
        assert route_stage_latency["controls_present"] is False
        assert route_stage_latency["prometheus_alertmanager_feed"] is False
        assert route_stage_latency["feed_state"]["source"] == "not_configured"
        assert route_stage_latency["feed_state"]["controls_present"] is False
        assert route_stage_latency["feed_state"]["active_count"] == 0
        assert "waggledance_route_stage_request_latency_histogram_ms_bucket" in (
            route_stage_latency["metrics"]
        )
        assert any(
            "histogram_quantile(0.95" in panel["query"]
            for panel in route_stage_latency["panels"]
        )
        assert any(
            item["id"] == "RouteStageLatencyP99Critical"
            for item in route_stage_latency["alert_thresholds"]
        )
        assert "route_stage_trace" not in str(route_stage_latency)
        magma_handoff = data["magma_share_import_handoff"]
        assert magma_handoff["source"] == "not_configured"
        assert magma_handoff["status"] == "not_configured"
        assert magma_handoff["controls_present"] is False
        assert magma_handoff["runtime_authority_granted"] is False
        assert magma_handoff["payload_files_imported"] == 0
        assert magma_handoff["active_count"] == 0
        assert magma_handoff["provider_health"]["source"] == "not_configured"
        assert magma_handoff["provider_health"]["provider_configured"] is False
        assert magma_handoff["provider_health"]["snapshot_valid"] is False
        assert magma_handoff["provider_health"]["controls_present"] is False
        metrics_alert_state = (
            magma_handoff["provider_health"]["metrics_alert_state"]
        )
        assert metrics_alert_state["source"] == "not_configured"
        assert metrics_alert_state["prometheus_alertmanager_feed"] is False
        assert metrics_alert_state["controls_present"] is False
        assert metrics_alert_state["active_count"] == 0
        assert len(metrics_alert_state["slo_panels"]) == 4
        assert metrics_alert_state["slo_panels"][0]["id"] == (
            "magma_alert_feed_availability_5m"
        )
        assert metrics_alert_state["slo_panels"][0]["status"] == (
            "not_configured"
        )
        assert metrics_alert_state["drill_evidence"]["controls_present"] is False
        assert (
            metrics_alert_state["drill_evidence"]["external_writes_applied"]
            is False
        )
        assert {
            item["id"]
            for item in magma_handoff["provider_health"]["alert_thresholds"]
        } == {
            "MagmaShareImportHandoffProviderFreshnessWarning",
            "MagmaShareImportHandoffProviderRetentionDropped",
            "MagmaShareImportHandoffProviderRetentionLimitReached",
        }

    def test_ops_autogrowth_section_hides_ticker_exception_details(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _autogrowth_section,
        )

        class BrokenContainer:
            @property
            def autogrowth_background_ticker(self):
                raise RuntimeError("private scheduler detail")

        section = _autogrowth_section(BrokenContainer())

        assert section["enabled"] is False
        assert section["up"] is False
        assert section["alert_state"]["status"] == "warning"
        assert section["alert_state"]["active"][0]["id"] == "AutogrowthSourceDown"
        assert "private scheduler detail" not in str(section)

    def test_ops_autogrowth_alert_state_reports_errors_without_details(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _autogrowth_section,
        )

        class Stats:
            wakeups_total = 2
            non_idle_ticks = 1
            errors_total = 1
            last_error = "private stack trace"

        class Ticker:
            is_running = True
            interval_seconds = 30.0
            max_ticks_per_wake = 20
            stats = Stats()

        class Container:
            autogrowth_background_ticker = Ticker()

        section = _autogrowth_section(Container())
        alert_state = section["alert_state"]
        alert_ids = {item["id"] for item in alert_state["active"]}

        assert section["up"] is True
        assert alert_state["status"] == "warning"
        assert alert_state["severity"] == "warning"
        assert "AutogrowthErrorsObserved" in alert_ids
        assert "AutogrowthSourceDown" not in alert_ids
        assert "private stack trace" not in str(section)

    def test_ops_autogrowth_alert_feed_sanitizes_snapshot(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _autogrowth_section,
        )

        class Stats:
            wakeups_total = 2
            non_idle_ticks = 1
            errors_total = 0

        class Ticker:
            is_running = True
            interval_seconds = 30.0
            max_ticks_per_wake = 20
            stats = Stats()

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-06-03T20:45:00Z",
                    "active_alerts": [
                        {
                            "labels": {
                                "alertname": "AutogrowthErrorBurst",
                                "severity": "critical",
                                "host": "prod-db",
                            },
                            "status": {"state": "active"},
                            "annotations": {
                                "summary": "PRIVATE path=C:/private",
                            },
                            "generatorURL": "http://alertmanager/private",
                            "value": "3",
                        },
                        {
                            "labels": {"alertname": "UnknownAutogrowthAlert"},
                            "status": {"state": "active"},
                            "annotations": {"summary": "PRIVATE_UNKNOWN"},
                        },
                        {
                            "labels": {"alertname": "AutogrowthWakeupBurst"},
                            "status": {"state": "resolved"},
                        },
                    ],
                    "provider_health": {
                        "source": "alertmanager_adapter",
                        "status": "nominal",
                        "configured": True,
                        "available": True,
                        "cache_enabled": True,
                        "cache_present": True,
                        "controls_present": True,
                        "runtime_authority_granted": True,
                        "external_writes_applied": True,
                        "fetch_success_count": 1,
                    },
                }

        class Container:
            autogrowth_background_ticker = Ticker()
            autogrowth_alert_feed = Feed()

        section = _autogrowth_section(Container())
        alert_state = section["alert_state"]
        feed_health = alert_state["feed_health"]
        serialized = str(section)

        assert alert_state["source"] == "prometheus_alertmanager_snapshot"
        assert alert_state["prometheus_alertmanager_feed"] is True
        assert alert_state["updated_at"] == "2026-06-03T20:45:00Z"
        assert alert_state["status"] == "critical"
        assert alert_state["severity"] == "critical"
        assert alert_state["controls_present"] is False
        assert alert_state["active_count"] == 1
        assert alert_state["active"][0]["id"] == "AutogrowthErrorBurst"
        assert alert_state["active"][0]["metric"] == (
            "waggledance_autogrowth_errors_total"
        )
        assert alert_state["active"][0]["value"] == 3.0
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert feed_health["external_writes_applied"] is False
        assert "prod-db" not in serialized
        assert "PRIVATE" not in serialized
        assert "C:/private" not in serialized
        assert "generatorURL" not in serialized

    def test_autogrowth_alertmanager_feed_reads_operator_alerts(self):
        from waggledance.adapters.http.autogrowth_alert_feed import (
            AutogrowthAlertFeedHttpResponse,
            AutogrowthAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _autogrowth_section,
        )

        calls = []

        def transport(url, headers, timeout_seconds, params):
            calls.append((url, dict(headers), timeout_seconds, dict(params)))
            body = [
                {
                    "labels": {
                        "alertname": "AutogrowthWakeupBurst",
                        "severity": "warning",
                        "host": "prod-db",
                    },
                    "status": {"state": "active"},
                    "annotations": {"summary": "PRIVATE_ANNOTATION"},
                    "generatorURL": "http://alertmanager/private",
                    "value": "41",
                },
                {
                    "labels": {"alertname": "UnknownAutogrowthAlert"},
                    "status": {"state": "active"},
                },
            ]
            return AutogrowthAlertFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                status_code=200,
                source_url=url,
            )

        feed = AutogrowthAlertmanagerFeed(
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            timeout_seconds=2,
            cache_ttl_seconds=60,
            transport=transport,
        )

        class Stats:
            wakeups_total = 2
            non_idle_ticks = 1
            errors_total = 0

        class Ticker:
            is_running = True
            interval_seconds = 30.0
            max_ticks_per_wake = 20
            stats = Stats()

        class Container:
            autogrowth_background_ticker = Ticker()
            autogrowth_alert_feed = feed

        section = _autogrowth_section(Container())
        cached_section = _autogrowth_section(Container())
        alert_state = cached_section["alert_state"]
        feed_health = alert_state["feed_health"]
        serialized = str(section)

        assert len(calls) == 1
        assert calls[0][0].endswith("/api/v2/alerts")
        assert calls[0][1]["Accept"] == "application/json"
        assert calls[0][1]["User-Agent"] == "waggledance-autogrowth-alert-feed/3.8"
        assert calls[0][2] == 2
        assert calls[0][3] == {}
        assert alert_state["source"] == "prometheus_alertmanager_snapshot"
        assert alert_state["prometheus_alertmanager_feed"] is True
        assert alert_state["active_count"] == 1
        assert alert_state["active"][0]["id"] == "AutogrowthWakeupBurst"
        assert alert_state["active"][0]["value"] == 41.0
        assert feed_health["status"] == "nominal"
        assert feed_health["configured"] is True
        assert feed_health["available"] is True
        assert feed_health["cache_hit_count"] == 1
        assert feed_health["cache_miss_count"] == 1
        assert feed_health["fetch_success_count"] == 1
        assert feed_health["fetch_failure_count"] == 0
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert "prod-db" not in serialized
        assert "PRIVATE_ANNOTATION" not in serialized
        assert "generatorURL" not in serialized

    def test_autogrowth_alert_feed_failure_is_sanitized(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _autogrowth_section,
        )

        class Feed:
            def snapshot(self):
                raise RuntimeError("C:\\private\\alertmanager\\token")

        class Container:
            autogrowth_background_ticker = None
            autogrowth_alert_feed = Feed()

        section = _autogrowth_section(Container())
        alert_state = section["alert_state"]

        assert alert_state["source"] == "prometheus_alertmanager_unavailable"
        assert alert_state["status"] == "warning"
        assert alert_state["severity"] == "warning"
        assert alert_state["controls_present"] is False
        assert alert_state["active"][0]["id"] == "AutogrowthAlertFeedUnavailable"
        assert "C:\\private" not in str(section)

    def test_container_wires_configured_autogrowth_alert_feed(self):
        from waggledance.adapters.http.autogrowth_alert_feed import (
            AutogrowthAlertmanagerFeed,
        )

        settings = WaggleSettings(
            profile="HOME",
            api_key="test-key-123",
            _extras={
                "autogrowth_alert_feed": {
                    "enabled": True,
                    "alertmanager_base_url": "https://alerts.example",
                    "timeout_s": 2,
                    "max_response_bytes": 1000,
                    "cache_ttl_s": 11,
                    "failure_backoff_s": 12,
                },
            },
        )
        container = Container(settings=settings, stub=True)

        assert isinstance(container.autogrowth_alert_feed, AutogrowthAlertmanagerFeed)
        health = container.autogrowth_alert_feed.provider_health()
        assert health["cache_ttl_seconds"] == 11
        assert health["failure_backoff_seconds"] == 12

    def test_ops_magma_handoff_section_is_read_only_and_sanitized(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )
        from waggledance.core.magma.share_manifest import (
            IMPORT_REPORT_VERSION,
            build_magma_share_import_peer_review_handoff,
        )

        report = {
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
            "share_id": "magma:share:ops-summary",
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
        handoff = build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id="operator:decision:ops-summary",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
            import_decision="accepted_for_peer_review",
            decision_reason_ref="reason:cross_instance_replay_review",
        )

        class Container:
            pass

        container = Container()
        container.magma_share_import_handoff_status = handoff
        section = _magma_share_import_handoff_section(container)

        assert section["status"] == "ready_for_peer_review"
        assert section["controls_present"] is False
        assert section["runtime_authority_granted"] is False
        assert section["payload_files_imported"] == 0
        assert section["local_paths_recorded"] is False
        assert section["active_count"] == 1
        provider_health = section["provider_health"]
        assert provider_health["source"] == "local_ops_snapshot"
        assert provider_health["status"] == "nominal"
        assert provider_health["provider_configured"] is True
        assert provider_health["snapshot_available"] is True
        assert provider_health["snapshot_valid"] is True
        assert provider_health["history_feed_present"] is False
        assert provider_health["snapshot_kind"] == "handoff"
        assert provider_health["snapshot_count"] == 1
        assert provider_health["active_count"] == 0
        assert provider_health["latest_created_at_utc"] == handoff["created_at_utc"]
        assert provider_health["freshness_source"] == "not_configured"
        assert provider_health["freshness_source_configured"] is False
        assert provider_health["freshness_source_valid"] is False
        assert provider_health["feed_staleness_state"] == "unknown"
        assert any(
            item["id"] == "MagmaShareImportHandoffProviderFreshnessWarning"
            for item in provider_health["alert_thresholds"]
        )
        assert provider_health["controls_present"] is False
        assert provider_health["runtime_authority_granted"] is False
        assert section["latest"]["share_id"] == "magma:share:ops-summary"
        assert section["latest"]["entry_count"] == 1
        serialized = json.dumps(section)
        assert "operator:decision:ops-summary" not in serialized
        assert "C:\\private" not in serialized

        for path, value in [
            (("share_id",), "C:/private/share"),
            (
                ("operator_ownership", "operator_agent_id"),
                "C:/private/agent",
            ),
            (
                ("operator_ownership", "decision_reason_ref"),
                "C:/private/reason",
            ),
        ]:
            tampered = json.loads(json.dumps(handoff))
            target = tampered
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            container.magma_share_import_handoff_status = tampered
            invalid_section = _magma_share_import_handoff_section(container)
            assert invalid_section["source"] == (
                "magma_share_import_handoff_invalid"
            )
            assert invalid_section["controls_present"] is False
            assert invalid_section["runtime_authority_granted"] is False
            assert invalid_section["provider_health"]["reason"] == (
                "snapshot_invalid"
            )
            assert invalid_section["provider_health"]["snapshot_valid"] is False
            assert invalid_section["provider_health"]["controls_present"] is False
            assert value not in str(invalid_section)

    def test_ops_magma_handoff_section_accepts_bounded_history(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )
        from waggledance.core.magma.share_manifest import (
            IMPORT_REPORT_VERSION,
            build_magma_share_import_peer_review_handoff,
        )

        report = {
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
            "share_id": "magma:share:ops-history",
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
        older = build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id="operator:decision:ops-history:older",
            operator_agent_id="operator:ops-history:older",
            bridge_event_ref="bridge:ops-history:older",
            import_decision="deferred_for_operator_review",
            decision_reason_ref="reason:ops-history:older",
            now_utc=datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc),
        )
        newer = build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id="operator:decision:ops-history:newer",
            operator_agent_id="operator:ops-history:newer",
            bridge_event_ref="bridge:ops-history:newer",
            import_decision="accepted_for_peer_review",
            decision_reason_ref="reason:ops-history:newer",
            now_utc=datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc),
        )

        class Container:
            magma_share_import_handoff_history = [older, newer]
            magma_share_import_handoff_feed_freshness = {
                "latest_created_at_utc": newer["created_at_utc"],
                "observed_at_utc": "2026-05-28T09:05:00Z",
                "item_count": 2,
                "window_seconds": 300,
                "staleness_state": "fresh",
                "source": "C:/private/operator-feed.json",
            }

        section = _magma_share_import_handoff_section(Container())

        assert section["handoff_count"] == 2
        assert section["history_retained_count"] == 2
        assert section["history_truncated"] is False
        assert section["latest"]["handoff_id"] == newer["handoff_id"]
        assert [item["handoff_id"] for item in section["history"]] == [
            newer["handoff_id"],
            older["handoff_id"],
        ]
        assert section["controls_present"] is False
        assert section["runtime_authority_granted"] is False
        assert section["payload_files_imported"] == 0
        provider_health = section["provider_health"]
        assert provider_health["status"] == "nominal"
        assert provider_health["history_feed_present"] is True
        assert provider_health["snapshot_kind"] == "history"
        assert provider_health["snapshot_count"] == 2
        assert provider_health["history_retained_count"] == 2
        assert provider_health["history_dropped_count"] == 0
        assert provider_health["active_count"] == 0
        assert provider_health["freshness_source"] == (
            "operator_peer_review_handoff_feed"
        )
        assert provider_health["freshness_source_configured"] is True
        assert provider_health["freshness_source_available"] is True
        assert provider_health["freshness_source_valid"] is True
        assert provider_health["freshness_source_precedence"] == "operator_feed"
        assert provider_health["feed_latest_created_at_utc"] == (
            newer["created_at_utc"]
        )
        assert provider_health["feed_observed_at_utc"] == "2026-05-28T09:05:00Z"
        assert provider_health["feed_item_count"] == 2
        assert provider_health["feed_window_seconds"] == 300
        assert provider_health["feed_staleness_state"] == "fresh"
        freshness_threshold = next(
            item for item in provider_health["alert_thresholds"]
            if item["id"] == "MagmaShareImportHandoffProviderFreshnessWarning"
        )
        assert freshness_threshold["source"] == (
            "operator_peer_review_handoff_feed.latest_created_at_utc"
        )
        assert freshness_threshold["latest_created_at_utc"] == (
            newer["created_at_utc"]
        )
        assert provider_health["controls_present"] is False
        assert provider_health["payload_files_imported"] == 0
        serialized = json.dumps(section)
        assert "operator:decision:ops-history" not in serialized
        assert "C:\\private" not in serialized
        assert "C:/private/operator-feed.json" not in serialized

        class StaleFreshnessContainer:
            magma_share_import_handoff_history = [older, newer]
            magma_share_import_handoff_feed_freshness = {
                "latest_created_at_utc": older["created_at_utc"],
                "observed_at_utc": "2026-05-28T09:05:00Z",
                "item_count": 2,
                "staleness_state": "stale",
            }

        stale_section = _magma_share_import_handoff_section(
            StaleFreshnessContainer()
        )
        stale_health = stale_section["provider_health"]
        stale_alert_ids = {item["id"] for item in stale_health["active"]}

        assert stale_health["status"] == "warning"
        assert stale_health["severity"] == "warning"
        assert "MagmaShareImportHandoffProviderFreshnessWarning" in (
            stale_alert_ids
        )
        assert stale_health["controls_present"] is False
        assert stale_health["runtime_authority_granted"] is False

        class InvalidFreshnessContainer:
            magma_share_import_handoff_history = [older, newer]
            magma_share_import_handoff_feed_freshness = {
                "latest_created_at_utc": "C:/private/feed.json",
                "observed_at_utc": "2026-05-28T09:05:00Z",
                "staleness_state": "fresh",
            }

        invalid_freshness_section = _magma_share_import_handoff_section(
            InvalidFreshnessContainer()
        )
        invalid_freshness_health = invalid_freshness_section["provider_health"]

        assert invalid_freshness_health["status"] == "warning"
        assert invalid_freshness_health["freshness_source_configured"] is True
        assert invalid_freshness_health["freshness_source_valid"] is False
        assert invalid_freshness_health["active"][0]["id"] == (
            "MagmaShareImportHandoffFreshnessSourceInvalid"
        )
        assert invalid_freshness_health["controls_present"] is False
        assert "C:/private/feed.json" not in str(invalid_freshness_section)

        retained_source = [
            build_magma_share_import_peer_review_handoff(
                import_report=report,
                operator_decision_id=f"operator:decision:ops-history:{idx}",
                operator_agent_id=f"operator:ops-history:{idx}",
                bridge_event_ref=f"bridge:ops-history:{idx}",
                import_decision="accepted_for_peer_review",
                decision_reason_ref=f"reason:ops-history:{idx}",
                now_utc=datetime(2026, 5, 28, 10, idx, tzinfo=timezone.utc),
            )
            for idx in range(6)
        ]

        class RetentionContainer:
            magma_share_import_handoff_history = retained_source

        retention_section = _magma_share_import_handoff_section(
            RetentionContainer()
        )
        retention_health = retention_section["provider_health"]
        retention_alert_ids = {
            item["id"] for item in retention_health["active"]
        }

        assert retention_section["history_retained_count"] == 5
        assert retention_section["history_dropped_count"] == 1
        assert retention_section["history_truncated"] is True
        assert retention_health["status"] == "warning"
        assert retention_health["severity"] == "warning"
        assert retention_health["active_count"] == 1
        assert "MagmaShareImportHandoffProviderRetentionDropped" in (
            retention_alert_ids
        )
        assert retention_health["controls_present"] is False
        assert retention_health["runtime_authority_granted"] is False
        assert retention_health["payload_files_imported"] == 0
        assert any(
            item["id"] == "MagmaShareImportHandoffProviderRetentionDropped"
            for item in retention_health["alert_thresholds"]
        )

        tampered = json.loads(json.dumps(older))
        tampered["operator_ownership"]["bridge_event_ref"] = "C:/private/bridge"

        class TamperedContainer:
            magma_share_import_handoff_history = [newer, tampered]

        invalid_section = _magma_share_import_handoff_section(TamperedContainer())
        assert invalid_section["source"] == "magma_share_import_handoff_invalid"
        assert invalid_section["controls_present"] is False
        assert invalid_section["runtime_authority_granted"] is False
        assert invalid_section["provider_health"]["reason"] == "snapshot_invalid"
        assert invalid_section["provider_health"]["snapshot_kind"] == "history"
        assert invalid_section["provider_health"]["snapshot_count"] == 2
        assert invalid_section["provider_health"]["active"][0]["id"] == (
            "MagmaShareImportHandoffProviderInvalid"
        )
        assert "C:/private/bridge" not in str(invalid_section)

    def test_ops_magma_handoff_failure_hides_exception_details(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        class Feed:
            def snapshot(self):
                raise RuntimeError("C:\\private\\secret\\handoff.json")

        class Container:
            magma_share_import_handoff_status = Feed()

        section = _magma_share_import_handoff_section(Container())

        assert section["source"] == "magma_share_import_handoff_unavailable"
        assert section["status"] == "warning"
        assert section["controls_present"] is False
        assert section["runtime_authority_granted"] is False
        assert section["active"][0]["id"] == "MagmaShareImportHandoffUnavailable"
        assert section["provider_health"]["reason"] == "provider_unavailable"
        assert section["provider_health"]["snapshot_available"] is False
        assert section["provider_health"]["snapshot_valid"] is False
        assert section["provider_health"]["active"][0]["id"] == (
            "MagmaShareImportHandoffProviderUnavailable"
        )
        assert "C:\\private" not in str(section)

    def test_ops_magma_handoff_metrics_alert_state_sanitizes_snapshot(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        class Container:
            magma_share_import_handoff_metrics_alert_feed = {
                "updated_at": "2026-05-28T10:30:00Z",
                "active_alerts": [
                    {
                        "labels": {
                            "alertname": (
                                "MagmaHandoffRuntimeAuthorityReported"
                            ),
                            "severity": "critical",
                            "source": "C:/private/prometheus.yml",
                        },
                        "annotations": {
                            "summary": "private operator stack trace",
                        },
                        "state": "firing",
                        "value": "1",
                    },
                    {
                        "id": "MagmaHandoffFreshnessStale",
                        "state": "resolved",
                        "summary": "private stale detail",
                    },
                    {
                        "id": "UnknownMagmaAlert",
                        "state": "firing",
                        "summary": "private unknown detail",
                    },
                ],
            }

        section = _magma_share_import_handoff_section(Container())
        alert_state = section["provider_health"]["metrics_alert_state"]

        assert alert_state["source"] == "prometheus_alertmanager_snapshot"
        assert alert_state["updated_at"] == "2026-05-28T10:30:00Z"
        assert alert_state["status"] == "critical"
        assert alert_state["severity"] == "critical"
        assert alert_state["prometheus_alertmanager_feed"] is True
        assert alert_state["controls_present"] is False
        assert alert_state["active_count"] == 1
        assert alert_state["active"][0]["id"] == (
            "MagmaHandoffRuntimeAuthorityReported"
        )
        assert alert_state["active"][0]["metric"] == (
            "waggledance_magma_handoff_runtime_authority_granted"
        )
        assert alert_state["active"][0]["value"] == 1.0
        assert "C:/private/prometheus.yml" not in str(section)
        assert "private operator stack trace" not in str(section)
        assert "private stale detail" not in str(section)
        assert "private unknown detail" not in str(section)
        json.dumps(section, allow_nan=False)

    def test_ops_magma_handoff_metrics_alert_feed_failure_is_sanitized(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        class Feed:
            def snapshot(self):
                raise RuntimeError("C:\\private\\alertmanager\\token")

        class Container:
            magma_share_import_handoff_metrics_alert_feed = Feed()

        section = _magma_share_import_handoff_section(Container())
        alert_state = section["provider_health"]["metrics_alert_state"]

        assert alert_state["source"] == "prometheus_alertmanager_unavailable"
        assert alert_state["status"] == "warning"
        assert alert_state["severity"] == "warning"
        assert alert_state["controls_present"] is False
        assert alert_state["active_count"] == 1
        assert alert_state["active"][0]["id"] == (
            "MagmaHandoffMetricsAlertFeedUnavailable"
        )
        assert "C:\\private" not in str(section)

    def test_magma_handoff_metrics_alertmanager_feed_reads_operator_alerts(self):
        from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
            MagmaHandoffMetricsAlertFeedHttpResponse,
            MagmaHandoffMetricsAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        calls = []

        def transport(url, headers, timeout_seconds, params):
            calls.append((url, dict(headers), timeout_seconds, dict(params)))
            body = [
                {
                    "labels": {
                        "alertname": "MagmaHandoffRuntimeAuthorityReported",
                        "severity": "critical",
                        "host": "prod-db",
                    },
                    "status": {"state": "active"},
                    "annotations": {
                        "summary": "PRIVATE_ANNOTATION path=C:/private",
                    },
                    "generatorURL": "http://alertmanager/private",
                    "value": "1",
                },
                {
                    "labels": {"alertname": "UnknownMagmaAlert"},
                    "status": {"state": "active"},
                    "annotations": {"summary": "PRIVATE_UNKNOWN"},
                },
                {
                    "labels": {"alertname": "MagmaHandoffFreshnessStale"},
                    "status": {"state": "resolved"},
                },
            ]
            return MagmaHandoffMetricsAlertFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                status_code=200,
                source_url=url,
            )

        feed = MagmaHandoffMetricsAlertmanagerFeed(
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            timeout_seconds=2,
            transport=transport,
        )

        class Container:
            magma_share_import_handoff_metrics_alert_feed = feed

        section = _magma_share_import_handoff_section(Container())
        cached_section = _magma_share_import_handoff_section(Container())
        alert_state = cached_section["provider_health"]["metrics_alert_state"]
        feed_health = alert_state["feed_health"]
        serialized = str(section)

        assert len(calls) == 1
        assert calls[0][0].endswith("/api/v2/alerts")
        assert calls[0][1]["Accept"] == "application/json"
        assert calls[0][2] == 2
        assert calls[0][3] == {}
        assert alert_state["source"] == "prometheus_alertmanager_snapshot"
        assert alert_state["prometheus_alertmanager_feed"] is True
        assert alert_state["updated_at"].endswith("Z")
        assert alert_state["status"] == "critical"
        assert alert_state["active_count"] == 1
        assert alert_state["active"][0]["id"] == (
            "MagmaHandoffRuntimeAuthorityReported"
        )
        assert alert_state["active"][0]["metric"] == (
            "waggledance_magma_handoff_runtime_authority_granted"
        )
        assert feed_health["source"] == "alertmanager_adapter"
        assert feed_health["status"] == "nominal"
        assert feed_health["configured"] is True
        assert feed_health["available"] is True
        assert feed_health["cache_enabled"] is True
        assert feed_health["cache_present"] is True
        assert feed_health["cache_hit_count"] == 1
        assert feed_health["cache_miss_count"] == 1
        assert feed_health["fetch_success_count"] == 1
        assert feed_health["fetch_failure_count"] == 0
        assert feed_health["backoff_active"] is False
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert [panel["id"] for panel in alert_state["slo_panels"]] == [
            "magma_alert_feed_availability_5m",
            "magma_alert_feed_fetch_failures_15m",
            "magma_alert_feed_backoff_15m",
            "magma_alert_feed_cache_stale_15m",
        ]
        assert alert_state["slo_panels"][0]["status"] == "nominal"
        assert alert_state["slo_panels"][1]["current_value"] == 0.0
        drill = alert_state["drill_evidence"]
        assert drill["source"] == "operator_runbook"
        assert drill["controls_present"] is False
        assert drill["runtime_authority_granted"] is False
        assert "exception_text" in drill["privacy_exclusions"]
        assert "PRIVATE_ANNOTATION" not in serialized
        assert "PRIVATE_UNKNOWN" not in serialized
        assert "C:/private" not in serialized
        assert "prod-db" not in serialized
        assert "generatorURL" not in serialized

    def test_magma_handoff_metrics_alertmanager_feed_uses_bounded_backoff(self):
        from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
            MagmaHandoffMetricsAlertFeedHttpResponse,
            MagmaHandoffMetricsAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        calls = []
        now = [0.0]

        def utc_now():
            return datetime(
                2026,
                5,
                28,
                10,
                int(now[0]),
                tzinfo=timezone.utc,
            )

        def transport(url, headers, timeout_seconds, params):
            calls.append((url, dict(params)))
            if len(calls) > 1:
                raise RuntimeError("C:/private/alertmanager-token")
            body = [{
                "labels": {
                    "alertname": "MagmaHandoffMetricsSourceDown",
                    "severity": "warning",
                },
                "status": {"state": "active"},
            }]
            return MagmaHandoffMetricsAlertFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
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
            utc_now=utc_now,
            transport=transport,
        )

        class Container:
            magma_share_import_handoff_metrics_alert_feed = feed

        first = _magma_share_import_handoff_section(Container())
        now[0] = 2.0
        failed_refresh = _magma_share_import_handoff_section(Container())
        now[0] = 3.0
        backoff_reuse = _magma_share_import_handoff_section(Container())

        first_state = first["provider_health"]["metrics_alert_state"]
        failed_state = failed_refresh["provider_health"]["metrics_alert_state"]
        backoff_state = backoff_reuse["provider_health"]["metrics_alert_state"]
        feed_health = backoff_state["feed_health"]
        serialized = str(backoff_reuse)

        assert len(calls) == 2
        assert first_state["active_count"] == 1
        assert failed_state["active_count"] == 1
        assert backoff_state["active"][0]["id"] == "MagmaHandoffMetricsSourceDown"
        assert feed_health["status"] == "warning"
        assert feed_health["available"] is True
        assert feed_health["cache_present"] is True
        assert feed_health["cache_stale"] is True
        assert feed_health["backoff_active"] is True
        assert feed_health["fetch_success_count"] == 1
        assert feed_health["fetch_failure_count"] == 1
        assert feed_health["backoff_skip_count"] == 1
        assert feed_health["last_failure_reason"] == "NETWORK_REQUEST_FAILED"
        panel_status = {
            panel["id"]: panel["status"]
            for panel in backoff_state["slo_panels"]
        }
        assert panel_status["magma_alert_feed_fetch_failures_15m"] == "warning"
        assert panel_status["magma_alert_feed_backoff_15m"] == "warning"
        assert panel_status["magma_alert_feed_cache_stale_15m"] == "warning"
        assert backoff_state["drill_evidence"]["controls_present"] is False
        assert "C:/private/alertmanager-token" not in serialized
        assert "127.0.0.1" not in serialized

    def test_magma_handoff_metrics_alert_feed_preserves_fixed_refusal_reason(self):
        from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
            MagmaHandoffMetricsAlertFeedHttpResponse,
            MagmaHandoffMetricsAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _magma_share_import_handoff_section,
        )

        def transport(url, headers, timeout_seconds, params):
            return MagmaHandoffMetricsAlertFeedHttpResponse(
                body=b"[]",
                content_type="application/json",
                status_code=200,
                source_url=f"{url}/redirected",
            )

        feed = MagmaHandoffMetricsAlertmanagerFeed(
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            cache_ttl_seconds=1,
            failure_backoff_seconds=10,
            transport=transport,
        )

        class Container:
            magma_share_import_handoff_metrics_alert_feed = feed

        section = _magma_share_import_handoff_section(Container())
        alert_state = section["provider_health"]["metrics_alert_state"]
        feed_health = alert_state["feed_health"]
        serialized = str(section)

        assert alert_state["source"] == "prometheus_alertmanager_unavailable"
        assert feed_health["status"] == "warning"
        assert feed_health["fetch_failure_count"] == 1
        assert feed_health["last_failure_reason"] == "RESPONSE_SOURCE_URL_REFUSED"
        assert "redirected" not in serialized
        assert "127.0.0.1" not in serialized

    def test_magma_handoff_metrics_alertmanager_feed_guardrails_refuse_secrets(self):
        from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
            MagmaHandoffMetricsAlertFeedError,
            MagmaHandoffMetricsAlertmanagerFeed,
        )

        with pytest.raises(MagmaHandoffMetricsAlertFeedError) as private_host:
            MagmaHandoffMetricsAlertmanagerFeed(
                alertmanager_base_url="http://127.0.0.1:9093",
            )
        assert "LOCAL_HOST_REFUSED" in str(private_host.value)

        with pytest.raises(MagmaHandoffMetricsAlertFeedError) as query_secret:
            MagmaHandoffMetricsAlertmanagerFeed(
                alertmanager_base_url=(
                    "https://alerts.example/api?api_key=SECRET"
                ),
            )
        assert "URL_QUERY_REFUSED" in str(query_secret.value)

        with pytest.raises(MagmaHandoffMetricsAlertFeedError) as userinfo:
            MagmaHandoffMetricsAlertmanagerFeed(
                alertmanager_base_url="https://user:pass@alerts.example",
            )
        assert "URL_USERINFO_REFUSED" in str(userinfo.value)

        with pytest.raises(MagmaHandoffMetricsAlertFeedError) as header_secret:
            MagmaHandoffMetricsAlertmanagerFeed.from_config({
                "alertmanager_base_url": "https://alerts.example",
                "headers": {"Authorization": "Bearer abc"},
            })
        assert "CREDENTIAL_HEADER_REFUSED" in str(header_secret.value)

    def test_container_wires_configured_magma_handoff_metrics_alert_feed(self):
        from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
            MagmaHandoffMetricsAlertmanagerFeed,
        )

        settings = WaggleSettings(
            profile="HOME",
            api_key="test-key-123",
            _extras={
                "magma_handoff_metrics_alert_feed": {
                    "enabled": True,
                    "alertmanager_base_url": "https://alerts.example",
                    "timeout_s": 2,
                    "max_response_bytes": 1000,
                    "cache_ttl_s": 11,
                    "failure_backoff_s": 12,
                },
            },
        )
        container = Container(settings=settings, stub=True)

        assert isinstance(
            container.magma_share_import_handoff_metrics_alert_feed,
            MagmaHandoffMetricsAlertmanagerFeed,
        )

    def test_ops_route_stage_latency_panels_are_read_only_promql_templates(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        section = _route_stage_latency_panels()
        panel_queries = {panel["id"]: panel["query"] for panel in section["panels"]}
        alert_exprs = {
            alert["id"]: alert["expr"]
            for alert in section["alert_thresholds"]
        }

        assert section["source"] == "prometheus_query_templates"
        assert section["controls_present"] is False
        assert section["prometheus_alertmanager_feed"] is False
        assert section["feed_state"]["source"] == "not_configured"
        assert section["feed_state"]["panel_values"] == []
        assert [
            panel["id"]
            for panel in section["feed_state"]["slo_panels"]
        ] == [
            "route_stage_latency_feed_availability_5m",
            "route_stage_latency_feed_fetch_failures_total",
            "route_stage_latency_feed_backoff_15m",
            "route_stage_latency_feed_cache_stale_15m",
        ]
        assert {
            panel["status"]
            for panel in section["feed_state"]["slo_panels"]
        } == {"not_configured"}
        assert all(
            panel["controls_present"] is False
            for panel in section["feed_state"]["slo_panels"]
        )
        drill_evidence = section["feed_state"]["drill_evidence"]
        assert drill_evidence["source"] == "operator_runbook"
        assert drill_evidence["runbook_path"] == (
            "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md"
        )
        assert drill_evidence["controls_present"] is False
        assert drill_evidence["runtime_authority_granted"] is False
        assert drill_evidence["external_writes_applied"] is False
        assert "waggledance_route_stage_request_latency_histogram_ms_bucket" in (
            section["metrics"]
        )
        assert "histogram_quantile(0.95" in (
            panel_queries["route_stage_latency_p95_ms"]
        )
        assert "histogram_quantile(0.99" in (
            panel_queries["route_stage_latency_p99_ms"]
        )
        assert "RouteStageLatencyP95Warning" in alert_exprs
        assert "RouteStageLatencyP99Critical" in alert_exprs
        assert "route_stage_trace" not in str(section)
        assert "query=" not in str(section)

    def test_ops_route_stage_latency_feed_state_sanitizes_snapshot(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-05-28T04:15:00Z",
                    "panel_values": [
                        {
                            "id": "route_stage_latency_p95_ms",
                            "stage": "language_detection",
                            "value": 3123.456,
                            "query": "PRIVATE_QUERY_MARKER",
                        },
                        {
                            "id": "route_stage_latency_p99_ms",
                            "labels": {"stage": "hot_cache"},
                            "value_ms": 6100,
                        },
                        {
                            "id": "route_stage_request_rate",
                            "stage": "memory_context",
                            "value": 2.5,
                        },
                        {
                            "id": "route_stage_latency_p95_ms",
                            "stage": "raw_query",
                            "value": 9999,
                        },
                    ],
                    "active_alerts": [
                        {
                            "labels": {
                                "alertname": "RouteStageLatencyP99Critical",
                                "stage": "hot_cache",
                                "severity": "critical",
                            },
                            "value_ms": 6100,
                            "summary": "private stack trace",
                        },
                        {
                            "id": "RouteStageLatencyP95Warning",
                            "stage": "raw_query",
                            "value": 9999,
                        },
                    ],
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]

        assert section["prometheus_alertmanager_feed"] is True
        assert feed_state["source"] == "prometheus_alertmanager_snapshot"
        assert feed_state["updated_at"] == "2026-05-28T04:15:00Z"
        assert feed_state["status"] == "critical"
        assert feed_state["severity"] == "critical"
        assert feed_state["controls_present"] is False
        assert len(feed_state["panel_values"]) == 3
        assert feed_state["panel_values"][0]["value"] == 3123.456
        assert feed_state["panel_values"][0]["status"] == "warning"
        assert feed_state["active_count"] == 1
        assert feed_state["active"][0]["id"] == "RouteStageLatencyP99Critical"
        assert feed_state["active"][0]["stage"] == "hot_cache"
        assert "raw_query" not in str(section)
        assert "PRIVATE_QUERY_MARKER" not in str(section)
        assert "private stack trace" not in str(section)

    def test_ops_route_stage_latency_feed_state_rejects_non_finite_numbers(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-05-28T04:15:00Z",
                    "panel_values": [
                        {
                            "id": "route_stage_latency_p95_ms",
                            "stage": "language_detection",
                            "value": float("nan"),
                        },
                        {
                            "id": "route_stage_latency_p99_ms",
                            "stage": "hot_cache",
                            "value": "Inf",
                        },
                        {
                            "id": "route_stage_request_rate",
                            "stage": "memory_context",
                            "current_value": "-Inf",
                        },
                    ],
                    "active_alerts": [{
                        "id": "RouteStageLatencyP99Critical",
                        "stage": "hot_cache",
                        "severity": "critical",
                        "value": "NaN",
                    }],
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]

        assert feed_state["panel_values"] == []
        assert feed_state["active_count"] == 1
        assert "value_ms" not in feed_state["active"][0]
        json.dumps(section, allow_nan=False)

    def test_ops_route_stage_latency_feed_health_forces_no_authority_flags(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-05-28T04:15:00Z",
                    "panel_values": [],
                    "active_alerts": [],
                    "provider_health": {
                        "status": "nominal",
                        "configured": True,
                        "available": True,
                        "cache_enabled": True,
                        "cache_present": True,
                        "controls_present": True,
                        "runtime_authority_granted": True,
                        "external_writes_applied": True,
                        "cache_hit_count": "not-a-number",
                        "last_failure_reason": "C:/private/prometheus-token",
                    },
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]
        feed_health = feed_state["feed_health"]
        slo_panels = {panel["id"]: panel for panel in feed_state["slo_panels"]}
        drill_evidence = feed_state["drill_evidence"]

        assert feed_health["configured"] is True
        assert feed_health["available"] is True
        assert feed_health["cache_enabled"] is True
        assert feed_health["cache_present"] is True
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert feed_health["external_writes_applied"] is False
        assert feed_health["cache_hit_count"] == 0.0
        assert feed_health["last_failure_reason"] == "FEED_READ_FAILED"
        assert slo_panels[
            "route_stage_latency_feed_availability_5m"
        ]["current_value"] == 1.0
        assert slo_panels[
            "route_stage_latency_feed_availability_5m"
        ]["status"] == "nominal"
        assert all(
            panel["controls_present"] is False
            for panel in slo_panels.values()
        )
        assert drill_evidence["controls_present"] is False
        assert drill_evidence["runtime_authority_granted"] is False
        assert drill_evidence["external_writes_applied"] is False
        assert "route_stage_latency.feed_state.feed_health" in str(
            drill_evidence["required_artifacts"]
        )
        assert "C:/private/prometheus-token" not in str(section)

    def test_ops_route_stage_latency_feed_slo_drill_evidence_tracks_health(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-05-28T04:15:00Z",
                    "panel_values": [],
                    "active_alerts": [],
                    "provider_health": {
                        "status": "warning",
                        "configured": True,
                        "available": True,
                        "backoff_active": True,
                        "cache_stale": True,
                        "fetch_failure_count": 2,
                    },
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]
        slo_panels = {panel["id"]: panel for panel in feed_state["slo_panels"]}

        assert slo_panels[
            "route_stage_latency_feed_availability_5m"
        ]["status"] == "nominal"
        assert slo_panels[
            "route_stage_latency_feed_fetch_failures_total"
        ]["current_value"] == 2.0
        assert slo_panels[
            "route_stage_latency_feed_fetch_failures_total"
        ]["status"] == "warning"
        assert slo_panels[
            "route_stage_latency_feed_backoff_15m"
        ]["current_value"] == 1.0
        assert slo_panels[
            "route_stage_latency_feed_backoff_15m"
        ]["status"] == "warning"
        assert slo_panels[
            "route_stage_latency_feed_cache_stale_15m"
        ]["current_value"] == 1.0
        assert slo_panels[
            "route_stage_latency_feed_cache_stale_15m"
        ]["status"] == "warning"
        assert feed_state["drill_evidence"]["source"] == "operator_runbook"
        assert json.dumps(section, allow_nan=False)

    def test_ops_route_stage_latency_feed_health_preserves_none_reason(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": "2026-05-28T04:15:00Z",
                    "panel_values": [],
                    "active_alerts": [],
                    "provider_health": {
                        "status": "nominal",
                        "configured": True,
                        "available": True,
                        "last_failure_reason": "none",
                    },
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_health = section["feed_state"]["feed_health"]

        assert feed_health["last_failure_reason"] == "none"

    def test_ops_route_stage_latency_feed_state_rejects_raw_updated_at(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
            _route_stage_latency_updated_at,
        )

        class Feed:
            def snapshot(self):
                return {
                    "updated_at": (
                        "user_id=alice api_key=SECRET host=prod-db exception=boom"
                    ),
                    "panel_values": [{
                        "id": "route_stage_latency_p95_ms",
                        "stage": "language_detection",
                        "value": 100.0,
                    }],
                    "active": [],
                }

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]

        assert feed_state["updated_at"] is None
        assert _route_stage_latency_updated_at({
            "updated_at": "2026-13-99T99:99:99Z",
        }) is None
        assert "user_id=alice" not in str(section)
        assert "api_key=SECRET" not in str(section)
        assert "host=prod-db" not in str(section)
        assert "exception=boom" not in str(section)

    def test_ops_route_stage_latency_feed_failure_hides_exception_details(self):
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        class Feed:
            def snapshot(self):
                raise RuntimeError("private query=secret")

        class Container:
            route_stage_latency_feed = Feed()

        section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]

        assert section["prometheus_alertmanager_feed"] is False
        assert feed_state["source"] == "prometheus_alertmanager_unavailable"
        assert feed_state["status"] == "warning"
        assert feed_state["active"][0]["id"] == "RouteStageLatencyFeedUnavailable"
        assert "private query=secret" not in str(section)
        assert "query=" not in str(section)

    def test_route_stage_latency_feed_provider_reads_operator_endpoints(self):
        from waggledance.adapters.http.route_stage_latency_feed import (
            RouteStageLatencyFeedHttpResponse,
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        calls = []

        def transport(url, headers, timeout_seconds, params):
            calls.append((url, dict(headers), timeout_seconds, dict(params)))
            if url.endswith("/api/v1/query"):
                body = {
                    "status": "success",
                    "data": {
                        "result": [{
                            "metric": {
                                "stage": "language_detection",
                                "query": "PRIVATE_QUERY_MARKER",
                            },
                            "value": [1_716_888_000, "3123.4567"],
                        }],
                    },
                }
            else:
                body = [{
                    "labels": {
                        "alertname": "RouteStageLatencyP99Critical",
                        "stage": "hot_cache",
                        "severity": "critical",
                        "host": "prod-db",
                    },
                    "status": {"state": "active"},
                    "annotations": {
                        "summary": "PRIVATE_ANNOTATION query=secret",
                    },
                    "generatorURL": "http://alertmanager/private",
                }]
            return RouteStageLatencyFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                status_code=200,
                source_url=url,
            )

        feed = RouteStageLatencyPrometheusAlertmanagerFeed(
            prometheus_base_url="http://127.0.0.1:9090",
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            transport=transport,
        )

        class Container:
            route_stage_latency_feed = feed

        section = _route_stage_latency_panels(Container())
        cached_section = _route_stage_latency_panels(Container())
        feed_state = section["feed_state"]
        cached_feed_state = cached_section["feed_state"]
        feed_health = cached_feed_state["feed_health"]
        serialized = str(section)

        assert len([call for call in calls if call[0].endswith("/api/v1/query")]) == 3
        assert len(calls) == 4
        assert calls[-1][0].endswith("/api/v2/alerts")
        assert feed_state["source"] == "prometheus_alertmanager_snapshot"
        assert feed_state["prometheus_alertmanager_feed"] is True
        assert feed_state["updated_at"].endswith("Z")
        assert feed_state["panel_values"][0]["id"] == "route_stage_latency_p95_ms"
        assert feed_state["panel_values"][0]["stage"] == "language_detection"
        assert feed_state["panel_values"][0]["value"] == 3123.457
        assert feed_state["panel_values"][0]["status"] == "warning"
        assert feed_state["active"][0]["id"] == "RouteStageLatencyP99Critical"
        assert feed_state["active"][0]["summary"] == (
            "RouteStageLatencyP99Critical active for hot_cache."
        )
        assert feed_health["source"] == "prometheus_alertmanager_adapter"
        assert feed_health["status"] == "nominal"
        assert feed_health["configured"] is True
        assert feed_health["available"] is True
        assert feed_health["cache_enabled"] is True
        assert feed_health["cache_present"] is True
        assert feed_health["cache_hit_count"] == 1
        assert feed_health["cache_miss_count"] == 1
        assert feed_health["fetch_success_count"] == 1
        assert feed_health["fetch_failure_count"] == 0
        assert feed_health["backoff_active"] is False
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert "PRIVATE_QUERY_MARKER" not in serialized
        assert "PRIVATE_ANNOTATION" not in serialized
        assert "query=secret" not in serialized
        assert "prod-db" not in serialized
        assert "generatorURL" not in serialized

    def test_route_stage_latency_feed_provider_uses_bounded_backoff(self):
        from waggledance.adapters.http.route_stage_latency_feed import (
            RouteStageLatencyFeedHttpResponse,
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )
        from waggledance.adapters.http.routes.compat_dashboard import (
            _route_stage_latency_panels,
        )

        calls = []
        now = [0.0]

        def utc_now():
            return datetime(
                2026,
                5,
                28,
                11,
                int(now[0]),
                tzinfo=timezone.utc,
            )

        def transport(url, headers, timeout_seconds, params):
            calls.append((url, dict(params)))
            if len(calls) > 4:
                raise RuntimeError("C:/private/prometheus-token")
            if url.endswith("/api/v1/query"):
                body = {
                    "status": "success",
                    "data": {
                        "result": [{
                            "metric": {"stage": "language_detection"},
                            "value": [1_716_888_000, "99.0"],
                        }],
                    },
                }
            else:
                body = [{
                    "labels": {
                        "alertname": "RouteStageLatencyP95Warning",
                        "stage": "language_detection",
                        "severity": "warning",
                    },
                    "status": {"state": "active"},
                }]
            return RouteStageLatencyFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
                content_type="application/json",
                status_code=200,
                source_url=url,
            )

        feed = RouteStageLatencyPrometheusAlertmanagerFeed(
            prometheus_base_url="http://127.0.0.1:9090",
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            cache_ttl_seconds=1,
            failure_backoff_seconds=10,
            monotonic=lambda: now[0],
            utc_now=utc_now,
            transport=transport,
        )

        class Container:
            route_stage_latency_feed = feed

        first = _route_stage_latency_panels(Container())
        now[0] = 2.0
        failed_refresh = _route_stage_latency_panels(Container())
        now[0] = 3.0
        backoff_reuse = _route_stage_latency_panels(Container())

        first_state = first["feed_state"]
        failed_state = failed_refresh["feed_state"]
        backoff_state = backoff_reuse["feed_state"]
        feed_health = backoff_state["feed_health"]
        serialized = str(backoff_reuse)

        assert len(calls) == 5
        assert first_state["active_count"] == 1
        assert failed_state["active_count"] == 1
        assert backoff_state["active"][0]["id"] == "RouteStageLatencyP95Warning"
        assert feed_health["status"] == "warning"
        assert feed_health["available"] is True
        assert feed_health["cache_present"] is True
        assert feed_health["cache_stale"] is True
        assert feed_health["backoff_active"] is True
        assert feed_health["fetch_success_count"] == 1
        assert feed_health["fetch_failure_count"] == 1
        assert feed_health["backoff_skip_count"] == 1
        assert feed_health["last_failure_reason"] == "NETWORK_REQUEST_FAILED"
        assert feed_health["controls_present"] is False
        assert feed_health["runtime_authority_granted"] is False
        assert "C:/private/prometheus-token" not in serialized
        assert "127.0.0.1" not in serialized

    def test_route_stage_latency_feed_provider_rejects_non_finite_numbers(self):
        from waggledance.adapters.http.route_stage_latency_feed import (
            RouteStageLatencyFeedHttpResponse,
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )

        def transport(url, headers, timeout_seconds, params):
            if url.endswith("/api/v1/query"):
                body = {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {"stage": "language_detection"},
                                "value": [1_716_888_000, "NaN"],
                            },
                            {
                                "metric": {"stage": "hot_cache"},
                                "value": [1_716_888_000, "+Inf"],
                            },
                            {
                                "metric": {"stage": "memory_context"},
                                "value": [1_716_888_000, "-Inf"],
                            },
                        ],
                    },
                }
            else:
                body = [{
                    "labels": {
                        "alertname": "RouteStageLatencyP99Critical",
                        "stage": "hot_cache",
                        "severity": "critical",
                    },
                    "status": {"state": "active"},
                    "value": "NaN",
                }]
            return RouteStageLatencyFeedHttpResponse(
                body=json.dumps(body).encode("utf-8"),
                content_type="application/json",
                status_code=200,
                source_url=url,
            )

        feed = RouteStageLatencyPrometheusAlertmanagerFeed(
            prometheus_base_url="http://127.0.0.1:9090",
            alertmanager_base_url="http://127.0.0.1:9093",
            allowed_private_hosts=["127.0.0.1"],
            transport=transport,
        )

        snapshot = feed.snapshot()

        assert snapshot["panel_values"] == []
        assert snapshot["active_alerts"][0]["value"] is None
        json.dumps(snapshot, allow_nan=False)

    def test_route_stage_latency_feed_provider_guardrails_refuse_secrets(self):
        from waggledance.adapters.http.route_stage_latency_feed import (
            RouteStageLatencyFeedError,
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )

        with pytest.raises(RouteStageLatencyFeedError) as private_host:
            RouteStageLatencyPrometheusAlertmanagerFeed(
                prometheus_base_url="http://127.0.0.1:9090",
            )
        assert "LOCAL_HOST_REFUSED" in str(private_host.value)

        with pytest.raises(RouteStageLatencyFeedError) as query_secret:
            RouteStageLatencyPrometheusAlertmanagerFeed(
                prometheus_base_url=(
                    "https://prometheus.example/api?api_key=SECRET"
                ),
            )
        assert "URL_QUERY_REFUSED" in str(query_secret.value)

        with pytest.raises(RouteStageLatencyFeedError) as userinfo:
            RouteStageLatencyPrometheusAlertmanagerFeed(
                prometheus_base_url="https://user:pass@prometheus.example",
            )
        assert "URL_USERINFO_REFUSED" in str(userinfo.value)

    def test_container_wires_configured_route_stage_latency_feed(self):
        from waggledance.adapters.http.route_stage_latency_feed import (
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )

        settings = WaggleSettings(
            profile="HOME",
            api_key="test-key-123",
            _extras={
                "route_stage_latency_feed": {
                    "enabled": True,
                    "prometheus_base_url": "https://prometheus.example",
                    "alertmanager_base_url": "https://alerts.example",
                    "timeout_s": 2,
                    "max_response_bytes": 1000,
                    "cache_ttl_s": 11,
                    "failure_backoff_s": 12,
                },
            },
        )
        container = Container(settings=settings, stub=True)

        assert isinstance(
            container.route_stage_latency_feed,
            RouteStageLatencyPrometheusAlertmanagerFeed,
        )
        health = container.route_stage_latency_feed.provider_health()
        assert health["cache_ttl_seconds"] == 11
        assert health["failure_backoff_seconds"] == 12

    def test_ops_still_has_status_and_recommendation(self):
        """Existing fields must not break."""
        client, key = self._get_client()
        r = client.get("/api/ops", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        assert "status" in data
        assert "recommendation" in data
        assert "load" in data["status"]
        assert "tier" in data["status"]


# ── /api/settings ─────────────────────────────────────────

class TestApiSettings:
    """Verify /api/settings GET and /api/settings/toggle POST."""

    @classmethod
    def _get_client(cls):
        from starlette.testclient import TestClient
        c = _make_container()
        _patch_scaler(c)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)
        return client, c._settings.api_key

    def test_get_settings_returns_toggles(self):
        client, key = self._get_client()
        r = client.get("/api/settings", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "toggles" in data
        assert isinstance(data["toggles"], dict)

    def test_get_settings_includes_known_keys(self):
        client, key = self._get_client()
        r = client.get("/api/settings", headers={"Authorization": f"Bearer {key}"})
        data = r.json()
        toggles = data["toggles"]
        assert "feeds.enabled" in toggles
        assert "mqtt.enabled" in toggles

    def test_toggle_rejects_unknown_key(self):
        client, key = self._get_client()
        r = client.post(
            "/api/settings/toggle",
            json={"key": "nonexistent.key", "value": True},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "error" in data
        assert "not toggleable" in data["error"]

    def test_toggle_requires_auth(self):
        client, _ = self._get_client()
        r = client.post(
            "/api/settings/toggle",
            json={"key": "feeds.enabled", "value": True},
            # No auth header
        )
        # Should be rejected by auth middleware (401 or 403)
        assert r.status_code in (401, 403)


# ── ResourceGuard integration ────────────────────────────

class TestResourceGuardWired:
    """Verify ResourceGuard is wired into ResourceKernel."""

    def test_guard_callable(self):
        c = _make_container()
        _patch_scaler(c)
        guard = c.resource_guard
        # should_throttle() runs without error
        result = guard.should_throttle()
        assert isinstance(result, bool)

    def test_guard_stats_available(self):
        c = _make_container()
        _patch_scaler(c)
        guard = c.resource_guard
        stats = guard.stats
        assert "memory_percent" in stats
        assert "gc_runs" in stats


# ═══════════════════════════════════════════════════════════
# Phase 2 — Hologram Ops tab: FlexHW + AutoThrottle rendering
# ═══════════════════════════════════════════════════════════

_V6_HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v6.html"


def _read_html():
    return _V6_HTML_PATH.read_text(encoding="utf-8")


class TestHologramOpsFlexHW:
    """Verify hologram-brain-v6.html Ops tab renders FlexHW and AutoThrottle."""

    def test_hologram_ops_renders_flexhw_section(self):
        html = _read_html()
        assert "ops.flexhw" in html or "fhw.tiers" in html
        assert "flexhw-tier" in html

    def test_hologram_ops_renders_throttle_section(self):
        html = _read_html()
        assert "ops.throttle" in html or "thr.machine_class" in html
        assert "thr.avg_latency_ms" in html

    def test_hologram_ops_renders_autogrowth_section(self):
        html = _read_html()
        assert "ops.autogrowth" in html
        assert "ops_autogrowth" in html
        assert "ag.wakeups_total" in html
        assert "ag.non_idle_ticks" in html
        assert "ag.errors_total" in html
        assert "ag.alert_state" in html
        assert "activeAutogrowthAlerts" in html
        assert "autogrowth_start" not in html
        assert "autogrowth_stop" not in html

    def test_hologram_ops_renders_route_stage_latency_templates(self):
        html = _read_html()
        assert "ops.route_stage_latency" in html
        assert "ops_route_stage_latency" in html
        assert "routeStagePanels" in html
        assert "routeStageAlerts" in html
        assert "routeStageFeed" in html
        assert "routeStagePanelValues" in html
        assert "activeRouteStageLatencyAlerts" in html
        assert "route_stage_latency_start" not in html
        assert "route_stage_latency_stop" not in html

    def test_hologram_ops_renders_magma_handoff_status(self):
        html = _read_html()
        assert "ops.magma_share_import_handoff" in html
        assert "ops_magma_handoff" in html
        assert "magmaHandoff.runtime_authority_granted" in html
        assert "magmaHandoff.controls_present" in html
        assert "magmaHandoff.history" in html
        assert "magmaHistory" in html
        assert "magmaHandoff.provider_health" in html
        assert "magmaProviderHealth" in html
        assert "activeMagmaProviderAlerts" in html
        assert "magmaProviderThresholds" in html
        assert "magmaProviderHealth.metrics_alert_state" in html
        assert "activeMagmaMetricsAlerts" in html
        assert "magmaMetricsSloPanels" in html
        assert "magmaMetricsDrillEvidence" in html
        assert "MAGMA Alert Feed SLOs" in html
        assert "MAGMA Alert Drill Evidence" in html
        assert "MAGMA Handoff Metrics Alerts" in html
        assert "Metrics Feed" in html
        assert "MAGMA Handoff Provider Thresholds" in html
        assert "Fresh Feed" in html
        assert "feed_staleness_state" in html
        assert "MAGMA Handoff Freshness Feed" in html
        assert "history_retained_count" in html
        assert "activeMagmaHandoffs" in html
        assert "magma_share_import_handoff_start" not in html
        assert "magma_share_import_handoff_stop" not in html

    def test_hologram_ops_has_flexhw_en_labels(self):
        html = _read_html()
        assert "ops_flexhw" in html
        assert '"FlexHW Tier"' in html

    def test_hologram_ops_has_flexhw_fi_labels(self):
        html = _read_html()
        assert '"FlexHW-taso"' in html
        assert '"Automaattisaato"' in html

    def test_hologram_ops_renders_five_tiers(self):
        """The tier ladder iterates fhw.tiers and marks the active one."""
        html = _read_html()
        assert "fhw.tiers" in html or "tiers.forEach" in html
        assert "flexhw-tier-active" in html
        assert "active_tier_index" in html


# ═══════════════════════════════════════════════════════════
# Phase 3 — Introspection API endpoints
# ═══════════════════════════════════════════════════════════

def _get_phase3_client():
    """Shared client for Phase 3 endpoint tests."""
    from starlette.testclient import TestClient
    c = _make_container()
    _patch_scaler(c)
    app = c.build_app()
    client = TestClient(app, raise_server_exceptions=False)
    return client, c._settings.api_key


class TestMagmaEndpoints:
    """MAGMA introspection endpoints."""

    def test_magma_stats_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/stats",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "audit_wired" in data
        assert "replay_wired" in data

    def test_magma_audit_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/audit",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "entries" in data
        assert "total" in data

    def test_magma_audit_agent_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/audit/agent/test_agent",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "entries" in r.json()

    def test_magma_overlays_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/overlays",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "overlays" in data
        assert data["available"] is False

    def test_magma_branches_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/branches",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "branches" in data
        assert data["available"] is False

    def test_magma_replay_manifest_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/magma/replay/manifest",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200

    def test_magma_branch_activate_requires_auth(self):
        client, _ = _get_phase3_client()
        r = client.post("/api/magma/branches/test/activate")
        assert r.status_code in (401, 403)

    def test_magma_rollback_requires_auth(self):
        client, _ = _get_phase3_client()
        r = client.post("/api/magma/rollback/test_agent")
        assert r.status_code in (401, 403)


class TestGraphEndpoints:
    """Cognitive graph endpoints."""

    def test_graph_node_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/graph/node/test_node",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "node" in data
        assert "edges" in data

    def test_graph_path_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/graph/path/src/tgt",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "path" in r.json()

    def test_graph_stats_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/graph/stats",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "edges" in data


class TestTrustEndpoints:
    """Trust engine endpoints."""

    def test_trust_ranking_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/trust/ranking",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "ranking" in r.json()

    def test_trust_agent_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/trust/agent/test_agent",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "reputation" in r.json()

    def test_trust_domain_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/trust/domain/capability",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "experts" in r.json()

    def test_trust_signals_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/trust/signals/test_agent",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "signals" in r.json()


class TestCrossAgentEndpoints:
    """Cross-agent endpoints."""

    def test_cross_channels_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/cross/channels",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "channels" in r.json()

    def test_cross_provenance_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/cross/provenance/fact_123",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "chain" in r.json()

    def test_cross_consensus_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/cross/consensus",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "facts" in r.json()


class TestAnalyticsEndpoints:
    """Analytics endpoints (file-based)."""

    def test_analytics_trends_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/analytics/trends",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        data = r.json()
        assert "days" in data

    def test_analytics_routes_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/analytics/routes",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "routes" in r.json()

    def test_analytics_models_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/analytics/models",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "models" in r.json()

    def test_analytics_facts_endpoint(self):
        client, key = _get_phase3_client()
        r = client.get("/api/analytics/facts",
                       headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        assert "days" in r.json()


# ═══════════════════════════════════════════════════════════
# Phase 4 — Legacy backend archival verification
# ═══════════════════════════════════════════════════════════

class TestLegacyArchive:
    """Verify backend archived and forward line is clean."""

    def test_backend_archived_to_archive_backend_legacy(self):
        archive = Path(__file__).resolve().parents[1] / "_archive" / "backend-legacy"
        assert archive.is_dir(), "_archive/backend-legacy/ must exist"
        assert (archive / "main.py").is_file()
        assert (archive / "auth.py").is_file()
        assert (archive / "routes").is_dir()
        # Original backend/ must not exist
        original = Path(__file__).resolve().parents[1] / "backend"
        assert not original.is_dir(), "backend/ must be archived, not present"

    def test_no_backend_imports_in_hexagonal(self):
        """waggledance/ must not import from backend/."""
        import ast
        hexagonal = Path(__file__).resolve().parents[1] / "waggledance"
        violations = []
        for py in hexagonal.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith("backend"):
                            violations.append(f"{py.name}: {node.module}")
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("backend"):
                                violations.append(f"{py.name}: {alias.name}")
            except SyntaxError:
                continue
        assert not violations, f"Backend imports in hexagonal: {violations}"

    def test_dashboard_has_no_legacy_register_routes(self):
        """dashboard.py must not have register_*_routes calls to backend."""
        dashboard = Path(__file__).resolve().parents[1] / "web" / "dashboard.py"
        if not dashboard.exists():
            pytest.skip("dashboard.py not found")
        content = dashboard.read_text(encoding="utf-8")
        assert "register_magma_routes" not in content
        assert "register_cross_agent_routes" not in content
        assert "register_trust_routes" not in content
        assert "register_graph_routes" not in content

    def test_dashboard_does_not_import_backend_auth(self):
        """dashboard.py must not import from backend.auth."""
        dashboard = Path(__file__).resolve().parents[1] / "web" / "dashboard.py"
        if not dashboard.exists():
            pytest.skip("dashboard.py not found")
        content = dashboard.read_text(encoding="utf-8")
        assert "from backend.auth import" not in content
