"""Vector store port — embedding-based similarity search."""

from typing import Protocol


class VectorStorePort(Protocol):
    """Port for vector similarity search and storage."""

    async def upsert(
        self,
        id: str,
        text: str,
        metadata: dict,
        collection: str = "default",
    ) -> None: ...

    async def query(
        self,
        text: str,
        n_results: int = 5,
        collection: str = "default",
        where: dict | None = None,
    ) -> list[dict]: ...

    async def is_ready(self) -> bool: ...
