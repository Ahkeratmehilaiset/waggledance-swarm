"""Audio adapter — wraps legacy AudioMonitor for bee/bird sound analysis."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_AudioMonitor = None


def _get_audio_monitor():
    global _AudioMonitor
    if _AudioMonitor is None:
        try:
            from integrations.audio_monitor import AudioMonitor
            _AudioMonitor = AudioMonitor
        except ImportError:
            pass
    return _AudioMonitor


class AudioAdapter:
    """Wraps legacy AudioMonitor for the autonomy sensor layer.

    Provides structured audio analysis for bee colony sounds
    and bird detection through the standard execute() interface.
    """

    CAPABILITY_ID = "sense.audio"

    def __init__(self, audio_monitor=None, world_model=None,
                 anomaly_engine=None):
        self._monitor = audio_monitor
        self._world_model = world_model
        self._anomaly_engine = anomaly_engine
        self._event_count = 0
        self._anomaly_count = 0

    @property
    def available(self) -> bool:
        return self._monitor is not None

    def execute(self, topic: str = "", payload: Optional[Dict[str, Any]] = None,
                analysis_type: str = "auto", **kwargs) -> Dict[str, Any]:
        """Process an audio event or trigger analysis.

        Args:
            topic: MQTT topic for audio events
            payload: Audio event payload
            analysis_type: "bee", "bird", or "auto" (both)

        Returns:
            Dict with success, observations list, anomalies list.
        """
        t0 = time.monotonic()
        self._event_count += 1

        observations = []
        anomalies = []

        if payload:
            obs = self._parse_audio_event(topic, payload)
            if obs:
                observations.append(obs)
                self._update_world_model(obs)
                anomaly = self._check_anomaly(obs)
                if anomaly:
                    anomalies.append(anomaly)

        # Run live analysis if monitor is available
        if self._monitor:
            if analysis_type in ("bee", "auto"):
                bee_obs = self._get_bee_observation()
                if bee_obs:
                    observations.append(bee_obs)
                    self._update_world_model(bee_obs)

            if analysis_type in ("bird", "auto"):
                bird_obs = self._get_bird_observation()
                if bird_obs:
                    observations.append(bird_obs)
                    self._update_world_model(bird_obs)

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

    def _parse_audio_event(self, topic: str,
                           payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse an audio MQTT event into an observation dict."""
        event_type = payload.get("type", payload.get("event", "audio"))
        value = payload.get("confidence", payload.get("score",
                            payload.get("level", 0.5)))

        try:
            value = float(value)
        except (ValueError, TypeError):
            value = 0.5

        entity_id = payload.get("hive_id", payload.get("source", "audio_monitor"))
        metric = payload.get("metric", f"audio_{event_type}")

        return {
            "sensor_id": f"{entity_id}.{metric}",
            "entity_id": entity_id,
            "metric": metric,
            "value": value,
            "unit": "confidence",
            "source": "audio",
            "timestamp": time.time(),
            "quality": min(value, 1.0),
            "metadata": {
                "topic": topic,
                "event_type": event_type,
            },
        }

    def _get_bee_observation(self) -> Optional[Dict[str, Any]]:
        """Get bee colony sound analysis from the monitor."""
        try:
            if hasattr(self._monitor, "get_bee_analysis"):
                result = self._monitor.get_bee_analysis()
            elif hasattr(self._monitor, "analyze_bees"):
                result = self._monitor.analyze_bees()
            else:
                return None

            if not isinstance(result, dict) or "error" in result:
                return None

            score = result.get("score", result.get("health", 0.5))
            return {
                "sensor_id": "audio_monitor.bee_health",
                "entity_id": "audio_monitor",
                "metric": "bee_health",
                "value": float(score),
                "unit": "score",
                "source": "audio",
                "timestamp": time.time(),
                "quality": 0.7,
                "metadata": {"analysis_type": "bee", "raw_result": result},
            }
        except Exception as exc:
            logger.debug("Bee analysis failed: %s", exc)
        return None

    def _get_bird_observation(self) -> Optional[Dict[str, Any]]:
        """Get bird detection results from the monitor."""
        try:
            if hasattr(self._monitor, "get_bird_detections"):
                result = self._monitor.get_bird_detections()
            elif hasattr(self._monitor, "detect_birds"):
                result = self._monitor.detect_birds()
            else:
                return None

            if not isinstance(result, dict) or "error" in result:
                return None

            count = result.get("count", result.get("detections", 0))
            return {
                "sensor_id": "audio_monitor.bird_count",
                "entity_id": "audio_monitor",
                "metric": "bird_count",
                "value": float(count),
                "unit": "count",
                "source": "audio",
                "timestamp": time.time(),
                "quality": 0.7,
                "metadata": {"analysis_type": "bird", "raw_result": result},
            }
        except Exception as exc:
            logger.debug("Bird detection failed: %s", exc)
        return None

    def _update_world_model(self, obs: Dict[str, Any]) -> None:
        """Update world model from an audio observation."""
        if not self._world_model:
            return
        try:
            self._world_model.update_baseline(
                obs["entity_id"], obs["metric"], obs["value"],
                source_type="observed", confidence=obs.get("quality", 0.7),
            )
            self._world_model.register_entity(
                obs["entity_id"], "audio_sensor",
                attributes={"source": "audio"},
            )
        except Exception as exc:
            logger.debug("WorldModel update failed: %s", exc)

    def _check_anomaly(self, obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run anomaly detection on an audio observation."""
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

    def get_bee_analysis(self) -> Dict[str, Any]:
        """Get bee colony sound analysis."""
        if not self._monitor:
            return {"available": False, "error": "AudioMonitor not available"}
        try:
            if hasattr(self._monitor, "get_bee_analysis"):
                return self._monitor.get_bee_analysis()
            if hasattr(self._monitor, "analyze_bees"):
                return self._monitor.analyze_bees()
        except Exception as exc:
            logger.warning("Audio bee analysis failed: %s", exc)
        return {"available": True, "error": "No bee analysis method available"}

    def get_bird_detections(self) -> Dict[str, Any]:
        """Get bird detection results."""
        if not self._monitor:
            return {"available": False, "error": "AudioMonitor not available"}
        try:
            if hasattr(self._monitor, "get_bird_detections"):
                return self._monitor.get_bird_detections()
            if hasattr(self._monitor, "detect_birds"):
                return self._monitor.detect_birds()
        except Exception as exc:
            logger.warning("Audio bird detection failed: %s", exc)
        return {"available": True, "error": "No bird detection method available"}

    def ingest_audio_event(self, topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Process an audio event and return structured result."""
        self._event_count += 1
        return {
            "success": True,
            "topic": topic,
            "payload": payload,
            "timestamp": time.time(),
            "capability_id": self.CAPABILITY_ID,
        }

    def get_status(self) -> Dict[str, Any]:
        """Get audio monitor status."""
        status = {"available": self.available, "events_processed": self._event_count}
        if self._monitor and hasattr(self._monitor, "status"):
            try:
                status["monitor_status"] = self._monitor.status()
            except Exception:
                pass
        return status

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "events_processed": self._event_count,
            "anomalies_detected": self._anomaly_count,
        }
