# implements MemoryRepositoryPort
"""
Production MemoryRepositoryPort backed by ChromaDB via VectorStorePort.

Uses composition (not inheritance): delegates all vector operations to
the injected VectorStorePort instance. This adapter translates between
MemoryRecord domain objects and the flat dict format of VectorStorePort.

Using InMemoryRepository in non-stub mode is FORBIDDEN per the master plan.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

# Conditional import of MemoryRecord: try Agent 1's domain module first,
# fall back to a compatible local dataclass if it does not exist yet.
try:
    from waggledance.core.domain.memory_record import MemoryRecord
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class MemoryRecord:  # type: ignore[no-redef]
        id: str
        content: str
        content_fi: str | None
        source: str
        confidence: float
        tags: list[str]
        agent_id: str | None
        created_at: float
        ttl_seconds: int | None

if TYPE_CHECKING:
    from waggledance.core.ports.vector_store_port import VectorStorePort


class ChromaMemoryRepository:
    """Production MemoryRepositoryPort backed by ChromaDB via VectorStorePort.

    - ``store()`` upserts to the ``waggle_memory`` collection.
    - ``search()`` queries ``waggle_memory``, converts results to MemoryRecord.
    - ``store_correction()`` upserts to the ``corrections`` collection.
    """

    def __init__(
        self,
        vector_store: VectorStorePort,
        collection: str = "waggle_memory",
    ) -> None:
        self._vector_store = vector_store
        self._collection = collection

    async def search(
        self,
        query: str,
        limit: int = 5,
        language: str = "en",
        tags: list[str] | None = None,
    ) -> list[MemoryRecord]:
        """Search via vector store, convert results to MemoryRecord.

        If ``tags`` are provided, a ChromaDB ``$in`` where-filter is applied
        to narrow results before scoring. Language is stored in metadata
        but does not currently filter results (bilingual records are common).
        """
        where: dict | None = None
        if tags:
            where = {"tags": {"$in": tags}}

        results = await self._vector_store.query(
            text=query,
            n_results=limit,
            collection=self._collection,
            where=where,
        )
        return [self._to_memory_record(r) for r in results]

    async def store(self, record: MemoryRecord) -> None:
        """Persist a MemoryRecord to the vector store.

        Metadata is flattened for ChromaDB storage. The ``tags`` field
        is serialised as a JSON string because ChromaDB metadata values
        must be scalars.
        """
        import json

        # Flatten tags to JSON string for ChromaDB scalar metadata requirement
        tags_serialised = json.dumps(record.tags) if record.tags else "[]"

        metadata: dict = {
            "source": record.source,
            "confidence": record.confidence,
            "tags": tags_serialised,
            "agent_id": record.agent_id or "",
            "created_at": record.created_at,
            "language": "fi" if record.content_fi else "en",
        }
        if record.content_fi:
            metadata["content_fi"] = record.content_fi
        if record.ttl_seconds is not None:
            metadata["ttl_seconds"] = record.ttl_seconds

        await self._vector_store.upsert(
            id=record.id,
            text=record.content,
            metadata=metadata,
            collection=self._collection,
        )

    async def store_correction(
        self,
        query: str,
        wrong: str,
        correct: str,
    ) -> None:
        """Store a correction record in the ``corrections`` collection.

        Corrections are stored as documents combining the original query,
        the wrong answer, and the correct answer, enabling both keyword
        and semantic retrieval of past corrections.
        """
        correction_id = str(uuid.uuid4())
        correction_text = f"Q: {query}\nWrong: {wrong}\nCorrect: {correct}"
        metadata = {
            "type": "correction",
            "query": query[:500],
            "created_at": time.time(),
        }
        await self._vector_store.upsert(
            id=correction_id,
            text=correction_text,
            metadata=metadata,
            collection="corrections",
        )

    def _to_memory_record(self, result: dict) -> MemoryRecord:
        """Convert a VectorStorePort result dict to a MemoryRecord."""
        import json

        meta = result.get("metadata", {})

        # Deserialise tags from JSON string or accept list directly
        raw_tags = meta.get("tags", "[]")
        if isinstance(raw_tags, str):
            try:
                tags = json.loads(raw_tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        elif isinstance(raw_tags, list):
            tags = raw_tags
        else:
            tags = []

        agent_id_raw = meta.get("agent_id", "")
        agent_id = agent_id_raw if agent_id_raw else None

        return MemoryRecord(
            id=result.get("id", ""),
            content=result.get("text", ""),
            content_fi=meta.get("content_fi"),
            source=meta.get("source", "vector_store"),
            confidence=float(meta.get("confidence", result.get("score", 0.0))),
            tags=tags,
            agent_id=agent_id,
            created_at=float(meta.get("created_at", 0.0)),
            ttl_seconds=meta.get("ttl_seconds"),
        )
