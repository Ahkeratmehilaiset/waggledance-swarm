"""
WaggleDance — Phase 5: Sensor Hub (Orchestrator)
==================================================
Top-level orchestrator for all smart home sensor integrations:
- MQTTHub (paho-mqtt client)
- FrigateIntegration (camera NVR events)
- HomeAssistantBridge (HA REST API)
- AlertDispatcher (Telegram + Webhook alerts)

Init order: AlertDispatcher → MQTTHub → Frigate → HA
All components gracefully degrade if disabled or unavailable.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("sensor_hub")


@dataclass
class SensorEvent:
    """Standardized sensor event dataclass."""
    source: str
    event_type: str
    severity: str
    title: str
    data: dict = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


class SensorHub:
    """Top-level sensor integration orchestrator."""

    def __init__(self, config: dict, consciousness=None, loop=None,
                 ws_broadcast=None):
        """
        Args:
            config: Full settings dict (should have mqtt, home_assistant,
                    frigate, alerts keys)
            consciousness: Consciousness instance for ChromaDB storage
            loop: asyncio event loop for MQTT thread bridge
            ws_broadcast: async callable(event_type, data) for WebSocket push
        """
        self.config = config
        self.consciousness = consciousness
        self._loop = loop
        self._ws_broadcast = ws_broadcast

        # Components (init'd in start())
        self.alert_dispatcher = None
        self.mqtt_hub = None
        self.frigate = None
        self.home_assistant = None
        self.audio_monitor = None

        self._started = False
        self._start_time: Optional[float] = None

    async def start(self):
        """Initialize and start all sensor components.

        Order: AlertDispatcher → MQTTHub → Frigate → HA
        Each component gracefully degrades on failure.
        """
        log.info("SensorHub starting...")
        self._start_time = time.monotonic()

        # 1. Alert Dispatcher (no deps)
        try:
            from integrations.alert_dispatcher import AlertDispatcher
            alerts_cfg = self.config.get("alerts", {})
            self.alert_dispatcher = AlertDispatcher(alerts_cfg)
            await self.alert_dispatcher.start()
            if self.alert_dispatcher.enabled:
                log.info("  AlertDispatcher: OK")
            else:
                log.info("  AlertDispatcher: disabled")
        except Exception as e:
            log.warning(f"  AlertDispatcher init failed: {e}")
            self.alert_dispatcher = None

        # 2. MQTT Hub (no deps)
        try:
            from integrations.mqtt_hub import MQTTHub
            mqtt_cfg = self.config.get("mqtt", {})
            self.mqtt_hub = MQTTHub(mqtt_cfg, loop=self._loop)
            self.mqtt_hub.start()
            if self.mqtt_hub.enabled:
                log.info("  MQTTHub: OK")
            else:
                log.info("  MQTTHub: disabled")
        except Exception as e:
            log.warning(f"  MQTTHub init failed: {e}")
            self.mqtt_hub = None

        # 3. Audio Monitor (needs MQTT + AlertDispatcher)
        try:
            from integrations.audio_monitor import AudioMonitor
            audio_cfg = self.config.get("audio", {})
            self.audio_monitor = AudioMonitor(
                config=audio_cfg,
                mqtt_hub=self.mqtt_hub,
                consciousness=self.consciousness,
                alert_dispatcher=self.alert_dispatcher,
            )
            await self.audio_monitor.start()
            if self.audio_monitor._enabled:
                log.info("  AudioMonitor: OK")
            else:
                log.info("  AudioMonitor: disabled")
        except Exception as e:
            log.warning(f"  AudioMonitor init failed: {e}")
            self.audio_monitor = None

        # 4. Frigate (needs MQTT + AlertDispatcher)
        try:
            from integrations.frigate_mqtt import FrigateIntegration
            frigate_cfg = self.config.get("frigate", {})
            self.frigate = FrigateIntegration(
                config=frigate_cfg,
                mqtt_hub=self.mqtt_hub,
                alert_dispatcher=self.alert_dispatcher,
                consciousness=self.consciousness,
            )
            self.frigate.start()
            if self.frigate.enabled:
                log.info("  Frigate: OK")
            else:
                log.info("  Frigate: disabled")
        except Exception as e:
            log.warning(f"  Frigate init failed: {e}")
            self.frigate = None

        # 5. Home Assistant (independent)
        try:
            from integrations.home_assistant import HomeAssistantBridge
            ha_cfg = self.config.get("home_assistant", {})
            self.home_assistant = HomeAssistantBridge(
                config=ha_cfg,
                consciousness=self.consciousness,
            )
            await self.home_assistant.start()
            if self.home_assistant.enabled:
                log.info("  HomeAssistant: OK")
            else:
                log.info("  HomeAssistant: disabled")
        except Exception as e:
            log.warning(f"  HomeAssistant init failed: {e}")
            self.home_assistant = None

        # 6. MQTT Sensor Ingest — hive temperature (v1.18.0)
        self._sensor_ingest = None
        if self.mqtt_hub and self.mqtt_hub.enabled:
            try:
                from core.mqtt_sensor_ingest import MQTTSensorIngest

                def _on_store(reading):
                    """Write valid reading to SharedMemory."""
                    try:
                        if self.consciousness and hasattr(self.consciousness, 'shared_memory'):
                            self.consciousness.shared_memory.set(
                                f"hive_{reading.hive_id}_temperature",
                                {"temp_c": reading.temperature_c,
                                 "ts": reading.timestamp,
                                 "hive_id": reading.hive_id},
                            )
                    except Exception as e:
                        log.debug("SharedMemory write: %s", e)

                def _on_anomaly(reading, desc):
                    """Fire alert on temperature anomaly."""
                    if self.alert_dispatcher:
                        try:
                            from integrations.alert_dispatcher import Alert
                            import asyncio
                            alert = Alert(
                                severity="high",
                                title="Sensor anomaly",
                                message=desc,
                                source="sensor_anomaly",
                            )
                            loop = asyncio.get_running_loop()
                            loop.create_task(self.alert_dispatcher.send_alert(alert))
                        except Exception:
                            pass
                    log.warning("Hive anomaly: %s", desc)

                self._sensor_ingest = MQTTSensorIngest(
                    on_store=_on_store, on_anomaly=_on_anomaly)

                async def _mqtt_temp_handler(topic, payload):
                    """MQTT callback: route hive/+/temperature to ingest."""
                    self._sensor_ingest.on_message(topic, payload)

                self.mqtt_hub.subscribe("hive/+/temperature", _mqtt_temp_handler)
                log.info("  MQTTSensorIngest: OK (hive/+/temperature)")
            except Exception as e:
                log.warning("  MQTTSensorIngest init failed: %s", e)

        self._started = True
        log.info("SensorHub started")

    async def stop(self):
        """Stop all sensor components in reverse order."""
        log.info("SensorHub stopping...")

        if self.home_assistant:
            try:
                await self.home_assistant.stop()
            except Exception as e:
                log.warning(f"HA stop error: {e}")

        if self.audio_monitor:
            try:
                await self.audio_monitor.stop()
            except Exception as e:
                log.warning(f"AudioMonitor stop error: {e}")

        if self.frigate:
            try:
                self.frigate.stop()
            except Exception as e:
                log.warning(f"Frigate stop error: {e}")

        if self.mqtt_hub:
            try:
                self.mqtt_hub.stop()
            except Exception as e:
                log.warning(f"MQTT stop error: {e}")

        if self.alert_dispatcher:
            try:
                await self.alert_dispatcher.stop()
            except Exception as e:
                log.warning(f"Alert dispatcher stop error: {e}")

        self._started = False
        log.info("SensorHub stopped")

    def get_sensor_context(self) -> str:
        """Generate Finnish summary of all sensor data for agent context."""
        parts = []

        # Home Assistant context
        if self.home_assistant and self.home_assistant.enabled:
            ha_ctx = self.home_assistant.get_home_context()
            if ha_ctx:
                parts.append(ha_ctx)

        # Bee audio context
        if self.audio_monitor and self.audio_monitor._enabled:
            bee = self.audio_monitor._bee_analyzer
            if bee:
                for hive_id, result in bee._hive_status.items():
                    if result.anomaly:
                        parts.append(
                            f"Pesä {hive_id}: {result.description_fi}"
                        )

        # Recent camera events
        if self.frigate and self.frigate.enabled:
            recent = self.frigate.get_recent_events(limit=5)
            if recent:
                cam_texts = [e.get("text", "") for e in recent if e.get("text")]
                if cam_texts:
                    parts.append(
                        "Viimeisimmät kamerahavainnot: "
                        + "; ".join(cam_texts[:3])
                    )

        return " | ".join(parts) if parts else ""

    async def broadcast_event(self, event: SensorEvent):
        """Broadcast sensor event via WebSocket."""
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(
                    "sensor_update", event.to_dict()
                )
            except Exception as e:
                log.warning(f"WS broadcast failed: {e}")

    async def broadcast_camera_alert(self, event_data: dict):
        """Broadcast camera alert via WebSocket."""
        if self._ws_broadcast:
            try:
                await self._ws_broadcast("camera_alert", event_data)
            except Exception as e:
                log.warning(f"WS camera alert broadcast failed: {e}")

    def get_status(self) -> dict:
        """Aggregated status from all components."""
        uptime = None
        if self._start_time:
            uptime = round(time.monotonic() - self._start_time)

        return {
            "started": self._started,
            "uptime_s": uptime,
            "mqtt": (
                self.mqtt_hub.get_status()
                if self.mqtt_hub else {"enabled": False}
            ),
            "home_assistant": (
                self.home_assistant.get_status()
                if self.home_assistant else {"enabled": False}
            ),
            "frigate": (
                self.frigate.get_status()
                if self.frigate else {"enabled": False}
            ),
            "audio_monitor": (
                self.audio_monitor.get_status()
                if self.audio_monitor else {"enabled": False}
            ),
            "alerts": (
                self.alert_dispatcher.get_status()
                if self.alert_dispatcher else {"enabled": False}
            ),
        }
