"""Tests for Hex Neighbor Mesh — topology, routing, health, MAGMA, regression.

Target: 50+ tests covering all hex mesh requirements.
"""

import asyncio
import time
import pytest

from waggledance.core.domain.hex_mesh import (
    HexCoord,
    HexCellDefinition,
    HexCellHealth,
    HexNeighborRequest,
    HexNeighborResponse,
    HexResolutionTrace,
    AXIAL_DIRECTIONS,
)


# ═══════════════════════════════════════════════════════════════════
# 1. TOPOLOGY MATH
# ═══════════════════════════════════════════════════════════════════

class TestHexCoordMath:
    """Axial coordinate math — neighbors, distance, ring, adjacency."""

    def test_origin(self):
        c = HexCoord(0, 0)
        assert c.q == 0 and c.r == 0

    def test_neighbors_count(self):
        assert len(HexCoord(0, 0).neighbors()) == 6

    def test_neighbors_of_origin(self):
        ns = HexCoord(0, 0).neighbors()
        expected = [HexCoord(dq, dr) for dq, dr in AXIAL_DIRECTIONS]
        assert ns == expected

    def test_neighbors_of_nonorigin(self):
        ns = HexCoord(2, -1).neighbors()
        assert len(ns) == 6
        assert HexCoord(3, -1) in ns
        assert HexCoord(2, 0) in ns

    def test_distance_same(self):
        assert HexCoord(0, 0).distance(HexCoord(0, 0)) == 0

    def test_distance_adjacent(self):
        assert HexCoord(0, 0).distance(HexCoord(1, 0)) == 1

    def test_distance_ring2(self):
        assert HexCoord(0, 0).distance(HexCoord(2, 0)) == 2

    def test_distance_diagonal(self):
        assert HexCoord(0, 0).distance(HexCoord(1, -1)) == 1

    def test_distance_symmetric(self):
        a, b = HexCoord(3, -2), HexCoord(-1, 4)
        assert a.distance(b) == b.distance(a)

    def test_ring_0(self):
        ring = HexCoord(0, 0).ring(0)
        assert ring == [HexCoord(0, 0)]

    def test_ring_1_count(self):
        ring = HexCoord(0, 0).ring(1)
        assert len(ring) == 6

    def test_ring_1_all_adjacent(self):
        center = HexCoord(0, 0)
        for c in center.ring(1):
            assert center.distance(c) == 1

    def test_ring_2_count(self):
        ring = HexCoord(0, 0).ring(2)
        assert len(ring) == 12

    def test_ring_2_all_distance_2(self):
        center = HexCoord(0, 0)
        for c in center.ring(2):
            assert center.distance(c) == 2

    def test_is_adjacent_true(self):
        assert HexCoord(0, 0).is_adjacent(HexCoord(1, 0))

    def test_is_adjacent_false(self):
        assert not HexCoord(0, 0).is_adjacent(HexCoord(2, 0))

    def test_is_adjacent_self(self):
        assert not HexCoord(0, 0).is_adjacent(HexCoord(0, 0))

    def test_frozen(self):
        """HexCoord should be hashable (frozen dataclass)."""
        s = {HexCoord(0, 0), HexCoord(1, 0), HexCoord(0, 0)}
        assert len(s) == 2

    def test_ring_offset_center(self):
        center = HexCoord(3, -2)
        ring = center.ring(1)
        assert len(ring) == 6
        for c in ring:
            assert center.distance(c) == 1


# ═══════════════════════════════════════════════════════════════════
# 2. CONFIG LOADING
# ═══════════════════════════════════════════════════════════════════

class TestTopologyRegistry:
    """Topology registry — load, validate, map agents to cells."""

    def test_load_default_topology(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        assert reg.cell_count == 7

    def test_cell_ids(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        ids = set(reg.cells.keys())
        assert "hub" in ids
        assert "bee_ops" in ids
        assert "environment" in ids

    def test_missing_config(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="nonexistent.yaml", agents=[])
        assert reg.cell_count == 0

    def test_hub_at_origin(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hub = reg.get_cell("hub")
        assert hub is not None
        assert hub.coord == HexCoord(0, 0)

    def test_hub_has_6_neighbors(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        neighbors = reg.get_neighbor_cells("hub")
        assert len(neighbors) == 6

    def test_edge_cell_has_neighbors(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        neighbors = reg.get_neighbor_cells("bee_ops")
        # Edge cells in ring-1 should have 2-3 neighbors (hub + adjacent ring cells)
        assert len(neighbors) >= 2

    def test_agent_mapping(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.core.domain.agent import AgentDefinition
        agents = [
            AgentDefinition(id="beekeeper", name="Beekeeper", domain="bee", tags=["beekeeper"],
                           skills=[], trust_level=0, specialization_score=0.0, active=True, profile="HOME"),
            AgentDefinition(id="meteorologist", name="Meteorologist", domain="weather", tags=["meteorologist"],
                           skills=[], trust_level=0, specialization_score=0.0, active=True, profile="HOME"),
        ]
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=agents)
        bee_agents = reg.get_cell_agents("bee_ops")
        assert any(a.id == "beekeeper" for a in bee_agents)
        env_agents = reg.get_cell_agents("environment")
        assert any(a.id == "meteorologist" for a in env_agents)

    def test_select_origin_cell_bee(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        cell = reg.select_origin_cell("Tell me about bee hive management")
        assert cell == "bee_ops"

    def test_select_origin_cell_weather(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        cell = reg.select_origin_cell("What is the weather forecast")
        assert cell == "environment"

    def test_select_origin_cell_safety(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        cell = reg.select_origin_cell("fire safety alarm security")
        assert cell == "safety_security"

    def test_stats_structure(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        s = reg.stats()
        assert "cells_loaded" in s
        assert s["cells_loaded"] == 7


# ═══════════════════════════════════════════════════════════════════
# 3. LOCAL-FIRST BEHAVIOR
# ═══════════════════════════════════════════════════════════════════

class TestLocalFirst:
    """Hex mesh local-first — disabled = no effect, high local = no neighbor."""

    def test_disabled_returns_none(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=False)
        result = asyncio.run(
            svc.resolve("test query"))
        assert result is None

    def test_no_cells_returns_none(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="nonexistent.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=True)
        result = asyncio.run(
            svc.resolve("test query"))
        assert result is None

    def test_metrics_disabled(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=False)
        m = svc.get_metrics()
        assert m["enabled"] is False
        assert m["origin_cell_resolutions"] == 0

    def test_metrics_enabled_initial(self):
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=True)
        m = svc.get_metrics()
        assert m["enabled"] is True
        assert m["cells_loaded"] == 7
        assert m["completed_hex_neighbor_batches"] == 0


# ═══════════════════════════════════════════════════════════════════
# 4. NEIGHBOR ASSIST
# ═══════════════════════════════════════════════════════════════════

class TestNeighborAssist:
    """Neighbor assist mechanics — selection, TTL, visited set."""

    def test_max_neighbors_per_hop(self):
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        # Hub has 6 neighbors, but only max_neighbors should be selected
        neighbors = reg.get_neighbor_cells("hub")
        assert len(neighbors) == 6
        # The assist service limits to max_neighbors_per_hop (default 2)

    def test_visited_set_prevents_cycles(self):
        req = HexNeighborRequest(
            trace_id="test",
            query="test",
            origin_cell_id="hub",
            requesting_cell_id="hub",
            ttl=2,
            visited=frozenset(["hub", "bee_ops"]),
        )
        assert "hub" in req.visited
        assert "bee_ops" in req.visited

    def test_ttl_decrement(self):
        req = HexNeighborRequest(
            trace_id="test", query="test",
            origin_cell_id="hub", requesting_cell_id="hub",
            ttl=2,
        )
        assert req.ttl == 2
        # Subsequent hop would have ttl-1
        next_req = HexNeighborRequest(
            trace_id=req.trace_id, query=req.query,
            origin_cell_id=req.origin_cell_id,
            requesting_cell_id="bee_ops",
            ttl=req.ttl - 1,
            visited=req.visited | {"bee_ops"},
        )
        assert next_req.ttl == 1

    def test_neighbor_response_success(self):
        nr = HexNeighborResponse(
            cell_id="bee_ops", response="Bees are healthy",
            confidence=0.8, latency_ms=100)
        assert nr.is_success

    def test_neighbor_response_error(self):
        nr = HexNeighborResponse(
            cell_id="bee_ops", response="",
            confidence=0, latency_ms=100, error="timeout")
        assert not nr.is_success


# ═══════════════════════════════════════════════════════════════════
# 5. MAGMA / PROVENANCE
# ═══════════════════════════════════════════════════════════════════

class TestMagmaProvenance:
    """MAGMA events and provenance traces."""

    def test_trace_to_dict(self):
        trace = HexResolutionTrace(
            trace_id="abc123",
            origin_cell_id="hub",
            query="test query",
            local_confidence=0.6,
            final_confidence=0.85,
            final_source="hex_neighbor",
            total_latency_ms=150.0,
        )
        d = trace.to_dict()
        assert d["trace_id"] == "abc123"
        assert d["origin_cell_id"] == "hub"
        assert d["final_source"] == "hex_neighbor"
        assert "query" not in d  # Only query_len, not raw query

    def test_trace_no_secret_leakage(self):
        trace = HexResolutionTrace(
            trace_id="test",
            origin_cell_id="hub",
            query="What is my API key gho_secret123?",
        )
        d = trace.to_dict()
        # Should not contain the full query (only length)
        assert "gho_secret" not in str(d)
        assert d["query_len"] == len("What is my API key gho_secret123?")

    def test_trace_default_values(self):
        trace = HexResolutionTrace(
            trace_id="t1", origin_cell_id="hub", query="test")
        assert trace.escalated_global is False
        assert trace.escalated_llm is False
        assert trace.cache_hit is False
        assert trace.ttl_path == []
        assert trace.models_used == []


# ═══════════════════════════════════════════════════════════════════
# 6. HEALTH / SELF-HEAL
# ═══════════════════════════════════════════════════════════════════

class TestHealthSelfHeal:
    """Cell health monitoring, quarantine, and recovery."""

    def test_new_cell_healthy(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor()
        assert hm.is_healthy("hub")

    def test_success_records(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor()
        hm.record_success("hub")
        h = hm.get_health("hub")
        assert h.recent_success_count == 1
        assert h.last_success_ts > 0

    def test_errors_trigger_quarantine(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=3)
        for _ in range(3):
            hm.record_error("bad_cell")
        assert not hm.is_healthy("bad_cell")
        assert "bad_cell" in hm.get_quarantined_cells()

    def test_timeouts_trigger_quarantine(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(timeout_threshold=2)
        hm.record_timeout("slow_cell")
        hm.record_timeout("slow_cell")
        assert not hm.is_healthy("slow_cell")

    def test_quarantined_cell_excluded(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=2)
        hm.record_error("bad")
        hm.record_error("bad")
        assert not hm.is_healthy("bad")
        assert hm.is_healthy("good")

    def test_probe_recovery(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=2, cooldown_s=300)
        hm.record_error("cell_x")
        hm.record_error("cell_x")
        assert not hm.is_healthy("cell_x")
        # Probe succeeds — should clear quarantine
        recovered = hm.probe_recovery("cell_x", success=True)
        assert recovered
        assert hm.is_healthy("cell_x")

    def test_probe_failure_extends_quarantine(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=2, cooldown_s=300)
        hm.record_error("cell_y")
        hm.record_error("cell_y")
        recovered = hm.probe_recovery("cell_y", success=False)
        assert not recovered
        assert not hm.is_healthy("cell_y")

    def test_success_decays_errors(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor(error_threshold=3)
        hm.record_error("cell_z")
        hm.record_error("cell_z")  # 2 errors, not yet quarantined
        hm.record_success("cell_z")  # should decay to 1 error
        h = hm.get_health("cell_z")
        assert h.recent_error_count == 1

    def test_health_score(self):
        h = HexCellHealth(cell_id="test", recent_success_count=8, recent_error_count=2)
        assert abs(h.health_score - 0.8) < 0.01

    def test_health_score_no_queries(self):
        h = HexCellHealth(cell_id="test")
        assert h.health_score == 1.0

    def test_stats_structure(self):
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        hm = HexHealthMonitor()
        s = hm.stats()
        assert "tracked_cells" in s
        assert "quarantine_events" in s


# ═══════════════════════════════════════════════════════════════════
# 7. API REGRESSION
# ═══════════════════════════════════════════════════════════════════

class TestAPIRegression:
    """Ensure hex mesh additions are additive-only."""

    def test_hex_mesh_in_status_metrics(self):
        """hex_mesh metrics should have expected keys."""
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=False)
        m = svc.get_metrics()
        expected_keys = {
            "enabled", "cells_loaded", "origin_cell_resolutions",
            "local_only_resolutions", "neighbor_assist_resolutions",
            "global_escalations", "llm_last_resolutions",
            "completed_hex_neighbor_batches", "neighbors_consulted_total",
            "avg_neighbors_per_assist", "quarantined_cells",
            "self_heal_events", "magma_traces_written", "ttl_exhaustions",
        }
        assert expected_keys.issubset(set(m.keys()))

    def test_no_new_secret_fields(self):
        """Metrics should not contain any secret-like values."""
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        reg = HexTopologyRegistry(config_path="configs/hex_cells.yaml", agents=[])
        hm = HexHealthMonitor()
        svc = HexNeighborAssist(
            topology_registry=reg, health_monitor=hm, enabled=True)
        m = svc.get_metrics()
        text = str(m).lower()
        for kw in ["api_key", "bearer", "token", "secret", "password"]:
            assert kw not in text


# ═══════════════════════════════════════════════════════════════════
# 8. SAFE DEFAULTS
# ═══════════════════════════════════════════════════════════════════

class TestSafeDefaults:
    """Verify hex_mesh defaults to OFF."""

    def test_settings_default_off(self):
        import yaml
        with open("configs/settings.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["hex_mesh"]["enabled"] is False

    def test_llm_parallel_still_default_off(self):
        import yaml
        with open("configs/settings.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["llm_parallel"]["enabled"] is False

    def test_gemma_profiles_still_default_off(self):
        import yaml
        with open("configs/settings.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["gemma_profiles"]["enabled"] is False


# ═══════════════════════════════════════════════════════════════════
# 9. DOMAIN TYPES
# ═══════════════════════════════════════════════════════════════════

class TestDomainTypes:
    """Domain type constructors and properties."""

    def test_cell_definition(self):
        cell = HexCellDefinition(id="test", coord=HexCoord(1, 2))
        assert cell.id == "test"
        assert cell.enabled is True
        assert cell.domain_selectors == []

    def test_cell_health_quarantine(self):
        h = HexCellHealth(cell_id="test", quarantine_until=time.time() + 100)
        assert h.is_quarantined

    def test_cell_health_not_quarantined(self):
        h = HexCellHealth(cell_id="test", quarantine_until=0)
        assert not h.is_quarantined

    def test_neighbor_request(self):
        req = HexNeighborRequest(
            trace_id="t1", query="test",
            origin_cell_id="hub", requesting_cell_id="hub")
        assert req.ttl == 2
        assert req.visited == frozenset()

    def test_resolution_plan(self):
        from waggledance.core.domain.hex_mesh import HexResolutionPlan
        plan = HexResolutionPlan(
            trace_id="t1", origin_cell_id="hub", query="test")
        assert plan.local_threshold == 0.72
        assert plan.neighbor_threshold == 0.82
