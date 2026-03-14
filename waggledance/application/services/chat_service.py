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

        features = extract_features(
            query=req.query,
            hot_cache_hit=False,
            memory_score=memory_score,
            matched_keywords=[],
            profile=req.profile,
            language=language,
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

    @staticmethod
    def _detect_language(query: str, hint: str) -> str:
        """Detect query language. FI chars -> fi, otherwise use hint or default."""
        if hint != "auto":
            return hint
        if any(c in FI_CHARS for c in query):
            return "fi"
        return "en"
