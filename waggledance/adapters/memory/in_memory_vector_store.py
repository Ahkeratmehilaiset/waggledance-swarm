# implements VectorStorePort
"""
In-memory VectorStorePort implementation for stub mode and unit tests.

No ChromaDB dependency. Uses simple substring matching for query.
All data is ephemeral and lost when the process exits.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class InMemoryVectorStore:
    """In-process VectorStorePort with substring-based query matching.

    Suitable for ``--stub`` mode and unit tests where no external
    vector database is available or desired.
    """

    def __init__(self) -> None:
        # Keyed by "{collection}:{id}" -> record dict
        self._records: dict[str, dict] = {}

    async def upsert(
        self,
        id: str,
        text: str,
        metadata: dict,
        collection: str = "default",
    ) -> None:
        """Store a document in the named collection."""
        composite_key = f"{collection}:{id}"
        self._records[composite_key] = {
            "id": id,
            "text": text,
            "metadata": dict(metadata),
            "collection": collection,
        }

    async def query(
        self,
        text: str,
        n_results: int = 5,
        collection: str = "default",
        where: dict | None = None,
    ) -> list[dict]:
        """Simple substring matching against documents in the collection.

        Returns list of dicts with keys: id, text, metadata, score.
        Score is computed as a rough relevance measure based on the
        fraction of query words found in the document text.
        """
        query_lower = text.lower()
        query_words = [w for w in query_lower.split() if len(w) > 1]

        candidates: list[dict] = []
        for record in self._records.values():
            if record["collection"] != collection:
                continue

            # Apply where filter if provided
            if where and not self._matches_where(record["metadata"], where):
                continue

            doc_lower = record["text"].lower()

            # Check if any query word appears as substring in the document
            if not query_words:
                # Empty query matches everything with low score
                candidates.append({
                    "id": record["id"],
                    "text": record["text"],
                    "metadata": record["metadata"],
                    "score": 0.1,
                })
                continue

            matched_words = sum(1 for w in query_words if w in doc_lower)
            if matched_words == 0:
                continue

            # Score: fraction of query words matched, scaled to 0.0-1.0
            score = matched_words / len(query_words)
            candidates.append({
                "id": record["id"],
                "text": record["text"],
                "metadata": record["metadata"],
                "score": score,
            })

        # Sort by score descending, limit to n_results
        candidates.sort(key=lambda r: r["score"], reverse=True)
        return candidates[:n_results]

    async def is_ready(self) -> bool:
        """Always ready (in-memory store needs no external services)."""
        return True

    @staticmethod
    def _matches_where(metadata: dict, where: dict) -> bool:
        """Evaluate a simple ChromaDB-style where filter against metadata.

        Supports:
          - Direct equality: {"key": "value"}
          - $in operator:    {"key": {"$in": [...]}}
          - $eq operator:    {"key": {"$eq": "value"}}

        Unrecognised operators are treated as non-matching.
        """
        for key, condition in where.items():
            meta_value = metadata.get(key)

            if isinstance(condition, dict):
                # Operator-based filter
                if "$in" in condition:
                    if isinstance(meta_value, list):
                        # meta_value is a list: check for any overlap
                        if not any(v in condition["$in"] for v in meta_value):
                            return False
                    else:
                        if meta_value not in condition["$in"]:
                            return False
                elif "$eq" in condition:
                    if meta_value != condition["$eq"]:
                        return False
                else:
                    # Unknown operator: conservative non-match
                    return False
            else:
                # Direct equality
                if meta_value != condition:
                    return False

        return True
