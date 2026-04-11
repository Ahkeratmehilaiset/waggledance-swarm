"""Minimal PriorityLock for the hex runtime.

Local reimplementation — intentionally does NOT import from legacy ``core.*`` so
the legacy-import-freeze guard (tests/test_legacy_import_freeze.py) stays green.

Contract: the only method invoked by :class:`integrations.data_scheduler.DataFeedScheduler`
is :meth:`wait_if_chat`. When the event is set, it returns immediately; callers
can later extend this with pause/resume hooks without touching feed code.
"""

import asyncio


class PriorityLock:
    """Async gate used by background feeds to yield to user chat traffic."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._event.set()

    async def wait_if_chat(self) -> None:
        """Block until no chat has priority (no-op in the default open state)."""
        await self._event.wait()
