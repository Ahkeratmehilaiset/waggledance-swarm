"""Use case: store a new fact in memory."""

from waggledance.application.services.memory_service import MemoryService
from waggledance.core.domain.memory_record import MemoryRecord


async def store_memory(
    content: str,
    source: str,
    memory_service: MemoryService,
    tags: list[str] | None = None,
) -> MemoryRecord:
    """Ingest a new fact into persistent memory."""
    return await memory_service.ingest(content=content, source=source, tags=tags)
