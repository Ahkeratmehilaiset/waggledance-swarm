"""Readiness service — checks all components for startup/health probes."""

import logging
import time

from waggledance.application.dto.readiness_dto import ComponentStatus, ReadinessStatus
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.ports.llm_port import LLMPort
from waggledance.core.ports.vector_store_port import VectorStorePort

log = logging.getLogger(__name__)


class ReadinessService:
    """Checks all system components and reports aggregate readiness."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        vector_store: VectorStorePort,
        llm: LLMPort,
    ) -> None:
        self._orchestrator = orchestrator
        self._vector_store = vector_store
        self._llm = llm
        self._start_time = time.monotonic()

    async def check(self) -> ReadinessStatus:
        """Check all components. Never raises, always returns status."""
        components: list[ComponentStatus] = []

        components.append(await self._check_llm())
        components.append(await self._check_vector_store())
        components.append(await self._check_orchestrator())

        all_ready = all(c.ready for c in components)
        uptime = time.monotonic() - self._start_time

        return ReadinessStatus(
            ready=all_ready,
            components=components,
            uptime_seconds=uptime,
        )

    async def wait_until_ready(
        self,
        timeout_seconds: float = 30.0,
    ) -> bool:
        """Poll readiness until ready or timeout. Returns final ready state."""
        import asyncio

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = await self.check()
            if status.ready:
                return True
            await asyncio.sleep(1.0)
        return False

    async def _check_llm(self) -> ComponentStatus:
        """Check LLM availability."""
        start = time.monotonic()
        try:
            available = await self._llm.is_available()
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="llm",
                ready=available,
                message="OK" if available else "LLM unavailable",
                latency_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="llm",
                ready=False,
                message=str(e),
                latency_ms=elapsed,
            )

    async def _check_vector_store(self) -> ComponentStatus:
        """Check vector store availability."""
        start = time.monotonic()
        try:
            ready = await self._vector_store.is_ready()
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="vector_store",
                ready=ready,
                message="OK" if ready else "Vector store not ready",
                latency_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="vector_store",
                ready=False,
                message=str(e),
                latency_ms=elapsed,
            )

    async def _check_orchestrator(self) -> ComponentStatus:
        """Check orchestrator readiness."""
        start = time.monotonic()
        try:
            ready = await self._orchestrator.is_ready()
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="orchestrator",
                ready=ready,
                message="OK" if ready else "Orchestrator not ready",
                latency_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ComponentStatus(
                name="orchestrator",
                ready=False,
                message=str(e),
                latency_ms=elapsed,
            )
