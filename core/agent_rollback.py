"""
Agent rollback for MAGMA memory architecture.
Layer 1: Undo agent writes by session, respecting layer safety.
"""

import logging
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.agent_rollback")


class AgentRollback:
    """Roll back an agent's memory writes by session or spawn tree."""

    def __init__(self, adapter, audit):
        self.adapter = adapter
        self.audit = audit

    def preview(self, agent_id: str, session_id: Optional[str] = None) -> Dict[str, list]:
        """Show what would be rolled back, grouped by layer."""
        entries = self.audit.query_by_agent(agent_id, session_id)
        result = {"working": [], "correction": [], "original": []}
        for e in entries:
            layer = e.get("layer", "working")
            if layer not in result:
                result[layer] = []
            result[layer].append({
                "doc_id": e["doc_id"],
                "action": e["action"],
                "timestamp": e["timestamp"],
                "collection": e.get("collection", "waggle_memory"),
            })
        return result

    def rollback(self, agent_id: str, session_id: Optional[str] = None) -> Dict[str, int]:
        """Roll back agent writes. Working: delete. Correction: invalidate. Original: NEVER."""
        entries = self.audit.query_by_agent(agent_id, session_id)
        counts = {"deleted": 0, "invalidated": 0, "skipped_original": 0}

        for e in entries:
            layer = e.get("layer", "working")
            doc_id = e["doc_id"]
            collection = e.get("collection", "waggle_memory")

            if layer == "original":
                counts["skipped_original"] += 1
                continue

            if layer == "correction":
                # Invalidate corrections (don't delete)
                meta = self.adapter.get_metadata(doc_id, collection)
                if meta is not None and not meta.get("_invalidated"):
                    meta["_invalidated"] = True
                    self.adapter.update_metadata(doc_id, meta, collection)
                    counts["invalidated"] += 1
                    self.audit.record(
                        "rollback_invalidate", doc_id,
                        collection=collection, layer=layer,
                        agent_id=agent_id, session_id=session_id or "",
                    )
            else:
                # Working layer: delete
                self.adapter.delete(doc_id, collection)
                counts["deleted"] += 1
                self.audit.record(
                    "rollback_delete", doc_id,
                    collection=collection, layer=layer,
                    agent_id=agent_id, session_id=session_id or "",
                )

        log.info(f"Rollback agent={agent_id}: {counts}")
        return counts

    def rollback_recursive(self, agent_id: str) -> Dict[str, int]:
        """Roll back agent and all spawned children via spawn_chain."""
        entries = self.audit.query_spawn_tree(agent_id)
        # Collect unique agent_ids from tree
        agent_ids = set()
        for e in entries:
            agent_ids.add(e["agent_id"])

        total = {"deleted": 0, "invalidated": 0, "skipped_original": 0}
        for aid in agent_ids:
            counts = self.rollback(aid)
            for k in total:
                total[k] += counts.get(k, 0)

        log.info(f"Recursive rollback root={agent_id}, agents={len(agent_ids)}: {total}")
        return total
