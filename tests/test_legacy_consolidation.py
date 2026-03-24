"""Legacy Consolidation tests — Phases 1-4.

Phase 1: Container wiring, /api/ops, /api/settings, no split-brain
Phase 2: Hologram Ops tab FlexHW + AutoThrottle rendering
Phase 3: MAGMA/graph/trust/cross-agent/analytics endpoints
Phase 4: Backend archival verification
"""

import sys
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
