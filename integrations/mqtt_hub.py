"""
WaggleDance — Phase 5: MQTT Hub
================================
Paho-mqtt client with background thread → asyncio bridge.
Handles subscribe/dedup/reconnect with graceful degradation.

Used by: FrigateIntegration, SensorHub
"""

import asyncio
import hashlib
import logging
import threading
import time
from collections import defaultdict
from typing import Callable, Optional

log = logging.getLogger("mqtt_hub")


class MQTTHub:
    """MQTT client with background thread, asyncio bridge, dedup, reconnect."""

    def __init__(self, config: dict, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.enabled = config.get("enabled", False)
        self.host = config.get("host", "192.168.1.100")
        self.port = config.get("port", 1883)
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.client_id = config.get("client_id", "waggledance")
        self.dedup_window_s = config.get("dedup_window_s", 5)

        self._loop = loop
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

        # Handlers: topic_pattern -> list of async callables
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

        # Dedup: md5 hash -> timestamp
        self._seen: dict[str, float] = {}

        # Reconnect backoff
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

        # Stats
        self._messages_received = 0
        self._messages_deduped = 0
        self._reconnect_count = 0
        self._errors = 0
        self._last_message_time: Optional[float] = None

    def subscribe(self, topic_pattern: str, handler: Callable):
        """Register async handler for MQTT topic pattern."""
        self._handlers[topic_pattern].append(handler)
        log.info(f"MQTT handler registered: {topic_pattern}")

        # If already connected, subscribe immediately
        if self._client and self._connected:
            try:
                self._client.subscribe(topic_pattern)
            except Exception as e:
                log.warning(f"MQTT subscribe failed for {topic_pattern}: {e}")

    def start(self):
        """Start MQTT client in background thread."""
        if not self.enabled:
            log.info("MQTT Hub disabled")
            return

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt not installed — MQTT Hub disabled")
            self.enabled = False
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_thread,
            name="mqtt-hub",
            daemon=True,
        )
        self._thread.start()
        log.info(f"MQTT Hub started → {self.host}:{self.port}")

    def stop(self):
        """Stop MQTT client and background thread."""
        self._running = False
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._connected = False
        log.info("MQTT Hub stopped")

    def _run_thread(self):
        """Background thread: connect, subscribe, loop."""
        import paho.mqtt.client as mqtt

        while self._running:
            try:
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.client_id,
                )
                self._client.on_connect = self._on_connect
                self._client.on_message = self._on_message
                self._client.on_disconnect = self._on_disconnect

                if self.username:
                    self._client.username_pw_set(self.username, self.password)

                self._client.connect(self.host, self.port, keepalive=60)
                self._client.loop_forever()

            except Exception as e:
                self._errors += 1
                if self._running:
                    log.warning(
                        f"MQTT connection failed: {e} — "
                        f"retry in {self._reconnect_delay:.0f}s"
                    )
                    time.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self._max_reconnect_delay,
                    )
                    self._reconnect_count += 1

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Called on successful MQTT connection."""
        self._connected = True
        self._reconnect_delay = 1.0  # Reset backoff
        log.info(f"MQTT connected to {self.host}:{self.port}")

        # Subscribe to all registered topics
        for topic in self._handlers:
            client.subscribe(topic)
            log.info(f"MQTT subscribed: {topic}")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        """Called on MQTT disconnect."""
        self._connected = False
        if self._running:
            log.warning(f"MQTT disconnected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        """Called on incoming MQTT message — bridge to asyncio."""
        try:
            payload = msg.payload.decode("utf-8", errors="replace")

            # Dedup check
            msg_hash = hashlib.md5(
                f"{msg.topic}:{payload}".encode()
            ).hexdigest()

            now = time.monotonic()
            if msg_hash in self._seen:
                if (now - self._seen[msg_hash]) < self.dedup_window_s:
                    self._messages_deduped += 1
                    return

            self._seen[msg_hash] = now
            self._messages_received += 1
            self._last_message_time = now

            # Clean old dedup entries
            self._cleanup_dedup(now)

            # Find matching handlers
            for pattern, handlers in self._handlers.items():
                if self._topic_matches(msg.topic, pattern):
                    for handler in handlers:
                        self._dispatch_async(handler, msg.topic, payload)

        except Exception as e:
            self._errors += 1
            log.warning(f"MQTT message handling error: {e}")

    def _topic_matches(self, topic: str, pattern: str) -> bool:
        """MQTT topic matching with + and # wildcards."""
        topic_parts = topic.split("/")
        pattern_parts = pattern.split("/")

        for i, pp in enumerate(pattern_parts):
            if pp == "#":
                return True
            if i >= len(topic_parts):
                return False
            if pp != "+" and pp != topic_parts[i]:
                return False

        return len(topic_parts) == len(pattern_parts)

    def _cleanup_dedup(self, now: float):
        """Remove expired dedup entries."""
        expired = [
            h for h, t in self._seen.items()
            if (now - t) > self.dedup_window_s * 2
        ]
        for h in expired:
            del self._seen[h]

    def _dispatch_async(self, handler: Callable, topic: str, payload: str):
        """Bridge from MQTT thread to asyncio event loop."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                log.warning("No asyncio loop for MQTT dispatch")
                return

        try:
            asyncio.run_coroutine_threadsafe(
                handler(topic, payload), loop
            )
        except Exception as e:
            self._errors += 1
            log.warning(f"MQTT async dispatch failed: {e}")

    def get_status(self) -> dict:
        """Status dict for dashboard/API."""
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "subscriptions": list(self._handlers.keys()),
            "messages_received": self._messages_received,
            "messages_deduped": self._messages_deduped,
            "reconnect_count": self._reconnect_count,
            "errors": self._errors,
            "last_message_time": self._last_message_time,
        }
