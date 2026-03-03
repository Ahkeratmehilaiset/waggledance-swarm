"""
WaggleDance — Phase 5: Frigate NVR Integration
================================================
Subscribes to Frigate MQTT events via MQTTHub.
Parses detections, classifies severity, deduplicates, stores Finnish-formatted
events in ChromaDB, triggers AlertDispatcher for high-severity events.

Severity rules:
  bear/wolf  → CRITICAL
  person (night 22-06) → HIGH
  dog/cat    → MEDIUM
  person/car (day) → INFO
"""

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("frigate_mqtt")

# Object label → Finnish translation
LABEL_FI = {
    "person": "ihminen",
    "car": "auto",
    "truck": "kuorma-auto",
    "motorcycle": "moottoripyörä",
    "bicycle": "polkupyörä",
    "dog": "koira",
    "cat": "kissa",
    "bird": "lintu",
    "bear": "karhu",
    "wolf": "susi",
    "moose": "hirvi",
    "deer": "peura",
    "fox": "kettu",
    "rabbit": "jänis",
    "horse": "hevonen",
}


class FrigateIntegration:
    """Frigate NVR event processing via MQTT."""

    def __init__(self, config: dict, mqtt_hub=None,
                 alert_dispatcher=None, consciousness=None):
        self.enabled = config.get("enabled", False)
        self.topic_prefix = config.get("mqtt_topic_prefix", "frigate")
        self.cameras = config.get("cameras", [])
        self.min_score = config.get("min_score", 0.6)
        self.dedup_window_s = config.get("dedup_window_s", 60)

        # Severity rules from config
        severity_rules = config.get("severity_rules", {})
        self.critical_labels = set(severity_rules.get("critical", ["bear", "wolf"]))
        self.high_labels = set(severity_rules.get("high", ["person_night"]))
        self.medium_labels = set(severity_rules.get("medium", ["dog", "cat"]))
        self.info_labels = set(severity_rules.get("info", ["person", "car", "bird"]))

        self.mqtt_hub = mqtt_hub
        self.alert_dispatcher = alert_dispatcher
        self.consciousness = consciousness

        # Dedup: (label, camera) -> last_event_time
        self._recent: dict[tuple[str, str], float] = {}

        # Recent events for API
        self._events: deque = deque(maxlen=100)

        # Stats
        self._total_events = 0
        self._stored_events = 0
        self._alerts_triggered = 0
        self._filtered_low_score = 0
        self._deduped = 0
        self._errors = 0

    def start(self):
        """Subscribe to Frigate MQTT topics."""
        if not self.enabled:
            log.info("Frigate integration disabled")
            return

        if not self.mqtt_hub:
            log.warning("Frigate: no MQTT hub available")
            return

        # Subscribe to frigate events
        self.mqtt_hub.subscribe(
            f"{self.topic_prefix}/events", self._handle_event
        )
        # Also subscribe to per-camera reviews
        self.mqtt_hub.subscribe(
            f"{self.topic_prefix}/+/+/+", self._handle_detection
        )
        log.info(f"Frigate integration started (prefix={self.topic_prefix})")

    def stop(self):
        """No resources to clean up — MQTT hub handles connection."""
        log.info("Frigate integration stopped")

    async def _handle_event(self, topic: str, payload: str):
        """Handle frigate/events messages."""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self._errors += 1
            return

        await self._process_event(data)

    async def _handle_detection(self, topic: str, payload: str):
        """Handle frigate/{camera}/{label}/{zone} messages."""
        # Parse topic parts
        parts = topic.split("/")
        if len(parts) < 4:
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # Some detection topics send simple values
            return

        if isinstance(data, dict):
            # Ensure camera/label from topic if not in payload
            if "camera" not in data and len(parts) > 1:
                data["camera"] = parts[1]
            if "label" not in data and len(parts) > 2:
                data["label"] = parts[2]
            await self._process_event(data)

    async def _process_event(self, data: dict):
        """Process a single Frigate event/detection."""
        self._total_events += 1

        # Extract fields — Frigate sends events in different formats
        # Handle both "before"/"after" wrapper and flat format
        event = data
        if "after" in data:
            event = data["after"]
        elif "before" in data:
            event = data["before"]

        label = event.get("label", "").lower()
        score = event.get("score", event.get("top_score", 0))
        camera = event.get("camera", "unknown")
        zones = event.get("zones", event.get("current_zones", []))
        event_type = event.get("type", data.get("type", "detection"))

        if not label:
            return

        # Score filter
        if isinstance(score, (int, float)) and score < self.min_score:
            self._filtered_low_score += 1
            return

        # Dedup: same label+camera within window
        key = (label, camera)
        now = time.monotonic()
        if key in self._recent:
            if (now - self._recent[key]) < self.dedup_window_s:
                self._deduped += 1
                return

        self._recent[key] = now
        self._cleanup_recent(now)

        # Classify severity
        severity = self._classify_severity(label)

        # Finnish label
        fi_label = LABEL_FI.get(label, label)

        # Format Finnish text for ChromaDB
        score_pct = round(score * 100) if isinstance(score, float) else score
        zone_str = ", ".join(zones) if zones else ""
        text = f"Kamerahavainto ({camera}): {fi_label}"
        if zone_str:
            text += f" alueella {zone_str}"
        text += f" (varmuus {score_pct}%)"

        # Build event record
        event_record = {
            "label": label,
            "label_fi": fi_label,
            "camera": camera,
            "score": score,
            "zones": zones,
            "severity": severity,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._events.append(event_record)

        # Store in ChromaDB
        if self.consciousness:
            try:
                self.consciousness.learn(
                    text,
                    agent_id="frigate",
                    source_type="camera_detection",
                    confidence=min(0.95, score) if isinstance(score, float) else 0.85,
                    metadata={
                        "category": "camera_event",
                        "camera": camera,
                        "label": label,
                        "severity": severity,
                        "ttl_hours": 168,
                        "source": "frigate",
                    },
                )
                self._stored_events += 1
            except Exception as e:
                self._errors += 1
                log.warning(f"Frigate ChromaDB store failed: {e}")

        # Trigger alert for high+ severity
        from integrations.alert_dispatcher import (
            Alert, SEVERITY_HIGH, SEVERITY_CRITICAL, SEVERITY_ORDER,
        )
        if (self.alert_dispatcher
                and SEVERITY_ORDER.get(severity, 0)
                >= SEVERITY_ORDER.get(SEVERITY_HIGH, 3)):
            try:
                alert = Alert(
                    severity=severity,
                    title=f"Kamerahavainto: {fi_label} ({camera})",
                    message=text,
                    source="frigate",
                    metadata={"camera": camera, "label": label, "score": score},
                )
                await self.alert_dispatcher.send_alert(alert)
                self._alerts_triggered += 1
            except Exception as e:
                self._errors += 1
                log.warning(f"Frigate alert dispatch failed: {e}")

        log.info(f"Frigate event: {severity} — {text}")

    def _classify_severity(self, label: str) -> str:
        """Classify detection severity based on label and time of day."""
        from integrations.alert_dispatcher import (
            SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_INFO,
        )

        if label in self.critical_labels:
            return SEVERITY_CRITICAL

        # Person at night = HIGH
        if label == "person" and self._is_night():
            return SEVERITY_HIGH

        if "person_night" in self.high_labels and label == "person" and self._is_night():
            return SEVERITY_HIGH

        if label in self.medium_labels:
            return SEVERITY_MEDIUM

        return SEVERITY_INFO

    @staticmethod
    def _is_night() -> bool:
        """Check if current local time is night (22:00-06:00)."""
        hour = datetime.now().hour
        return hour >= 22 or hour < 6

    def _cleanup_recent(self, now: float):
        """Remove expired dedup entries."""
        expired = [
            k for k, t in self._recent.items()
            if (now - t) > self.dedup_window_s * 2
        ]
        for k in expired:
            del self._recent[k]

    def get_recent_events(self, limit: int = 20) -> list:
        """Return recent events for API."""
        return list(self._events)[-limit:]

    def get_status(self) -> dict:
        """Status dict for dashboard/API."""
        return {
            "enabled": self.enabled,
            "topic_prefix": self.topic_prefix,
            "cameras": self.cameras,
            "min_score": self.min_score,
            "total_events": self._total_events,
            "stored_events": self._stored_events,
            "alerts_triggered": self._alerts_triggered,
            "filtered_low_score": self._filtered_low_score,
            "deduped": self._deduped,
            "errors": self._errors,
            "recent_event_count": len(self._events),
        }
