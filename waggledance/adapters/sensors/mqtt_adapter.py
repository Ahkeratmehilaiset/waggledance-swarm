"""MQTT adapter — wraps legacy MQTTHub + MQTTSensorIngest for sensor data ingestion."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MQTTHub = None
_MQTTSensorIngest = None


def _get_mqtt():
    global _MQTTHub
    if _MQTTHub is None:
        try:
            from integrations.mqtt_hub import MQTTHub
            _MQTTHub = MQTTHub
        except ImportError:
            pass
    return _MQTTHub


def _get_ingest():
    global _MQTTSensorIngest
    if _MQTTSensorIngest is None:
        try:
            from core.mqtt_sensor_ingest import MQTTSensorIngest
            _MQTTSensorIngest = MQTTSensorIngest
        except ImportError:
            pass
    return _MQTTSensorIngest


class MQTTAdapter:
    """Wraps legacy MQTTHub + MQTTSensorIngest for the autonomy sensor layer.

    Provides structured message ingestion, parsing, validation, and
    normalized SensorObservation output through the standard execute() interface.
    """

    CAPABILITY_ID = "sense.mqtt_ingest"

    def __init__(self, mqtt_hub=None, ingest=None, world_model=None,
                 anomaly_engine=None):
        self._hub = mqtt_hub
        self._ingest = ingest
        self._world_model = world_model
        self._anomaly_engine = anomaly_engine
        self._message_count = 0
        self._parse_errors = 0
        self._anomaly_count = 0
        self._callbacks: List[Callable] = []

    @property
    def available(self) -> bool:
        return self._hub is not None or self._ingest is not None

    def subscribe(self, topic: str, callback: Callable = None) -> bool:
        """Subscribe to an MQTT topic."""
        if not self._hub:
            return False
        try:
            if hasattr(self._hub, "subscribe"):
                self._hub.subscribe(topic, callback)
            return True
        except Exception as exc:
            logger.warning("MQTT subscribe failed: %s", exc)
            return False

    def publish(self, topic: str, payload: Any) -> bool:
        """Publish a message to an MQTT topic."""
        if not self._hub:
            return False
        try:
            if hasattr(self._hub, "publish"):
                self._hub.publish(topic, payload)
            return True
        except Exception as exc:
            logger.warning("MQTT publish failed: %s", exc)
            return False

    def execute(self, topic: str = "", payload: Any = "",
                **kwargs) -> Dict[str, Any]:
        """Process an MQTT message and return normalized sensor observations.

        Args:
            topic: MQTT topic string (e.g., "hive/hive_1/temperature")
            payload: Raw payload (string, bytes, or dict)

        Returns:
            Dict with success, observations list, anomalies list.
        """
        t0 = time.monotonic()
        self._message_count += 1

        if not topic:
            return {
                "success": False,
                "error": "No topic provided",
                "capability_id": self.CAPABILITY_ID,
            }

        observations = []
        anomalies = []

        # Try parsing through MQTTSensorIngest if available
        if self._ingest:
            reading = self._ingest.on_message(topic, payload)
            if reading is not None:
                if reading.valid:
                    obs = {
                        "sensor_id": f"{reading.hive_id}.temperature",
                        "entity_id": reading.hive_id,
                        "metric": "temperature",
                        "value": reading.temperature_c,
                        "unit": "°C",
                        "source": "mqtt",
                        "timestamp": reading.timestamp,
                        "quality": 1.0,
                        "metadata": {"raw_topic": reading.raw_topic},
                    }
                    observations.append(obs)
                    self._update_world_model(obs)
                    anomaly = self._check_anomaly(obs)
                    if anomaly:
                        anomalies.append(anomaly)
                else:
                    self._parse_errors += 1
                    return {
                        "success": False,
                        "error": reading.error or "Invalid reading",
                        "capability_id": self.CAPABILITY_ID,
                        "latency_ms": round((time.monotonic() - t0) * 1000, 2),
                    }
            else:
                # Topic didn't match ingest pattern — try generic parse
                obs = self._parse_generic(topic, payload)
                if obs:
                    observations.append(obs)
                    self._update_world_model(obs)
                    anomaly = self._check_anomaly(obs)
                    if anomaly:
                        anomalies.append(anomaly)
        else:
            # No ingest engine — generic parse
            obs = self._parse_generic(topic, payload)
            if obs:
                observations.append(obs)
                self._update_world_model(obs)
                anomaly = self._check_anomaly(obs)
                if anomaly:
                    anomalies.append(anomaly)

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

    def _parse_generic(self, topic: str, payload: Any) -> Optional[Dict[str, Any]]:
        """Parse a generic MQTT message into an observation dict."""
        parts = topic.strip("/").split("/")
        if len(parts) < 2:
            self._parse_errors += 1
            return None

        entity_id = parts[-2] if len(parts) >= 2 else parts[0]
        metric = parts[-1]

        # Try to extract numeric value
        value = self._extract_value(payload)
        if value is None:
            self._parse_errors += 1
            return None

        return {
            "sensor_id": f"{entity_id}.{metric}",
            "entity_id": entity_id,
            "metric": metric,
            "value": value,
            "unit": "",
            "source": "mqtt",
            "timestamp": time.time(),
            "quality": 0.8,  # generic parse = lower confidence
            "metadata": {"raw_topic": topic},
        }

    @staticmethod
    def _extract_value(payload: Any) -> Optional[float]:
        """Extract a numeric value from various payload formats."""
        if isinstance(payload, (int, float)):
            return float(payload)
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if isinstance(payload, str):
            try:
                return float(payload.strip())
            except ValueError:
                return None
        if isinstance(payload, dict):
            for key in ("value", "temperature", "temp", "humidity", "state"):
                if key in payload:
                    try:
                        return float(payload[key])
                    except (ValueError, TypeError):
                        pass
        return None

    def _update_world_model(self, obs: Dict[str, Any]) -> None:
        """Update world model baselines from an observation."""
        if not self._world_model:
            return
        try:
            self._world_model.update_baseline(
                obs["entity_id"], obs["metric"], obs["value"],
                source_type="observed", confidence=obs.get("quality", 0.8),
            )
            self._world_model.register_entity(
                obs["entity_id"], "sensor",
                attributes={"last_metric": obs["metric"],
                            "last_value": obs["value"],
                            "source": "mqtt"},
            )
        except Exception as exc:
            logger.debug("WorldModel update failed: %s", exc)

    def _check_anomaly(self, obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run anomaly detection on an observation."""
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

    def get_status(self) -> Dict[str, Any]:
        status = {"available": self.available, "messages_processed": self._message_count}
        if self._hub and hasattr(self._hub, "status"):
            try:
                status["hub_status"] = self._hub.status()
            except Exception:
                pass
        return status

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "messages_processed": self._message_count,
            "parse_errors": self._parse_errors,
            "anomalies_detected": self._anomaly_count,
        }
