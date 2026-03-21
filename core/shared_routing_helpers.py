"""Shared routing helpers — v1.18.0 convergence layer.

Avoids duplicating routing logic between legacy (chat_handler.py) and
hexagonal (ChatService) runtimes.  Both runtimes import these helpers
rather than maintaining parallel implementations.
"""

import logging

log = logging.getLogger(__name__)

# ─── Cached micromodel singleton ───────────────────────────────

_pattern_engine = None
_pattern_engine_init_attempted = False


def probe_micromodel(query: str) -> tuple[bool, float]:
    """Probe V1 PatternMatchEngine.  Returns (hit, confidence).

    The engine is instantiated once and reused for all subsequent calls.
    """
    global _pattern_engine, _pattern_engine_init_attempted
    if not _pattern_engine_init_attempted:
        _pattern_engine_init_attempted = True
        try:
            from core.micro_model import PatternMatchEngine
            _pattern_engine = PatternMatchEngine()
        except Exception as e:
            log.warning("PatternMatchEngine init failed: %s", e)
            _pattern_engine = None

    if _pattern_engine is None:
        return False, 0.0
    try:
        result = _pattern_engine.predict(query)
        if result and result.get("confidence", 0) > 0:
            return True, float(result["confidence"])
    except Exception:
        pass
    return False, 0.0


# ─── Route telemetry singleton ─────────────────────────────────

_route_telemetry = None


def get_route_telemetry():
    """Return the shared RouteTelemetry singleton."""
    global _route_telemetry
    if _route_telemetry is None:
        from core.route_telemetry import RouteTelemetry
        _route_telemetry = RouteTelemetry()
    return _route_telemetry


# ─── Learning ledger singleton ─────────────────────────────────

_learning_ledger = None


def get_learning_ledger():
    """Return the shared LearningLedger singleton."""
    global _learning_ledger
    if _learning_ledger is None:
        from core.learning_ledger import LearningLedger
        _learning_ledger = LearningLedger()
    return _learning_ledger
