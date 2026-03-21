"""
WaggleDance — Phase 6: Audio Monitor
======================================
Main orchestrator for ESP32 audio sensor data.
Subscribes to MQTT spectrum/event/status topics, routes data to
BeeAudioAnalyzer and BirdMonitor, stores events in ChromaDB,
triggers alerts via AlertDispatcher.

MQTT topics:
  waggledance/audio/+/spectrum — FFT spectrum data
  waggledance/audio/+/event    — Audio events
  waggledance/audio/+/status   — Sensor status
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("audio_monitor")


class AudioMonitor:
    """Orchestrates audio sensor data from ESP32 devices via MQTT."""

    def __init__(self, config: dict, mqtt_hub=None, consciousness=None,
                 alert_dispatcher=None):
        """
        Args:
            config: audio section from settings.yaml
            mqtt_hub: MQTTHub instance for MQTT subscriptions
            consciousness: Consciousness instance for ChromaDB storage
            alert_dispatcher: AlertDispatcher for sending alerts
        """
        self._config = config
        self._enabled = config.get("enabled", False)
        self._mqtt_hub = mqtt_hub
        self._consciousness = consciousness
        self._alert_dispatcher = alert_dispatcher

        # Sub-components
        self._bee_analyzer = None
        self._bird_monitor = None

        # MQTT topics
        topics_cfg = config.get("mqtt_topics", {})
        self._topic_spectrum = topics_cfg.get(
            "spectrum", "waggledance/audio/+/spectrum"
        )
        self._topic_event = topics_cfg.get(
            "event", "waggledance/audio/+/event"
        )
        self._topic_status = topics_cfg.get(
            "status", "waggledance/audio/+/status"
        )

        # Event buffer
        self._recent_events: deque = deque(maxlen=100)
        self._total_events = 0
        self._total_spectrums = 0
        self._started = False
        self._start_time: Optional[float] = None

        log.info("AudioMonitor initialized (enabled=%s)", self._enabled)

    async def start(self):
        """Initialize sub-components and subscribe to MQTT topics."""
        if not self._enabled:
            log.info("AudioMonitor disabled in config")
            return

        self._start_time = time.monotonic()

        # Initialize BeeAudioAnalyzer
        try:
            from integrations.bee_audio import BeeAudioAnalyzer
            bee_cfg = self._config.get("bee_audio", {})
            self._bee_analyzer = BeeAudioAnalyzer(bee_cfg)
            log.info("  BeeAudioAnalyzer: OK")
        except Exception as e:
            log.warning("  BeeAudioAnalyzer init failed: %s", e)

        # Initialize BirdMonitor
        try:
            from integrations.bird_monitor import BirdMonitor
            bird_cfg = self._config.get("bird_monitor", {})
            self._bird_monitor = BirdMonitor(bird_cfg)
            await self._bird_monitor.initialize()
            log.info("  BirdMonitor: OK (model_loaded=%s)",
                     self._bird_monitor._model_loaded if self._bird_monitor else False)
        except Exception as e:
            log.warning("  BirdMonitor init failed: %s", e)

        # Subscribe to MQTT topics
        if self._mqtt_hub and hasattr(self._mqtt_hub, 'subscribe'):
            try:
                self._mqtt_hub.subscribe(
                    self._topic_spectrum, self._handle_spectrum
                )
                self._mqtt_hub.subscribe(
                    self._topic_event, self._handle_event
                )
                self._mqtt_hub.subscribe(
                    self._topic_status, self._handle_status
                )
                log.info("  MQTT subscriptions: 3 topics")
            except Exception as e:
                log.warning("  MQTT subscribe failed: %s", e)
        else:
            log.info("  No MQTT hub — running without live data")

        self._started = True
        log.info("AudioMonitor started")

    async def stop(self):
        """Cleanup resources."""
        self._started = False
        log.info("AudioMonitor stopped")

    async def _handle_spectrum(self, topic: str, payload: str):
        """Handle incoming FFT spectrum data from MQTT.

        Expected payload: JSON with "hive_id" and "spectrum" array
        """
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            log.debug("Invalid spectrum JSON from %s", topic)
            return

        self._total_spectrums += 1
        hive_id = data.get("hive_id", self._extract_device_id(topic))
        spectrum = data.get("spectrum", [])

        if not spectrum:
            return

        # Bee audio analysis
        if self._bee_analyzer:
            result = self._bee_analyzer.analyze_spectrum(spectrum, hive_id)
            self._bee_analyzer.update_baseline(hive_id, spectrum)

            if result.anomaly:
                self._on_bee_anomaly(result)

            # Store in event buffer
            event = {
                "type": "bee_spectrum",
                "hive_id": hive_id,
                "status": result.status,
                "stress_level": result.stress_level,
                "fundamental_hz": result.fundamental_hz,
                "description_fi": result.description_fi,
                "timestamp": result.timestamp,
            }
            self._recent_events.appendleft(event)

        # Bird detection (if audio_data includes bird classification)
        bird_data = data.get("bird", {})
        if bird_data and self._bird_monitor:
            detection = self._bird_monitor.classify(bird_data)
            if detection:
                event = {
                    "type": "bird_detection",
                    "species": detection.species,
                    "species_fi": detection.species_fi,
                    "confidence": detection.confidence,
                    "is_predator": detection.is_predator,
                    "timestamp": detection.timestamp,
                }
                self._recent_events.appendleft(event)
                self._total_events += 1

                if detection.is_predator:
                    self._on_predator_detected(detection)

    async def _handle_event(self, topic: str, payload: str):
        """Handle audio event messages from MQTT."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            log.debug("Invalid event JSON from %s", topic)
            return

        self._total_events += 1
        device_id = data.get("device_id", self._extract_device_id(topic))

        event = {
            "type": data.get("event_type", "audio_event"),
            "device_id": device_id,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._recent_events.appendleft(event)

        # Store in ChromaDB
        self._store_event(event)

    async def _handle_status(self, topic: str, payload: str):
        """Handle sensor status messages from MQTT."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return

        device_id = data.get("device_id", self._extract_device_id(topic))
        log.debug("Audio sensor %s status: %s", device_id,
                  data.get("status", "unknown"))

    def _extract_device_id(self, topic: str) -> str:
        """Extract device ID from MQTT topic (waggledance/audio/DEVICE/...)."""
        parts = topic.split("/")
        if len(parts) >= 3:
            return parts[2]
        return "unknown"

    def _on_bee_anomaly(self, result):
        """Handle bee anomaly — store in ChromaDB and send alert."""
        # Store in ChromaDB
        text = f"Pesä {result.hive_id}: {result.description_fi} " \
               f"(taajuus {result.fundamental_hz} Hz, stressi {result.stress_level:.0%})"
        self._store_fact(text, result.confidence)

        # Alert for critical states
        if self._alert_dispatcher and result.status in ("swarming", "queen_piping"):
            severity = "critical" if result.status == "swarming" else "high"
            try:
                from integrations.alert_dispatcher import Alert
                alert = Alert(
                    severity=severity,
                    title=f"Mehiläishälytys: {result.description_fi}",
                    message=text,
                    source="bee_audio",
                    metadata={"hive_id": result.hive_id},
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._alert_dispatcher.send_alert(alert))
                except RuntimeError:
                    log.debug("No event loop for alert dispatch")
            except Exception as e:
                log.warning("Alert send failed: %s", e)

    def _on_predator_detected(self, detection):
        """Handle predator detection — alert."""
        if self._alert_dispatcher:
            try:
                from integrations.alert_dispatcher import Alert
                alert = Alert(
                    severity="high",
                    title=f"Petoeläin havaittu: {detection.species_fi}",
                    message=f"{detection.species_fi} ({detection.species}) "
                            f"tunnistettu (luottamus {detection.confidence:.0%})",
                    source="bird_monitor",
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._alert_dispatcher.send_alert(alert))
                except RuntimeError:
                    log.debug("No event loop for alert dispatch")
            except Exception as e:
                log.warning("Predator alert failed: %s", e)

    def _store_event(self, event: dict):
        """Store audio event in ChromaDB."""
        text = f"Audio event: {event.get('type', 'unknown')} " \
               f"from {event.get('device_id', 'unknown')}"
        self._store_fact(text, 0.80)

    def _store_fact(self, text: str, confidence: float):
        """Store a fact in ChromaDB via consciousness."""
        if not self._consciousness:
            return
        try:
            if hasattr(self._consciousness, 'store_fact'):
                self._consciousness.store_fact(
                    text=text,
                    category="audio_event",
                    confidence=confidence,
                    source="audio_monitor",
                )
            elif hasattr(self._consciousness, 'memory'):
                self._consciousness.memory.store(
                    text=text,
                    metadata={
                        "category": "audio_event",
                        "confidence": confidence,
                        "source": "audio_monitor",
                    },
                )
        except Exception as e:
            log.debug("ChromaDB store failed: %s", e)

    def get_recent_events(self, limit: int = 20) -> list:
        """Get recent audio events."""
        return list(self._recent_events)[:limit]

    def get_status(self) -> dict:
        """Full status of AudioMonitor and sub-components."""
        uptime = None
        if self._start_time:
            uptime = round(time.monotonic() - self._start_time)

        return {
            "enabled": self._enabled,
            "started": self._started,
            "uptime_s": uptime,
            "total_events": self._total_events,
            "total_spectrums": self._total_spectrums,
            "recent_events": len(self._recent_events),
            "bee_analyzer": (
                self._bee_analyzer.stats if self._bee_analyzer else {"available": False}
            ),
            "bird_monitor": (
                self._bird_monitor.stats if self._bird_monitor else {"available": False}
            ),
        }

    @property
    def stats(self) -> dict:
        return self.get_status()
