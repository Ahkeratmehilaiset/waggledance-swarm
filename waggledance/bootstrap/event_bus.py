"""In-memory event bus with failure tracking."""
# implements EventBusPort

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from waggledance.core.domain.events import DomainEvent, EventType
except ImportError:
    from dataclasses import dataclass
    from enum import Enum

    class EventType(Enum):
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
        type: EventType
        payload: dict
        timestamp: float
        source: str


HANDLER_TIMEOUT = 5.0


class InMemoryEventBus:
    """Simple in-process event bus with failure tracking."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Callable]] = {}
        self._failure_counts: dict[EventType, int] = {}

    async def publish(self, event: DomainEvent) -> None:
        """Publish event to all subscribed handlers."""
        for handler in self._handlers.get(event.type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await asyncio.wait_for(handler(event), timeout=HANDLER_TIMEOUT)
                else:
                    handler(event)
            except Exception as e:
                self._failure_counts[event.type] = (
                    self._failure_counts.get(event.type, 0) + 1
                )
                logger.error(
                    "Event handler error for %s: %s",
                    event.type,
                    e,
                    exc_info=True,
                )

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """Subscribe a handler to an event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    def get_failure_count(self, event_type: EventType) -> int:
        """Observable failure count for testing and monitoring."""
        return self._failure_counts.get(event_type, 0)
