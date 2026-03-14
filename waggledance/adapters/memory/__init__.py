"""Memory adapters for the WaggleDance refactored architecture.

Exports:
    ChromaVectorStore       — Production VectorStorePort (ChromaDB)
    InMemoryVectorStore     — Stub VectorStorePort (no deps)
    InMemoryRepository      — Stub MemoryRepositoryPort (no deps)
    ChromaMemoryRepository  — Production MemoryRepositoryPort (ChromaDB via VectorStorePort)
    HotCache                — HotCachePort (in-process LRU with TTL)
    SQLiteSharedMemory      — Shared memory (aiosqlite, WAL mode)
"""
from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.memory.chroma_memory_repository import ChromaMemoryRepository
from waggledance.adapters.memory.hot_cache import HotCache
from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory

__all__ = [
    "ChromaVectorStore",
    "InMemoryVectorStore",
    "InMemoryRepository",
    "ChromaMemoryRepository",
    "HotCache",
    "SQLiteSharedMemory",
]
