"""Tests for resource monitoring and OOM protection."""
from core.resource_guard import ResourceGuard, ResourceState


def test_check_returns_state():
    guard = ResourceGuard()
    state = guard.check()
    assert isinstance(state, ResourceState)
    assert 0 <= state.memory_percent <= 100
    assert 0 <= state.disk_percent <= 100


def test_emergency_gc():
    guard = ResourceGuard()
    result = guard.trigger_emergency_gc()
    assert "before" in result
    assert "after" in result
    assert "freed" in result


def test_stats():
    guard = ResourceGuard()
    stats = guard.stats
    assert "memory_percent" in stats
    assert "gc_runs" in stats


def test_throttle_threshold():
    guard = ResourceGuard(max_memory_percent=0.1)  # Extremely low -> always throttle
    assert guard.should_throttle() is True


def test_no_throttle_high_threshold():
    guard = ResourceGuard(max_memory_percent=99.9)  # Extremely high -> never throttle
    assert guard.should_throttle() is False
