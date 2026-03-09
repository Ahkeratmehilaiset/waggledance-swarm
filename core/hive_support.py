"""
Support classes for HiveMind orchestrator.
Extracted from hivemind.py to reduce file size.

Contains:
- PriorityLock: chat-priority lock for background task coordination
- StructuredLogger: JSON-line logger for analytics
"""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


class PriorityLock:
    """PHASE1 TASK3: Pauses background work when user chats.

    Uses asyncio.Event for async workers AND a bool property for sync checks.
    Both are always in sync — single source of truth.
    """
    def __init__(self):
        self._event = asyncio.Event()
        self._event.set()  # Not active = workers can proceed
        self._cooldown_until = 0.0  # monotonic timestamp

    @property
    def chat_active(self) -> bool:
        """Thread-safe check: is chat currently holding the lock?"""
        return not self._event.is_set()

    @property
    def in_cooldown(self) -> bool:
        """Is the post-chat cooldown still active?"""
        return time.monotonic() < self._cooldown_until

    @property
    def should_skip(self) -> bool:
        """Heartbeat should skip if chat active OR in cooldown."""
        return self.chat_active or self.in_cooldown

    async def chat_enters(self, cooldown_s: float = 3.0):
        self._event.clear()  # Block workers
        self._cooldown_until = time.monotonic() + cooldown_s

    async def chat_exits(self):
        self._event.set()  # Release workers

    async def wait_if_chat(self):
        """Workers call this between steps. Blocks if chat is active."""
        await self._event.wait()


class StructuredLogger:
    """JSON-line logger for analytics — writes to data/learning_metrics.jsonl."""

    def __init__(self, path: str = "data/learning_metrics.jsonl"):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_chat(self, *, query: str, method: str, agent_id: str = "",
                 response_time_ms: float = 0.0, confidence: float = 0.0,
                 was_hallucination: bool = False, cache_hit: bool = False,
                 model_used: str = "", route: str = "", language: str = "fi",
                 translated: bool = False, extra: dict = None):
        """Append one JSON line per chat response."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "query_hash": hashlib.md5(query.encode("utf-8", errors="replace")).hexdigest()[:12],
            "method": method,
            "agent_id": agent_id,
            "model_used": model_used,
            "route": route,
            "response_time_ms": round(response_time_ms, 1),
            "confidence": round(confidence, 3),
            "was_hallucination": was_hallucination,
            "cache_hit": cache_hit,
            "language": language,
            "translated": translated,
        }
        if extra:
            record.update(extra)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Never crash chat for logging

    def log_learning(self, *, event: str, count: int = 0, duration_ms: float = 0.0,
                     source: str = "", extra: dict = None):
        """Append one JSON line for learning/background events."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": event,
            "count": count,
            "duration_ms": round(duration_ms, 1),
            "source": source,
        }
        if extra:
            record.update(extra)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
