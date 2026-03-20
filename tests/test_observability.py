"""Tests for Prometheus observability."""
import pytest


def test_metrics_import():
    """Observability module must import without errors."""
    from core.observability import CHAT_REQUESTS, CHAT_LATENCY, ACTIVE_AGENTS
    assert CHAT_REQUESTS is not None


def test_counter_increment():
    """Counters must be incrementable."""
    from core.observability import CHAT_REQUESTS
    before = CHAT_REQUESTS.labels(method="test", language="fi", agent_id="test")._value.get()
    CHAT_REQUESTS.labels(method="test", language="fi", agent_id="test").inc()
    after = CHAT_REQUESTS.labels(method="test", language="fi", agent_id="test")._value.get()
    assert after == before + 1


def test_histogram():
    """Latency histogram must accept observations."""
    from core.observability import CHAT_LATENCY
    CHAT_LATENCY.observe(0.123)  # Should not raise


def test_build_info():
    """Build info must be settable."""
    from core.observability import BUILD_INFO
    BUILD_INFO.info({"version": "3.2.0", "profile": "cottage"})
