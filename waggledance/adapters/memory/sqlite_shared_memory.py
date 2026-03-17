"""
SQLite-backed shared memory for task tracking and inter-agent state.

Ported from memory/shared_memory.py with improvements:
- WAL mode for concurrent read performance
- Schema version tracking for future migrations
- Whitelist-based update_task to prevent injection
- aiosqlite for async I/O
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "result", "error", "completed_at",
    "agent_id", "confidence", "latency_ms",
    "title", "description", "assigned_agent",
    "project_id", "priority", "started_at", "updated_at",
})

# ── Schema DDL ────────────────────────────────────────────────

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    status TEXT DEFAULT 'idle',
    config TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    agent_id TEXT,
    project_id TEXT,
    memory_type TEXT DEFAULT 'observation',
    importance REAL DEFAULT 0.5,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT,
    event_type TEXT,
    description TEXT,
    data TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT,
    to_agent TEXT,
    content TEXT,
    message_type TEXT DEFAULT 'info',
    read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    assigned_agent TEXT,
    project_id TEXT,
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 5,
    result TEXT,
    error TEXT,
    confidence REAL,
    latency_ms REAL,
    agent_id TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteSharedMemory:
    """Async SQLite shared memory for task tracking and inter-agent state.

    Uses WAL mode for better concurrent read performance and a whitelist
    for update_task to prevent arbitrary column writes.
    """

    def __init__(self, db_path: str = "./shared_memory.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database, enable WAL mode, create schema, check version."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for concurrent read performance
        await self._db.execute("PRAGMA journal_mode=WAL")

        # Create all tables
        await self._db.executescript(_SCHEMA_DDL)

        # Insert or verify schema version
        cursor = await self._db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = await cursor.fetchone()
        if row is None:
            await self._db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif row[0] > SCHEMA_VERSION:
            log.warning(
                "Database schema version %d is newer than code version %d",
                row[0], SCHEMA_VERSION,
            )

        # Phase 1 autonomy: add canonical_id columns to agent-referencing tables
        await self._add_canonical_columns()

        await self._db.commit()
        log.info("SQLiteSharedMemory initialized: %s (schema v%d)", self._db_path, SCHEMA_VERSION)

    async def _add_canonical_columns(self) -> None:
        """Add canonical_id column to tables that reference agents (idempotent)."""
        tables_with_agent = ["agents", "memories", "events", "tasks"]
        for table in tables_with_agent:
            cursor = await self._db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] async for row in cursor}
            if "canonical_id" not in cols:
                await self._db.execute(
                    f"ALTER TABLE {table} ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
                )
                log.info("Added canonical_id column to %s", table)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    def _ensure_db(self) -> aiosqlite.Connection:
        """Return the active connection or raise if not initialized."""
        if self._db is None:
            raise RuntimeError("SQLiteSharedMemory not initialized. Call initialize() first.")
        return self._db

    # ── Agents ────────────────────────────────────────────────

    async def register_agent(
        self,
        agent_id: str,
        name: str,
        agent_type: str,
        config: dict | None = None,
    ) -> None:
        """Register or update an agent record."""
        db = self._ensure_db()
        await db.execute(
            "INSERT OR REPLACE INTO agents (id, name, type, config) VALUES (?,?,?,?)",
            (agent_id, name, agent_type, json.dumps(config or {})),
        )
        await db.commit()

    async def update_agent_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status field."""
        db = self._ensure_db()
        await db.execute(
            "UPDATE agents SET status = ? WHERE id = ?",
            (status, agent_id),
        )
        await db.commit()

    async def get_agents(self) -> list[dict]:
        """Return all registered agents."""
        db = self._ensure_db()
        cursor = await db.execute("SELECT * FROM agents ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]

    # ── Memory ────────────────────────────────────────────────

    async def store_memory(
        self,
        content: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        memory_type: str = "observation",
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """Store a memory record. Returns the generated id."""
        db = self._ensure_db()
        mid = uuid.uuid4().hex[:12]
        await db.execute(
            """INSERT INTO memories (id, content, agent_id, project_id,
               memory_type, importance, metadata) VALUES (?,?,?,?,?,?,?)""",
            (mid, content, agent_id, project_id, memory_type,
             importance, json.dumps(metadata or {})),
        )
        await db.commit()
        return mid

    async def recall(
        self,
        query: str,
        limit: int = 10,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Simple text search across memories using SQL LIKE."""
        db = self._ensure_db()
        safe_q = query[:50].replace("%", "\\%").replace("_", "\\_")
        if agent_id:
            cursor = await db.execute(
                """SELECT * FROM memories WHERE agent_id = ?
                   AND content LIKE ? ESCAPE '\\' ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (agent_id, f"%{safe_q}%", limit),
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM memories WHERE content LIKE ? ESCAPE '\\'
                   ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (f"%{safe_q}%", limit),
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_recent_memories(
        self,
        limit: int = 10,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Return most recent memories, optionally filtered by agent."""
        db = self._ensure_db()
        if agent_id:
            cursor = await db.execute(
                "SELECT * FROM memories WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in await cursor.fetchall()]

    # ── Messages ──────────────────────────────────────────────

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        message_type: str = "info",
    ) -> None:
        """Send a message from one agent to another."""
        db = self._ensure_db()
        await db.execute(
            "INSERT INTO messages (from_agent, to_agent, content, message_type) VALUES (?,?,?,?)",
            (from_agent, to_agent, content, message_type),
        )
        await db.commit()

    async def get_messages(self, agent_id: str) -> list[dict]:
        """Get unread messages for an agent."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT * FROM messages WHERE to_agent = ? AND read = 0 ORDER BY created_at DESC",
            (agent_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def mark_messages_read(self, agent_id: str) -> None:
        """Mark all messages as read for an agent."""
        db = self._ensure_db()
        await db.execute(
            "UPDATE messages SET read = 1 WHERE to_agent = ?",
            (agent_id,),
        )
        await db.commit()

    # ── Events ────────────────────────────────────────────────

    async def log_event(
        self,
        agent_id: str,
        event_type: str,
        description: str,
        data: dict | None = None,
    ) -> None:
        """Log an event to the timeline."""
        db = self._ensure_db()
        await db.execute(
            "INSERT INTO events (agent_id, event_type, description, data) VALUES (?,?,?,?)",
            (agent_id, event_type, description, json.dumps(data or {})),
        )
        await db.commit()

    async def get_timeline(
        self,
        limit: int = 20,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Return recent events from the timeline."""
        db = self._ensure_db()
        if agent_id:
            cursor = await db.execute(
                "SELECT * FROM events WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in await cursor.fetchall()]

    # ── Tasks ─────────────────────────────────────────────────

    async def create_task(self, task_id: str, task_type: str) -> None:
        """Create a new task with the given id and type as title."""
        db = self._ensure_db()
        await db.execute(
            """INSERT INTO tasks (id, title, status, created_at)
               VALUES (?, ?, 'pending', datetime('now'))""",
            (task_id, task_type),
        )
        await db.commit()

    async def add_task(
        self,
        title: str,
        description: str = "",
        assigned_agent: str | None = None,
        project_id: str | None = None,
        priority: int = 5,
    ) -> str:
        """Add a task with a generated id. Returns the task id."""
        db = self._ensure_db()
        tid = uuid.uuid4().hex[:12]
        await db.execute(
            """INSERT INTO tasks (id, title, description, assigned_agent,
               project_id, priority) VALUES (?,?,?,?,?,?)""",
            (tid, title, description, assigned_agent, project_id, priority),
        )
        await db.commit()
        return tid

    async def update_task(self, task_id: str, updates: dict | None = None, **kwargs: Any) -> None:
        """Update task fields, restricted to ALLOWED_UPDATE_COLUMNS.

        Accepts either a ``updates`` dict or keyword arguments (not both).
        Raises ValueError for any column not in the whitelist.
        """
        if updates is None:
            updates = kwargs

        if not updates:
            return

        forbidden = set(updates.keys()) - ALLOWED_UPDATE_COLUMNS
        if forbidden:
            raise ValueError(f"Update of columns not allowed: {forbidden}")

        db = self._ensure_db()
        for col, value in updates.items():
            await db.execute(
                f"UPDATE tasks SET {col} = ? WHERE id = ?",
                (value, task_id),
            )
        await db.commit()

    async def get_task(self, task_id: str) -> dict | None:
        """Retrieve a single task by id, or None if not found."""
        db = self._ensure_db()
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_tasks(self, status: str | None = None) -> list[dict]:
        """Return tasks, optionally filtered by status."""
        db = self._ensure_db()
        if status:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC",
                (status,),
            )
        else:
            cursor = await db.execute("SELECT * FROM tasks ORDER BY priority DESC")
        return [dict(r) for r in await cursor.fetchall()]

    # ── Projects ──────────────────────────────────────────────

    async def add_project(
        self,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """Create a project. Returns the project id."""
        db = self._ensure_db()
        pid = uuid.uuid4().hex[:12]
        await db.execute(
            "INSERT INTO projects (id, name, description, tags) VALUES (?,?,?,?)",
            (pid, name, description, json.dumps(tags or [])),
        )
        await db.commit()
        return pid

    async def get_projects(self) -> list[dict]:
        """Return all projects."""
        db = self._ensure_db()
        cursor = await db.execute("SELECT * FROM projects")
        return [dict(r) for r in await cursor.fetchall()]

    # ── Statistics ────────────────────────────────────────────

    async def get_memory_stats(self) -> dict:
        """Return row counts for each table."""
        db = self._ensure_db()
        stats: dict[str, int] = {}
        for table in ("memories", "events", "messages", "tasks", "agents", "projects"):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            stats[table] = row[0] if row else 0
        return stats
