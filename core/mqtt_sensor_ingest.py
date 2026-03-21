"""MQTT sensor ingest — parse hive temperature data, validate, store, trigger anomaly alerts."""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOPIC_PATTERN = re.compile(r"^hive/([^/]+)/temperature$")

@dataclass
class SensorReading:
    hive_id: str
    temperature_c: float
    timestamp: float = field(default_factory=time.time)
    raw_topic: str = ""
    raw_payload: str = ""
    valid: bool = True
    error: str = ""

class MQTTSensorIngest:
    """Ingests MQTT hive temperature messages, validates, stores to SharedMemory, triggers anomaly alerts."""

    TEMP_MIN = -40.0
    TEMP_MAX = 80.0
    ANOMALY_LOW = 10.0   # Below this = cold anomaly for active hive
    ANOMALY_HIGH = 45.0  # Above this = heat anomaly

    def __init__(self, on_store: Callable[[SensorReading], None] | None = None,
                 on_anomaly: Callable[[SensorReading, str], None] | None = None):
        self._on_store = on_store
        self._on_anomaly = on_anomaly
        self._readings: list[SensorReading] = []

    def parse_topic(self, topic: str) -> str | None:
        """Extract hive_id from MQTT topic. Returns None if topic doesn't match."""
        m = TOPIC_PATTERN.match(topic)
        return m.group(1) if m else None

    def parse_payload(self, payload: str | bytes) -> float | None:
        """Parse temperature from payload. Returns None if invalid."""
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            return float(payload.strip())
        except (ValueError, UnicodeDecodeError):
            return None

    def validate_range(self, temp_c: float) -> tuple[bool, str]:
        """Check if temperature is within valid range."""
        if temp_c < self.TEMP_MIN:
            return False, f"Below minimum ({self.TEMP_MIN}\u00b0C)"
        if temp_c > self.TEMP_MAX:
            return False, f"Above maximum ({self.TEMP_MAX}\u00b0C)"
        return True, ""

    def check_anomaly(self, reading: SensorReading) -> str | None:
        """Return anomaly description or None."""
        if reading.temperature_c < self.ANOMALY_LOW:
            return f"Cold anomaly: {reading.temperature_c:.1f}\u00b0C (threshold {self.ANOMALY_LOW}\u00b0C)"
        if reading.temperature_c > self.ANOMALY_HIGH:
            return f"Heat anomaly: {reading.temperature_c:.1f}\u00b0C (threshold {self.ANOMALY_HIGH}\u00b0C)"
        return None

    def on_message(self, topic: str, payload: str | bytes) -> SensorReading | None:
        """Process an incoming MQTT message. Returns SensorReading or None if topic doesn't match."""
        hive_id = self.parse_topic(topic)
        if hive_id is None:
            return None

        temp = self.parse_payload(payload)
        raw_payload = payload.decode("utf-8") if isinstance(payload, bytes) else payload

        if temp is None:
            reading = SensorReading(
                hive_id=hive_id, temperature_c=0.0, raw_topic=topic,
                raw_payload=raw_payload, valid=False, error="Invalid payload")
            log.warning(f"Invalid temperature payload from {topic}: {raw_payload}")
            return reading

        valid, error = self.validate_range(temp)
        reading = SensorReading(
            hive_id=hive_id, temperature_c=temp, raw_topic=topic,
            raw_payload=raw_payload, valid=valid, error=error)

        if valid:
            self._readings.append(reading)
            if self._on_store:
                self._on_store(reading)

            anomaly = self.check_anomaly(reading)
            if anomaly and self._on_anomaly:
                self._on_anomaly(reading, anomaly)
        else:
            log.warning(f"Out-of-range temperature from {topic}: {temp}\u00b0C \u2014 {error}")

        return reading

    @property
    def reading_count(self) -> int:
        return len(self._readings)

    def recent_readings(self, hive_id: str | None = None, limit: int = 10) -> list[SensorReading]:
        readings = self._readings if hive_id is None else [r for r in self._readings if r.hive_id == hive_id]
        return readings[-limit:]
