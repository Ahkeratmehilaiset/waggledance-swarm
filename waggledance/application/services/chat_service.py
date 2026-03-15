"""Chat service — owns hot cache reads/writes per STATE_OWNERSHIP.md.

Ported from core/chat_handler.py and backend/routes/chat.py.
All memory access goes through MemoryService.retrieve_context().
"""

import logging
import time
import uuid

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.routing_policy import (
    extract_features,
    select_route,
)
from waggledance.core.policies.confidence_policy import should_cache_result
from waggledance.core.policies.escalation_policy import EscalationPolicy
from waggledance.core.ports.config_port import ConfigPort
from waggledance.core.ports.hot_cache_port import HotCachePort

log = logging.getLogger(__name__)

FI_CHARS = set("äöåÄÖÅ")

# v1.18.0: use shared helpers (convergence layer)
from core.shared_routing_helpers import probe_micromodel as _shared_probe_micromodel


class ChatService:
    """Handles chat requests end-to-end: cache, route, execute, escalate."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        memory_service: "MemoryService",  # noqa: F821 — forward ref
        hot_cache: HotCachePort,
        routing_policy_fn: object,
        config: ConfigPort,
    ) -> None:
        self._orchestrator = orchestrator
        self._memory_service = memory_service
        self._hot_cache = hot_cache
        self._routing_policy_fn = routing_policy_fn
        self._config = config
        self._escalation = EscalationPolicy()
        self._query_frequency: dict[str, int] = {}
        # v1.18.0: telemetry + ledger (lazy-init)
        self._telemetry = None
        self._ledger = None

    async def handle(self, req: ChatRequest) -> ChatResult:
        """Process a chat request through the full pipeline.

        1. Detect language
        2. Check hot cache
        3. Fetch memory context
        4. Extract routing features
        5. Select route
        6. Execute via orchestrator
        7. Apply escalation policy if needed
        8. Store result if worth caching
        9. Return ChatResult
        """
        start = time.monotonic()

        language = self._detect_language(req.query, req.language)

        cache_key = req.query.strip().lower()
        cached = self._hot_cache.get(cache_key)
        if cached is not None:
            elapsed = (time.monotonic() - start) * 1000
            return ChatResult(
                response=cached,
                language=language,
                source="hotcache",
                confidence=1.0,
                latency_ms=elapsed,
                agent_id=None,
                round_table=False,
                cached=True,
            )

        memory_context = await self._memory_service.retrieve_context(
            query=req.query,
            language=language,
            limit=5,
        )
        memory_score = max(
            (r.confidence for r in memory_context), default=0.0
        )

        self._query_frequency[cache_key] = (
            self._query_frequency.get(cache_key, 0) + 1
        )

        mm_enabled = bool(self._config.get("advanced_learning.micro_model_enabled", False))
        mm_hit = False
        mm_confidence = 0.0
        if mm_enabled:
            mm_hit, mm_confidence = self._probe_micromodel(req.query)

        features = extract_features(
            query=req.query,
            hot_cache_hit=False,
            memory_score=memory_score,
            matched_keywords=[],
            profile=req.profile,
            language=language,
            micromodel_enabled=mm_enabled,
            micromodel_hit=mm_hit,
            micromodel_confidence=mm_confidence,
        )
        route = select_route(features, self._config)

        task = TaskRequest(
            id=str(uuid.uuid4()),
            query=req.query,
            language=language,
            profile=req.profile,
            user_id=req.user_id,
            context=[],
            timestamp=time.time(),
        )

        result = await self._orchestrator.handle_task(task, route)

        round_table_used = False
        if self._escalation.needs_round_table(result, task):
            consensus = await self._orchestrator.run_round_table(task)
            if consensus.confidence > result.confidence:
                result = result.__class__(
                    agent_id="round_table",
                    response=consensus.consensus,
                    confidence=consensus.confidence,
                    latency_ms=consensus.latency_ms,
                    source="swarm",
                    metadata={},
                )
                round_table_used = True

        if should_cache_result(result, self._query_frequency.get(cache_key, 0)):
            self._hot_cache.set(cache_key, result.response, ttl=3600)

        elapsed = (time.monotonic() - start) * 1000

        # v1.18.0: record telemetry + ledger
        self._record_telemetry(
            route.route_type, result.confidence, elapsed, True, req.query)

        return ChatResult(
            response=result.response,
            language=language,
            source=result.source,
            confidence=result.confidence,
            latency_ms=elapsed,
            agent_id=result.agent_id,
            round_table=round_table_used,
            cached=False,
        )

    def _record_telemetry(self, route_type: str, confidence: float,
                           latency_ms: float, success: bool, query: str):
        """v1.18.0: Record telemetry + low-confidence ledger entries."""
        try:
            if self._telemetry is None:
                from core.route_telemetry import RouteTelemetry
                self._telemetry = RouteTelemetry()
            self._telemetry.record(route_type, latency_ms, success)
        except Exception:
            pass
        try:
            if confidence < 0.6:
                if self._ledger is None:
                    from core.learning_ledger import LearningLedger
                    self._ledger = LearningLedger()
                self._ledger.log(
                    "low_confidence_query",
                    agent_id=route_type,
                    query=query[:500],
                    confidence=confidence,
                    route=route_type,
                )
        except Exception:
            pass

    @staticmethod
    def _probe_micromodel(query: str) -> tuple[bool, float]:
        """Try legacy PatternMatchEngine (cached singleton). Returns (hit, confidence)."""
        return _shared_probe_micromodel(query)

    @staticmethod
    def _detect_language(query: str, hint: str) -> str:
        """Detect query language. FI chars -> fi, otherwise use hint or default."""
        if hint != "auto":
            return hint
        if any(c in FI_CHARS for c in query):
            return "fi"
        return "en"
