"""
Central write guard for MAGMA memory architecture.
Layer 1: Role-based access, layer enforcement, correction chains.
"""

import hashlib
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.memory_proxy")


class Role(Enum):
    ADMIN = "admin"
    WORKER = "worker"
    ENRICHER = "enricher"
    READONLY = "readonly"


class WriteMode(Enum):
    NEW = "new"
    CORRECTION = "correction"
    INVALIDATE_RANGE = "invalidate_range"


# Permissions: role -> set of allowed write modes
_ROLE_PERMISSIONS = {
    Role.ADMIN:    {WriteMode.NEW, WriteMode.CORRECTION, WriteMode.INVALIDATE_RANGE},
    Role.ENRICHER: {WriteMode.NEW, WriteMode.CORRECTION},
    Role.WORKER:   {WriteMode.NEW},
    Role.READONLY:  set(),
}


class MemoryWriteProxy:
    """Mediates all memory writes. Enforces roles, layers, audit."""

    def __init__(self, adapter, audit, *,
                 role: str = "worker",
                 agent_id: str = "",
                 session_id: str = "",
                 spawn_chain: str = "",
                 replay_store=None):
        self.adapter = adapter
        self.audit = audit
        self.role = Role(role)
        self.agent_id = agent_id
        self.session_id = session_id
        self.spawn_chain = spawn_chain
        self.replay_store = replay_store

    def _check_permission(self, mode: WriteMode):
        allowed = _ROLE_PERMISSIONS.get(self.role, set())
        if mode not in allowed:
            raise PermissionError(
                f"Role '{self.role.value}' cannot perform '{mode.value}'"
            )

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def write(self, doc_id: str, text: str, embedding: list,
              metadata: Optional[dict] = None, *,
              mode: str = "new",
              collection: str = "waggle_memory",
              corrects: Optional[str] = None) -> str:
        """Write a document. Returns the actual doc_id used."""
        wm = WriteMode(mode)
        self._check_permission(wm)

        meta = dict(metadata or {})
        content_hash = self._content_hash(text)
        now = time.time()

        # Determine layer
        if wm == WriteMode.NEW:
            layer = "working"
        elif wm == WriteMode.CORRECTION:
            layer = "correction"
            if not corrects:
                raise ValueError("correction mode requires 'corrects' doc_id")
            meta["_corrects"] = corrects
            # Generate unique correction id
            doc_id = f"{doc_id}__corr_{int(now * 1000)}"
        elif wm == WriteMode.INVALIDATE_RANGE:
            layer = "working"
            # Invalidate via metadata flag
            existing_meta = self.adapter.get_metadata(doc_id, collection)
            if existing_meta is not None:
                existing_meta["_invalidated"] = True
                existing_meta["_invalidated_at"] = now
                self.adapter.update_metadata(doc_id, existing_meta, collection)
            self.audit.record(
                "invalidate", doc_id, collection=collection, layer=layer,
                agent_id=self.agent_id, session_id=self.session_id,
                spawn_chain=self.spawn_chain, content_hash=content_hash,
            )
            return doc_id

        # Stamp metadata
        meta["_layer"] = layer
        meta["_content_hash"] = content_hash
        meta["_created_at"] = now
        meta["_agent_id"] = self.agent_id
        meta["_session_id"] = self.session_id

        self.adapter.add(doc_id, text, embedding, meta, collection)

        self.audit.record(
            wm.value, doc_id, collection=collection, layer=layer,
            agent_id=self.agent_id, session_id=self.session_id,
            spawn_chain=self.spawn_chain, content_hash=content_hash,
            details=text,
        )

        if self.replay_store is not None:
            self.replay_store.store(doc_id, text, content_hash, meta)

        return doc_id

    def read(self, doc_id: str, collection: str = "waggle_memory") -> Optional[dict]:
        """Read a document, merging latest non-invalidated correction over original."""
        doc = self.adapter.get(doc_id, collection)
        if doc is None:
            return None
        meta = doc.get("metadata", {})
        if meta.get("_invalidated"):
            return None

        # Check for corrections pointing to this doc
        # Search audit for corrections
        audit_entries = self.audit.query_by_doc(doc_id)
        correction_ids = [
            e["doc_id"] for e in audit_entries
            if e["action"] == "correction" and e["doc_id"] != doc_id
        ]
        # Also check if any doc has _corrects pointing here
        # We scan audit for any correction action mentioning this doc_id
        # (audit details field or the corrects chain)

        # Try to find correction docs
        latest_correction = None
        for entry in reversed(audit_entries):
            if entry["action"] == "correction" and entry["doc_id"] != doc_id:
                corr_doc = self.adapter.get(entry["doc_id"], collection)
                if corr_doc and not corr_doc.get("metadata", {}).get("_invalidated"):
                    latest_correction = corr_doc
                    break

        if latest_correction:
            # Merge: correction text over original, combine metadata
            merged = dict(doc)
            merged["document"] = latest_correction["document"]
            merged["metadata"] = {**meta, **latest_correction.get("metadata", {})}
            merged["_corrected_by"] = latest_correction["id"]
            return merged

        return doc

    def search(self, embedding: list, top_k: int = 5,
               collection: str = "waggle_memory") -> List[dict]:
        """Search, filtering out invalidated docs."""
        results = self.adapter.search(embedding, top_k=top_k, collection=collection)
        return [r for r in results if not r.get("metadata", {}).get("_invalidated")]
