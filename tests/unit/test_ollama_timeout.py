"""Unit tests for OllamaAdapter — timeout and circuit breaker behavior."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from waggledance.adapters.llm.ollama_adapter import (
    OPEN_THRESHOLD,
    RECOVERY_SECONDS,
    OllamaAdapter,
)


class TestOllamaAdapterTimeout:
    """Timeout configuration."""

    def test_default_timeout_is_at_least_120_seconds(self) -> None:
        adapter = OllamaAdapter()
        assert adapter._timeout_seconds >= 120.0

    def test_custom_timeout_is_passed_to_httpx_client(self) -> None:
        adapter = OllamaAdapter(timeout_seconds=300.0)
        assert adapter._timeout_seconds == 300.0
        # Verify the httpx client was created with the custom timeout
        client_timeout = adapter._client.timeout
        assert client_timeout.read == 300.0
        assert client_timeout.write == 300.0
        # connect timeout is always 10.0
        assert client_timeout.connect == 10.0


class TestOllamaAdapterCircuitBreaker:
    """Circuit breaker activates after OPEN_THRESHOLD failures."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_activates_after_threshold_failures(self) -> None:
        adapter = OllamaAdapter(timeout_seconds=5.0, max_retries=1)

        # Simulate OPEN_THRESHOLD failures by calling _record_failure directly
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()

        # Circuit should now be open
        assert adapter._is_circuit_open() is True
        assert adapter._circuit_total_trips == 1

        # generate() should return empty string when circuit is open
        result = await adapter.generate("test prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovers_after_timeout(self) -> None:
        adapter = OllamaAdapter(timeout_seconds=5.0, max_retries=1)

        # Trip the circuit breaker
        for _ in range(OPEN_THRESHOLD):
            adapter._record_failure()
        assert adapter._is_circuit_open() is True

        # Simulate time passing beyond RECOVERY_SECONDS
        adapter._circuit_open_since = time.monotonic() - RECOVERY_SECONDS - 1.0

        # Circuit should now be closed
        assert adapter._is_circuit_open() is False

    def test_success_resets_failure_count(self) -> None:
        adapter = OllamaAdapter()
        adapter._circuit_failures = 2
        adapter._record_success()
        assert adapter._circuit_failures == 0

    def test_open_threshold_value(self) -> None:
        assert OPEN_THRESHOLD == 3

    @pytest.mark.asyncio
    async def test_close_shuts_down_client(self) -> None:
        adapter = OllamaAdapter()
        assert not adapter._client.is_closed
        await adapter.close()
        assert adapter._client.is_closed
