"""Sensor port — external sensor data access (stub-only in this sprint)."""

from typing import Protocol


class SensorPort(Protocol):
    """Port for sensor data access."""

    async def get_latest(self, sensor_id: str) -> dict | None: ...

    async def get_all_active(self) -> list[dict]: ...
