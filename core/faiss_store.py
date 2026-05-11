"""
FAISS vector store with persistence.
Provides named collections backed by IndexFlatIP (cosine on L2-normalized vectors).

Usage:
    registry = FaissRegistry()
    col = registry.get_or_create("axioms")
    col.add("doc1", "text", vector_768d, {"meta": "value"})
    results = col.search(query_vector, k=5)
    registry.save_all()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import faiss
import numpy as np

log = logging.getLogger(__name__)

_DEFAULT_FAISS_DIR = Path(__file__).parent.parent / "data" / "faiss"


@dataclass
class SearchResult:
    doc_id: str
    text: str
    score: float          # cosine similarity [−1, 1], typically [0, 1]
    metadata: Dict[str, Any] = field(default_factory=dict)


class FaissCollection:
    """A single named FAISS index with JSON metadata sidecar.

    Uses IndexFlatIP (inner product) on L2-normalized vectors — equivalent
    to cosine similarity, matching ChromaDB's 'cosine' metric.
    """

    def __init__(
        self,
        name: str,
        dim: int = 768,
        persist_dir: Optional[str] = None,
    ):
        self.name = name
        self.dim = dim
        self._dir = Path(persist_dir) if persist_dir else (_DEFAULT_FAISS_DIR / name)

        self._index: faiss.IndexFlatIP = faiss.IndexFlatIP(dim)
        self._doc_ids: List[str] = []
        self._texts: List[str] = []
        self._metadata: List[Dict[str, Any]] = []

        # Try to restore from disk
        self._try_load()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        doc_id: str,
        text: str,
        vector: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add a single document."""
        if self.has_doc_id(doc_id):
            return False
        vec = self._normalize(vector.astype(np.float32).reshape(1, -1))
        self._index.add(vec)
        self._doc_ids.append(doc_id)
        self._texts.append(text)
        self._metadata.append(metadata or {})
        return True

    def add_batch(
        self,
        doc_ids: List[str],
        texts: List[str],
        vectors: np.ndarray,
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ):
        """Add multiple documents at once (faster than individual adds)."""
        if not doc_ids:
            return
        filtered_doc_ids: List[str] = []
        filtered_texts: List[str] = []
        filtered_vectors: List[np.ndarray] = []
        filtered_metadatas: List[Dict[str, Any]] = []
        metadata_items = metadatas or [{} for _ in doc_ids]
        seen = set(self._doc_ids)
        for doc_id, text, vector, metadata in zip(doc_ids, texts, vectors, metadata_items):
            if doc_id in seen:
                continue
            seen.add(doc_id)
            filtered_doc_ids.append(doc_id)
            filtered_texts.append(text)
            filtered_vectors.append(vector)
            filtered_metadatas.append(metadata or {})
        if not filtered_doc_ids:
            return
        vecs = self._normalize(np.asarray(filtered_vectors).astype(np.float32))
        self._index.add(vecs)
        self._doc_ids.extend(filtered_doc_ids)
        self._texts.extend(filtered_texts)
        self._metadata.extend(filtered_metadatas)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(self, query_vector: np.ndarray, k: int = 5) -> List[SearchResult]:
        """Return up to k nearest neighbours, sorted by descending score."""
        if self._index.ntotal == 0:
            return []
        k_eff = min(k, self._index.ntotal)
        vec = self._normalize(query_vector.astype(np.float32).reshape(1, -1))
        scores, indices = self._index.search(vec, k_eff)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            results.append(SearchResult(
                doc_id=self._doc_ids[idx],
                text=self._texts[idx],
                score=float(score),
                metadata=self._metadata[idx],
            ))
        return results

    @property
    def count(self) -> int:
        return self._index.ntotal

    def has_doc_id(self, doc_id: str) -> bool:
        return doc_id in self._doc_ids

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        """Persist index + metadata to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._dir / "index.faiss"))
        meta = {
            "name": self.name,
            "dim": self.dim,
            "doc_ids": self._doc_ids,
            "texts": self._texts,
            "metadata": self._metadata,
        }
        with open(self._dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log.info("FaissCollection '%s' saved (%d vectors)", self.name, self.count)

    def _try_load(self):
        index_path = self._dir / "index.faiss"
        meta_path = self._dir / "meta.json"
        if not (index_path.exists() and meta_path.exists()):
            return
        try:
            self._index = faiss.read_index(str(index_path))
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
            self.dim = data.get("dim", self.dim)
            self._doc_ids = data["doc_ids"]
            self._texts = data["texts"]
            self._metadata = data["metadata"]
            log.info(
                "FaissCollection '%s' loaded (%d vectors from disk)",
                self.name, self._index.ntotal,
            )
        except Exception as exc:
            log.warning("Could not load FaissCollection '%s': %s", self.name, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        """L2-normalize rows so IndexFlatIP equals cosine similarity."""
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        norms = np.where(norms == 0.0, 1.0, norms)
        return (v / norms).astype(np.float32)


class FaissRegistry:
    """Manages multiple named FaissCollections, all rooted at one directory."""

    def __init__(self, base_dir: Optional[str] = None, default_dim: int = 768):
        self._base_dir = Path(base_dir) if base_dir else _DEFAULT_FAISS_DIR
        self._default_dim = default_dim
        self._collections: Dict[str, FaissCollection] = {}

    def get_or_create(self, name: str, dim: Optional[int] = None) -> FaissCollection:
        if name not in self._collections:
            self._collections[name] = FaissCollection(
                name=name,
                dim=dim or self._default_dim,
                persist_dir=str(self._base_dir / name),
            )
        return self._collections[name]

    def get_existing(self, name: str, dim: Optional[int] = None) -> Optional[FaissCollection]:
        if name in self._collections:
            return self._collections[name]
        collection_dir = self._base_dir / name
        if not collection_dir.exists():
            return None
        collection = FaissCollection(
            name=name,
            dim=dim or self._default_dim,
            persist_dir=str(collection_dir),
        )
        self._collections[name] = collection
        return collection

    def count_if_exists(self, name: str, dim: Optional[int] = None) -> int:
        collection = self.get_existing(name, dim=dim)
        return collection.count if collection is not None else 0

    def document_exists(self, doc_id: str) -> bool:
        for collection in self._collections.values():
            if collection.has_doc_id(doc_id):
                return True
        loaded_names = set(self._collections)
        for name in sorted(self._persisted_collection_names() - loaded_names):
            collection = self.get_existing(name)
            if collection is not None and collection.has_doc_id(doc_id):
                return True
        return False

    def _persisted_collection_names(self) -> set[str]:
        if not self._base_dir.exists():
            return set()
        names: set[str] = set()
        for child in self._base_dir.iterdir():
            if child.is_dir() and ((child / "index.faiss").exists() or (child / "meta.json").exists()):
                names.add(child.name)
        return names

    def list_collections(self) -> List[str]:
        return sorted(set(self._collections) | self._persisted_collection_names())

    def stats(self) -> Dict[str, int]:
        return {name: self.count_if_exists(name) for name in self.list_collections()}

    def save_all(self):
        for col in self._collections.values():
            col.save()
