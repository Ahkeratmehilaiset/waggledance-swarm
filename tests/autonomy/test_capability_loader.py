"""Tests for capability_loader — executor binding."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.bootstrap.capability_loader import bind_executors


class TestBindExecutors:
    def test_returns_int(self):
        registry = CapabilityRegistry()
        count = bind_executors(registry)
        assert isinstance(count, int)
        assert count >= 0

    def test_bound_executors_match_count(self):
        registry = CapabilityRegistry()
        count = bind_executors(registry)
        assert registry.executor_count() == count

    def test_executor_has_capability_id(self):
        registry = CapabilityRegistry()
        bind_executors(registry)
        # Check any bound executor has CAPABILITY_ID
        for cap_id in registry.list_ids():
            executor = registry.get_executor(cap_id)
            if executor is not None:
                assert hasattr(executor, "CAPABILITY_ID")
                assert executor.CAPABILITY_ID == cap_id

    def test_executor_has_available(self):
        registry = CapabilityRegistry()
        bind_executors(registry)
        for cap_id in registry.list_ids():
            executor = registry.get_executor(cap_id)
            if executor is not None:
                assert hasattr(executor, "available")

    def test_executor_has_execute(self):
        registry = CapabilityRegistry()
        bind_executors(registry)
        for cap_id in registry.list_ids():
            executor = registry.get_executor(cap_id)
            if executor is not None:
                assert hasattr(executor, "execute") or hasattr(executor, "stats")

    def test_graceful_on_empty_registry(self):
        registry = CapabilityRegistry(load_builtins=False)
        count = bind_executors(registry)
        assert isinstance(count, int)

    def test_no_crash_on_repeated_bind(self):
        registry = CapabilityRegistry()
        count1 = bind_executors(registry)
        count2 = bind_executors(registry)
        assert count1 == count2

    def test_registry_stats_include_executors(self):
        registry = CapabilityRegistry()
        bind_executors(registry)
        stats = registry.stats()
        assert "executors_bound" in stats
