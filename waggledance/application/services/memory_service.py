"""Memory service — sole writer of persistent memory per STATE_OWNERSHIP.md.

Ported from core/memory_engine.py service-level operations.
"""

import logging
import time
import uuid

from waggledance.core.domain.events import DomainEvent, EventType
from waggledance.core.domain.memory_record import MemoryRecord
from waggledance.core.ports.event_bus_port import EventBusPort
from waggledance.core.ports.memory_repository_port import MemoryRepositoryPort
from waggledance.core.ports.vector_store_port import VectorStorePort

log = logging.getLogger(__name__)


class MemoryService:
    """Service for memory ingestion, retrieval, and corrections."""

    def __init__(
        self,
        vector_store: VectorStorePort,
        memory: MemoryRepositoryPort,
        event_bus: EventBusPort,
        hybrid_retrieval=None,
    ) -> None:
        assert memory is not None, "MemoryRepositoryPort must not be None"
        self._vector_store = vector_store
        self._memory = memory
        self._event_bus = event_bus
        self._hybrid_retrieval = hybrid_retrieval

    async def ingest(
        self,
        content: str,
        source: str,
        tags: list[str] | None = None,
        agent_id: str | None = None,
        intent: str = "chat",
    ) -> MemoryRecord:
        """Store a new fact in persistent memory and vector store.

        When hybrid retrieval is enabled, additionally mirrors to
        the correct cell-local FAISS index. Failure does not block
        the global ChromaDB path.
        """
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            content=content,
            content_fi=None,
            source=source,
            confidence=0.8,
            tags=tags or [],
            agent_id=agent_id,
            created_at=time.time(),
            ttl_seconds=None,
        )

        await self._memory.store(record)

        await self._vector_store.upsert(
            id=record.id,
            text=record.content,
            metadata={
                "source": record.source,
                "agent_id": record.agent_id or "",
                "tags": ",".join(record.tags),
            },
            collection="waggle_memory",
        )

        # v3.4: Mirror to cell-local FAISS when hybrid is enabled
        if self._hybrid_retrieval and self._hybrid_retrieval.enabled:
            try:
                embed_fn = getattr(self._hybrid_retrieval, '_embed_fn', None)
                if embed_fn:
                    vec = embed_fn(record.content)
                    if vec is not None:
                        await self._hybrid_retrieval.ingest(
                            doc_id=record.id,
                            text=record.content,
                            vector=vec,
                            intent=intent,
                            metadata={
                                "source": record.source,
                                "agent_id": record.agent_id or "",
                            },
                        )
            except Exception as e:
                log.debug("Hybrid FAISS mirror failed (non-blocking): %s", e)

        await self._event_bus.publish(DomainEvent(
            type=EventType.MEMORY_STORED,
            payload={"record_id": record.id, "source": source},
            timestamp=time.time(),
            source="memory_service",
        ))

        return record

    async def retrieve_context(
        self,
        query: str,
        language: str = "en",
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Search memory for relevant context."""
        results = await self._memory.search(
            query=query,
            limit=limit,
            language=language,
        )

        if results:
            await self._event_bus.publish(DomainEvent(
                type=EventType.MEMORY_RETRIEVED,
                payload={"query": query[:50], "results": len(results)},
                timestamp=time.time(),
                source="memory_service",
            ))

        return results

    async def record_correction(
        self,
        query: str,
        wrong: str,
        correct: str,
    ) -> None:
        """Store a correction in persistent memory."""
        await self._memory.store_correction(query, wrong, correct)

        await self._event_bus.publish(DomainEvent(
            type=EventType.CORRECTION_RECORDED,
            payload={"query": query[:50]},
            timestamp=time.time(),
            source="memory_service",
        ))
