"""Adapter wrapping legacy core.faiss_store.FaissRegistry as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FaissRegistry = None


def _get_class():
    global _FaissRegistry
    if _FaissRegistry is None:
        try:
            from core.faiss_store import FaissRegistry
            _FaissRegistry = FaissRegistry
        except ImportError:
            _FaissRegistry = None
    return _FaissRegistry


class VectorSearchAdapter:
    """Capability adapter for FAISS vector similarity search.

    Wraps FaissCollection.search() through the autonomy capability interface.
    """

    CAPABILITY_ID = "retrieve.vector_search"

    def __init__(self, faiss_registry=None):
        cls = _get_class()
        self._registry = faiss_registry or (cls() if cls else None)
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._registry is not None

    def execute(self, embedding: Optional[List[float]] = None,
                collection: str = "default", top_k: int = 5,
                **kwargs) -> Dict[str, Any]:
        """Search FAISS index for similar vectors.

        Args:
            embedding: query vector (list of floats)
            collection: named FAISS collection
            top_k: number of results
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._registry:
            return {"success": False, "error": "FAISS registry not available",
                    "capability_id": self.CAPABILITY_ID}

        if embedding is None:
            return {"success": False, "error": "No embedding provided",
                    "capability_id": self.CAPABILITY_ID}

        try:
            import numpy as np
            col = self._registry.get_or_create(collection)
            vec = np.array(embedding, dtype=np.float32)
            results = col.search(vec, k=top_k)
            elapsed = (time.monotonic() - t0) * 1000

            if results:
                self._success_count += 1
                return {
                    "success": True,
                    "results": [
                        {"doc_id": r.doc_id, "text": r.text,
                         "score": round(r.score, 4), "metadata": r.metadata}
                        for r in results
                    ],
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
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": str(exc),
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
