"""Sensor fusion adapter — combines multiple sensor sources into unified observations.

Provides unified sensor data access across MQTT, Home Assistant,
Frigate, and audio sensors through a single interface, with
world model and anomaly engine integration.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SensorHub = None


def _get_sensor_hub():
    global _SensorHub
    if _SensorHub is None:
        try:
            from integrations.sensor_hub import SensorHub
            _SensorHub = SensorHub
        except ImportError:
            pass
    return _SensorHub


class SensorFusionAdapter:
    """Unified sensor interface combining multiple sensor adapters.

    Provides:
    - Fused observations from all active sensor sources
    - Staleness detection for sensor readings
    - Conflict resolution when multiple sources report on same entity
    - Context enrichment for the WorldModel
    """

    CAPABILITY_ID = "sense.fusion"

    DEFAULT_MAX_AGE = 3600.0  # 1 hour

    def __init__(self, sensor_hub=None, sensor_adapters: Optional[List] = None,
                 world_model=None, anomaly_engine=None):
        self._hub = sensor_hub
        self._adapters = sensor_adapters or []
        self._world_model = world_model
        self._anomaly_engine = anomaly_engine
        self._readings: Dict[str, Dict[str, Any]] = {}
        self._last_update: Dict[str, float] = {}
        self._fusion_count = 0

    @property
    def available(self) -> bool:
        return self._hub is not None or len(self._adapters) > 0 or len(self._readings) > 0

    def execute(self, sources: Optional[List[str]] = None,
                max_age_seconds: float = DEFAULT_MAX_AGE,
                **kwargs) -> Dict[str, Any]:
        """Fuse observations from all active sensor sources.

        Args:
            sources: Filter to specific sources (e.g., ["mqtt", "home_assistant"]).
                     None means all sources.
            max_age_seconds: Maximum age for stale reading filtering.

        Returns:
            Dict with success, fused observations, stale sensors, anomalies.
        """
        t0 = time.monotonic()
        self._fusion_count += 1

        observations = []
        anomalies = []
        stale_sensors = []

        # Collect from sub-adapters
        for adapter in self._adapters:
            source = getattr(adapter, "CAPABILITY_ID", "unknown")
            source_name = source.split(".")[-1] if "." in source else source

            if sources and source_name not in sources:
                continue

            if not getattr(adapter, "available", False):
                continue

            try:
                result = adapter.execute(**kwargs)
                if result.get("success") and result.get("observations"):
                    for obs in result["observations"]:
                        observations.append(obs)
                        # Track reading
                        sid = obs.get("sensor_id", "")
                        if sid:
                            self._readings[sid] = obs
                            self._last_update[sid] = obs.get("timestamp", time.time())
                if result.get("anomalies"):
                    anomalies.extend(result["anomalies"])
            except Exception as exc:
                logger.debug("Sub-adapter %s failed: %s", source, exc)

        # Include cached readings that are still fresh
        now = time.time()
        for sid, reading in self._readings.items():
            ts = reading.get("timestamp", 0)
            age = now - ts
            if age > max_age_seconds:
                stale_sensors.append({
                    "sensor_id": sid,
                    "age_seconds": round(age, 1),
                    "last_value": reading.get("value"),
                })
            else:
                # Add cached readings not already in observations
                if not any(o.get("sensor_id") == sid for o in observations):
                    observations.append(reading)

        # Resolve conflicts (same entity.metric from multiple sources)
        fused = self._resolve_conflicts(observations)

        # Update world model with fused observations
        for obs in fused:
            self._update_world_model(obs)

        elapsed = (time.monotonic() - t0) * 1000
        return {
            "success": True,
            "observations": fused,
            "observation_count": len(fused),
            "anomalies": anomalies,
            "anomaly_count": len(anomalies),
            "stale_sensors": stale_sensors,
            "stale_count": len(stale_sensors),
            "sources_polled": len(self._adapters),
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold" if fused else "bronze",
            "latency_ms": round(elapsed, 2),
        }

    def _resolve_conflicts(self, observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resolve conflicts when multiple observations cover the same metric.

        Strategy: prefer higher quality, then more recent timestamp.
        """
        by_key: Dict[str, Dict[str, Any]] = {}
        for obs in observations:
            key = obs.get("sensor_id", "")
            if not key:
                continue
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = obs
            else:
                # Higher quality wins; if equal, more recent wins
                new_q = obs.get("quality", 0)
                old_q = existing.get("quality", 0)
                if new_q > old_q or (new_q == old_q and
                        obs.get("timestamp", 0) > existing.get("timestamp", 0)):
                    by_key[key] = obs
        return list(by_key.values())

    def update_reading(self, sensor_id: str, value: Any,
                       unit: str = "", source: str = "") -> None:
        """Record a sensor reading manually."""
        now = time.time()
        self._readings[sensor_id] = {
            "sensor_id": sensor_id,
            "entity_id": sensor_id.split(".")[0] if "." in sensor_id else sensor_id,
            "metric": sensor_id.split(".", 1)[1] if "." in sensor_id else "value",
            "value": value,
            "unit": unit,
            "source": source,
            "timestamp": now,
            "quality": 0.8,
            "metadata": {"manual": True},
        }
        self._last_update[sensor_id] = now

    def get_reading(self, sensor_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest reading for a sensor."""
        return self._readings.get(sensor_id)

    def get_all_readings(self, max_age_seconds: float = DEFAULT_MAX_AGE) -> Dict[str, Dict[str, Any]]:
        """Get all sensor readings, optionally filtered by age."""
        now = time.time()
        return {
            sid: reading for sid, reading in self._readings.items()
            if (now - reading.get("timestamp", 0)) <= max_age_seconds
        }

    def get_context_for_world_model(self) -> Dict[str, Any]:
        """Build sensor context dict for WorldModel snapshot enrichment."""
        context = {}
        for sid, reading in self._readings.items():
            context[sid] = reading.get("value")
        if self._hub and hasattr(self._hub, "get_context"):
            try:
                hub_context = self._hub.get_context()
                if isinstance(hub_context, dict):
                    context.update(hub_context)
            except Exception:
                pass
        return context

    def _update_world_model(self, obs: Dict[str, Any]) -> None:
        """Update world model from a fused observation."""
        if not self._world_model:
            return
        try:
            self._world_model.update_baseline(
                obs["entity_id"], obs["metric"], obs["value"],
                source_type="observed",
                confidence=obs.get("quality", 0.8),
            )
            self._world_model.register_entity(
                obs["entity_id"], "sensor",
                attributes={"source": obs.get("source", "fusion")},
            )
        except Exception as exc:
            logger.debug("WorldModel update failed: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        """Get sensor subsystem status."""
        status = {
            "adapter_available": self.available,
            "sensors_active": len(self._readings),
            "sensor_ids": list(self._readings.keys()),
            "sub_adapters": len(self._adapters),
        }
        if self._hub and hasattr(self._hub, "status"):
            try:
                hub_status = self._hub.status()
                if isinstance(hub_status, dict):
                    status["hub_status"] = hub_status
            except Exception:
                pass
        return status

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "active_sensors": len(self._readings),
            "fusion_count": self._fusion_count,
            "sub_adapters": len(self._adapters),
        }
