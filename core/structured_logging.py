"""Structured logging setup with structlog.

Produces JSON logs in production, colored console in development.
Integrates with OpenTelemetry trace context.

Usage:
    from core.structured_logging import setup_logging, get_logger
    setup_logging(json_output=True)
    log = get_logger("my_module")
    log.info("chat_request", query="hello", agent_id="beekeeper", latency_ms=42.5)
"""

import logging
import os
import sys


def setup_logging(json_output: bool = None, level: str = "INFO"):
    """Setup structured logging. Call once at startup."""
    if json_output is None:
        json_output = os.environ.get("WAGGLE_LOG_FORMAT", "text") == "json"

    try:
        import structlog

        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]

        # Add trace context if OTEL available
        try:
            from opentelemetry import trace

            def add_trace_context(logger, method_name, event_dict):
                span = trace.get_current_span()
                ctx = span.get_span_context()
                if ctx.is_valid:
                    event_dict["trace_id"] = format(ctx.trace_id, "032x")
                    event_dict["span_id"] = format(ctx.span_id, "016x")
                return event_dict

            processors.append(add_trace_context)
        except ImportError:
            pass

        if json_output:
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.append(structlog.dev.ConsoleRenderer())

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper(), logging.INFO)),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True)

        logging.basicConfig(
            format="%(message)s", stream=sys.stdout,
            level=getattr(logging, level.upper(), logging.INFO))

    except ImportError:
        # structlog not available — use standard logging
        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            level=getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str):
    """Get a structured logger. Falls back to standard logging."""
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)
