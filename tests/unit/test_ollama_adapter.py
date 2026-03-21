"""Unit tests for OllamaAdapter — circuit breaker, retries, error handling."""

import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from waggledance.adapters.llm.ollama_adapter import (
    OllamaAdapter,
    OPEN_THRESHOLD,
    RECOVERY_SECONDS,
)


@pytest.fixture
def adapter():
    return OllamaAdapter(
        base_url="http://localhost:11434",
        default_model="test-model",
        timeout_seconds=5.0,
        max_retries=2,
    )


class TestOllamaInit:
    """Constructor and basic properties."""

    def test_default_model(self, adapter):
        assert adapter.get_active_model() == "test-model"

    def test_default_base_url(self):
        a = OllamaAdapter()
        assert a._base_url == "http://localhost:11434"

    def test_custom_num_gpu(self):
        a = OllamaAdapter(num_gpu=1)
        assert a._num_gpu == 1


class TestCircuitBreaker:
    """Circuit breaker open/close logic."""

    def test_circuit_starts_closed(self, adapter):
        assert not adapter._is_circuit_open()

    def test_circuit_opens_after_threshold(self, adapter):
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        assert adapter._is_circuit_open()

    def test_circuit_stays_closed_below_threshold(self, adapter):
        for _ in range(OPEN_THRESHOLD - 1):
            adapter._record_failure()
        assert not adapter._is_circuit_open()

    def test_success_resets_failures(self, adapter):
        adapter._record_failure()
        adapter._record_failure()
        adapter._record_success()
        assert adapter._circuit_failures == 0

    def test_circuit_recovers_after_timeout(self, adapter):
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        assert adapter._is_circuit_open()
        # Simulate time passing
        adapter._circuit_open_since = time.monotonic() - RECOVERY_SECONDS - 1
        assert not adapter._is_circuit_open()

    def test_trip_counter_increments(self, adapter):
        assert adapter._circuit_total_trips == 0
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        assert adapter._circuit_total_trips == 1

    def test_success_closes_open_circuit(self, adapter):
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        assert adapter._is_circuit_open()
        adapter._record_success()
        assert not adapter._is_circuit_open()


class TestGenerate:
    """generate() method with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_successful_generate(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Generated text"}
        mock_resp.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.generate("test prompt")
        assert result == "Generated text"

    @pytest.mark.asyncio
    async def test_generate_with_custom_model(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Custom"}
        mock_resp.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.generate("prompt", model="llama3.2:1b")
        call_kwargs = adapter._client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["model"] == "llama3.2:1b"

    @pytest.mark.asyncio
    async def test_generate_default_model(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Default"}
        mock_resp.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=mock_resp)

        await adapter.generate("prompt", model="default")
        payload = adapter._client.post.call_args[1]["json"]
        assert payload["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_generate_blocked_by_circuit_breaker(self, adapter):
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        result = await adapter.generate("blocked prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_generate_connect_error_returns_empty(self, adapter):
        adapter._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        result = await adapter.generate("prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_generate_connect_error_opens_circuit(self, adapter):
        adapter._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        for _ in range(OPEN_THRESHOLD):
            await adapter.generate("prompt")
        assert adapter._is_circuit_open()

    @pytest.mark.asyncio
    async def test_generate_empty_response_retries(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": ""}
        mock_resp.raise_for_status = MagicMock()
        adapter._client.post = AsyncMock(return_value=mock_resp)

        result = await adapter.generate("prompt")
        assert result == ""
        assert adapter._client.post.call_count == 2  # max_retries=2

    @pytest.mark.asyncio
    async def test_generate_includes_num_gpu(self):
        a = OllamaAdapter(num_gpu=2, max_retries=1)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "ok"}
        mock_resp.raise_for_status = MagicMock()
        a._client.post = AsyncMock(return_value=mock_resp)

        await a.generate("prompt")
        payload = a._client.post.call_args[1]["json"]
        assert payload["options"]["num_gpu"] == 2


class TestIsAvailable:
    """is_available() health check."""

    @pytest.mark.asyncio
    async def test_available_when_200(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        adapter._client.get = AsyncMock(return_value=mock_resp)
        assert await adapter.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_500(self, adapter):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        adapter._client.get = AsyncMock(return_value=mock_resp)
        assert await adapter.is_available() is False

    @pytest.mark.asyncio
    async def test_unavailable_on_exception(self, adapter):
        adapter._client.get = AsyncMock(side_effect=Exception("connection refused"))
        assert await adapter.is_available() is False


class TestClose:
    """close() cleanup."""

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, adapter):
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        adapter._client = mock_client
        await adapter.close()
        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_skips_if_already_closed(self, adapter):
        mock_client = MagicMock()
        mock_client.is_closed = True
        mock_client.aclose = AsyncMock()
        adapter._client = mock_client
        await adapter.close()
        mock_client.aclose.assert_not_awaited()
