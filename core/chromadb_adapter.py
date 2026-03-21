"""
Thin adapter over ChromaDB collections for MAGMA memory architecture.
Layer 1: Provides StoreAdapter protocol for MemoryWriteProxy.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

log = logging.getLogger("waggledance.chromadb_adapter")


@runtime_checkable
class StoreAdapter(Protocol):
    """Protocol for memory store backends."""
    def add(self, doc_id: str, text: str, embedding: list, metadata: dict,
            collection: str = "waggle_memory") -> None: ...
    def get(self, doc_id: str, collection: str = "waggle_memory") -> Optional[dict]: ...
    def get_metadata(self, doc_id: str, collection: str = "waggle_memory") -> Optional[dict]: ...
    def search(self, embedding: list, top_k: int = 5,
               collection: str = "waggle_memory") -> List[dict]: ...
    def delete(self, doc_id: str, collection: str = "waggle_memory") -> None: ...
    def update_metadata(self, doc_id: str, metadata: dict,
                        collection: str = "waggle_memory") -> None: ...
    def bulk_invalidate(self, doc_ids: List[str],
                        collection: str = "waggle_memory") -> int: ...


class ChromaDBAdapter:
    """Wraps an existing MemoryStore (or raw chromadb client) with StoreAdapter interface."""

    def __init__(self, memory_store=None, client=None):
        if memory_store is not None:
            self._client = memory_store.client
            self._breaker = getattr(memory_store, 'breaker', None)
        elif client is not None:
            self._client = client
            self._breaker = None
        else:
            raise ValueError("Provide memory_store or client")
        self._collections: Dict[str, Any] = {}

    def _col(self, name: str):
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )
        return self._collections[name]

    def _check_breaker(self) -> bool:
        if self._breaker and not self._breaker.allow_request():
            log.warning("ChromaDB adapter blocked by circuit breaker")
            return False
        return True

    def _record_success(self):
        if self._breaker:
            self._breaker.record_success()

    def _record_failure(self):
        if self._breaker:
            self._breaker.record_failure()

    def add(self, doc_id: str, text: str, embedding: list, metadata: dict,
            collection: str = "waggle_memory") -> None:
        if not self._check_breaker():
            return
        try:
            meta = metadata if metadata else {"_placeholder": "1"}
            self._col(collection).upsert(
                ids=[doc_id], documents=[text],
                embeddings=[embedding], metadatas=[meta]
            )
            self._record_success()
        except Exception as e:
            log.error(f"ChromaDB adapter add: {e}")
            self._record_failure()

    def get(self, doc_id: str, collection: str = "waggle_memory") -> Optional[dict]:
        if not self._check_breaker():
            return None
        try:
            result = self._col(collection).get(ids=[doc_id],
                                                include=["documents", "metadatas"])
            self._record_success()
            if not result["ids"]:
                return None
            return {
                "id": result["ids"][0],
                "document": result["documents"][0] if result["documents"] else "",
                "metadata": result["metadatas"][0] if result["metadatas"] else {},
            }
        except Exception as e:
            log.error(f"ChromaDB adapter get: {e}")
            self._record_failure()
            return None

    def get_metadata(self, doc_id: str, collection: str = "waggle_memory") -> Optional[dict]:
        if not self._check_breaker():
            return None
        try:
            result = self._col(collection).get(ids=[doc_id], include=["metadatas"])
            self._record_success()
            if not result["ids"]:
                return None
            return result["metadatas"][0] if result["metadatas"] else {}
        except Exception as e:
            log.error(f"ChromaDB adapter get_metadata: {e}")
            self._record_failure()
            return None

    def search(self, embedding: list, top_k: int = 5,
               collection: str = "waggle_memory") -> List[dict]:
        if not self._check_breaker():
            return []
        try:
            col = self._col(collection)
            if col.count() == 0:
                return []
            results = col.query(
                query_embeddings=[embedding],
                n_results=min(top_k, col.count()),
                include=["documents", "metadatas", "distances"]
            )
            self._record_success()
            if not results["documents"] or not results["documents"][0]:
                return []
            out = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            ):
                out.append({"document": doc, "metadata": meta,
                            "score": 1.0 - (dist / 2.0)})
            return out
        except Exception as e:
            log.error(f"ChromaDB adapter search: {e}")
            self._record_failure()
            return []

    def delete(self, doc_id: str, collection: str = "waggle_memory") -> None:
        if not self._check_breaker():
            return
        try:
            self._col(collection).delete(ids=[doc_id])
            self._record_success()
        except Exception as e:
            log.error(f"ChromaDB adapter delete: {e}")
            self._record_failure()

    def update_metadata(self, doc_id: str, metadata: dict,
                        collection: str = "waggle_memory") -> None:
        if not self._check_breaker():
            return
        try:
            self._col(collection).update(ids=[doc_id], metadatas=[metadata])
            self._record_success()
        except Exception as e:
            log.error(f"ChromaDB adapter update_metadata: {e}")
            self._record_failure()

    def bulk_invalidate(self, doc_ids: List[str],
                        collection: str = "waggle_memory") -> int:
        """Mark docs as invalidated via metadata (not delete)."""
        count = 0
        for doc_id in doc_ids:
            meta = self.get_metadata(doc_id, collection)
            if meta is not None:
                meta["_invalidated"] = True
                meta["_invalidated_at"] = time.time()
                self.update_metadata(doc_id, meta, collection)
                count += 1
        return count

    # --- Overlay stubs for Layer 4 ---
    def create_overlay_collection(self, name: str) -> str:
        overlay_name = f"overlay_{name}"
        self._col(overlay_name)
        return overlay_name

    def add_to_overlay(self, overlay: str, doc_id: str, text: str,
                       embedding: list, metadata: dict) -> None:
        self.add(doc_id, text, embedding, metadata, collection=overlay)

    def get_overlay_nodes(self, overlay: str, doc_id: str) -> Optional[dict]:
        return self.get(doc_id, collection=overlay)
