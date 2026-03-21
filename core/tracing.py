"""OpenTelemetry tracing setup for WaggleDance.

Exports spans to OTLP endpoint (Jaeger, Grafana Tempo, etc.)
or falls back to console exporter for development.

Usage:
    from core.tracing import get_tracer
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("agent_id", agent_id)
        result = do_work()
"""

import logging
import os

log = logging.getLogger("waggledance.tracing")

_tracer_provider = None


def setup_tracing(service_name: str = "waggledance",
                  otlp_endpoint: str = None):
    """Initialize OpenTelemetry tracing. Call once at startup."""
    global _tracer_provider
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        _tracer_provider = TracerProvider(resource=resource)

        # OTLP exporter (Jaeger/Tempo)
        endpoint = otlp_endpoint or os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            exporter = OTLPSpanExporter(endpoint=endpoint)
            _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            log.info("OTEL tracing: exporting to %s", endpoint)
        else:
            # Console exporter for dev
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
            _tracer_provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter()))
            log.info("OTEL tracing: console exporter (no OTLP endpoint)")

        trace.set_tracer_provider(_tracer_provider)

    except ImportError:
        log.info("OpenTelemetry not installed — tracing disabled")


def get_tracer(name: str = "waggledance"):
    """Get a tracer instance. Returns no-op tracer if OTEL not available."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """Fallback tracer when OpenTelemetry is not installed."""
    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()


class _NoOpSpan:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def set_attribute(self, key, value):
        pass
    def set_status(self, status):
        pass
    def record_exception(self, exc):
        pass
