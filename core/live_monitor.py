# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
"""
WaggleDance Swarm AI Live Monitor — Reaaliaikainen tapahtumasyöte
"""
import asyncio
import json
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EventCategory(Enum):
    SYSTEM = "system"
    THOUGHT = "thought"
    CHAT = "chat"
    TASK = "task"
    AGENT = "agent"
    ERROR = "error"


@dataclass
class MonitorEvent:
    category: EventCategory
    agent_id: str
    agent_name: str
    title: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class LiveMonitor:
    """Kerää ja jakaa tapahtumia reaaliaikaisesti."""

    def __init__(self, max_history: int = 200):
        self.events: list[MonitorEvent] = []
        self.max_history = max_history
        self._callbacks: list = []

    @staticmethod
    def _fix_mojibake(s: str) -> str:
        """Korjaa double-encoded UTF-8: PÃ¤Ã¤ → Pää."""
        if not s:
            return s
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return s

    async def emit(self, event: MonitorEvent):
        # Korjaa mahdollinen mojibake agenttinimessä
        event.agent_name = self._fix_mojibake(event.agent_name)
        if event.title:
            event.title = self._fix_mojibake(event.title)

        self.events.append(event)
        if len(self.events) > self.max_history:
            self.events = self.events[-self.max_history:]

        for cb in self._callbacks:
            try:
                await cb(event.to_dict())
            except Exception:
                pass

        # Tulosta konsoliin (encoding-safe)
        cat = event.category.value[:4].upper()
        try:
            print(f"  [{cat}] {event.agent_name}: {event.title[:80]}")
        except UnicodeEncodeError:
            # Windows cp1252 fallback
            safe_name = event.agent_name.encode("utf-8", errors="replace").decode("utf-8")
            safe_title = event.title[:80].encode("utf-8", errors="replace").decode("utf-8")
            print(f"  [{cat}] {safe_name}: {safe_title}")

    async def system(self, message: str):
        await self.emit(MonitorEvent(
            EventCategory.SYSTEM, "system", "System", title=message
        ))

    async def thought(self, agent_id: str, agent_name: str,
                      prompt: str = "", response: str = ""):
        await self.emit(MonitorEvent(
            EventCategory.THOUGHT, agent_id, agent_name,
            title=f"💭 {response[:100]}",
            content=response,
            metadata={"prompt_preview": prompt[:200]}
        ))

    async def chat(self, from_id: str, from_name: str,
                   to_id: str, to_name: str, message: str):
        await self.emit(MonitorEvent(
            EventCategory.CHAT, from_id, from_name,
            title=f"💬 → {to_name}: {message[:80]}",
            content=message,
            metadata={"to_agent": to_id, "to_name": to_name}
        ))

    async def agent_spawned(self, agent_id: str, agent_name: str,
                             agent_type: str):
        await self.emit(MonitorEvent(
            EventCategory.AGENT, agent_id, agent_name,
            title=f"🐣 Luotu: {agent_name} ({agent_type})"
        ))

    def register_callback(self, callback):
        self._callbacks.append(callback)

    def get_history(self, limit: int = 50) -> list[dict]:
        return [e.to_dict() for e in self.events[-limit:]]
