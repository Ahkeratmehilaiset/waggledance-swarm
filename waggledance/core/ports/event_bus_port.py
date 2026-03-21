"""Event bus port — publish/subscribe for domain events."""

from typing import Callable, Protocol

from waggledance.core.domain.events import DomainEvent, EventType


class EventBusPort(Protocol):
    """Port for domain event publish/subscribe."""

    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
    ) -> None: ...
