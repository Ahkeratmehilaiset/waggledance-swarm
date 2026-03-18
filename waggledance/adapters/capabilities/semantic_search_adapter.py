"""Adapter wrapping legacy core.chromadb_adapter.ChromaDBAdapter as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ChromaDBAdapter = None


def _get_class():
    global _ChromaDBAdapter
    if _ChromaDBAdapter is None:
        try:
            from core.chromadb_adapter import ChromaDBAdapter
            _ChromaDBAdapter = ChromaDBAdapter
        except ImportError:
            _ChromaDBAdapter = None
    return _ChromaDBAdapter


class SemanticSearchAdapter:
    """Capability adapter for ChromaDB semantic memory search.

    Wraps ChromaDBAdapter.search() to provide embedding-based retrieval
    through the autonomy capability interface.
    """

    CAPABILITY_ID = "retrieve.semantic_search"

    def __init__(self, chromadb_adapter=None):
        self._adapter = chromadb_adapter
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._adapter is not None

    def execute(self, embedding: Optional[List[float]] = None,
                query: str = "", top_k: int = 5,
                collection: str = "waggle_memory") -> Dict[str, Any]:
        """Search ChromaDB for semantically similar documents.

        Args:
            embedding: query embedding vector
            query: text query (for logging only; embedding required for search)
            top_k: number of results to return
            collection: ChromaDB collection name
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._adapter:
            return {"success": False, "error": "ChromaDB adapter not available",
                    "capability_id": self.CAPABILITY_ID}

        if embedding is None:
            return {"success": False, "error": "No embedding provided",
                    "capability_id": self.CAPABILITY_ID}

        results = self._adapter.search(embedding, top_k=top_k,
                                        collection=collection)
        elapsed = (time.monotonic() - t0) * 1000

        if results:
            self._success_count += 1
            return {
                "success": True,
                "results": results,
                "count": len(results),
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "silver",
                "latency_ms": round(elapsed, 2),
            }
        return {
            "success": False,
            "error": "No results found",
            "results": [],
            "capability_id": self.CAPABILITY_ID,
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "successes": self._success_count,
            "success_rate": (self._success_count / self._call_count
                             if self._call_count else 0.0),
        }
