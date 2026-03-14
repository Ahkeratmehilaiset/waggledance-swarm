"""Domain event types and event model."""

from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    """All domain event types in the system."""

    TASK_RECEIVED = "task_received"
    ROUTE_SELECTED = "route_selected"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    ROUND_TABLE_STARTED = "round_table_started"
    ROUND_TABLE_COMPLETED = "round_table_completed"
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    NIGHT_MODE_STARTED = "night_mode_started"
    NIGHT_MODE_TICK = "night_mode_tick"
    NIGHT_STALL_DETECTED = "night_stall_detected"
    CORRECTION_RECORDED = "correction_recorded"
    CIRCUIT_OPEN = "circuit_open"
    CIRCUIT_CLOSED = "circuit_closed"
    TASK_ERROR = "task_error"
    APP_SHUTDOWN = "app_shutdown"


@dataclass
class DomainEvent:
    """Immutable domain event."""

    type: EventType
    payload: dict
    timestamp: float
    source: str
