"""Unit tests for InMemoryEventBus — event pub/sub with failure tracking."""

import asyncio
import time

import pytest

from waggledance.bootstrap.event_bus import InMemoryEventBus
from waggledance.core.domain.events import DomainEvent, EventType


def _make_event(
    event_type: EventType = EventType.TASK_RECEIVED,
    payload: dict | None = None,
) -> DomainEvent:
    return DomainEvent(
        type=event_type,
        payload=payload or {},
        timestamp=time.time(),
        source="unit_test",
    )


class TestEventBusSubscribePublish:
    """Basic subscribe and publish."""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish_triggers_handler(
        self, event_bus: InMemoryEventBus
    ) -> None:
        received = []

        async def handler(event: DomainEvent) -> None:
            received.append(event)

        event_bus.subscribe(EventType.TASK_RECEIVED, handler)
        event = _make_event(EventType.TASK_RECEIVED, {"task": "test"})
        await event_bus.publish(event)

        assert len(received) == 1
        assert received[0].payload["task"] == "test"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_called(
        self, event_bus: InMemoryEventBus
    ) -> None:
        calls_a = []
        calls_b = []

        async def handler_a(event: DomainEvent) -> None:
            calls_a.append(event)

        async def handler_b(event: DomainEvent) -> None:
            calls_b.append(event)

        event_bus.subscribe(EventType.MEMORY_STORED, handler_a)
        event_bus.subscribe(EventType.MEMORY_STORED, handler_b)
        await event_bus.publish(_make_event(EventType.MEMORY_STORED))

        assert len(calls_a) == 1
        assert len(calls_b) == 1


class TestEventBusHandlerTypes:
    """Async and sync handler support."""

    @pytest.mark.asyncio
    async def test_async_handler_works(
        self, event_bus: InMemoryEventBus
    ) -> None:
        result = []

        async def async_handler(event: DomainEvent) -> None:
            await asyncio.sleep(0)  # yield control
            result.append("async_ok")

        event_bus.subscribe(EventType.AGENT_STARTED, async_handler)
        await event_bus.publish(_make_event(EventType.AGENT_STARTED))

        assert result == ["async_ok"]

    @pytest.mark.asyncio
    async def test_sync_handler_works(
        self, event_bus: InMemoryEventBus
    ) -> None:
        result = []

        def sync_handler(event: DomainEvent) -> None:
            result.append("sync_ok")

        event_bus.subscribe(EventType.AGENT_COMPLETED, sync_handler)
        await event_bus.publish(_make_event(EventType.AGENT_COMPLETED))

        assert result == ["sync_ok"]


class TestEventBusErrorHandling:
    """Error resilience and failure tracking."""

    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash_publisher(
        self, event_bus: InMemoryEventBus
    ) -> None:
        after_error = []

        async def failing_handler(event: DomainEvent) -> None:
            raise ValueError("handler exploded")

        async def good_handler(event: DomainEvent) -> None:
            after_error.append("survived")

        event_bus.subscribe(EventType.TASK_ERROR, failing_handler)
        event_bus.subscribe(EventType.TASK_ERROR, good_handler)

        # Should not raise
        await event_bus.publish(_make_event(EventType.TASK_ERROR))

        # The good handler should still run
        assert after_error == ["survived"]

    @pytest.mark.asyncio
    async def test_failing_handler_increments_failure_count(
        self, event_bus: InMemoryEventBus
    ) -> None:
        async def failing_handler(event: DomainEvent) -> None:
            raise RuntimeError("boom")

        event_bus.subscribe(EventType.CIRCUIT_OPEN, failing_handler)

        await event_bus.publish(_make_event(EventType.CIRCUIT_OPEN))
        assert event_bus.get_failure_count(EventType.CIRCUIT_OPEN) == 1

        await event_bus.publish(_make_event(EventType.CIRCUIT_OPEN))
        assert event_bus.get_failure_count(EventType.CIRCUIT_OPEN) == 2

    @pytest.mark.asyncio
    async def test_get_failure_count_returns_0_for_no_failure_event_types(
        self, event_bus: InMemoryEventBus
    ) -> None:
        assert event_bus.get_failure_count(EventType.APP_SHUTDOWN) == 0


class TestEventBusUnregistered:
    """Publishing to unsubscribed event types."""

    @pytest.mark.asyncio
    async def test_unregistered_event_type_publishes_silently(
        self, event_bus: InMemoryEventBus
    ) -> None:
        # No handlers subscribed for NIGHT_MODE_STARTED
        event = _make_event(EventType.NIGHT_MODE_STARTED)
        # Should not raise
        await event_bus.publish(event)

    @pytest.mark.asyncio
    async def test_events_do_not_leak_between_types(
        self, event_bus: InMemoryEventBus
    ) -> None:
        received = []

        async def handler(event: DomainEvent) -> None:
            received.append(event.type)

        event_bus.subscribe(EventType.MEMORY_STORED, handler)

        # Publishing a different event type should not trigger the handler
        await event_bus.publish(_make_event(EventType.TASK_RECEIVED))
        assert received == []

        # But the correct type should trigger it
        await event_bus.publish(_make_event(EventType.MEMORY_STORED))
        assert received == [EventType.MEMORY_STORED]
