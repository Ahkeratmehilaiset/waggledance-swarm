"""Unit tests for ReadinessService — component checks and readiness."""

import pytest
from unittest.mock import AsyncMock

from waggledance.application.services.readiness_service import ReadinessService


class TestReadinessCheck:
    """Tests for ReadinessService.check()."""

    @pytest.mark.asyncio
    async def test_all_ready_returns_true(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is True

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_not_ready(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_llm.is_available.return_value = False
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False

    @pytest.mark.asyncio
    async def test_vector_store_not_ready(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_vector_store.is_ready.return_value = False
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False

    @pytest.mark.asyncio
    async def test_orchestrator_not_ready(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_orchestrator.is_ready.return_value = False
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False

    @pytest.mark.asyncio
    async def test_check_returns_three_components(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert len(status.components) == 3
        names = {c.name for c in status.components}
        assert names == {"llm", "vector_store", "orchestrator"}

    @pytest.mark.asyncio
    async def test_check_uptime_is_positive(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_component_has_latency(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        for comp in status.components:
            assert comp.latency_ms is not None
            assert comp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_component_messages_on_success(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        for comp in status.components:
            assert comp.message == "OK"

    @pytest.mark.asyncio
    async def test_component_message_on_failure(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_llm.is_available.return_value = False
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        llm_comp = next(c for c in status.components if c.name == "llm")
        assert llm_comp.message == "LLM unavailable"
        assert llm_comp.ready is False


class TestReadinessException:
    """Tests for exception handling in component checks."""

    @pytest.mark.asyncio
    async def test_llm_exception_returns_not_ready(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_llm.is_available.side_effect = ConnectionError("connection refused")
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False
        llm_comp = next(c for c in status.components if c.name == "llm")
        assert "connection refused" in llm_comp.message

    @pytest.mark.asyncio
    async def test_vector_store_exception(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_vector_store.is_ready.side_effect = RuntimeError("db locked")
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False
        vs_comp = next(c for c in status.components if c.name == "vector_store")
        assert "db locked" in vs_comp.message

    @pytest.mark.asyncio
    async def test_orchestrator_exception(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_orchestrator.is_ready.side_effect = TimeoutError("timeout")
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False
        orch_comp = next(c for c in status.components if c.name == "orchestrator")
        assert "timeout" in orch_comp.message

    @pytest.mark.asyncio
    async def test_all_components_failing(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_llm.is_available.side_effect = Exception("llm down")
        mock_vector_store.is_ready.side_effect = Exception("vs down")
        mock_orchestrator.is_ready.side_effect = Exception("orch down")
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        status = await svc.check()
        assert status.ready is False
        assert all(not c.ready for c in status.components)


class TestReadinessWaitUntilReady:
    """Tests for ReadinessService.wait_until_ready()."""

    @pytest.mark.asyncio
    async def test_wait_returns_true_when_ready(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        result = await svc.wait_until_ready(timeout_seconds=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_returns_false_on_timeout(
        self, mock_orchestrator, mock_vector_store, mock_llm
    ):
        mock_llm.is_available.return_value = False
        svc = ReadinessService(mock_orchestrator, mock_vector_store, mock_llm)
        result = await svc.wait_until_ready(timeout_seconds=0.1)
        assert result is False
