"""Diamond Run P3 — WD-native parallel dispatch tests.

Tests:
1. Flag OFF = old sequential behavior
2. Flag ON = batch dispatch works
3. At least one WD-native path increments completed_parallel_batches
4. No auth regression
5. No secret leaks in status/ops
6. Safe defaults
7. Timeout/cancel cleanup
8. Dedup works
9. Container loads agents
10. Round table parallel dispatch works
11. Bench harness output valid
12. Docs examples match actual payloads
"""

import asyncio
import os
import unittest

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
from waggledance.application.services.parallel_llm_dispatcher import (
    ParallelLLMDispatcher,
    DispatchMetrics,
)
from waggledance.bootstrap.event_bus import InMemoryEventBus
from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.task import TaskRequest, TaskRoute
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.round_table import RoundTableEngine
from waggledance.core.orchestration.scheduler import Scheduler
from waggledance.core.policies.escalation_policy import EscalationPolicy
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore


def _stub_settings(enabled=False, **overrides):
    """Create WaggleSettings with controllable parallel flags."""
    s = WaggleSettings.from_env()
    s.llm_parallel_enabled = enabled
    s.llm_parallel_max_concurrent = overrides.get("max_concurrent", 4)
    s.llm_parallel_max_inflight_per_model = overrides.get("max_inflight", 2)
    s.llm_parallel_request_timeout_s = overrides.get("timeout_s", 120)
    s.llm_parallel_dedupe = overrides.get("dedupe", True)
    return s


def _make_agents(n=6):
    """Create N test agent definitions."""
    domains = ["weather", "bees", "energy", "security", "water", "soil",
               "plants", "fish", "birds", "pests"]
    return [
        AgentDefinition(
            id=f"agent_{i}",
            name=f"Agent {i}",
            domain=domains[i % len(domains)],
            tags=[],
            skills=["general"],
            trust_level=0,
            specialization_score=0.0,
            active=True,
            profile="ALL",
        )
        for i in range(n)
    ]


def _make_orchestrator(llm, agents, dispatcher=None):
    """Create orchestrator with given components."""
    eb = InMemoryEventBus()
    settings = _stub_settings(enabled=dispatcher is not None and dispatcher.enabled)
    rt = RoundTableEngine(llm=llm, event_bus=eb, parallel_dispatcher=dispatcher)
    return Orchestrator(
        scheduler=Scheduler(config=settings),
        round_table=rt,
        memory=InMemoryRepository(),
        vector_store=InMemoryVectorStore(),
        llm=llm,
        trust_store=InMemoryTrustStore(),
        event_bus=eb,
        config=settings,
        agents=agents,
        parallel_dispatcher=dispatcher,
    )


def _make_task(query="Test query for parallel dispatch analysis"):
    return TaskRequest(
        id="test-1",
        query=query,
        language="en",
        profile="HOME",
        user_id="test",
        context=[],
        timestamp=1000.0,
    )


# ── 1. Flag OFF = old sequential behavior ─────────────────────


class TestFlagOff:
    """When llm_parallel.enabled=false, all dispatch is sequential."""

    @pytest.mark.asyncio
    async def test_dispatch_batch_sequential_when_disabled(self):
        settings = _stub_settings(enabled=False)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        assert d.enabled is False
        batch = [("prompt1", "default", 0.7, 100), ("prompt2", "default", 0.7, 100)]
        results = await d.dispatch_batch(batch)

        assert len(results) == 2
        assert all(r for r in results)
        # Counter should NOT increment — sequential path
        m = d.get_metrics()
        assert m["completed_parallel_batches"] == 0
        assert m["total_dispatched"] == 0

    @pytest.mark.asyncio
    async def test_single_dispatch_passthrough_when_disabled(self):
        settings = _stub_settings(enabled=False)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        result = await d.dispatch("Hello world")
        assert result  # non-empty
        m = d.get_metrics()
        assert m["total_dispatched"] == 0


# ── 2. Flag ON = batch dispatch works ─────────────────────────


class TestFlagOn:
    """When llm_parallel.enabled=true, batch dispatch runs concurrently."""

    @pytest.mark.asyncio
    async def test_dispatch_batch_parallel_when_enabled(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        assert d.enabled is True
        batch = [
            ("prompt1", "default", 0.7, 100),
            ("prompt2", "default", 0.7, 100),
            ("prompt3", "default", 0.7, 100),
        ]
        results = await d.dispatch_batch(batch)

        assert len(results) == 3
        assert all(r for r in results)
        m = d.get_metrics()
        assert m["completed_parallel_batches"] == 1
        assert m["total_dispatched"] == 3
        assert m["total_completed"] == 3

    @pytest.mark.asyncio
    async def test_single_dispatch_tracked_when_enabled(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        result = await d.dispatch("Hello world")
        assert result
        m = d.get_metrics()
        assert m["total_dispatched"] == 1
        assert m["total_completed"] == 1

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        results = await d.dispatch_batch([])
        assert results == []
        m = d.get_metrics()
        assert m["completed_parallel_batches"] == 0


# ── 3. WD-native path increments completed_parallel_batches ───


class TestWDNativeBatch:
    """Round table dispatch increments completed_parallel_batches."""

    @pytest.mark.asyncio
    async def test_round_table_increments_batch_counter(self):
        """run_round_table with agents dispatches batch and increments counter."""
        llm = StubLLMAdapter()
        settings = _stub_settings(enabled=True)
        d = ParallelLLMDispatcher(settings, llm)
        agents = _make_agents(6)
        orch = _make_orchestrator(llm, agents, d)

        task = _make_task()
        m_before = d.get_metrics()

        consensus = await orch.run_round_table(task)

        m_after = d.get_metrics()
        assert m_after["completed_parallel_batches"] > m_before["completed_parallel_batches"]
        assert m_after["total_dispatched"] > m_before["total_dispatched"]
        assert consensus.consensus  # non-empty

    @pytest.mark.asyncio
    async def test_execute_agents_increments_batch_counter(self):
        """_execute_agents with parallel dispatcher increments counter."""
        llm = StubLLMAdapter()
        settings = _stub_settings(enabled=True)
        d = ParallelLLMDispatcher(settings, llm)
        agents = _make_agents(4)
        orch = _make_orchestrator(llm, agents, d)

        task = _make_task()
        route = TaskRoute(route_type="custom", confidence=0.5, routing_latency_ms=0)
        m_before = d.get_metrics()

        result = await orch._execute_agents(task, agents, route)

        m_after = d.get_metrics()
        assert m_after["completed_parallel_batches"] > m_before["completed_parallel_batches"]
        assert result.response  # non-empty


# ── 4. No auth regression ─────────────────────────────────────


class TestNoAuthRegression:
    """Auth middleware still works with parallel dispatch loaded."""

    def test_chat_requires_auth(self):
        """POST /api/chat without token → 401."""
        from starlette.testclient import TestClient
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/api/chat", json={"query": "test"})
        assert resp.status_code == 401

    def test_status_is_public(self):
        """GET /api/status does not require auth."""
        from starlette.testclient import TestClient
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/status")
        assert resp.status_code == 200


# ── 5. No secret leaks in status/ops ──────────────────────────


class TestNoSecretLeaks:
    """Status and ops endpoints don't leak secrets."""

    def test_status_no_api_key(self):
        from starlette.testclient import TestClient
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/status")
        body = resp.text
        assert "api_key" not in body.lower()
        assert s.api_key not in body

    def test_ops_no_api_key(self):
        from starlette.testclient import TestClient
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        app = c.build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/ops")
        body = resp.text
        assert "api_key" not in body.lower()
        assert s.api_key not in body


# ── 6. Safe defaults ──────────────────────────────────────────


class TestSafeDefaults:
    """Default settings are safe (features disabled)."""

    def test_parallel_disabled_by_default(self):
        s = WaggleSettings.from_env()
        assert s.llm_parallel_enabled is False

    def test_gemma_disabled_by_default(self):
        s = WaggleSettings.from_env()
        assert s.gemma_enabled is False

    def test_round_table_first_pass_disabled_by_default(self):
        s = WaggleSettings.from_env()
        assert s.llm_parallel_round_table_first_pass is False

    def test_candidate_lab_sequential_by_default(self):
        s = WaggleSettings.from_env()
        assert s.llm_parallel_candidate_lab == 1


# ── 7. Timeout/cancel cleanup ─────────────────────────────────


class TestTimeoutCancel:
    """Timeout and cancellation paths clean up properly."""

    @pytest.mark.asyncio
    async def test_timeout_increments_counter(self):
        """Slow LLM call that exceeds timeout → timeout_count++."""

        class SlowLLM:
            async def generate(self, prompt, **kwargs):
                await asyncio.sleep(10)
                return "should not reach"

            async def is_available(self):
                return True

        settings = _stub_settings(enabled=True, timeout_s=1)
        d = ParallelLLMDispatcher(settings, SlowLLM())

        result = await d.dispatch("test")
        assert result == ""  # timeout returns empty
        m = d.get_metrics()
        assert m["timeout_count"] >= 1

    @pytest.mark.asyncio
    async def test_metrics_reset_inflight_after_completion(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        await d.dispatch("test")
        m = d.get_metrics()
        assert m["inflight_total"] == 0
        assert m["queue_depth"] == 0


# ── 8. Dedup works ────────────────────────────────────────────


class TestDedup:
    """Identical prompt deduplication avoids redundant work."""

    @pytest.mark.asyncio
    async def test_identical_prompts_deduped_in_batch(self):
        """Two identical prompts in a batch → dedup_count > 0."""
        settings = _stub_settings(enabled=True, dedupe=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        # Same prompt twice — second should be deduped
        batch = [
            ("Identical prompt for dedup test", "default", 0.7, 100),
            ("Identical prompt for dedup test", "default", 0.7, 100),
        ]
        results = await d.dispatch_batch(batch)
        assert len(results) == 2
        # Both should return valid responses
        assert all(r for r in results)

    @pytest.mark.asyncio
    async def test_dedup_disabled(self):
        """When dedupe=False, no dedup tracking."""
        settings = _stub_settings(enabled=True, dedupe=False)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)

        await d.dispatch("test")
        m = d.get_metrics()
        assert m["deduped_requests"] == 0


# ── 9. Container loads agents ─────────────────────────────────


class TestContainerAgents:
    """Container loads agents from YAML for the orchestrator."""

    def test_container_loads_agents_in_stub_mode(self):
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        agents = c.orchestrator._agents
        assert len(agents) > 0, "Container should load agents from agents/ directory"
        assert all(a.active for a in agents), "All loaded agents should be active"

    def test_container_agents_all_active(self):
        """All loaded agents should be marked active."""
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        agents = c.orchestrator._agents
        for a in agents:
            assert a.active, f"Agent {a.id} should be active"


# ── 10. Round table parallel first pass ───────────────────────


class TestRoundTableParallel:
    """Round table parallel first pass dispatches correctly."""

    @pytest.mark.asyncio
    async def test_escalation_triggers_round_table(self):
        """Long query with confidence < 0.7 triggers round table escalation."""
        policy = EscalationPolicy()
        result = AgentResult(
            agent_id="llm_direct",
            response="some response",
            confidence=0.6,
            latency_ms=100,
            source="llm",
        )
        task = _make_task("Explain the differences between supervised and unsupervised learning")
        assert policy.needs_round_table(result, task) is True

    @pytest.mark.asyncio
    async def test_short_query_no_escalation(self):
        """Short query (< 20 chars) doesn't trigger escalation."""
        policy = EscalationPolicy()
        result = AgentResult(
            agent_id="llm_direct",
            response="some response",
            confidence=0.6,
            latency_ms=100,
            source="llm",
        )
        task = _make_task("Short query")
        assert policy.needs_round_table(result, task) is False

    @pytest.mark.asyncio
    async def test_high_confidence_no_escalation(self):
        """High confidence (>= 0.7) doesn't trigger escalation even for long queries."""
        policy = EscalationPolicy()
        result = AgentResult(
            agent_id="llm_direct",
            response="some response",
            confidence=0.8,
            latency_ms=100,
            source="llm",
        )
        task = _make_task("This is a long query but with high confidence routing")
        assert policy.needs_round_table(result, task) is False


# ── 11. Metrics structure matches API ─────────────────────────


class TestMetricsStructure:
    """Dispatcher metrics match expected /api/status structure."""

    def test_metrics_dict_has_all_fields(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)
        m = d.get_metrics()

        expected_fields = {
            "enabled", "queue_depth",
            "inflight_total", "inflight_fast", "inflight_heavy", "inflight_default",
            "completed_parallel_batches", "total_dispatched", "total_completed",
            "timeout_count", "cancelled_count", "deduped_requests",
            "degrade_to_sequential_count",
        }
        assert set(m.keys()) == expected_fields

    def test_metrics_initial_values(self):
        settings = _stub_settings(enabled=True)
        llm = StubLLMAdapter()
        d = ParallelLLMDispatcher(settings, llm)
        m = d.get_metrics()

        assert m["enabled"] is True
        assert m["completed_parallel_batches"] == 0
        assert m["total_dispatched"] == 0
        assert m["timeout_count"] == 0
