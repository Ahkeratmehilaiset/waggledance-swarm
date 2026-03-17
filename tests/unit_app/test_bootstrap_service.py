"""Unit tests for BootstrapService — agent loading and cache warming."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from waggledance.application.services.bootstrap_service import BootstrapService
from waggledance.core.domain.memory_record import MemoryRecord


@pytest.fixture
def mock_lifecycle():
    lc = MagicMock()
    lc.spawn_for_profile.side_effect = lambda agents, profile: agents
    return lc


@pytest.fixture
def bootstrap_svc(mock_config, mock_lifecycle):
    return BootstrapService(mock_config, mock_lifecycle)


class TestBootstrapLoadAgents:
    """Tests for BootstrapService.load_agents()."""

    @pytest.mark.asyncio
    async def test_missing_dir_returns_empty(self, bootstrap_svc, tmp_path):
        result = await bootstrap_svc.load_agents(tmp_path / "nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        result = await bootstrap_svc.load_agents(agents_dir)
        assert result == []

    @pytest.mark.asyncio
    async def test_loads_valid_yaml(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test_agent.yaml").write_text(
            "header:\n"
            "  agent_id: test_1\n"
            "  agent_name: Test Agent\n"
            "  domain: testing\n"
            "profiles:\n"
            "  - COTTAGE\n"
            "tags:\n"
            "  - test\n"
            "DECISION_METRICS_AND_THRESHOLDS:\n"
            "  skill_a: 0.5\n",
            encoding="utf-8",
        )
        result = await bootstrap_svc.load_agents(agents_dir)
        assert len(result) == 1
        assert result[0].id == "test_1"
        assert result[0].name == "Test Agent"
        assert result[0].domain == "testing"

    @pytest.mark.asyncio
    async def test_skips_invalid_yaml(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "bad.yaml").write_text("not: valid: yaml: {{", encoding="utf-8")
        (agents_dir / "good.yaml").write_text(
            "header:\n  agent_id: good\n  agent_name: Good\n",
            encoding="utf-8",
        )
        result = await bootstrap_svc.load_agents(agents_dir)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_skips_empty_yaml(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "empty.yaml").write_text("", encoding="utf-8")
        result = await bootstrap_svc.load_agents(agents_dir)
        assert result == []

    @pytest.mark.asyncio
    async def test_calls_lifecycle_spawn(
        self, bootstrap_svc, mock_lifecycle, tmp_path
    ):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "a.yaml").write_text(
            "header:\n  agent_id: a1\n  agent_name: Agent A\n",
            encoding="utf-8",
        )
        await bootstrap_svc.load_agents(agents_dir)
        mock_lifecycle.spawn_for_profile.assert_called_once()

    @pytest.mark.asyncio
    async def test_loads_subdirectories(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        sub = agents_dir / "sub"
        sub.mkdir(parents=True)
        (sub / "nested.yaml").write_text(
            "header:\n  agent_id: nested\n  agent_name: Nested\n",
            encoding="utf-8",
        )
        result = await bootstrap_svc.load_agents(agents_dir)
        assert len(result) == 1
        assert result[0].id == "nested"

    @pytest.mark.asyncio
    async def test_agent_defaults(self, bootstrap_svc, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "minimal.yaml").write_text(
            "header:\n  agent_id: min\n",
            encoding="utf-8",
        )
        result = await bootstrap_svc.load_agents(agents_dir)
        assert len(result) == 1
        assert result[0].domain == "general"
        assert result[0].tags == []
        assert result[0].active is False  # lifecycle mock returns as-is


class TestBootstrapWarmCache:
    """Tests for BootstrapService.warm_cache_candidates()."""

    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self, bootstrap_svc):
        mock_ms = AsyncMock()
        mock_ms.retrieve_context.return_value = []
        result = await bootstrap_svc.warm_cache_candidates(mock_ms)
        assert result == []

    @pytest.mark.asyncio
    async def test_high_confidence_included(self, bootstrap_svc):
        mock_ms = AsyncMock()
        mock_ms.retrieve_context.return_value = [
            MemoryRecord(
                id="r1", content="Important fact about bees",
                content_fi=None, source="test", confidence=0.9,
            )
        ]
        result = await bootstrap_svc.warm_cache_candidates(mock_ms)
        assert len(result) == 1
        key, value, ttl = result[0]
        assert "important fact" in key
        assert value == "Important fact about bees"
        assert ttl == 7200

    @pytest.mark.asyncio
    async def test_low_confidence_excluded(self, bootstrap_svc):
        mock_ms = AsyncMock()
        mock_ms.retrieve_context.return_value = [
            MemoryRecord(
                id="r1", content="Weak fact",
                content_fi=None, source="test", confidence=0.5,
            )
        ]
        result = await bootstrap_svc.warm_cache_candidates(mock_ms)
        assert result == []

    @pytest.mark.asyncio
    async def test_exactly_threshold_included(self, bootstrap_svc):
        mock_ms = AsyncMock()
        mock_ms.retrieve_context.return_value = [
            MemoryRecord(
                id="r1", content="Borderline fact",
                content_fi=None, source="test", confidence=0.8,
            )
        ]
        result = await bootstrap_svc.warm_cache_candidates(mock_ms)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_queries_common_questions(self, bootstrap_svc):
        mock_ms = AsyncMock()
        mock_ms.retrieve_context.return_value = []
        await bootstrap_svc.warm_cache_candidates(mock_ms)
        mock_ms.retrieve_context.assert_awaited_once_with(
            query="common questions", language="en", limit=20,
        )
