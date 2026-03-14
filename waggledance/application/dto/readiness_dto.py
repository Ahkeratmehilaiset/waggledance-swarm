"""Readiness check DTOs."""

from dataclasses import dataclass, field


@dataclass
class ComponentStatus:
    """Health status of a single component."""

    name: str
    ready: bool
    message: str
    latency_ms: float | None = None


@dataclass
class ReadinessStatus:
    """Aggregate readiness of all components."""

    ready: bool
    components: list[ComponentStatus] = field(default_factory=list)
    uptime_seconds: float = 0.0
