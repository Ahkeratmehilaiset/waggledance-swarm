"""Hot cache port — fast in-memory key-value cache with TTL."""

from typing import Protocol


class HotCachePort(Protocol):
    """Port for in-memory hot cache (synchronous, no I/O)."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    def evict_expired(self) -> int: ...

    def stats(self) -> dict: ...
