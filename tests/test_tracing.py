"""Tests for OpenTelemetry tracing integration."""


def test_get_tracer_without_otel():
    """get_tracer must work even without opentelemetry installed."""
    from core.tracing import get_tracer
    tracer = get_tracer("test")
    # Must not crash
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("key", "value")


def test_noop_span():
    from core.tracing import _NoOpSpan
    span = _NoOpSpan()
    span.set_attribute("x", 1)
    span.record_exception(ValueError("test"))


def test_noop_span_context_manager():
    from core.tracing import _NoOpSpan
    with _NoOpSpan() as span:
        span.set_attribute("key", "value")
        span.set_status("OK")


def test_structured_logging_setup():
    """setup_logging must not crash."""
    from core.structured_logging import setup_logging, get_logger
    setup_logging(json_output=False)
    log = get_logger("test")
    assert log is not None


def test_get_logger_returns_logger():
    from core.structured_logging import get_logger
    log = get_logger("waggledance.test")
    assert log is not None
