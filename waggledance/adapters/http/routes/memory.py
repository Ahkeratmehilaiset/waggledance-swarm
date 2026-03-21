"""Memory ingest and search HTTP routes -- thin wrappers around MemoryService."""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from waggledance.adapters.http.deps import get_memory_service

router = APIRouter()


class IngestRequest(BaseModel):
    """Pydantic model for memory ingest requests."""

    content: str
    source: str = "api"
    tags: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """Pydantic model for memory search requests."""

    query: str
    language: str = "en"
    limit: int = 5


@router.post("/memory/ingest")
async def ingest_memory(
    request: IngestRequest,
    memory_service=Depends(get_memory_service),
):
    """Ingest a piece of content into memory via MemoryService."""
    record = await memory_service.ingest(
        content=request.content,
        source=request.source,
        tags=request.tags,
    )
    return {"id": record.id, "status": "stored"}


@router.post("/memory/search")
async def search_memory(
    request: SearchRequest,
    memory_service=Depends(get_memory_service),
):
    """Search memory via MemoryService and return matching records."""
    results = await memory_service.retrieve_context(
        query=request.query,
        language=request.language,
        limit=request.limit,
    )
    return {"results": [asdict(r) for r in results]}
