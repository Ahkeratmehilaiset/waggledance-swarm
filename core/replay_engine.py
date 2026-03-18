# BUSL-1.1 - see LICENSE-CORE.md
"""
Selective replay engine for MAGMA Layer 2.
Reads audit log entries and re-applies memory operations through a MemoryWriteProxy.
"""

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.replay_engine")


class ReplayEngine:
    """Replays audited memory operations selectively."""

    def __init__(self, adapter, audit, replay_store=None, cognitive_graph=None):
        self.adapter = adapter
        self.audit = audit
        self.replay_store = replay_store
        self._graph = cognitive_graph

    # ── Manifest ──────────────────────────────────────────────────

    def get_manifest(self, *, session_id: Optional[str] = None,
                     agent_id: Optional[str] = None,
                     start_ts: Optional[float] = None,
                     end_ts: Optional[float] = None) -> dict:
        """Structured summary of replayable entries."""
        if session_id:
            # Get all entries and filter by session
            all_entries = self.audit.query_by_time_range(0, float("inf"),
                                                         agent_id=agent_id)
            entries = [e for e in all_entries if e["session_id"] == session_id]
        elif start_ts is not None and end_ts is not None:
            entries = self.audit.query_by_time_range(start_ts, end_ts,
                                                     agent_id=agent_id)
        elif agent_id:
            entries = self.audit.query_by_agent(agent_id)
        else:
            entries = self.audit.query_by_time_range(0, float("inf"))

        by_layer = Counter(e["layer"] for e in entries)
        by_action = Counter(e["action"] for e in entries)
        by_agent = Counter(e["agent_id"] for e in entries if e["agent_id"])

        return {
            "total": len(entries),
            "by_layer": dict(by_layer),
            "by_action": dict(by_action),
            "by_agent": dict(by_agent),
            "entries": entries,
        }

    # ── Text recovery ─────────────────────────────────────────────

    def _recover_text(self, entry: dict) -> Optional[str]:
        """Try to recover document text from audit details or replay store."""
        # 1. Audit details field
        if entry.get("details"):
            return entry["details"]
        # 2. Replay store by doc_id
        if self.replay_store:
            rec = self.replay_store.get_by_doc(entry["doc_id"])
            if rec:
                return rec["text"]
            # 3. By content hash
            if entry.get("content_hash"):
                rec = self.replay_store.get_by_hash(entry["content_hash"])
                if rec:
                    return rec["text"]
        return None

    # ── Session replay ────────────────────────────────────────────

    def replay_session(self, session_id: str, *, proxy, dry_run: bool = True) -> List[dict]:
        """Replay all writes from a session. Returns list of action summaries."""
        manifest = self.get_manifest(session_id=session_id)
        return self._replay_entries(manifest["entries"], proxy=proxy, dry_run=dry_run)

    # ── Time range replay ─────────────────────────────────────────

    def replay_time_range(self, start_ts: float, end_ts: float, *,
                          proxy, agent_id: Optional[str] = None,
                          layer: Optional[str] = None,
                          dry_run: bool = True) -> List[dict]:
        entries = self.audit.query_by_time_range(start_ts, end_ts,
                                                  agent_id=agent_id, layer=layer)
        return self._replay_entries(entries, proxy=proxy, dry_run=dry_run)

    # ── Correction chain replay ───────────────────────────────────

    def replay_corrections(self, doc_id: str, *, proxy, dry_run: bool = True) -> List[dict]:
        """Rebuild the correction chain for a document."""
        entries = self.audit.query_by_doc(doc_id)
        # Also find correction entries (doc_id__corr_*) via time range scan
        all_entries = self.audit.query_by_time_range(0, float("inf"))
        corr_ids = {e["id"] for e in entries}
        for e in all_entries:
            if e["id"] not in corr_ids and e["doc_id"].startswith(doc_id + "__corr_"):
                entries.append(e)
        entries.sort(key=lambda e: e["timestamp"])
        return self._replay_entries(entries, proxy=proxy, dry_run=dry_run)

    # ── Dedup ─────────────────────────────────────────────────────

    def deduplicate(self, collection: str = "waggle_memory") -> List[dict]:
        """Find near-duplicate docs by content_hash. Returns candidate groups."""
        all_entries = self.audit.query_by_time_range(0, float("inf"))
        hash_groups: Dict[str, list] = {}
        for e in all_entries:
            h = e.get("content_hash", "")
            if not h:
                continue
            hash_groups.setdefault(h, []).append(e)
        return [
            {"content_hash": h, "count": len(group), "doc_ids": [e["doc_id"] for e in group]}
            for h, group in hash_groups.items()
            if len(group) > 1
        ]

    # ── Causal replay (requires CognitiveGraph) ─────────────────

    def preview_causal(self, node_id: str, max_depth: int = 5) -> dict:
        """Show what would be re-evaluated if this node changes."""
        graph = self._graph
        if not graph:
            return {"error": "No cognitive graph wired", "dependents": []}
        dependents = graph.find_dependents(node_id, max_depth=max_depth)
        dep_details = []
        for dep_id, depth in dependents:
            node = graph.get_node(dep_id)
            dep_details.append({
                "id": dep_id,
                "depth": depth,
                "agent_id": node.get("agent_id", "") if node else "",
                "source_type": node.get("source_type", "") if node else "",
            })
        return {
            "changed_node": node_id,
            "would_replay": len(dependents),
            "dependents": dep_details,
        }

    def replay_causal(self, node_id: str, *, proxy,
                      dry_run: bool = True,
                      max_depth: int = 5) -> dict:
        """
        Replay all downstream nodes that causally depend on node_id.
        Uses cognitive graph edges (causal, derived_from) to find dependents.
        Each dependent is re-written via the proxy if its audit text can be recovered.
        """
        graph = self._graph
        if not graph:
            return {"error": "No cognitive graph wired", "replayed": 0, "results": []}

        dependents = graph.find_dependents(node_id, max_depth=max_depth)
        results = []

        for dep_id, depth in dependents:
            # Find audit entries for this dependent
            entries = self.audit.query_by_doc(dep_id)
            if not entries:
                results.append({"doc_id": dep_id, "depth": depth,
                                "status": "no_audit_entry"})
                continue

            # Use the most recent store entry
            store_entries = [e for e in entries if e.get("action") in ("store", "new")]
            if not store_entries:
                results.append({"doc_id": dep_id, "depth": depth,
                                "status": "no_store_entry"})
                continue

            entry = store_entries[-1]
            text = self._recover_text(entry)
            if text is None:
                results.append({"doc_id": dep_id, "depth": depth,
                                "status": "no_text"})
                continue

            if dry_run:
                results.append({"doc_id": dep_id, "depth": depth,
                                "status": "would_replay", "text_len": len(text)})
            else:
                try:
                    proxy.write(
                        dep_id, text, [0.0] * 768,
                        mode="new",
                        collection=entry.get("collection", "waggle_memory"),
                    )
                    results.append({"doc_id": dep_id, "depth": depth,
                                    "status": "replayed"})
                except Exception as e:
                    results.append({"doc_id": dep_id, "depth": depth,
                                    "status": "error", "error": str(e)})

        return {
            "trigger": node_id,
            "replayed": len([r for r in results if r["status"] in ("replayed", "would_replay")]),
            "results": results,
        }

    # ── Internal replay logic ─────────────────────────────────────

    def _replay_entries(self, entries: List[dict], *, proxy, dry_run: bool) -> List[dict]:
        results = []
        for entry in entries:
            action = entry.get("action", "")
            if action not in ("new", "correction"):
                results.append({"doc_id": entry["doc_id"], "action": action, "status": "skipped"})
                continue

            text = self._recover_text(entry)
            if text is None:
                results.append({"doc_id": entry["doc_id"], "action": action, "status": "no_text"})
                continue

            content_hash = entry.get("content_hash", "")
            # Idempotent: skip if doc already exists in target adapter
            if not dry_run:
                existing_doc = proxy.adapter.get(entry["doc_id"], entry.get("collection", "waggle_memory"))
                if existing_doc is not None:
                    results.append({"doc_id": entry["doc_id"], "action": action, "status": "dedup_skip"})
                    continue

            if dry_run:
                results.append({"doc_id": entry["doc_id"], "action": action, "status": "would_replay", "text_len": len(text)})
            else:
                corrects = None
                if action == "correction":
                    # Try to find original doc from metadata
                    corrects = entry.get("doc_id", "").split("__corr_")[0] if "__corr_" in entry.get("doc_id", "") else None

                proxy.write(
                    entry["doc_id"], text, [0.0] * 768,
                    mode=action,
                    collection=entry.get("collection", "waggle_memory"),
                    corrects=corrects,
                )
                results.append({"doc_id": entry["doc_id"], "action": action, "status": "replayed"})

        return results

    # ── v2.0: Mission-level replay ──────────────────────────────

    def replay_mission(self, goal_id: str, *, proxy=None, dry_run: bool = True) -> dict:
        """Replay all actions from an autonomy mission (goal → plan → actions).

        Extends the replay engine to support the new mission/plan/action/case
        path from the autonomy runtime.
        """
        try:
            from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
            builder = CaseTrajectoryBuilder()
            cases = [c for c in builder.get_all() if c.goal and c.goal.goal_id == goal_id]
            if not cases:
                return {"goal_id": goal_id, "status": "not_found", "actions": []}
            results = []
            for case in cases:
                for action in case.actions:
                    results.append({
                        "action_id": action.action_id,
                        "capability": action.capability_id,
                        "status": "would_replay" if dry_run else "replayed",
                    })
            return {"goal_id": goal_id, "status": "ok", "actions": results, "dry_run": dry_run}
        except ImportError:
            return {"goal_id": goal_id, "status": "autonomy_not_available"}
