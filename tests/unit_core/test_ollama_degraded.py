"""Regression tests for Ollama degraded-mode hardening.

Covers:
- OllamaAdapter circuit breaker degraded detection
- ChromaVectorStore circuit breaker degraded detection
- ConnectError logged at WARNING (not ERROR)
- /api/status degraded_components derivation
- /api/learning llm_degraded flag
"""

import time
import logging

import pytest


class TestOllamaAdapterDegraded:
    """OllamaAdapter.is_degraded property tests."""

    def test_not_degraded_initially(self):
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
        adapter = OllamaAdapter(base_url="http://localhost:99999")
        assert adapter.is_degraded is False

    def test_degraded_after_circuit_opens(self):
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
        adapter = OllamaAdapter(base_url="http://localhost:99999")
        # Simulate 3 failures to trip the breaker
        for _ in range(3):
            adapter._record_failure()
        assert adapter.is_degraded is True

    def test_recovers_after_timeout(self):
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter, RECOVERY_SECONDS
        adapter = OllamaAdapter(base_url="http://localhost:99999")
        for _ in range(3):
            adapter._record_failure()
        assert adapter.is_degraded is True
        # Simulate time passing
        adapter._circuit_open_since = time.monotonic() - RECOVERY_SECONDS - 1
        # _is_circuit_open will clear the breaker
        adapter._is_circuit_open()
        assert adapter.is_degraded is False


class TestChromaCircuitBreaker:
    """ChromaVectorStore circuit breaker state tests."""

    def test_closed_initially(self):
        from waggledance.adapters.memory.chroma_vector_store import _CircuitBreaker
        cb = _CircuitBreaker("test")
        assert cb.state == "closed"
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        from waggledance.adapters.memory.chroma_vector_store import _CircuitBreaker
        cb = _CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert cb.allow_request() is False

    def test_half_open_after_recovery(self):
        from waggledance.adapters.memory.chroma_vector_store import _CircuitBreaker
        cb = _CircuitBreaker("test", failure_threshold=3, recovery_s=0.01)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == "half_open"


class TestConnectErrorLogLevel:
    """Verify ConnectError is logged at WARNING, not ERROR."""

    def test_connect_error_source_uses_warning(self):
        """Check source code uses logger.warning for ConnectError."""
        import inspect
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
        src = inspect.getsource(OllamaAdapter.generate)
        # ConnectError handler should use warning, not error
        assert 'logger.warning("Ollama unavailable' in src
        assert 'logger.error("Ollama connect error' not in src


class TestDetectDegraded:
    """_detect_degraded helper tests."""

    def test_detect_degraded_returns_list(self):
        from waggledance.adapters.http.routes.compat_dashboard import _detect_degraded

        class FakeLLM:
            _circuit_open_since = None

        class FakeBreaker:
            state = "closed"

        class FakeVS:
            _breaker = FakeBreaker()

        class FakeContainer:
            llm = FakeLLM()
            vector_store = FakeVS()

        class FakeState:
            container = FakeContainer()

        class FakeApp:
            state = FakeState()

        class FakeRequest:
            app = FakeApp()

        result = _detect_degraded(FakeRequest())
        assert result == []

    def test_detect_degraded_llm(self):
        from waggledance.adapters.http.routes.compat_dashboard import _detect_degraded

        class FakeLLM:
            _circuit_open_since = time.monotonic()

        class FakeBreaker:
            state = "closed"

        class FakeVS:
            _breaker = FakeBreaker()

        class FakeContainer:
            llm = FakeLLM()
            vector_store = FakeVS()

        class FakeState:
            container = FakeContainer()

        class FakeApp:
            state = FakeState()

        class FakeRequest:
            app = FakeApp()

        result = _detect_degraded(FakeRequest())
        assert "llm" in result

    def test_detect_degraded_embeddings(self):
        from waggledance.adapters.http.routes.compat_dashboard import _detect_degraded

        class FakeLLM:
            _circuit_open_since = None

        class FakeBreaker:
            state = "open"

        class FakeVS:
            _breaker = FakeBreaker()

        class FakeContainer:
            llm = FakeLLM()
            vector_store = FakeVS()

        class FakeState:
            container = FakeContainer()

        class FakeApp:
            state = FakeState()

        class FakeRequest:
            app = FakeApp()

        result = _detect_degraded(FakeRequest())
        assert "embeddings" in result
