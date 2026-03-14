"""Configuration port — read-only config access."""

from typing import Any, Protocol


class ConfigPort(Protocol):
    """Port for configuration access."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def get_profile(self) -> str: ...

    def get_hardware_tier(self) -> str: ...
