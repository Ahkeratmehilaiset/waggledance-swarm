"""Frigate adapter — wraps legacy FrigateIntegration for camera events."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_Frigate = None


def _get_frigate():
    global _Frigate
    if _Frigate is None:
        try:
            from integrations.frigate_mqtt import FrigateIntegration
            _Frigate = FrigateIntegration
        except ImportError:
            pass
    return _Frigate


# Severity weights for different detection types
_SEVERITY_MAP = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "info": 0.2,
}

# Object types that map to specific metrics
_OBJECT_METRICS = {
    "person": ("detection", "person_count"),
    "bear": ("detection", "bear_alert"),
    "car": ("detection", "vehicle_count"),
    "dog": ("detection", "animal_count"),
    "cat": ("detection", "animal_count"),
    "bird": ("detection", "bird_count"),
}


class FrigateAdapter:
    """Wraps legacy FrigateIntegration for the autonomy sensor layer.

    Converts Frigate detection events into normalized SensorObservation dicts
    with world model + anomaly engine integration.
    """

    CAPABILITY_ID = "sense.camera_frigate"

    def __init__(self, frigate=None, world_model=None, anomaly_engine=None):
        self._frigate = frigate
        self._world_model = world_model
        self._anomaly_engine = anomaly_engine
        self._event_count = 0
        self._detection_count = 0
        self._anomaly_count = 0

    @property
    def available(self) -> bool:
        return self._frigate is not None

    def execute(self, event: Optional[Dict[str, Any]] = None,
                **kwargs) -> Dict[str, Any]:
        """Process a Frigate detection event and return normalized observations.

        Args:
            event: Frigate event dict with keys like 'type', 'after.label',
                   'after.camera', 'after.score', 'after.current_zones'.

        Returns:
            Dict with success, observations list, anomalies list.
        """
        t0 = time.monotonic()
        self._event_count += 1

        if not event:
            return {
                "success": False,
                "error": "No event provided",
                "capability_id": self.CAPABILITY_ID,
            }

        observations = []
        anomalies = []

        # Delegate to legacy if available
        legacy_result = None
        if self._frigate and hasattr(self._frigate, "process_event"):
            try:
                legacy_result = self._frigate.process_event(event)
            except Exception as exc:
                logger.debug("Frigate legacy process failed: %s", exc)

        # Parse event into observation
        obs = self._parse_event(event, legacy_result)
        if obs:
            observations.append(obs)
            self._detection_count += 1
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

    def _parse_event(self, event: Dict[str, Any],
                     legacy_result: Any = None) -> Optional[Dict[str, Any]]:
        """Parse a Frigate event into an observation dict."""
        # Extract from nested 'after' structure (Frigate MQTT format)
        after = event.get("after", event)
        label = after.get("label", event.get("label", ""))
        camera = after.get("camera", event.get("camera", "unknown"))
        score = after.get("score", after.get("top_score",
                          event.get("score", 0.5)))
        zones = after.get("current_zones", event.get("zones", []))
        event_type = event.get("type", "detection")
        severity = event.get("severity", "info")

        if not label:
            return None

        # Map label to metric
        metric_info = _OBJECT_METRICS.get(label, ("detection", f"{label}_count"))
        metric_type, metric_name = metric_info

        # Determine entity_id from camera name
        entity_id = f"camera_{camera}"

        return {
            "sensor_id": f"{entity_id}.{metric_name}",
            "entity_id": entity_id,
            "metric": metric_name,
            "value": float(score) if isinstance(score, (int, float)) else 0.5,
            "unit": "confidence",
            "source": "frigate",
            "timestamp": time.time(),
            "quality": min(float(score), 1.0) if isinstance(score, (int, float)) else 0.5,
            "metadata": {
                "label": label,
                "camera": camera,
                "zones": zones if isinstance(zones, list) else [],
                "event_type": event_type,
                "severity": severity,
                "severity_score": _SEVERITY_MAP.get(severity, 0.2),
            },
        }

    def _update_world_model(self, obs: Dict[str, Any]) -> None:
        """Update world model from a camera observation."""
        if not self._world_model:
            return
        try:
            self._world_model.register_entity(
                obs["entity_id"], "camera",
                attributes={
                    "last_detection": obs["metadata"].get("label", ""),
                    "last_score": obs["value"],
                    "source": "frigate",
                },
            )
            self._world_model.update_baseline(
                obs["entity_id"], obs["metric"], obs["value"],
                source_type="observed", confidence=obs.get("quality", 0.5),
            )
        except Exception as exc:
            logger.debug("WorldModel update failed: %s", exc)

    def _check_anomaly(self, obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if a detection is anomalous (unusual object/high severity)."""
        if not self._anomaly_engine:
            return None
        try:
            severity = obs["metadata"].get("severity_score", 0.2)
            if severity >= 0.8:
                self._anomaly_count += 1
                return {
                    "sensor_id": obs["sensor_id"],
                    "checks": [{
                        "method": "severity_threshold",
                        "is_anomaly": True,
                        "severity": severity,
                        "message": f"High-severity detection: {obs['metadata'].get('label', '')}",
                    }],
                    "max_severity": severity,
                }
        except Exception as exc:
            logger.debug("Anomaly check failed: %s", exc)
        return None

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "events_processed": self._event_count,
            "detections": self._detection_count,
            "anomalies_detected": self._anomaly_count,
        }
