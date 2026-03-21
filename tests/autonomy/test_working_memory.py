"""
Phase 3 Tests: Working Memory.

Tests cover:
- Slot creation, retrieval, update
- TTL expiry and automatic purge
- Salience-based eviction at capacity
- Category filtering and bulk queries
- Context dict export
- Clear operations
- Stats
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.memory.working_memory import (
    DEFAULT_MAX_SLOTS,
    DEFAULT_TTL_SECONDS,
    MemorySlot,
    WorkingMemory,
)


class TestMemorySlot:
    def test_create_defaults(self):
        slot = MemorySlot(key="test", value="data", category="scratchpad")
        assert slot.key == "test"
        assert slot.value == "data"
        assert slot.salience == 0.5
        assert slot.is_expired is False

    def test_expired_slot(self):
        slot = MemorySlot(key="old", value="x", category="scratchpad",
                          created_at=time.time() - 1000, ttl_seconds=1)
        assert slot.is_expired is True

    def test_no_expiry(self):
        slot = MemorySlot(key="permanent", value="x", category="goal",
                          ttl_seconds=0)
        assert slot.is_expired is False

    def test_to_dict(self):
        slot = MemorySlot(key="test", value=42, category="observation")
        d = slot.to_dict()
        assert d["key"] == "test"
        assert d["value"] == 42
        assert d["category"] == "observation"
        assert "expired" in d


class TestWorkingMemory:
    def test_put_and_get(self):
        wm = WorkingMemory()
        wm.put("goal", "observe hive temperature", category="goal", salience=1.0)
        slot = wm.get("goal")
        assert slot is not None
        assert slot.value == "observe hive temperature"
        assert slot.salience == 1.0

    def test_get_value(self):
        wm = WorkingMemory()
        wm.put("key1", 42, category="scratchpad")
        assert wm.get_value("key1") == 42
        assert wm.get_value("nonexistent", "default") == "default"

    def test_put_update_existing(self):
        wm = WorkingMemory()
        wm.put("key1", "old", category="scratchpad")
        wm.put("key1", "new", category="scratchpad")
        assert wm.get_value("key1") == "new"
        assert wm.size == 1

    def test_remove(self):
        wm = WorkingMemory()
        wm.put("key1", "data", category="scratchpad")
        assert wm.remove("key1") is True
        assert wm.get("key1") is None
        assert wm.remove("nonexistent") is False

    def test_has(self):
        wm = WorkingMemory()
        wm.put("key1", "data", category="scratchpad")
        assert wm.has("key1") is True
        assert wm.has("nonexistent") is False

    def test_expired_slot_returns_none(self):
        wm = WorkingMemory()
        wm.put("old", "data", category="scratchpad", ttl_seconds=0.001)
        time.sleep(0.01)
        assert wm.get("old") is None
        assert wm.has("old") is False

    def test_by_category(self):
        wm = WorkingMemory()
        wm.put("g1", "goal1", category="goal", salience=0.8)
        wm.put("g2", "goal2", category="goal", salience=0.9)
        wm.put("obs1", "obs", category="observation")
        goals = wm.by_category("goal")
        assert len(goals) == 2
        assert goals[0].value == "goal2"  # higher salience first

    def test_all_active(self):
        wm = WorkingMemory()
        wm.put("k1", "v1", category="scratchpad", salience=0.3)
        wm.put("k2", "v2", category="goal", salience=0.9)
        active = wm.all_active()
        assert len(active) == 2
        assert active[0].key == "k2"  # higher salience first

    def test_as_context_dict(self):
        wm = WorkingMemory()
        wm.put("temp", 35.0, category="observation")
        wm.put("goal", "check hive", category="goal")
        ctx = wm.as_context_dict()
        assert ctx["temp"] == 35.0
        assert ctx["goal"] == "check hive"

    def test_categories(self):
        wm = WorkingMemory()
        wm.put("g1", "x", category="goal")
        wm.put("g2", "x", category="goal")
        wm.put("o1", "x", category="observation")
        cats = wm.categories()
        assert cats["goal"] == 2
        assert cats["observation"] == 1

    def test_clear_all(self):
        wm = WorkingMemory()
        wm.put("k1", "v1", category="goal")
        wm.put("k2", "v2", category="observation")
        wm.clear()
        assert wm.size == 0

    def test_clear_by_category(self):
        wm = WorkingMemory()
        wm.put("k1", "v1", category="goal")
        wm.put("k2", "v2", category="observation")
        wm.clear(category="goal")
        assert wm.size == 1
        assert wm.get("k2") is not None

    def test_capacity(self):
        wm = WorkingMemory(max_slots=3)
        assert wm.capacity == 3

    def test_stats(self):
        wm = WorkingMemory(max_slots=10)
        wm.put("k1", "v1", category="goal")
        wm.put("k2", "v2", category="observation")
        s = wm.stats()
        assert s["size"] == 2
        assert s["capacity"] == 10
        assert s["categories"]["goal"] == 1


class TestWorkingMemoryEviction:
    def test_evict_expired_first(self):
        wm = WorkingMemory(max_slots=2)
        wm.put("old", "data", category="scratchpad", salience=1.0, ttl_seconds=0.001)
        wm.put("keep", "data", category="scratchpad", salience=0.5)
        time.sleep(0.01)
        # Adding a third slot should evict the expired "old" one
        wm.put("new", "data", category="scratchpad")
        assert wm.has("keep") is True
        assert wm.has("new") is True
        assert wm.size == 2

    def test_evict_lowest_salience(self):
        wm = WorkingMemory(max_slots=2)
        wm.put("low", "data", category="scratchpad", salience=0.1, ttl_seconds=0)
        wm.put("high", "data", category="goal", salience=0.9, ttl_seconds=0)
        wm.put("mid", "data", category="observation", salience=0.5, ttl_seconds=0)
        # "low" should be evicted
        assert wm.has("high") is True
        assert wm.has("mid") is True
        assert wm.size == 2

    def test_capacity_limit_enforced(self):
        wm = WorkingMemory(max_slots=5)
        for i in range(10):
            wm.put(f"key_{i}", f"value_{i}", category="scratchpad", ttl_seconds=0)
        assert wm.size == 5
