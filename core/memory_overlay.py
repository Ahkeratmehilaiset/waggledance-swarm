"""
MAGMA Layer 3: Memory overlay networks.
Provides filtered views over ChromaDB collections using metadata filters.
Each overlay shows only documents from specified agent_ids.

Extended: OverlayBranch for branchable memory states (replacement sets),
MoodPreset for agent contribution transforms, A/B comparison.
"""

import json
import logging
import time
from typing import Callable, Dict, List, Optional

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


# ═══════════════════════════════════════════════════════════════
# OVERLAY BRANCH: Branchable memory states (replacement sets)
# ═══════════════════════════════════════════════════════════════

class OverlayBranch:
    """
    Named set of replacement nodes. When active, queries that match
    a replaced node ID return the replacement instead.
    Base data is NEVER modified.
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.created = time.time()
        self._replacements: Dict[str, dict] = {}  # original_id -> replacement node
        self.active = False

    def add_replacement(self, original_id: str, content: str,
                        metadata: Optional[dict] = None):
        """Add a replacement node for a base node."""
        self._replacements[original_id] = {
            "id": f"ov_{self.name}_{original_id}",
            "replaces": original_id,
            "content": content,
            "overlay": self.name,
            "timestamp": time.time(),
            **(metadata or {}),
        }

    def remove_replacement(self, original_id: str) -> bool:
        if original_id in self._replacements:
            del self._replacements[original_id]
            return True
        return False

    def apply_to_results(self, results: List[dict]) -> List[dict]:
        """Replace matching nodes in search results."""
        if not self._replacements:
            return results
        out = []
        for r in results:
            rid = r.get("id", "")
            if rid in self._replacements:
                out.append(self._replacements[rid])
            else:
                out.append(r)
        return out

    @property
    def replacement_count(self) -> int:
        return len(self._replacements)

    @property
    def replaced_ids(self) -> List[str]:
        return list(self._replacements.keys())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "replacement_count": self.replacement_count,
            "replaced_ids": self.replaced_ids,
        }


class BranchManager:
    """Manages overlay branches with activation/deactivation and A/B comparison."""

    def __init__(self):
        self._branches: Dict[str, OverlayBranch] = {}
        self._active: Optional[str] = None

    def create(self, name: str, description: str = "") -> OverlayBranch:
        branch = OverlayBranch(name, description)
        self._branches[name] = branch
        return branch

    def get(self, name: str) -> Optional[OverlayBranch]:
        return self._branches.get(name)

    def delete(self, name: str) -> bool:
        if name in self._branches:
            if self._active == name:
                self._active = None
            del self._branches[name]
            return True
        return False

    def activate(self, name: str) -> bool:
        """Activate a branch. Deactivates any previously active branch."""
        if name not in self._branches:
            return False
        if self._active and self._active in self._branches:
            self._branches[self._active].active = False
        self._active = name
        self._branches[name].active = True
        log.info(f"Overlay branch '{name}' activated")
        return True

    def deactivate(self) -> Optional[str]:
        """Deactivate current branch, return to base state."""
        prev = self._active
        if prev and prev in self._branches:
            self._branches[prev].active = False
        self._active = None
        return prev

    @property
    def active_branch(self) -> Optional[OverlayBranch]:
        if self._active:
            return self._branches.get(self._active)
        return None

    def apply_active(self, results: List[dict]) -> List[dict]:
        """Apply active branch replacements to search results."""
        branch = self.active_branch
        if branch:
            return branch.apply_to_results(results)
        return results

    def compare(self, results: List[dict], branch_a: str,
                branch_b: str) -> dict:
        """A/B compare: apply two branches to same results."""
        ba = self._branches.get(branch_a)
        bb = self._branches.get(branch_b)
        return {
            "base": results,
            "branch_a": {
                "name": branch_a,
                "results": ba.apply_to_results(results) if ba else results,
            },
            "branch_b": {
                "name": branch_b,
                "results": bb.apply_to_results(results) if bb else results,
            },
        }

    def list_all(self) -> dict:
        return {name: b.to_dict() for name, b in self._branches.items()}

    def create_from_agent_data(self, name: str, agent_id: str,
                               entries: List[dict],
                               transform: Optional[Callable] = None,
                               description: str = "") -> OverlayBranch:
        """
        Create a branch replacing an agent's contributions.

        Args:
            entries: list of dicts with 'doc_id' and 'content' keys
            transform: optional fn(content) -> new_content
        """
        branch = self.create(name, description or f"Replacement for agent {agent_id}")
        for entry in entries:
            doc_id = entry.get("doc_id", "")
            content = entry.get("content", "")
            if not doc_id:
                continue
            if transform:
                new_content = transform(content)
            else:
                new_content = f"[PENDING REPLACEMENT: was {doc_id}]"
            branch.add_replacement(doc_id, new_content,
                                   {"original_agent": agent_id})
        return branch


# ═══════════════════════════════════════════════════════════════
# MOOD PRESETS: Transform agent contributions by style
# ═══════════════════════════════════════════════════════════════

class MoodPreset:
    """Pre-built transforms for common overlay patterns."""

    @staticmethod
    def cautious(content: str) -> str:
        """Add uncertainty markers."""
        return f"[UNCERTAIN] {content} [Requires verification]"

    @staticmethod
    def verified_only(content: str) -> str:
        """Only keep content with source citations."""
        indicators = ["lähde:", "source:", "http", "doi:", "isbn:"]
        if any(ind in content.lower() for ind in indicators):
            return content
        return "[HIDDEN: no source citation]"

    @staticmethod
    def concise(content: str) -> str:
        """Truncate to first sentence."""
        for sep in [". ", ".\n", "! ", "? "]:
            idx = content.find(sep)
            if idx > 0:
                return content[:idx + 1]
        return content[:200] if len(content) > 200 else content
