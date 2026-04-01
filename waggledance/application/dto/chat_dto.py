"""Chat request/response DTOs."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatRequest:
    """Incoming chat request from user."""

    query: str
    language: str = "auto"
    profile: str = "HOME"
    user_id: str | None = None
    session_id: str | None = None
    context_turns: int = 5


@dataclass
class ChatResult:
    """Chat response returned to user."""

    response: str
    language: str
    source: str
    confidence: float
    latency_ms: float
    agent_id: str | None
    round_table: bool
    cached: bool
    hybrid_trace: dict[str, Any] | None = None
