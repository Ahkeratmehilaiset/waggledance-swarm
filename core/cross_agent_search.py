"""
MAGMA Layer 4: Cross-agent memory search facade.
Provides role/domain/provenance-filtered queries over shared memory.
"""

import logging
from typing import List

log = logging.getLogger("waggledance.cross_agent_search")


class CrossAgentSearch:
    """Facade for cross-agent memory queries with role/domain/provenance filtering."""

    def __init__(self, consciousness, overlay_registry, channel_registry, provenance):
        self._consciousness = consciousness
        self._overlays = overlay_registry
        self._channels = channel_registry
        self._provenance = provenance

    def _get_or_create_overlay(self, name: str, agent_ids: List[str]):
        ov = self._overlays.get(name)
        if not ov:
            ov = self._overlays.register(name, agent_ids)
        return ov

    def search_by_role(self, query_embedding, role: str,
                       top_k: int = 5) -> List[dict]:
        ch = self._channels.get(role)
        if not ch:
            return []
        ov = self._get_or_create_overlay(f"role_{role}", ch.agent_ids)
        return ov.search(query_embedding, top_k=top_k)

    def search_by_channel(self, query_embedding, channel_name: str,
                          top_k: int = 5) -> List[dict]:
        ch = self._channels.get(channel_name)
        if not ch:
            return []
        ov = self._get_or_create_overlay(f"ch_{channel_name}", ch.agent_ids)
        return ov.search(query_embedding, top_k=top_k)

    def search_with_provenance(self, query_embedding,
                               top_k: int = 5) -> List[dict]:
        results = self._consciousness.memory.search(
            query_embedding, top_k=top_k)
        decorated = []
        for r in results:
            doc_id = r.id if hasattr(r, 'id') else r.get('id', '')
            entry = {
                "id": doc_id,
                "text": r.text if hasattr(r, 'text') else r.get('text', ''),
                "score": r.score if hasattr(r, 'score') else r.get('score', 0),
                "metadata": r.metadata if hasattr(r, 'metadata') else r.get('metadata', {}),
                "provenance": self._provenance.get_provenance_chain(doc_id),
            }
            decorated.append(entry)
        return decorated

    def get_consensus_facts(self, query_embedding,
                            min_validators: int = 2,
                            top_k: int = 5) -> List[dict]:
        validated = self._provenance.get_validated_facts(min_validators)
        if not validated:
            return []
        fact_ids = {v["fact_id"] for v in validated}
        results = self._consciousness.memory.search(
            query_embedding, top_k=top_k * 3)
        out = []
        for r in results:
            doc_id = r.id if hasattr(r, 'id') else r.get('id', '')
            if doc_id in fact_ids:
                out.append({
                    "id": doc_id,
                    "text": r.text if hasattr(r, 'text') else r.get('text', ''),
                    "score": r.score if hasattr(r, 'score') else r.get('score', 0),
                    "provenance": self._provenance.get_provenance_chain(doc_id),
                })
                if len(out) >= top_k:
                    break
        return out
