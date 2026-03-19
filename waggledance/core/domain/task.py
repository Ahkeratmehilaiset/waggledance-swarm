"""Task domain models."""

from dataclasses import dataclass, field


@dataclass
class TaskRequest:
    """Incoming task to be routed and executed."""

    id: str
    query: str
    language: str  # "fi" | "en" | "auto"
    profile: str
    user_id: str | None
    context: list[dict] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class TaskRoute:
    """Routing decision for a task."""

    route_type: str  # "hotcache" | "memory" | "solver" | "llm" | "swarm"
    selected_agents: list[str] = field(default_factory=list)
    confidence: float = 0.0
    routing_latency_ms: float = 0.0
