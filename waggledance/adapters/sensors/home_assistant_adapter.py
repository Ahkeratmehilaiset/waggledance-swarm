"""Home Assistant adapter — wraps legacy HomeAssistantBridge for the autonomy core."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HABridge = None


def _get_ha():
    global _HABridge
    if _HABridge is None:
        try:
            from integrations.home_assistant import HomeAssistantBridge
            _HABridge = HomeAssistantBridge
        except ImportError:
            pass
    return _HABridge


# HA entity domain → metric/unit mappings
_DOMAIN_MAP = {
    "sensor": {"metric_suffix": "", "default_unit": ""},
    "binary_sensor": {"metric_suffix": "state", "default_unit": "bool"},
    "climate": {"metric_suffix": "temperature", "default_unit": "°C"},
    "weather": {"metric_suffix": "temperature", "default_unit": "°C"},
    "light": {"metric_suffix": "brightness", "default_unit": "%"},
    "switch": {"metric_suffix": "state", "default_unit": "bool"},
}


class HomeAssistantAdapter:
    """Wraps legacy HomeAssistantBridge for the autonomy sensor layer.

    Polls HA entities and converts them into normalized SensorObservation dicts
    with world model + anomaly engine integration.
    """

    CAPABILITY_ID = "sense.home_assistant"

    def __init__(self, bridge=None, world_model=None, anomaly_engine=None):
        self._bridge = bridge
        self._world_model = world_model
        self._anomaly_engine = anomaly_engine
        self._poll_count = 0
        self._observation_count = 0
        self._anomaly_count = 0

    @property
    def available(self) -> bool:
        return self._bridge is not None

    def execute(self, entity_id: str = "", poll_all: bool = False,
                **kwargs) -> Dict[str, Any]:
        """Poll HA entity states and return normalized observations.

        Args:
            entity_id: Specific HA entity ID to poll (e.g., "sensor.outdoor_temp")
            poll_all: If True, poll all known entities

        Returns:
            Dict with success, observations list, anomalies list.
        """
        t0 = time.monotonic()
        self._poll_count += 1

        if not self._bridge:
            return {
                "success": False,
                "error": "HomeAssistant bridge not available",
                "capability_id": self.CAPABILITY_ID,
            }

        observations = []
        anomalies = []

        try:
            if entity_id and not poll_all:
                # Poll single entity
                state = self._get_entity(entity_id)
                if state:
                    obs = self._parse_entity(entity_id, state)
                    if obs:
                        observations.append(obs)
                        self._update_world_model(obs)
                        anomaly = self._check_anomaly(obs)
                        if anomaly:
                            anomalies.append(anomaly)
            else:
                # Poll all entities
                states = self._get_all_states()
                for eid, state in states.items():
                    obs = self._parse_entity(eid, state)
                    if obs:
                        observations.append(obs)
                        self._update_world_model(obs)
                        anomaly = self._check_anomaly(obs)
                        if anomaly:
                            anomalies.append(anomaly)
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": str(exc),
                "capability_id": self.CAPABILITY_ID,
                "latency_ms": round(elapsed, 2),
            }

        self._observation_count += len(observations)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "success": True,
            "observations": observations,
            "observation_count": len(observations),
            "anomalies": anomalies,
            "anomaly_count": len(anomalies),
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold" if observations else "bronze",
            "latency_ms": round(elapsed, 2),
        }

    def _get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a single HA entity state."""
        try:
            if hasattr(self._bridge, "get_entity"):
                return self._bridge.get_entity(entity_id)
        except Exception as exc:
            logger.debug("HA get_entity failed: %s", exc)
        return None

    def _get_all_states(self) -> Dict[str, Any]:
        """Get all HA entity states."""
        try:
            if hasattr(self._bridge, "get_states"):
                result = self._bridge.get_states()
                return result if isinstance(result, dict) else {}
            if hasattr(self._bridge, "poll"):
                result = self._bridge.poll()
                return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug("HA poll failed: %s", exc)
        return {}

    def _parse_entity(self, entity_id: str,
                      state: Any) -> Optional[Dict[str, Any]]:
        """Parse an HA entity state into an observation dict."""
        # Extract numeric value
        value = self._extract_value(state)
        if value is None:
            return None

        # Determine domain, metric, unit from entity_id
        domain = entity_id.split(".")[0] if "." in entity_id else "sensor"
        name = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        domain_info = _DOMAIN_MAP.get(domain, _DOMAIN_MAP["sensor"])

        metric = domain_info["metric_suffix"] or name
        unit = ""
        if isinstance(state, dict):
            unit = state.get("attributes", {}).get(
                "unit_of_measurement",
                state.get("unit", domain_info["default_unit"]))
        else:
            unit = domain_info["default_unit"]

        return {
            "sensor_id": f"ha_{name}.{metric}",
            "entity_id": f"ha_{name}",
            "metric": metric,
            "value": value,
            "unit": unit or "",
            "source": "home_assistant",
            "timestamp": time.time(),
            "quality": 0.9,
            "metadata": {
                "ha_entity_id": entity_id,
                "domain": domain,
            },
        }

    @staticmethod
    def _extract_value(state: Any) -> Optional[float]:
        """Extract a numeric value from an HA state."""
        if isinstance(state, (int, float)):
            return float(state)
        if isinstance(state, str):
            # Binary states
            if state.lower() in ("on", "true", "open", "home"):
                return 1.0
            if state.lower() in ("off", "false", "closed", "away",
                                 "unavailable", "unknown"):
                return 0.0
            try:
                return float(state)
            except ValueError:
                return None
        if isinstance(state, dict):
            # Try common keys
            for key in ("state", "value", "temperature", "humidity",
                        "brightness"):
                v = state.get(key)
                if v is not None:
                    return HomeAssistantAdapter._extract_value(v)
        return None

    def _update_world_model(self, obs: Dict[str, Any]) -> None:
        """Update world model from an HA observation."""
        if not self._world_model:
            return
        try:
            self._world_model.update_baseline(
                obs["entity_id"], obs["metric"], obs["value"],
                source_type="observed", confidence=obs.get("quality", 0.9),
            )
            self._world_model.register_entity(
                obs["entity_id"], "ha_entity",
                attributes={
                    "ha_entity_id": obs["metadata"].get("ha_entity_id", ""),
                    "domain": obs["metadata"].get("domain", ""),
                    "source": "home_assistant",
                },
            )
        except Exception as exc:
            logger.debug("WorldModel update failed: %s", exc)

    def _check_anomaly(self, obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run anomaly detection on an HA observation."""
        if not self._anomaly_engine:
            return None
        try:
            key = f"{obs['entity_id']}.{obs['metric']}"
            baseline = None
            if self._world_model:
                baseline = self._world_model.get_baseline(
                    obs["entity_id"], obs["metric"])
            results = self._anomaly_engine.check_all(
                key, obs["value"], baseline=baseline)
            flagged = [r.to_dict() for r in results if r.is_anomaly]
            if flagged:
                self._anomaly_count += 1
                return {
                    "sensor_id": obs["sensor_id"],
                    "checks": flagged,
                    "max_severity": max(r["severity"] for r in flagged),
                }
        except Exception as exc:
            logger.debug("Anomaly check failed: %s", exc)
        return None

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "poll_count": self._poll_count,
            "observations": self._observation_count,
            "anomalies_detected": self._anomaly_count,
        }
