"""Memory repository port — persistent fact storage and search."""

from typing import Protocol

from waggledance.core.domain.memory_record import MemoryRecord


class MemoryRepositoryPort(Protocol):
    """Port for persistent memory storage and retrieval."""

    async def search(
        self,
        query: str,
        limit: int = 5,
        language: str = "en",
        tags: list[str] | None = None,
    ) -> list[MemoryRecord]: ...

    async def store(self, record: MemoryRecord) -> None: ...

    async def store_correction(
        self,
        query: str,
        wrong: str,
        correct: str,
    ) -> None: ...
