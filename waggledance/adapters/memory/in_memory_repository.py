# implements MemoryRepositoryPort
"""
In-memory MemoryRepositoryPort implementation for stub mode and unit tests.

No external dependencies. Uses simple substring matching for search.
All data is ephemeral and lost when the process exits.

IMPORTANT: Using InMemoryRepository in non-stub Container is FORBIDDEN
per WAGGLEDANCE_REFACTOR_MASTER_v2.3.md (NO SILENT FALLBACKS RULE).
"""
from __future__ import annotations

import logging
import time

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


class InMemoryRepository:
    """In-memory implementation of MemoryRepositoryPort for testing.

    Stores MemoryRecord objects in a plain list and corrections in a
    separate list. Search uses case-insensitive substring matching
    with optional tag filtering and language-aware content selection.
    """

    def __init__(self) -> None:
        self._records: list[MemoryRecord] = []
        self._corrections: list[dict] = []

    async def search(
        self,
        query: str,
        limit: int = 5,
        language: str = "en",
        tags: list[str] | None = None,
    ) -> list[MemoryRecord]:
        """Substring-based search across stored MemoryRecords.

        Matches against both ``content`` and ``content_fi`` fields.
        Results are scored by a simple word-overlap heuristic and
        returned sorted by descending score.
        """
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 1]

        scored: list[tuple[float, MemoryRecord]] = []
        now = time.time()

        for record in self._records:
            # TTL expiry check
            if record.ttl_seconds is not None:
                if now - record.created_at > record.ttl_seconds:
                    continue

            # Tag filtering
            if tags is not None:
                if not any(t in record.tags for t in tags):
                    continue

            # Build searchable text from both language fields
            searchable_parts: list[str] = []
            if record.content:
                searchable_parts.append(record.content.lower())
            if record.content_fi:
                searchable_parts.append(record.content_fi.lower())
            searchable = " ".join(searchable_parts)

            if not query_words:
                # Empty query matches everything with low score
                scored.append((0.1, record))
                continue

            matched = sum(1 for w in query_words if w in searchable)
            if matched == 0:
                continue

            score = matched / len(query_words)
            # Boost by stored confidence
            score = min(score * (0.5 + record.confidence * 0.5), 1.0)
            scored.append((score, record))

        # Sort by descending score
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]

    async def store(self, record: MemoryRecord) -> None:
        """Append a MemoryRecord to the in-memory store.

        If a record with the same id already exists, it is replaced.
        """
        # Replace existing record with same id
        self._records = [r for r in self._records if r.id != record.id]
        self._records.append(record)

    async def store_correction(
        self,
        query: str,
        wrong: str,
        correct: str,
    ) -> None:
        """Store a correction entry for later retrieval."""
        self._corrections.append({
            "query": query,
            "wrong": wrong,
            "correct": correct,
            "created_at": time.time(),
        })

    # ── Test helpers (not part of MemoryRepositoryPort) ───────

    @property
    def record_count(self) -> int:
        """Number of stored records (test introspection only)."""
        return len(self._records)

    @property
    def correction_count(self) -> int:
        """Number of stored corrections (test introspection only)."""
        return len(self._corrections)

    def clear(self) -> None:
        """Clear all records and corrections (test cleanup)."""
        self._records.clear()
        self._corrections.clear()
