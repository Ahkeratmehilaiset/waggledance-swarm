# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
"""
WaggleDance Swarm AI Shared Memory — Jaettu muisti kaikille agenteille (aiosqlite)
"""
import aiosqlite
import json
import uuid
from datetime import datetime
from typing import Optional


class SharedMemory:
    """Async SQLite-pohjainen jaettu muisti."""

    def __init__(self, db_path: str = "data/waggle_dance.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT, type TEXT, status TEXT DEFAULT 'idle',
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
                agent_id TEXT, event_type TEXT,
                description TEXT, data TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_agent TEXT, to_agent TEXT,
                content TEXT, message_type TEXT DEFAULT 'info',
                read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT, description TEXT,
                assigned_agent TEXT, project_id TEXT,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 5,
                result TEXT, started_at TEXT, completed_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT, description TEXT,
                status TEXT DEFAULT 'active',
                tags TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    # ── Agentit ──────────────────────────────────────────────

    async def register_agent(self, agent_id, name, agent_type, config=None):
        await self._db.execute(
            "INSERT OR REPLACE INTO agents (id, name, type, config) VALUES (?,?,?,?)",
            (agent_id, name, agent_type, json.dumps(config or {}))
        )
        await self._db.commit()

    async def update_agent_status(self, agent_id, status):
        await self._db.execute(
            "UPDATE agents SET status = ? WHERE id = ?", (status, agent_id)
        )
        await self._db.commit()

    # ── Muisti ───────────────────────────────────────────────

    async def store_memory(self, content, agent_id=None, project_id=None,
                           memory_type="observation", importance=0.5,
                           metadata=None):
        mid = uuid.uuid4().hex[:12]
        await self._db.execute(
            """INSERT INTO memories (id, content, agent_id, project_id,
               memory_type, importance, metadata) VALUES (?,?,?,?,?,?,?)""",
            (mid, content, agent_id, project_id, memory_type,
             importance, json.dumps(metadata or {}))
        )
        await self._db.commit()
        return mid

    async def recall(self, query, limit=10, agent_id=None):
        """Yksinkertainen tekstihaku muistista."""
        if agent_id:
            cursor = await self._db.execute(
                """SELECT * FROM memories WHERE agent_id = ?
                   AND content LIKE ? ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (agent_id, f"%{query[:50]}%", limit)
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM memories WHERE content LIKE ?
                   ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (f"%{query[:50]}%", limit)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_recent_memories(self, limit=10, agent_id=None):
        if agent_id:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_full_context(self, topic="", limit=15):
        """Koosta täysi konteksti aiheesta."""
        memories = await self.recall(topic, limit=limit)
        recent = await self.get_recent_memories(limit=5)
        all_memories = memories + [m for m in recent if m not in memories]
        return "\n".join(m.get("content", "")[:200] for m in all_memories[:limit])

    # ── Viestit ──────────────────────────────────────────────

    async def send_message(self, from_agent, to_agent, content, message_type="info"):
        await self._db.execute(
            "INSERT INTO messages (from_agent, to_agent, content, message_type) VALUES (?,?,?,?)",
            (from_agent, to_agent, content, message_type)
        )
        await self._db.commit()

    async def get_messages(self, agent_id):
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE to_agent = ? AND read = 0 ORDER BY created_at DESC",
            (agent_id,)
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        return rows

    async def mark_messages_read(self, agent_id):
        await self._db.execute(
            "UPDATE messages SET read = 1 WHERE to_agent = ?", (agent_id,)
        )
        await self._db.commit()

    # ── Tapahtumat ───────────────────────────────────────────

    async def log_event(self, agent_id, event_type, description, data=None):
        await self._db.execute(
            "INSERT INTO events (agent_id, event_type, description, data) VALUES (?,?,?,?)",
            (agent_id, event_type, description, json.dumps(data or {}))
        )
        await self._db.commit()

    async def get_timeline(self, limit=20, agent_id=None):
        if agent_id:
            cursor = await self._db.execute(
                "SELECT * FROM events WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    # ── Tehtävät ─────────────────────────────────────────────

    async def add_task(self, title, description="", assigned_agent=None,
                       project_id=None, priority=5):
        tid = uuid.uuid4().hex[:12]
        await self._db.execute(
            """INSERT INTO tasks (id, title, description, assigned_agent,
               project_id, priority) VALUES (?,?,?,?,?,?)""",
            (tid, title, description, assigned_agent, project_id, priority)
        )
        await self._db.commit()
        return tid

    async def get_tasks(self, status=None):
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC", (status,)
            )
        else:
            cursor = await self._db.execute("SELECT * FROM tasks ORDER BY priority DESC")
        return [dict(r) for r in await cursor.fetchall()]

    async def update_task(self, task_id, **kwargs):
        for key, value in kwargs.items():
            await self._db.execute(
                f"UPDATE tasks SET {key} = ? WHERE id = ?", (value, task_id)
            )
        await self._db.commit()

    # ── Projektit ────────────────────────────────────────────

    async def add_project(self, name, description="", tags=None):
        pid = uuid.uuid4().hex[:12]
        await self._db.execute(
            "INSERT INTO projects (id, name, description, tags) VALUES (?,?,?,?)",
            (pid, name, description, json.dumps(tags or []))
        )
        await self._db.commit()
        return pid

    async def get_projects(self):
        cursor = await self._db.execute("SELECT * FROM projects")
        return [dict(r) for r in await cursor.fetchall()]

    # ── Tilastot ─────────────────────────────────────────────

    async def get_memory_stats(self):
        stats = {}
        for table in ["memories", "events", "messages", "tasks", "agents"]:
            cursor = await self._db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            stats[table] = row[0]
        return stats
