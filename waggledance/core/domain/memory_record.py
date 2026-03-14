"""Memory record domain model."""

from dataclasses import dataclass, field


@dataclass
class MemoryRecord:
    """A single stored fact or knowledge item."""

    id: str
    content: str
    content_fi: str | None
    source: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    agent_id: str | None = None
    created_at: float = 0.0
    ttl_seconds: int | None = None
