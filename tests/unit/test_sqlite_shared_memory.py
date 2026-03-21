"""Unit tests for SQLiteSharedMemory — agents, memories, tasks, events, messages."""

import pytest
import asyncio

from waggledance.adapters.memory.sqlite_shared_memory import (
    SQLiteSharedMemory,
    ALLOWED_UPDATE_COLUMNS,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_shared.db")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestInitialization:
    """Database init and schema creation."""

    def test_initialize_creates_db(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.close())

    def test_double_initialize_safe(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.initialize())  # should not raise
        _run(sm.close())

    def test_not_initialized_raises(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            _run(sm.get_agents())


class TestAgents:
    """Agent registration and status."""

    def test_register_and_get_agent(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.register_agent("a1", "Agent One", "worker"))
        agents = _run(sm.get_agents())
        assert len(agents) == 1
        assert agents[0]["id"] == "a1"
        assert agents[0]["name"] == "Agent One"
        _run(sm.close())

    def test_register_with_config(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.register_agent("a1", "Agent", "worker", config={"key": "val"}))
        agents = _run(sm.get_agents())
        assert '"key"' in agents[0]["config"]
        _run(sm.close())

    def test_update_agent_status(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.register_agent("a1", "Agent", "worker"))
        _run(sm.update_agent_status("a1", "busy"))
        agents = _run(sm.get_agents())
        assert agents[0]["status"] == "busy"
        _run(sm.close())

    def test_register_replaces_existing(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.register_agent("a1", "Old", "worker"))
        _run(sm.register_agent("a1", "New", "worker"))
        agents = _run(sm.get_agents())
        assert len(agents) == 1
        assert agents[0]["name"] == "New"
        _run(sm.close())


class TestMemories:
    """Memory storage and recall."""

    def test_store_and_recall(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        mid = _run(sm.store_memory("Temperature is 22C", agent_id="a1"))
        assert len(mid) > 0
        results = _run(sm.recall("Temperature"))
        assert len(results) >= 1
        assert "22C" in results[0]["content"]
        _run(sm.close())

    def test_recall_no_match(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        results = _run(sm.recall("nonexistent_xyz"))
        assert results == []
        _run(sm.close())

    def test_recall_by_agent(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.store_memory("Fact A", agent_id="a1"))
        _run(sm.store_memory("Fact B", agent_id="a2"))
        results = _run(sm.recall("Fact", agent_id="a1"))
        assert all(r["agent_id"] == "a1" for r in results)
        _run(sm.close())

    def test_recall_sql_injection_safe(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        results = _run(sm.recall("'; DROP TABLE memories; --"))
        assert results == []
        # Table still works
        _run(sm.store_memory("Still works"))
        _run(sm.close())

    def test_get_recent_memories(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.store_memory("First"))
        _run(sm.store_memory("Second"))
        _run(sm.store_memory("Third"))
        results = _run(sm.get_recent_memories(limit=2))
        assert len(results) == 2
        _run(sm.close())

    def test_store_with_metadata(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.store_memory("Test", metadata={"key": "value"}))
        results = _run(sm.recall("Test"))
        assert '"key"' in results[0]["metadata"]
        _run(sm.close())


class TestTasks:
    """Task CRUD operations."""

    def test_add_and_get_task(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        tid = _run(sm.add_task("Test task", description="A test"))
        task = _run(sm.get_task(tid))
        assert task is not None
        assert task["title"] == "Test task"
        assert task["status"] == "pending"
        _run(sm.close())

    def test_create_task(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.create_task("t1", "inference"))
        task = _run(sm.get_task("t1"))
        assert task is not None
        assert task["title"] == "inference"
        _run(sm.close())

    def test_update_task_allowed_columns(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        tid = _run(sm.add_task("Task"))
        _run(sm.update_task(tid, status="completed", confidence=0.95))
        task = _run(sm.get_task(tid))
        assert task["status"] == "completed"
        assert task["confidence"] == 0.95
        _run(sm.close())

    def test_update_task_forbidden_column(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        tid = _run(sm.add_task("Task"))
        with pytest.raises(ValueError, match="not allowed"):
            _run(sm.update_task(tid, updates={"created_at": "2026-01-01"}))
        _run(sm.close())

    def test_update_task_empty_is_noop(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        tid = _run(sm.add_task("Task"))
        _run(sm.update_task(tid))  # no updates, should not raise
        _run(sm.close())

    def test_get_tasks_by_status(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.add_task("Pending"))
        tid = _run(sm.add_task("Done"))
        _run(sm.update_task(tid, status="completed"))
        pending = _run(sm.get_tasks(status="pending"))
        completed = _run(sm.get_tasks(status="completed"))
        assert len(pending) == 1
        assert len(completed) == 1
        _run(sm.close())

    def test_get_nonexistent_task(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        task = _run(sm.get_task("nonexistent"))
        assert task is None
        _run(sm.close())


class TestMessages:
    """Inter-agent messaging."""

    def test_send_and_get_messages(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.send_message("a1", "a2", "Hello"))
        msgs = _run(sm.get_messages("a2"))
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Hello"
        _run(sm.close())

    def test_mark_messages_read(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.send_message("a1", "a2", "Read me"))
        _run(sm.mark_messages_read("a2"))
        msgs = _run(sm.get_messages("a2"))
        assert len(msgs) == 0  # read=1, get_messages returns read=0
        _run(sm.close())


class TestEvents:
    """Event logging."""

    def test_log_and_get_timeline(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.log_event("a1", "inference", "Generated response"))
        timeline = _run(sm.get_timeline())
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "inference"
        _run(sm.close())

    def test_timeline_by_agent(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.log_event("a1", "type1", "Event 1"))
        _run(sm.log_event("a2", "type2", "Event 2"))
        timeline = _run(sm.get_timeline(agent_id="a1"))
        assert len(timeline) == 1
        _run(sm.close())


class TestProjects:
    """Project management."""

    def test_add_and_get_projects(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        pid = _run(sm.add_project("Test Project", tags=["ai", "bee"]))
        projects = _run(sm.get_projects())
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"
        _run(sm.close())


class TestStats:
    """Memory statistics."""

    def test_stats_all_zero_initially(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        stats = _run(sm.get_memory_stats())
        assert stats["memories"] == 0
        assert stats["tasks"] == 0
        assert stats["agents"] == 0
        _run(sm.close())

    def test_stats_reflect_data(self, db_path):
        sm = SQLiteSharedMemory(db_path)
        _run(sm.initialize())
        _run(sm.store_memory("Fact 1"))
        _run(sm.store_memory("Fact 2"))
        _run(sm.register_agent("a1", "Agent", "worker"))
        stats = _run(sm.get_memory_stats())
        assert stats["memories"] == 2
        assert stats["agents"] == 1
        _run(sm.close())
