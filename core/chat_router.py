"""Unified chat router — single entry point for all chat paths.

Replaces the inline autonomy/legacy/hex branching scattered across
chat_handler.py and chat_service.py.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("waggledance.chat_router")


@dataclass
class ChatResult:
    """Unified result from any chat path."""
    response: str
    method: str          # "autonomy" | "legacy" | "hexagonal" | "fallback"
    confidence: float = 0.0
    agent_id: str = ""
    model_used: str = ""
    fallback_used: bool = False
    latency_ms: float = 0.0


class ChatRouter:
    """Routes chat through autonomy → legacy → fallback chain.

    Usage:
        router = ChatRouter(autonomy_service=svc, legacy_handler=handler)
        result = await router.route("Mikä on pesän lämpötila?")
    """

    def __init__(self, autonomy_service=None, legacy_handler=None, hex_service=None):
        self._autonomy = autonomy_service
        self._legacy = legacy_handler
        self._hex = hex_service
        self._total_requests = 0
        self._fallback_count = 0

    async def route(self, message: str, language: str = "fi",
                    source: str = "chat") -> ChatResult:
        """Try paths in priority order: autonomy → legacy → fallback."""
        t0 = time.perf_counter()
        self._total_requests += 1

        # 1. Autonomy path (highest priority, if configured)
        if self._autonomy:
            try:
                result = self._autonomy.handle_query(
                    message, {"language": language, "source": source})
                if result.get("executed") and not result.get("error"):
                    answer = (result.get("result") or {}).get("answer", "")
                    if answer:
                        return ChatResult(
                            response=answer,
                            method="autonomy",
                            confidence=0.9 if result.get("quality_path") == "gold" else 0.7,
                            agent_id=result.get("capability", "autonomy"),
                            model_used="autonomy_runtime",
                            latency_ms=(time.perf_counter() - t0) * 1000)
                log.info("Autonomy path returned no result, trying legacy")
            except Exception as e:
                log.warning("Autonomy route failed, trying legacy: %s", e)

        # 2. Legacy path
        if self._legacy:
            try:
                response = await self._legacy._do_chat(message, language, source)
                if response:
                    return ChatResult(
                        response=response,
                        method="legacy",
                        confidence=0.5,
                        agent_id=getattr(self._legacy, '_last_chat_agent_id', ''),
                        model_used=getattr(self._legacy, '_last_chat_method', ''),
                        fallback_used=True,
                        latency_ms=(time.perf_counter() - t0) * 1000)
            except Exception as e:
                log.warning("Legacy route failed: %s", e)

        # 3. Hexagonal path (if available)
        if self._hex:
            try:
                response = await self._hex.handle(message, language)
                if response:
                    return ChatResult(
                        response=response,
                        method="hexagonal",
                        confidence=0.3,
                        fallback_used=True,
                        latency_ms=(time.perf_counter() - t0) * 1000)
            except Exception as e:
                log.warning("Hexagonal route failed: %s", e)

        # 4. Final fallback
        self._fallback_count += 1
        log.error("All chat paths failed for: %s", message[:80])
        return ChatResult(
            response="Anteeksi, en pysty vastaamaan juuri nyt. Yritä uudelleen.",
            method="fallback",
            confidence=0.0,
            fallback_used=True,
            latency_ms=(time.perf_counter() - t0) * 1000)

    @property
    def stats(self) -> dict:
        return {
            "total_requests": self._total_requests,
            "fallback_count": self._fallback_count,
            "fallback_rate": self._fallback_count / max(self._total_requests, 1),
        }
