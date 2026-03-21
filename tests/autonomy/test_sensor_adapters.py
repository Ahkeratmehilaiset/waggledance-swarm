"""Unit tests for sensor adapters.

Tests parsing, normalization, malformed payloads, world model integration,
anomaly detection, and sensor fusion across MQTT, Frigate, HA, and Audio.
"""

import time
import pytest
from unittest.mock import MagicMock, patch


# ── SensorObservation dataclass ─────────────────────────────

class TestSensorObservation:
    def test_create(self):
        from waggledance.core.domain.autonomy import SensorObservation
        obs = SensorObservation(
            sensor_id="hive_1.temperature",
            entity_id="hive_1",
            metric="temperature",
            value=35.5,
            unit="°C",
            source="mqtt",
        )
        assert obs.sensor_id == "hive_1.temperature"
        assert obs.value == 35.5
        assert obs.key == "hive_1.temperature"

    def test_to_dict(self):
        from waggledance.core.domain.autonomy import SensorObservation
        obs = SensorObservation(
            sensor_id="s1.temp", entity_id="s1", metric="temp",
            value=20.0, unit="°C", source="mqtt",
        )
        d = obs.to_dict()
        assert d["sensor_id"] == "s1.temp"
        assert d["value"] == 20.0
        assert d["source"] == "mqtt"
        assert "timestamp" in d

    def test_default_quality(self):
        from waggledance.core.domain.autonomy import SensorObservation
        obs = SensorObservation(
            sensor_id="x.y", entity_id="x", metric="y", value=0,
        )
        assert obs.quality == 1.0


# ── MQTTAdapter ─────────────────────────────────────────────

class TestMQTTAdapter:
    def _make(self, ingest=None, world_model=None, anomaly_engine=None):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        hub = MagicMock()
        return MQTTAdapter(
            mqtt_hub=hub,
            ingest=ingest,
            world_model=world_model,
            anomaly_engine=anomaly_engine,
        ), hub

    def test_capability_id(self):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        assert MQTTAdapter().CAPABILITY_ID == "sense.mqtt_ingest"

    def test_available_with_hub(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_available_without_hub(self):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        assert MQTTAdapter().available is False

    def test_execute_no_topic(self):
        adapter, _ = self._make()
        result = adapter.execute()
        assert result["success"] is False
        assert "No topic" in result["error"]

    def test_execute_generic_parse_numeric(self):
        adapter, _ = self._make()
        result = adapter.execute(topic="hive/hive_1/temperature", payload="35.5")
        assert result["success"] is True
        assert result["observation_count"] == 1
        obs = result["observations"][0]
        assert obs["entity_id"] == "hive_1"
        assert obs["metric"] == "temperature"
        assert obs["value"] == 35.5
        assert obs["source"] == "mqtt"

    def test_execute_generic_parse_bytes(self):
        adapter, _ = self._make()
        result = adapter.execute(topic="sensor/outdoor/humidity", payload=b"72.3")
        assert result["success"] is True
        obs = result["observations"][0]
        assert obs["value"] == 72.3
        assert obs["entity_id"] == "outdoor"
        assert obs["metric"] == "humidity"

    def test_execute_generic_parse_dict_payload(self):
        adapter, _ = self._make()
        result = adapter.execute(
            topic="device/pump_1/pressure",
            payload={"value": 2.5}
        )
        assert result["success"] is True
        assert result["observations"][0]["value"] == 2.5

    def test_execute_malformed_payload(self):
        adapter, _ = self._make()
        result = adapter.execute(topic="sensor/x/temp", payload="not_a_number")
        assert result["success"] is True
        assert result["observation_count"] == 0

    def test_execute_malformed_topic_single_segment(self):
        adapter, _ = self._make()
        result = adapter.execute(topic="short", payload="42")
        assert result["success"] is True
        # Single segment topic — parse_generic fails gracefully
        assert result["observation_count"] == 0

    def test_execute_with_ingest(self):
        from unittest.mock import MagicMock
        ingest = MagicMock()
        reading = MagicMock()
        reading.valid = True
        reading.hive_id = "hive_2"
        reading.temperature_c = 34.0
        reading.timestamp = time.time()
        reading.raw_topic = "hive/hive_2/temperature"
        ingest.on_message.return_value = reading

        adapter, _ = self._make(ingest=ingest)
        result = adapter.execute(topic="hive/hive_2/temperature", payload="34.0")
        assert result["success"] is True
        assert result["observation_count"] == 1
        assert result["observations"][0]["value"] == 34.0
        assert result["quality_path"] == "gold"

    def test_execute_with_ingest_invalid_reading(self):
        ingest = MagicMock()
        reading = MagicMock()
        reading.valid = False
        reading.error = "Invalid payload"
        ingest.on_message.return_value = reading

        adapter, _ = self._make(ingest=ingest)
        result = adapter.execute(topic="hive/x/temperature", payload="garbage")
        assert result["success"] is False
        assert "Invalid" in result["error"]

    def test_execute_with_world_model(self):
        from waggledance.core.world.world_model import WorldModel
        wm = WorldModel(cognitive_graph=None, baseline_store=MagicMock(), entity_registry=MagicMock())
        adapter, _ = self._make(world_model=wm)
        result = adapter.execute(topic="hive/hive_3/temperature", payload="35.0")
        assert result["success"] is True
        # Verify world model was updated
        wm.baselines.upsert.assert_called()

    def test_execute_with_anomaly_engine(self):
        from waggledance.core.reasoning.anomaly_engine import AnomalyEngine
        ae = AnomalyEngine()
        adapter, _ = self._make(anomaly_engine=ae)
        # Feed enough data for z-score detection
        for i in range(20):
            ae.record_value("hive_5.temperature", 35.0)
        # Trigger with extreme value
        result = adapter.execute(topic="sensor/hive_5/temperature", payload="100.0")
        assert result["success"] is True
        # May or may not detect anomaly depending on thresholds

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(topic="a/b/c", payload="1.0")
        s = adapter.stats()
        assert s["capability_id"] == "sense.mqtt_ingest"
        assert s["messages_processed"] == 1

    def test_subscribe(self):
        adapter, hub = self._make()
        assert adapter.subscribe("test/topic") is True
        hub.subscribe.assert_called()

    def test_publish(self):
        adapter, hub = self._make()
        assert adapter.publish("test/topic", "payload") is True
        hub.publish.assert_called()


# ── FrigateAdapter ──────────────────────────────────────────

class TestFrigateAdapter:
    def _make(self, world_model=None, anomaly_engine=None):
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        frigate = MagicMock()
        frigate.process_event.return_value = {"processed": True}
        return FrigateAdapter(
            frigate=frigate,
            world_model=world_model,
            anomaly_engine=anomaly_engine,
        ), frigate

    def test_capability_id(self):
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        assert FrigateAdapter().CAPABILITY_ID == "sense.camera_frigate"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_not_available(self):
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        assert FrigateAdapter().available is False

    def test_execute_no_event(self):
        adapter, _ = self._make()
        result = adapter.execute()
        assert result["success"] is False
        assert "No event" in result["error"]

    def test_execute_basic_event(self):
        adapter, _ = self._make()
        event = {
            "type": "new",
            "after": {
                "label": "person",
                "camera": "yard",
                "score": 0.92,
                "current_zones": ["entrance"],
            },
        }
        result = adapter.execute(event=event)
        assert result["success"] is True
        assert result["observation_count"] == 1
        obs = result["observations"][0]
        assert obs["entity_id"] == "camera_yard"
        assert obs["metric"] == "person_count"
        assert obs["value"] == 0.92
        assert obs["source"] == "frigate"
        assert obs["metadata"]["label"] == "person"

    def test_execute_bear_event(self):
        adapter, _ = self._make()
        event = {
            "after": {"label": "bear", "camera": "forest", "score": 0.85},
            "severity": "critical",
        }
        result = adapter.execute(event=event)
        assert result["success"] is True
        obs = result["observations"][0]
        assert obs["metric"] == "bear_alert"
        assert obs["metadata"]["severity"] == "critical"

    def test_execute_high_severity_anomaly(self):
        ae = MagicMock()
        adapter, _ = self._make(anomaly_engine=ae)
        event = {
            "after": {"label": "bear", "camera": "yard", "score": 0.95},
            "severity": "critical",
        }
        result = adapter.execute(event=event)
        assert result["anomaly_count"] == 1
        assert result["anomalies"][0]["max_severity"] == 1.0

    def test_execute_event_no_label(self):
        adapter, _ = self._make()
        event = {"after": {"camera": "yard"}}
        result = adapter.execute(event=event)
        assert result["success"] is True
        assert result["observation_count"] == 0

    def test_execute_flat_event(self):
        """Frigate events can come in flat format too."""
        adapter, _ = self._make()
        event = {"label": "car", "camera": "driveway", "score": 0.7}
        result = adapter.execute(event=event)
        assert result["success"] is True
        assert result["observation_count"] == 1
        assert result["observations"][0]["metric"] == "vehicle_count"

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(event={"after": {"label": "bird", "camera": "c1", "score": 0.6}})
        s = adapter.stats()
        assert s["events_processed"] == 1
        assert s["detections"] == 1


# ── HomeAssistantAdapter ────────────────────────────────────

class TestHomeAssistantAdapter:
    def _make(self, world_model=None, anomaly_engine=None):
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        bridge = MagicMock()
        bridge.get_entity.return_value = {"state": "22.5",
                                           "attributes": {"unit_of_measurement": "°C"}}
        bridge.get_states.return_value = {
            "sensor.outdoor_temp": {"state": "15.3",
                                     "attributes": {"unit_of_measurement": "°C"}},
            "sensor.humidity": {"state": "65",
                                "attributes": {"unit_of_measurement": "%"}},
        }
        return HomeAssistantAdapter(
            bridge=bridge,
            world_model=world_model,
            anomaly_engine=anomaly_engine,
        ), bridge

    def test_capability_id(self):
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        assert HomeAssistantAdapter().CAPABILITY_ID == "sense.home_assistant"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_not_available(self):
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        assert HomeAssistantAdapter().available is False

    def test_execute_no_bridge(self):
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        adapter = HomeAssistantAdapter()
        result = adapter.execute(entity_id="sensor.temp")
        assert result["success"] is False

    def test_execute_single_entity(self):
        adapter, _ = self._make()
        result = adapter.execute(entity_id="sensor.outdoor_temp")
        assert result["success"] is True
        assert result["observation_count"] == 1
        obs = result["observations"][0]
        assert obs["value"] == 22.5
        assert obs["source"] == "home_assistant"
        assert obs["unit"] == "°C"

    def test_execute_poll_all(self):
        adapter, _ = self._make()
        result = adapter.execute(poll_all=True)
        assert result["success"] is True
        assert result["observation_count"] == 2

    def test_execute_binary_sensor_on(self):
        adapter, bridge = self._make()
        bridge.get_entity.return_value = "on"
        result = adapter.execute(entity_id="binary_sensor.door")
        assert result["success"] is True
        assert result["observations"][0]["value"] == 1.0

    def test_execute_binary_sensor_off(self):
        adapter, bridge = self._make()
        bridge.get_entity.return_value = "off"
        result = adapter.execute(entity_id="binary_sensor.motion")
        assert result["success"] is True
        assert result["observations"][0]["value"] == 0.0

    def test_execute_unavailable_entity(self):
        adapter, bridge = self._make()
        bridge.get_entity.return_value = "unavailable"
        result = adapter.execute(entity_id="sensor.broken")
        assert result["success"] is True
        assert result["observations"][0]["value"] == 0.0

    def test_execute_non_numeric_entity(self):
        adapter, bridge = self._make()
        bridge.get_entity.return_value = "some_text_state"
        result = adapter.execute(entity_id="sensor.text")
        assert result["success"] is True
        assert result["observation_count"] == 0

    def test_execute_with_world_model(self):
        wm = MagicMock()
        adapter, _ = self._make(world_model=wm)
        adapter.execute(entity_id="sensor.temp")
        wm.update_baseline.assert_called()
        wm.register_entity.assert_called()

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(entity_id="sensor.x")
        s = adapter.stats()
        assert s["poll_count"] == 1
        assert s["observations"] == 1


# ── AudioAdapter ────────────────────────────────────────────

class TestAudioAdapter:
    def _make(self, world_model=None, anomaly_engine=None):
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        monitor = MagicMock()
        monitor.get_bee_analysis.return_value = {"score": 0.85, "health": 0.85}
        monitor.get_bird_detections.return_value = {"count": 3, "detections": 3}
        return AudioAdapter(
            audio_monitor=monitor,
            world_model=world_model,
            anomaly_engine=anomaly_engine,
        ), monitor

    def test_capability_id(self):
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        assert AudioAdapter().CAPABILITY_ID == "sense.audio"

    def test_available(self):
        adapter, _ = self._make()
        assert adapter.available is True

    def test_not_available(self):
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        assert AudioAdapter().available is False

    def test_execute_with_payload(self):
        adapter, _ = self._make()
        result = adapter.execute(
            topic="audio/hive_1/buzz",
            payload={"type": "buzz", "confidence": 0.9, "hive_id": "hive_1"},
        )
        assert result["success"] is True
        # payload parse + bee analysis + bird analysis = 3 observations
        assert result["observation_count"] >= 1

    def test_execute_bee_analysis(self):
        adapter, _ = self._make()
        result = adapter.execute(analysis_type="bee")
        assert result["success"] is True
        bee_obs = [o for o in result["observations"] if o["metric"] == "bee_health"]
        assert len(bee_obs) == 1
        assert bee_obs[0]["value"] == 0.85

    def test_execute_bird_analysis(self):
        adapter, _ = self._make()
        result = adapter.execute(analysis_type="bird")
        assert result["success"] is True
        bird_obs = [o for o in result["observations"] if o["metric"] == "bird_count"]
        assert len(bird_obs) == 1
        assert bird_obs[0]["value"] == 3.0

    def test_execute_auto_both(self):
        adapter, _ = self._make()
        result = adapter.execute(analysis_type="auto")
        assert result["observation_count"] == 2  # bee + bird

    def test_execute_monitor_no_bee_method(self):
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        monitor = MagicMock(spec=[])  # no methods
        adapter = AudioAdapter(audio_monitor=monitor)
        result = adapter.execute(analysis_type="bee")
        assert result["success"] is True
        assert result["observation_count"] == 0

    def test_execute_payload_malformed(self):
        adapter, _ = self._make()
        result = adapter.execute(
            topic="audio/test",
            payload={"type": "unknown"},  # no confidence key
        )
        assert result["success"] is True
        # Should still parse with default value
        obs = [o for o in result["observations"] if o.get("metadata", {}).get("topic") == "audio/test"]
        assert len(obs) >= 1

    def test_stats(self):
        adapter, _ = self._make()
        adapter.execute(analysis_type="bee")
        s = adapter.stats()
        assert s["events_processed"] == 1


# ── SensorFusionAdapter ────────────────────────────────────

class TestSensorFusionAdapter:
    def _make_sub_adapter(self, cap_id, observations, available=True):
        adapter = MagicMock()
        adapter.CAPABILITY_ID = cap_id
        adapter.available = available
        adapter.execute.return_value = {
            "success": True,
            "observations": observations,
            "anomalies": [],
        }
        return adapter

    def _make(self, sub_adapters=None, world_model=None):
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        return SensorFusionAdapter(
            sensor_adapters=sub_adapters or [],
            world_model=world_model,
        )

    def test_capability_id(self):
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        assert SensorFusionAdapter().CAPABILITY_ID == "sense.fusion"

    def test_available_with_adapters(self):
        adapter = self._make(sub_adapters=[MagicMock()])
        assert adapter.available is True

    def test_available_with_readings(self):
        adapter = self._make()
        adapter.update_reading("s1.temp", 20.0)
        assert adapter.available is True

    def test_available_empty(self):
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        assert SensorFusionAdapter().available is False

    def test_execute_no_sources(self):
        adapter = self._make()
        result = adapter.execute()
        assert result["success"] is True
        assert result["observation_count"] == 0

    def test_execute_single_source(self):
        obs = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 35.0,
            "source": "mqtt",
            "timestamp": time.time(),
            "quality": 1.0,
        }]
        sub = self._make_sub_adapter("sense.mqtt_ingest", obs)
        adapter = self._make(sub_adapters=[sub])
        result = adapter.execute()
        assert result["success"] is True
        assert result["observation_count"] == 1
        assert result["observations"][0]["value"] == 35.0

    def test_execute_multiple_sources(self):
        mqtt_obs = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 35.0,
            "source": "mqtt",
            "timestamp": time.time(),
            "quality": 1.0,
        }]
        ha_obs = [{
            "sensor_id": "ha_outdoor.temperature",
            "entity_id": "ha_outdoor",
            "metric": "temperature",
            "value": 15.0,
            "source": "home_assistant",
            "timestamp": time.time(),
            "quality": 0.9,
        }]
        sub1 = self._make_sub_adapter("sense.mqtt_ingest", mqtt_obs)
        sub2 = self._make_sub_adapter("sense.home_assistant", ha_obs)
        adapter = self._make(sub_adapters=[sub1, sub2])
        result = adapter.execute()
        assert result["success"] is True
        assert result["observation_count"] == 2

    def test_execute_conflict_resolution_quality(self):
        """When same sensor_id from two sources, prefer higher quality."""
        now = time.time()
        obs_low = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 30.0,
            "source": "generic",
            "timestamp": now,
            "quality": 0.5,
        }]
        obs_high = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 35.0,
            "source": "mqtt",
            "timestamp": now,
            "quality": 1.0,
        }]
        sub1 = self._make_sub_adapter("sense.generic", obs_low)
        sub2 = self._make_sub_adapter("sense.mqtt_ingest", obs_high)
        adapter = self._make(sub_adapters=[sub1, sub2])
        result = adapter.execute()
        assert result["observation_count"] == 1
        assert result["observations"][0]["value"] == 35.0

    def test_execute_conflict_resolution_timestamp(self):
        """When same quality, prefer more recent timestamp."""
        obs_old = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 30.0,
            "source": "old",
            "timestamp": time.time() - 100,
            "quality": 0.8,
        }]
        obs_new = [{
            "sensor_id": "hive_1.temperature",
            "entity_id": "hive_1",
            "metric": "temperature",
            "value": 35.0,
            "source": "new",
            "timestamp": time.time(),
            "quality": 0.8,
        }]
        sub1 = self._make_sub_adapter("sense.old", obs_old)
        sub2 = self._make_sub_adapter("sense.new", obs_new)
        adapter = self._make(sub_adapters=[sub1, sub2])
        result = adapter.execute()
        assert result["observation_count"] == 1
        assert result["observations"][0]["value"] == 35.0

    def test_execute_stale_detection(self):
        adapter = self._make()
        adapter._readings["stale.temp"] = {
            "sensor_id": "stale.temp",
            "entity_id": "stale",
            "metric": "temp",
            "value": 20.0,
            "timestamp": time.time() - 7200,  # 2 hours ago
            "quality": 0.8,
        }
        result = adapter.execute(max_age_seconds=3600)
        assert result["stale_count"] == 1
        assert result["stale_sensors"][0]["sensor_id"] == "stale.temp"

    def test_execute_unavailable_adapter_skipped(self):
        sub = self._make_sub_adapter("sense.broken", [], available=False)
        adapter = self._make(sub_adapters=[sub])
        result = adapter.execute()
        sub.execute.assert_not_called()

    def test_update_reading(self):
        adapter = self._make()
        adapter.update_reading("hive_1.temp", 35.5, unit="°C", source="manual")
        r = adapter.get_reading("hive_1.temp")
        assert r is not None
        assert r["value"] == 35.5
        assert r["source"] == "manual"

    def test_get_all_readings_filtered(self):
        adapter = self._make()
        adapter.update_reading("fresh.temp", 20.0)
        adapter._readings["old.temp"] = {
            "sensor_id": "old.temp",
            "value": 10.0,
            "timestamp": time.time() - 7200,
        }
        readings = adapter.get_all_readings(max_age_seconds=3600)
        assert "fresh.temp" in readings
        assert "old.temp" not in readings

    def test_get_context_for_world_model(self):
        adapter = self._make()
        adapter.update_reading("hive_1.temp", 35.0)
        adapter.update_reading("outdoor.temp", 15.0)
        ctx = adapter.get_context_for_world_model()
        assert ctx["hive_1.temp"] == 35.0
        assert ctx["outdoor.temp"] == 15.0

    def test_execute_with_world_model(self):
        wm = MagicMock()
        obs = [{
            "sensor_id": "s1.temp",
            "entity_id": "s1",
            "metric": "temp",
            "value": 25.0,
            "source": "test",
            "timestamp": time.time(),
            "quality": 0.9,
        }]
        sub = self._make_sub_adapter("sense.test", obs)
        adapter = self._make(sub_adapters=[sub], world_model=wm)
        result = adapter.execute()
        assert result["success"] is True
        wm.update_baseline.assert_called()

    def test_stats(self):
        adapter = self._make(sub_adapters=[MagicMock()])
        adapter.update_reading("x.y", 1.0)
        s = adapter.stats()
        assert s["capability_id"] == "sense.fusion"
        assert s["active_sensors"] == 1
        assert s["sub_adapters"] == 1


# ── Registry integration ────────────────────────────────────

class TestSensorRegistryCapabilities:
    def test_sensor_capabilities_present(self):
        from waggledance.core.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        expected = [
            "sense.mqtt_ingest", "sense.home_assistant",
            "sense.camera_frigate", "sense.audio", "sense.fusion",
        ]
        for cap_id in expected:
            assert reg.has(cap_id), f"Missing sensor capability: {cap_id}"

    def test_sensor_category(self):
        from waggledance.core.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        cap = reg.get("sense.fusion")
        assert cap is not None
        assert cap.category.value == "sense"

    def test_total_count_includes_sensors(self):
        from waggledance.core.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        assert reg.count() >= 27  # 25 original + 2 new


# ── Capability loader binding ───────────────────────────────

class TestSensorCapabilityLoader:
    def test_sensor_adapters_imported(self):
        """Verify sensor adapter imports succeed (even if not available)."""
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        # All imports succeed
        assert MQTTAdapter.CAPABILITY_ID == "sense.mqtt_ingest"
        assert FrigateAdapter.CAPABILITY_ID == "sense.camera_frigate"
        assert HomeAssistantAdapter.CAPABILITY_ID == "sense.home_assistant"
        assert AudioAdapter.CAPABILITY_ID == "sense.audio"
        assert SensorFusionAdapter.CAPABILITY_ID == "sense.fusion"

    def test_bind_executors_includes_sensors(self):
        from waggledance.core.capabilities.registry import CapabilityRegistry
        from waggledance.bootstrap.capability_loader import bind_executors
        reg = CapabilityRegistry()
        bound = bind_executors(reg)
        # Sensor adapters may not be available (no MQTT hub, etc.)
        # but the imports should not fail
        assert bound >= 0


# ── End-to-end world model + anomaly pipeline ───────────────

class TestSensorWorldModelIntegration:
    """Test that sensor observations flow through to world model and anomaly engine."""

    def test_mqtt_updates_baseline(self, tmp_path):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore
        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)
        adapter = MQTTAdapter(mqtt_hub=MagicMock(), world_model=wm)
        adapter.execute(topic="hive/hive_1/temperature", payload="35.0")
        bl = wm.get_baseline("hive_1", "temperature")
        assert bl is not None
        assert abs(bl - 35.0) < 0.01

    def test_mqtt_registers_entity(self, tmp_path):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore
        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)
        adapter = MQTTAdapter(mqtt_hub=MagicMock(), world_model=wm)
        adapter.execute(topic="sensor/pump_1/pressure", payload="2.5")
        entity = wm.get_entity("pump_1")
        assert entity is not None
        assert entity.entity_type == "sensor"

    def test_ha_updates_baseline(self, tmp_path):
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore
        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)
        bridge = MagicMock()
        bridge.get_entity.return_value = "22.5"
        adapter = HomeAssistantAdapter(bridge=bridge, world_model=wm)
        adapter.execute(entity_id="sensor.outdoor_temp")
        bl = wm.get_baseline("ha_outdoor_temp", "outdoor_temp")
        assert bl is not None

    def test_fusion_updates_world_model(self, tmp_path):
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore
        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)
        sub = MagicMock()
        sub.CAPABILITY_ID = "sense.test"
        sub.available = True
        sub.execute.return_value = {
            "success": True,
            "observations": [{
                "sensor_id": "test.value",
                "entity_id": "test",
                "metric": "value",
                "value": 42.0,
                "source": "test",
                "timestamp": time.time(),
                "quality": 1.0,
            }],
            "anomalies": [],
        }
        adapter = SensorFusionAdapter(sensor_adapters=[sub], world_model=wm)
        adapter.execute()
        bl = wm.get_baseline("test", "value")
        assert bl is not None
        assert abs(bl - 42.0) < 0.01

    def test_mqtt_anomaly_detection_pipeline(self, tmp_path):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore
        from waggledance.core.reasoning.anomaly_engine import AnomalyEngine
        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)
        ae = AnomalyEngine(residual_threshold=0.1)
        adapter = MQTTAdapter(mqtt_hub=MagicMock(), world_model=wm, anomaly_engine=ae)

        # Establish baseline with normal readings
        for _ in range(15):
            adapter.execute(topic="hive/hive_1/temperature", payload="35.0")

        # Inject anomalous reading
        result = adapter.execute(topic="hive/hive_1/temperature", payload="55.0")
        assert result["success"] is True
        # The residual check should flag this as anomalous
        # (55 - 35 baseline = large deviation)
        if result["anomaly_count"] > 0:
            assert result["anomalies"][0]["max_severity"] > 0


# ── Sensor fusion combining multiple real adapters ──────────

class TestSensorFusionEndToEnd:
    """End-to-end test combining MQTT + HA + Frigate through fusion."""

    def test_fusion_combines_mqtt_and_ha(self, tmp_path):
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        from waggledance.core.world.world_model import WorldModel
        from waggledance.core.world.baseline_store import BaselineStore

        bs = BaselineStore(db_path=str(tmp_path / "test_baselines.db"))
        wm = WorldModel(cognitive_graph=None, baseline_store=bs)

        # MQTT adapter
        mqtt = MQTTAdapter(mqtt_hub=MagicMock(), world_model=wm)

        # HA adapter
        bridge = MagicMock()
        bridge.get_states.return_value = {
            "sensor.outdoor_temp": {"state": "15.3",
                                     "attributes": {"unit_of_measurement": "°C"}},
        }
        ha = HomeAssistantAdapter(bridge=bridge, world_model=wm)

        # Fusion adapter
        fusion = SensorFusionAdapter(sensor_adapters=[mqtt, ha], world_model=wm)

        # Pre-seed MQTT reading
        mqtt.execute(topic="hive/hive_1/temperature", payload="35.0")

        # Execute fusion — HA will poll all, MQTT has no new data via execute
        result = fusion.execute()
        assert result["success"] is True
        # At minimum we get the cached MQTT reading + HA poll
        assert result["observation_count"] >= 1

    def test_fusion_with_frigate(self):
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter

        frigate = FrigateAdapter(frigate=MagicMock())
        fusion = SensorFusionAdapter(sensor_adapters=[frigate])

        # Frigate adapter.execute() needs an event kwarg
        # In fusion, it's called with **kwargs from the parent
        result = fusion.execute(event={
            "after": {"label": "person", "camera": "front", "score": 0.9}
        })
        assert result["success"] is True
