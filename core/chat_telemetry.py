"""Chat telemetry — metrics recording, hot cache, WebSocket notifications.

Extracted from chat_handler.py (v3.3 refactor).
"""
import logging

log = logging.getLogger("hivemind")

# Prometheus metrics (optional)
_METRICS_AVAILABLE = False
try:
    from core.observability import CHAT_REQUESTS, CHAT_LATENCY, HALLUCINATION_DETECTED
    _METRICS_AVAILABLE = True
except ImportError:
    pass

# Shared singletons
from core.shared_routing_helpers import get_route_telemetry as _get_route_telemetry
from core.shared_routing_helpers import get_learning_ledger as _get_learning_ledger


class ChatTelemetry:
    """Records metrics, route telemetry, and manages hot cache."""

    def __init__(self, hive):
        self._hive = hive

    def record_request_telemetry(self, route_type: str, confidence: float,
                                 latency_ms: float, success: bool,
                                 query: str, was_fallback: bool = False):
        """Record route telemetry and low-confidence ledger entries."""
        hive = self._hive

        # Prometheus metrics
        if _METRICS_AVAILABLE:
            try:
                CHAT_REQUESTS.labels(
                    method=getattr(hive, '_last_chat_method', route_type) or route_type,
                    language="fi",
                    agent_id=getattr(hive, '_last_chat_agent_id', route_type) or route_type,
                ).inc()
                CHAT_LATENCY.observe(latency_ms / 1000.0)
            except Exception:
                pass

        # Route telemetry
        try:
            _get_route_telemetry().record(route_type, latency_ms, success, was_fallback)
        except Exception:
            pass

        # Learning ledger for low-confidence queries
        try:
            if confidence < 0.6:
                _get_learning_ledger().log(
                    "low_confidence_query",
                    agent_id=route_type,
                    query=query[:500],
                    confidence=confidence,
                    route=route_type,
                    latency_ms=round(latency_ms, 1),
                )
        except Exception:
            pass

    def populate_hot_cache(self, query: str, response: str,
                           score: float = 0.75, source: str = "chat",
                           detected_lang: str = "fi"):
        """Auto-populate HotCache with successful Finnish answers."""
        hive = self._hive
        if not (getattr(hive, 'consciousness', None) and
                getattr(hive.consciousness, 'hot_cache', None)):
            return
        if not self._is_valid_response(response):
            return
        if detected_lang != "fi":
            return
        if score < 0.6:
            return
        try:
            hive.consciousness.hot_cache.put(query, response, score, source=source)
        except Exception:
            pass

    @staticmethod
    def _is_valid_response(response: str) -> bool:
        """Check if response is valid."""
        if not response or not response.strip():
            return False
        if len(response.strip()) < 5:
            return False
        bad_markers = ["[LLM-virhe", "[Ollama ei vastaa", "error", "503"]
        for marker in bad_markers:
            if marker in response[:50]:
                return False
        return True
