"""Agent domain models."""

from dataclasses import dataclass, field


@dataclass
class AgentDefinition:
    """Immutable agent identity and capabilities."""

    id: str
    name: str
    domain: str
    tags: list[str]
    skills: list[str]
    trust_level: int  # 0-4: NOVICE, APPRENTICE, JOURNEYMAN, EXPERT, MASTER
    specialization_score: float
    active: bool
    profile: str  # GADGET / COTTAGE / HOME / FACTORY / ALL


@dataclass
class AgentResult:
    """Result of an agent processing a task."""

    agent_id: str
    response: str
    confidence: float
    latency_ms: float
    source: str  # "hotcache" | "memory" | "llm" | "swarm" ONLY
    metadata: dict = field(default_factory=dict)
