"""
MAGMA Layer 3: Memory overlay networks.
Provides filtered views over ChromaDB collections using metadata filters.
Each overlay shows only documents from specified agent_ids.
"""

import logging
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.memory_overlay")


class MemoryOverlay:
    """Read-only filtered view over a MemoryStore collection."""

    def __init__(self, memory_store, agent_ids: List[str], label: str = ""):
        self.memory = memory_store
        self.agent_ids = list(agent_ids)
        self.label = label or ",".join(self.agent_ids)

    def _where_filter(self) -> dict:
        if len(self.agent_ids) == 1:
            return {"agent_id": self.agent_ids[0]}
        return {"agent_id": {"$in": self.agent_ids}}

    def search(self, embedding, top_k: int = 5, min_score: float = 0.0) -> List[dict]:
        try:
            results = self.memory.collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where=self._where_filter(),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            log.warning(f"Overlay search failed: {e}")
            return []

        out = []
        if results and results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                score = 1.0 - (dist / 2.0)
                if score >= min_score:
                    out.append({
                        "id": doc_id,
                        "text": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                        "score": score,
                    })
        return out

    def count(self) -> int:
        try:
            return self.memory.collection.count()
        except Exception:
            return 0

    def list_ids(self, limit: int = 100) -> List[str]:
        try:
            results = self.memory.collection.get(
                where=self._where_filter(),
                limit=limit,
                include=[],
            )
            return results["ids"] if results else []
        except Exception:
            return []


class OverlayRegistry:
    """Manages named overlay networks."""

    def __init__(self, memory_store):
        self.memory = memory_store
        self._overlays: Dict[str, MemoryOverlay] = {}

    def register(self, name: str, agent_ids: List[str]) -> MemoryOverlay:
        ov = MemoryOverlay(self.memory, agent_ids, label=name)
        self._overlays[name] = ov
        return ov

    def get(self, name: str) -> Optional[MemoryOverlay]:
        return self._overlays.get(name)

    def list_all(self) -> dict:
        return {name: {"agents": ov.agent_ids}
                for name, ov in self._overlays.items()}
