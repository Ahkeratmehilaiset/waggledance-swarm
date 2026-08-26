"""Memory adapters for the WaggleDance refactored architecture.

Exports:
    ChromaVectorStore       — Production VectorStorePort (ChromaDB, opt-in [chroma] extra)
    InMemoryVectorStore     — Stub VectorStorePort (no deps)
    InMemoryRepository      — Stub MemoryRepositoryPort (no deps)
    ChromaMemoryRepository  — Production MemoryRepositoryPort (ChromaDB via VectorStorePort)
    HotCache                — HotCachePort (in-process LRU with TTL)
    SQLiteSharedMemory      — Shared memory (aiosqlite, WAL mode)

Dependency remediation 2026-08-26: chromadb is de-scoped from the stable
default install, so the Chroma-backed exports resolve lazily (PEP 562).
Importing this package never requires chromadb; only actually touching
``ChromaVectorStore`` / ``ChromaMemoryRepository`` does, and a missing
package then surfaces as the container's explicit fail-closed error.
"""
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.memory.hot_cache import HotCache
from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory

_LAZY_EXPORTS = {
    "ChromaVectorStore": "waggledance.adapters.memory.chroma_vector_store",
    "ChromaMemoryRepository": "waggledance.adapters.memory.chroma_memory_repository",
}

__all__ = [
    "ChromaVectorStore",
    "InMemoryVectorStore",
    "InMemoryRepository",
    "ChromaMemoryRepository",
    "HotCache",
    "SQLiteSharedMemory",
]


def __getattr__(name: str):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(__all__)
