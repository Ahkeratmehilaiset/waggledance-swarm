"""
Working Memory — short-term context for the current reasoning cycle.

Working memory holds the "active context" that the system needs right now:
  - Current goal and plan step
  - Recent observations and residuals
  - Relevant facts retrieved from long-term memory
  - Scratchpad for intermediate computation results
  - Conversation context (recent messages)

Unlike long-term memory (ChromaDB, SQLite), working memory is:
  - Bounded (max slots)
  - Short-lived (cleared between reasoning cycles or after TTL)
  - Priority-ordered (high-salience items stay, low-salience items evict)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.memory.working")

DEFAULT_MAX_SLOTS = 64
DEFAULT_TTL_SECONDS = 300  # 5 minutes


@dataclass
class MemorySlot:
    """A single item in working memory."""
    key: str
    value: Any
    category: str  # goal, observation, fact, scratchpad, message, residual
    salience: float = 0.5  # 0.0 = low priority, 1.0 = critical
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds <= 0:
            return False  # no expiry
        return (time.time() - self.created_at) > self.ttl_seconds

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "salience": self.salience,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "expired": self.is_expired,
        }


class WorkingMemory:
    """
    Bounded, priority-ordered short-term memory.

    When capacity is reached, the lowest-salience expired slot is evicted.
    If no expired slots exist, the lowest-salience slot is evicted.
    """

    def __init__(self, max_slots: int = DEFAULT_MAX_SLOTS):
        self._slots: Dict[str, MemorySlot] = {}
        self._max_slots = max_slots

    # ── Core operations ───────────────────────────────────────

    def put(
        self,
        key: str,
        value: Any,
        category: str = "scratchpad",
        salience: float = 0.5,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemorySlot:
        """Add or replace a slot. Evicts if at capacity."""
        # Update existing
        if key in self._slots:
            slot = self._slots[key]
            slot.value = value
            slot.salience = salience
            slot.created_at = time.time()
            slot.ttl_seconds = ttl_seconds
            if metadata:
                slot.metadata.update(metadata)
            return slot

        # Evict if at capacity
        if len(self._slots) >= self._max_slots:
            self._evict_one()

        slot = MemorySlot(
            key=key,
            value=value,
            category=category,
            salience=salience,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
        )
        self._slots[key] = slot
        return slot

    def get(self, key: str) -> Optional[MemorySlot]:
        slot = self._slots.get(key)
        if slot is None:
            return None
        if slot.is_expired:
            del self._slots[key]
            return None
        return slot

    def get_value(self, key: str, default: Any = None) -> Any:
        slot = self.get(key)
        return slot.value if slot else default

    def remove(self, key: str) -> bool:
        if key in self._slots:
            del self._slots[key]
            return True
        return False

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    # ── Bulk queries ──────────────────────────────────────────

    def by_category(self, category: str) -> List[MemorySlot]:
        """Get all non-expired slots of a given category, sorted by salience desc."""
        self._purge_expired()
        result = [s for s in self._slots.values() if s.category == category]
        return sorted(result, key=lambda s: s.salience, reverse=True)

    def all_active(self) -> List[MemorySlot]:
        """Get all non-expired slots, sorted by salience desc."""
        self._purge_expired()
        return sorted(self._slots.values(), key=lambda s: s.salience, reverse=True)

    def as_context_dict(self) -> Dict[str, Any]:
        """Export active working memory as a flat dict for context enrichment."""
        self._purge_expired()
        return {s.key: s.value for s in self._slots.values()}

    def categories(self) -> Dict[str, int]:
        """Count of active slots per category."""
        self._purge_expired()
        counts: Dict[str, int] = {}
        for s in self._slots.values():
            counts[s.category] = counts.get(s.category, 0) + 1
        return counts

    # ── Lifecycle ─────────────────────────────────────────────

    def clear(self, category: Optional[str] = None):
        """Clear all slots, or only slots of a given category."""
        if category is None:
            self._slots.clear()
        else:
            self._slots = {k: v for k, v in self._slots.items() if v.category != category}

    @property
    def size(self) -> int:
        return len(self._slots)

    @property
    def capacity(self) -> int:
        return self._max_slots

    def stats(self) -> dict:
        self._purge_expired()
        return {
            "size": len(self._slots),
            "capacity": self._max_slots,
            "categories": self.categories(),
        }

    # ── Internal ──────────────────────────────────────────────

    def _evict_one(self):
        """Evict one slot: prefer expired, then lowest salience."""
        # First try expired
        for key, slot in list(self._slots.items()):
            if slot.is_expired:
                del self._slots[key]
                log.debug("Evicted expired slot: %s", key)
                return

        # Then lowest salience
        if not self._slots:
            return
        worst_key = min(self._slots, key=lambda k: self._slots[k].salience)
        del self._slots[worst_key]
        log.debug("Evicted low-salience slot: %s", worst_key)

    def _purge_expired(self):
        """Remove all expired slots."""
        expired = [k for k, v in self._slots.items() if v.is_expired]
        for k in expired:
            del self._slots[k]
