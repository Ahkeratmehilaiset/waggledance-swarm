"""
Chat history storage — SQLite-backed conversation persistence.
Stores user/assistant messages, agent attribution, and user feedback.
"""
import sqlite3
import threading
import time
from pathlib import Path


class ChatHistory:
    """Thread-safe SQLite chat history with feedback support."""

    def __init__(self, db_path: str = "data/chat_history.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        profile TEXT DEFAULT 'cottage'
                    );
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id INTEGER REFERENCES conversations(id),
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        agent_name TEXT,
                        language TEXT,
                        response_time_ms INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER REFERENCES messages(id),
                        rating INTEGER,
                        correction TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_conv
                        ON messages(conversation_id);
                    CREATE INDEX IF NOT EXISTS idx_feedback_msg
                        ON feedback(message_id);
                """)
                conn.commit()
            finally:
                conn.close()

    def create_conversation(self, profile: str = "cottage") -> int:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO conversations (profile) VALUES (?)",
                    (profile,))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_or_create_conversation(self, profile: str = "cottage",
                                    max_age_minutes: int = 30) -> int:
        """Get the most recent conversation or create a new one."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    """SELECT c.id FROM conversations c
                       JOIN messages m ON m.conversation_id = c.id
                       WHERE c.profile = ?
                       ORDER BY m.created_at DESC LIMIT 1""",
                    (profile,)).fetchone()
                if row:
                    # Check if last message is recent enough
                    last_msg = conn.execute(
                        "SELECT created_at FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                        (row["id"],)).fetchone()
                    if last_msg:
                        from datetime import datetime, timedelta
                        try:
                            last_ts = datetime.fromisoformat(last_msg["created_at"])
                            if datetime.now() - last_ts < timedelta(minutes=max_age_minutes):
                                return row["id"]
                        except (ValueError, TypeError):
                            pass
                # Create new conversation
                cur = conn.execute(
                    "INSERT INTO conversations (profile) VALUES (?)",
                    (profile,))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def add_message(self, conversation_id: int, role: str, content: str,
                    agent_name: str = None, language: str = None,
                    response_time_ms: int = None) -> int:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, agent_name, language, response_time_ms)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (conversation_id, role, content, agent_name, language,
                     response_time_ms))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def add_feedback(self, message_id: int, rating: int,
                     correction: str = None) -> int:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO feedback (message_id, rating, correction) VALUES (?, ?, ?)",
                    (message_id, rating, correction))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_conversations(self, limit: int = 20, offset: int = 0,
                          profile: str = None) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                query = """
                    SELECT c.id, c.created_at, c.profile,
                           COUNT(m.id) as message_count,
                           MAX(m.content) as last_message
                    FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                """
                params = []
                if profile:
                    query += " WHERE c.profile = ?"
                    params.append(profile)
                query += " GROUP BY c.id ORDER BY c.id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_conversation(self, conversation_id: int) -> dict:
        with self._lock:
            conn = self._get_conn()
            try:
                conv = conn.execute(
                    "SELECT * FROM conversations WHERE id = ?",
                    (conversation_id,)).fetchone()
                if not conv:
                    return None
                msgs = conn.execute(
                    """SELECT m.*, f.rating as feedback_rating
                       FROM messages m
                       LEFT JOIN feedback f ON f.message_id = m.id
                       WHERE m.conversation_id = ?
                       ORDER BY m.id""",
                    (conversation_id,)).fetchall()
                return {
                    "id": conv["id"],
                    "created_at": conv["created_at"],
                    "profile": conv["profile"],
                    "messages": [dict(m) for m in msgs],
                }
            finally:
                conn.close()

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        """Get most recent messages across all conversations."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT m.*, f.rating as feedback_rating
                       FROM messages m
                       LEFT JOIN feedback f ON f.message_id = m.id
                       ORDER BY m.id DESC LIMIT ?""",
                    (limit,)).fetchall()
                return [dict(r) for r in reversed(rows)]
            finally:
                conn.close()
